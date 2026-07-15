from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .errors import ArchMarshalError, require_workspace_root
from .inventory import collect_inventory
from .io import load_yaml_safe
from .ownership import require_owned_workspace, workspace_id
from .safety import (
    create_bytes_exclusive,
    ensure_managed_path,
    ensure_path_within,
    files_below_no_links,
    is_link_or_reparse,
    sha256_file,
    unique_path,
)
from .session import verify_committed_session
from .workspace_lock import workspace_mutation_lock


def learn_from_projects(
    roots: list[Path | str],
    *,
    apply: bool = False,
) -> dict[str, Any]:
    project_roots: list[Path] = []
    seen_roots: set[str] = set()
    for item in roots:
        root = require_workspace_root(item)
        identity = os.path.normcase(str(root))
        if identity not in seen_roots:
            seen_roots.add(identity)
            project_roots.append(root)
    if not project_roots:
        raise ValueError("At least one project root is required.")
    sessions: list[dict[str, Any]] = []
    legacy_unverified_session_count = 0
    skill_metadata: dict[tuple[str, str, str], dict[str, Any]] = {}
    for root in project_roots:
        for skill in collect_inventory(root).skills:
            skill_id = str(skill.get("id") or "")
            if skill_id:
                implementation_hash = str(
                    skill.get("_current_package_sha256")
                    or skill.get("_current_skill_sha256")
                    or ""
                )
                if not implementation_hash:
                    seed = f"{root}:{skill.get('_skill_dir')}"
                    implementation_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()
                skill_metadata[(str(root), skill_id, implementation_hash)] = {
                    "id": skill_id,
                    "name": skill.get("name"),
                    "kind": skill.get("kind"),
                    "source": skill.get("_skill_dir"),
                    "workspace_id": workspace_id(root),
                    "implementation_sha256": implementation_hash,
                }
        loaded_sessions, legacy_count = _load_sessions(root)
        sessions.extend(loaded_sessions)
        legacy_unverified_session_count += legacy_count

    skill_observations: dict[tuple[str, str], set[str]] = {}
    skill_details: dict[tuple[str, str], dict[str, Any]] = {}
    tag_observations: dict[str, set[str]] = {}
    script_observations: dict[str, set[str]] = {}
    script_sources: dict[str, set[tuple[str, str]]] = {}
    usage_by_id: dict[str, set[str]] = {}
    unversioned_skill_usage_count = 0
    unreviewed_skill_usage_count = 0
    ineligible_skill_usage_count = 0
    session_evidence = {
        f"{session['_project_root']}::{session['_session_path']}": {
            "workspace_id": workspace_id(session["_project_root"]),
            "session": session["_session_path"],
            "commit_sha256": session.get("_session_commit_sha256"),
        }
        for session in sessions
    }
    for session in sessions:
        session_id = f"{session['_project_root']}::{session['_session_path']}"
        project = str(session["_project_root"])
        usage_records = session.get("skill_usage")
        if not isinstance(usage_records, list):
            unversioned_skill_usage_count += len(
                {
                    str(value)
                    for value in session.get("used_skills") or []
                    if isinstance(value, str) and value
                }
            )
            usage_records = []
        for usage in usage_records:
            if not isinstance(usage, dict):
                unversioned_skill_usage_count += 1
                continue
            skill_id = usage.get("id")
            implementation = usage.get("package_sha256")
            if (
                not isinstance(skill_id, str)
                or not skill_id
                or not isinstance(implementation, str)
                or len(implementation) != 64
            ):
                unversioned_skill_usage_count += 1
                continue
            if usage.get("eligible_for_learning") is not True:
                if usage.get("review_state") in {"needs_review", "rejected"}:
                    unreviewed_skill_usage_count += 1
                else:
                    ineligible_skill_usage_count += 1
                continue
            metadata = skill_metadata.get((project, skill_id, implementation))
            key = (skill_id, implementation)
            skill_observations.setdefault(key, set()).add(session_id)
            usage_by_id.setdefault(skill_id, set()).add(session_id)
            skill_details.setdefault(
                key,
                metadata
                or {
                    "id": skill_id,
                    "name": usage.get("name"),
                    "kind": usage.get("kind"),
                    "source": usage.get("path"),
                    "workspace_id": workspace_id(session["_project_root"]),
                    "implementation_sha256": implementation,
                    "routing_subject_sha256": usage.get("routing_subject_sha256"),
                },
            )
        for tag in {
            str(value) for value in session.get("tags") or [] if isinstance(value, str) and value
        }:
            tag_observations.setdefault(tag, set()).add(session_id)
        for script in session.get("key_scripts") or []:
            if not isinstance(script, dict):
                continue
            digest = script.get("sha256")
            path = script.get("path")
            if not isinstance(digest, str) or len(digest) != 64 or not isinstance(path, str):
                continue
            script_observations.setdefault(digest, set()).add(session_id)
            script_sources.setdefault(digest, set()).add((project, path))

    common_skill_candidates = []
    for key, observed_sessions in sorted(
        skill_observations.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        metadata = skill_details[key]
        count = len(observed_sessions)
        if count < 2 or metadata.get("kind") in {"global_skill", "common_project_skill"}:
            continue
        candidate_seed = {
            "type": "common_skill",
            "skill_id": metadata.get("id"),
            "implementation_sha256": metadata.get("implementation_sha256"),
            "sessions": sorted(observed_sessions),
        }
        candidate_id = "candidate.skill." + _canonical_digest(candidate_seed)[:24]
        common_skill_candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_type": "common_skill",
                "skill_id": metadata.get("id"),
                **{key: value for key, value in metadata.items() if key != "id"},
                "observed_sessions": count,
                "evidence_refs": [session_evidence[item] for item in sorted(observed_sessions)],
                "suggested_kind": "common_project_skill",
                "status": "candidate",
                "promotion_policy": "human_review_required",
                "reason": "The same non-global skill was recorded in multiple sessions.",
            }
        )
    repeated_scripts = [
        {
            "sha256": digest,
            "sources": [
                {"project": project, "path": path}
                for project, path in sorted(script_sources[digest])
            ],
            "observed_sessions": len(observed_sessions),
            "suggestion": "Review as a reusable common-project skill script.",
        }
        for digest, observed_sessions in sorted(
            script_observations.items(), key=lambda item: (-len(item[1]), item[0])
        )
        if len(observed_sessions) >= 2
    ]
    preference_candidates = [
        {
            "candidate_id": "candidate.preference."
            + _canonical_digest(
                {"type": "preference", "tag": tag, "sessions": sorted(observed_sessions)}
            )[:24],
            "candidate_type": "preference",
            "key": f"preferred_project_tag.{tag}",
            "value": tag,
            "observed_sessions": len(observed_sessions),
            "evidence_refs": [session_evidence[item] for item in sorted(observed_sessions)],
            "status": "candidate",
            "promotion_policy": "human_review_required",
        }
        for tag, observed_sessions in sorted(
            tag_observations.items(), key=lambda item: (-len(item[1]), item[0].casefold())
        )[:50]
        if len(observed_sessions) >= 2
    ]
    profile = {
        "format": "archmarshal-learning-candidates-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_project_count": len(project_roots),
        "source_session_count": len(sessions),
        "legacy_unverified_session_count": legacy_unverified_session_count,
        "unversioned_skill_usage_count": unversioned_skill_usage_count,
        "unreviewed_skill_usage_count": unreviewed_skill_usage_count,
        "ineligible_skill_usage_count": ineligible_skill_usage_count,
        "limits": {
            "raw_history_included": False,
            "environment_variables_included": False,
            "automatic_global_skill_mutation": False,
            "max_preference_candidates": 50,
        },
        "common_skill_candidates": common_skill_candidates,
        "repeated_scripts": repeated_scripts,
        "preference_candidates": preference_candidates,
        "skill_usage": [
            {"id": skill_id, "sessions": len(observed_sessions)}
            for skill_id, observed_sessions in sorted(
                usage_by_id.items(), key=lambda item: (-len(item[1]), item[0])
            )[:50]
        ],
    }
    primary = project_roots[0]
    generated_at = datetime.now(timezone.utc)
    profile_bytes = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).encode("utf-8")
    profile_digest = hashlib.sha256(profile_bytes).hexdigest()
    timestamp = generated_at.strftime("%H%M%S")
    base = primary / ".agent" / "inbox" / "learning" / generated_at.strftime("%Y/%m/%d")
    pack_dir = unique_path(base / f"{timestamp}-{profile_digest[:12]}")
    target = pack_dir / "candidates.yaml"
    ensure_managed_path(primary, pack_dir, purpose="Learning candidate pack")
    commit = {
        "format": "archmarshal-learning-candidate-commit-v1",
        "candidate_format": profile["format"],
        "file": "candidates.yaml",
        "bytes": len(profile_bytes),
        "sha256": profile_digest,
        "candidate_ids": sorted(
            [item["candidate_id"] for item in common_skill_candidates]
            + [item["candidate_id"] for item in preference_candidates]
        ),
        "source_mutation": False,
    }
    commit_bytes = (
        json.dumps(commit, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    payload = {
        "tool": "archmarshal",
        "stage": "learn",
        "mode": "propose_only",
        "source_projects": [str(item) for item in project_roots],
        "source_session_count": len(sessions),
        "legacy_unverified_session_count": legacy_unverified_session_count,
        "unversioned_skill_usage_count": unversioned_skill_usage_count,
        "unreviewed_skill_usage_count": unreviewed_skill_usage_count,
        "ineligible_skill_usage_count": ineligible_skill_usage_count,
        "target": pack_dir.relative_to(primary).as_posix(),
        "candidate_pack_sha256": profile_digest,
        "common_skill_candidates": common_skill_candidates,
        "preference_candidates": preference_candidates,
        "repeated_scripts": repeated_scripts,
        "notes": [
            "Learning reads only ArchMarshal session manifests, not raw project history.",
            "Candidates never mutate existing skills or global policy.",
            "Promotion to a shared skill or user preference requires explicit human review.",
            "Usage lists are capped so the global layer can remain lightweight.",
            "Legacy v1 sessions are counted but remain untrusted until an explicit migration exists.",
        ],
    }
    if apply:
        require_owned_workspace(primary, operation="Learning candidate creation")
        with workspace_mutation_lock(primary, operation="learning_candidate") as held:
            pack_dir.mkdir(parents=True, exist_ok=False)
            held.verify()
            create_bytes_exclusive(target, profile_bytes, mode=0o600)
            held.verify()
            create_bytes_exclusive(pack_dir / "COMMITTED.json", commit_bytes, mode=0o600)
            verify_learning_pack(pack_dir)
            held.verify()
        payload["mode"] = "candidate_pack_created"
        payload["created"] = pack_dir.relative_to(primary).as_posix()
    return payload


def verify_learning_pack(pack_dir: Path | str) -> dict[str, Any]:
    directory = Path(pack_dir).resolve()
    marker = directory / "COMMITTED.json"
    candidate_file = directory / "candidates.yaml"
    if (
        not marker.is_file()
        or is_link_or_reparse(marker)
        or not candidate_file.is_file()
        or is_link_or_reparse(candidate_file)
    ):
        raise ArchMarshalError(
            "learning_pack_invalid",
            "Learning candidate pack is incomplete or linked.",
            details={"path": str(directory)},
        )
    try:
        commit = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError("learning_pack_invalid", "Candidate commit marker is invalid.") from exc
    if (
        not isinstance(commit, dict)
        or commit.get("format") != "archmarshal-learning-candidate-commit-v1"
        or commit.get("file") != "candidates.yaml"
        or commit.get("source_mutation") is not False
        or commit.get("bytes") != candidate_file.stat().st_size
        or commit.get("sha256") != sha256_file(candidate_file)
    ):
        raise ArchMarshalError(
            "learning_pack_integrity_failed",
            "Candidate pack bytes do not match the commit marker.",
        )
    result = load_yaml_safe(candidate_file)
    profile = result.data
    if (
        result.error
        or not isinstance(profile, dict)
        or profile.get("format") != "archmarshal-learning-candidates-v2"
    ):
        raise ArchMarshalError("learning_pack_invalid", "Candidate payload is invalid.")
    candidates = (profile.get("common_skill_candidates") or []) + (
        profile.get("preference_candidates") or []
    )
    ids = sorted(
        item.get("candidate_id")
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
    )
    if ids != commit.get("candidate_ids") or len(ids) != len(candidates):
        raise ArchMarshalError(
            "learning_pack_integrity_failed",
            "Candidate identities do not match the commit marker.",
        )
    return {
        "path": str(directory),
        "sha256": str(commit["sha256"]),
        "commit_sha256": sha256_file(marker),
        "commit": commit,
        "profile": profile,
    }


def _load_sessions(root: Path) -> tuple[list[dict[str, Any]], int]:
    history = root / ".agent" / "history"
    if not history.exists():
        return [], 0
    sessions: list[dict[str, Any]] = []
    legacy_unverified = 0
    files = files_below_no_links(
        history,
        purpose="Learning session discovery",
        max_files=100_000,
    )
    for session_path in (path for path in files if path.name == "session.yaml"):
        if (session_path.parent / "COMMITTED.json").exists():
            continue
        result = load_yaml_safe(session_path)
        if isinstance(result.data, dict) and result.data.get("format") == "archmarshal-session-v1":
            legacy_unverified += 1
    markers = [path for path in files if path.name == "COMMITTED.json"]
    for marker in markers[:10_000]:
        try:
            ensure_path_within(root, marker, purpose="Learning session commit marker")
            verified = verify_committed_session(marker.parent)
        except (ArchMarshalError, OSError, ValueError):
            continue
        session = verified["session"]
        if not all(
            isinstance(session.get(field, []), list)
            for field in ("used_skills", "tags", "key_scripts")
        ):
            continue
        sessions.append(
            {
                **session,
                "_project_root": str(root),
                "_session_path": (marker.parent / "session.yaml")
                .relative_to(root)
                .as_posix(),
                "_session_commit_sha256": sha256_file(marker),
            }
        )
    return sessions, legacy_unverified


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


__all__ = ["learn_from_projects", "verify_learning_pack"]
