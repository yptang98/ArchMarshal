"""Reconcile observed Codex Skill state with project review evidence, read-only."""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ArchMarshalError
from .portable import canonical, fail, relative, text
from .project_context import resolve_project_context
from .safety import (
    EXCLUDED_BACKUP_PARTS,
    ensure_managed_path,
    ensure_unlinked_path,
    fingerprint_directory,
    fingerprint_directory_matches,
)
from .skill_activation import skill_activation_block_reason
from .skill_index import load_skill_index

OBSERVATION_FORMAT = "archmarshal-codex-skills-observation-v1"
MAX_OBSERVATION_BYTES = 4 * 1024 * 1024
MAX_HOST_SKILLS = 1000
MAX_VERIFY_BYTES = 64 * 1024 * 1024
MAX_VERIFY_FILES = 10_000


def _path_key(value: Any) -> str | None:
    # Lexical matching only: host-supported symlinks are not followed or adopted.
    if not isinstance(value, str) or len(value) > 4096 or "\x00" in value:
        return None
    path = Path(value)
    return os.path.normcase(os.path.normpath(value)) if path.is_absolute() else None


def _observe(snapshot: dict[str, Any] | None, work: Path, host_id: str,
             max_age: int, now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if snapshot is None:
        return {"state": "unavailable", "live_runtime_verified": False}, []
    if (not isinstance(snapshot, dict) or len(canonical(snapshot)) > MAX_OBSERVATION_BYTES
            or snapshot.get("format") != OBSERVATION_FORMAT
            or snapshot.get("host_id") != host_id
            or type(snapshot.get("invalidated")) is not bool):
        fail("Unsupported Skill observation or different host identity.", "host_skills_invalid")
    response = snapshot.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list) or len(data) > 100:
        fail("Invalid bounded skills/list response.", "host_skills_invalid")
    matches = [entry for entry in data if isinstance(entry, dict)
               and _path_key(entry.get("cwd")) == _path_key(str(work))]
    if len(matches) != 1:
        fail("Skill observation must contain exactly one entry for the actual working directory.",
             "host_skills_cwd_mismatch")
    entry = matches[0]
    skills, errors = entry.get("skills"), entry.get("errors")
    if (not isinstance(skills, list) or len(skills) > MAX_HOST_SKILLS
            or not isinstance(errors, list) or len(errors) > MAX_HOST_SKILLS):
        fail("Skill observation exceeds its structure budget.", "host_skills_invalid")
    for skill in skills:
        if not isinstance(skill, dict) or not isinstance(skill.get("name"), str):
            fail("Invalid native Skill metadata.", "host_skills_invalid")
        text(skill["name"], "Skill name", 500, required=True)
        if type(skill.get("enabled")) is not bool:
            fail("Native Skill enabled state must be an explicit boolean.", "host_skills_invalid")
    origin = snapshot.get("origin", "supplied-observation")
    text(origin, "observation origin", 200, required=True)
    server = snapshot.get("server") or {}
    if not isinstance(server, dict):
        fail("Invalid native server identity.", "host_skills_invalid")
    server = {key: server.get(key) for key in ("userAgent", "platformOs", "platformFamily")}
    for value in server.values():
        if value is not None:
            text(value, "native server identity", 1000)
    try:
        observed = datetime.fromisoformat(snapshot["observed_at"])
        if observed.utcoffset() is None:
            raise ValueError("timezone missing")
        age = (now - observed).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise ArchMarshalError("host_skills_invalid", "Skill observation needs a timezone-aware timestamp.") from exc
    state = "invalidated" if snapshot["invalidated"] else "future" if age < -30 else "stale" if age > max_age else "fresh"
    return {"state": state, "observed_at": observed.isoformat(), "age_seconds": round(age, 3),
            "origin": origin, "host_id": host_id,
            "server": server, "discovery_error_count": len(errors),
            "live_runtime_verified": False}, skills


def _source_key(work: Path, source: str) -> str:
    return str(work) if source == "." else str(work / relative(source))


def _verify_source(work: Path, source: str, manifest: dict[str, Any], boundaries: list[str],
                   budget: list[int], *, installed_path: str | None = None) -> dict[str, Any]:
    expected = (manifest.get("source") or {}).get("package_sha256")
    if not expected:
        return {"state": "no_recorded_package_hash"}
    if budget[0] <= 0 or budget[1] <= 0:
        return {"state": "budget_exhausted"}
    try:
        source_directory = Path(_source_key(work, source))
        directory = (ensure_unlinked_path(Path(installed_path).parent, purpose="Explicit installed Skill")
                     if installed_path else ensure_managed_path(work, source_directory, purpose="Selected Skill source"))
        if not (directory / "SKILL.md").is_file():
            return {"state": "missing"}
        nested = [directory / Path(_source_key(work, item)).relative_to(source_directory) for item in boundaries
                  if item != source and source_directory in Path(_source_key(work, item)).parents]
        if installed_path:
            # A mapped installation may itself contain other project packages.
            # Preserve their actual boundaries as well as the source's mapped ones.
            nested.extend(Path(_source_key(work, item)) for item in boundaries
                          if directory in Path(_source_key(work, item)).parents)
        fingerprint = fingerprint_directory(directory, purpose="Selected Skill verification",
                                            entrypoint_only=source_directory == work, include_modes=True,
                                            excluded_parts=EXCLUDED_BACKUP_PARTS, excluded_directories=nested,
                                            max_content_bytes=budget[0], max_files=budget[1])
        budget[0] -= fingerprint["content_bytes"]
        budget[1] -= fingerprint["file_count"]
        return {"state": "verified" if fingerprint_directory_matches(fingerprint, expected) else "changed",
                "recorded_package_sha256": expected, "current_package_sha256": fingerprint["sha256"]}
    except (ArchMarshalError, OSError):
        # A failed scan may have spent its full allowance before discovering drift.
        budget[:] = [0, 0]
        return {"state": "unsafe_or_unreadable"}


def reconcile_host_skills(project_root: Path | str, *, work_root: Path | str | None = None,
                          snapshot: dict[str, Any] | None = None, host_id: str = "local",
                          max_age: int = 300, verify_sources: list[str] | None = None,
                          installed_paths: dict[str, str] | None = None,
                          limit: int = 100) -> dict[str, Any]:
    if type(max_age) is not int or not 0 <= max_age <= 86400 or type(limit) is not int or not 1 <= limit <= 1000:
        fail("Invalid Skill observation age or output limit.", "host_skills_invalid")
    context = resolve_project_context(project_root, work_root, host_id=host_id)
    work = Path(context["work_root"])
    requested = [] if verify_sources is None else verify_sources
    if not isinstance(requested, list) or len(requested) > 20 or any(not isinstance(p, str) for p in requested):
        fail("Select at most 20 exact project-relative Skill sources.", "host_skills_invalid")
    loaded = load_skill_index(work)
    generation = loaded.get("generation") or {}
    records = generation.get("skills", [])
    excluded = (generation.get("selection") or {}).get("excluded_packages", [])
    boundaries = [record["source"] for record in records] + excluded
    def is_excluded(source: str) -> bool:
        path = Path(_source_key(work, source))
        return any(path == Path(_source_key(work, item)) or Path(_source_key(work, item)) in path.parents
                   for item in excluded)
    indexed = {record["source"]: record for record in records if record["state"] == "active"}
    installed = installed_paths if installed_paths is not None else {}
    if not isinstance(installed, dict) or len(installed) > 20:
        fail("Installed Skill bindings must be a bounded explicit mapping.", "host_skills_source_invalid")
    installed_keys = set()
    for source, target in installed.items():
        key = _path_key(target)
        target_excluded = key is not None and any(
            Path(_path_key(_source_key(work, item))) in (Path(key).parent, *Path(key).parent.parents)
            for item in excluded)
        if (source not in indexed or is_excluded(source) or key is None
                or Path(key).name.lower() != "skill.md" or key in installed_keys or target_excluded):
            fail("Installed bindings require unique absolute SKILL.md paths and non-excluded sources and targets.",
                 "host_skills_source_invalid")
        installed_keys.add(key)
    effective_keys = [_path_key(installed.get(source, str(Path(_source_key(work, source)) / "SKILL.md")))
                      for source in indexed]
    if len(set(effective_keys)) != len(effective_keys):
        fail("Installed Skill bindings collide with another indexed Skill path.", "host_skills_source_invalid")
    for source in requested:
        if source not in indexed or is_excluded(source):
            fail("Verification requires an exact, non-excluded indexed Skill source.", "host_skills_source_invalid")
    observation, native = _observe(snapshot, work, host_id, max_age, datetime.now(timezone.utc))
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved = []
    for skill in native:
        key = _path_key(skill.get("path"))
        if key is None:
            unresolved.append({"name": skill["name"], "native_enabled_at_observation": skill["enabled"],
                               "alignment": "native_path_unavailable", "review": "unknown"})
        else:
            by_path[key].append(skill)
    rows = []
    budget = [MAX_VERIFY_BYTES, MAX_VERIFY_FILES]
    for source, record in sorted(indexed.items(), key=lambda item: (item[0] not in requested, item[0])):
        key = _path_key(installed.get(source, str(Path(_source_key(work, source)) / "SKILL.md")))
        host_rows = by_path.pop(key, [])
        manifest = record["manifest"]
        excluded_source = is_excluded(source)
        reason = skill_activation_block_reason(manifest)
        default_review = "needs_review" if (manifest.get("source") or {}).get("managed") is False else "not_required"
        review = "excluded" if excluded_source else manifest.get("review_state", default_review)
        verification = {"state": "excluded" if excluded_source else "unverified"}
        if source in requested:
            verification = _verify_source(work, source, manifest, boundaries, budget)
        installation = {"state": "unverified"} if source in installed else None
        if source in installed and source in requested and len(host_rows) == 1:
            installation = _verify_source(work, source, manifest, boundaries, budget,
                                          installed_path=installed[source])
        active_verification = installation if installation is not None else verification
        native_enabled = host_rows[0]["enabled"] if len(host_rows) == 1 else None
        if excluded_source:
            alignment = "excluded"
        elif len(host_rows) > 1:
            alignment = "ambiguous_native_path"
        elif observation["state"] != "fresh":
            alignment = "native_observation_" + observation["state"]
        elif not host_rows:
            alignment = "native_discovery_incomplete" if observation["discovery_error_count"] else "not_observed"
        elif not native_enabled:
            alignment = "native_disabled"
        elif reason:
            alignment = "native_enabled_review_blocked"
        elif active_verification["state"] != "verified":
            alignment = "native_enabled_" + ("installation_" if installation is not None else "source_") + active_verification["state"]
        else:
            alignment = "enabled_observation_matches_reviewed_" + ("installation" if installation is not None else "source")
        rows.append({"source": source, "path": key, "name": manifest.get("name"), "review": review,
                     "review_block_reason": reason, "native_enabled_at_observation": native_enabled,
                     "native_observations": len(host_rows), "source_verification": verification,
                     "installation_verification": installation,
                     "alignment": alignment, "runtime_active": None})
    for key, host_rows in sorted(by_path.items()):
        host_package = Path(key).parent
        excluded_source = any(host_package == Path(_source_key(work, item))
                              or Path(_source_key(work, item)) in host_package.parents for item in excluded)
        for skill in host_rows:
            rows.append({"path": key, "name": skill["name"], "review": "excluded" if excluded_source else "unmanaged",
                         "native_enabled_at_observation": skill["enabled"], "runtime_active": None,
                         "alignment": "excluded" if excluded_source else "ambiguous_native_path" if len(host_rows) > 1 else "native_only"})
    rows.extend(unresolved)
    # Metadata may have changed while explicitly selected source files were checked.
    if load_skill_index(work).get("head") != loaded.get("head"):
        fail("Skill review index changed during reconciliation; refresh the report.", "host_skills_index_changed")
    return {"mode": "read_only_host_skill_reconciliation", "context": context,
            "observation": observation, "index_head": loaded.get("head"),
            "skills": rows[:limit], "omitted_skills": max(0, len(rows) - limit),
            "project_files_modified": False, "native_configuration_modified": False}
