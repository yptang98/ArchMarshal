from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import ArchMarshalError, require_workspace_root
from .inventory import collect_inventory
from .io import load_yaml_safe, read_bytes_safe
from .ownership import require_owned_workspace, workspace_id
from .safety import (
    create_bytes_exclusive,
    ensure_managed_path,
    ensure_path_within,
    files_below_no_links,
    is_link_or_reparse,
    unique_path,
)
from .session import verify_committed_session
from .skill_index import load_skill_index, skill_index_exclusions
from .workspace_layout import WorkspaceLayout, load_workspace_layout
from .workspace_lock import workspace_mutation_lock

LEARNING_FORMAT = "archmarshal-learning-candidates-v3"
LEARNING_PLAN_FORMAT = "archmarshal-learning-plan-v1"
SUPPORTED_LEARNING_FORMATS = {
    "archmarshal-learning-candidates-v2",
    LEARNING_FORMAT,
}
MAX_LEARNING_COMMIT_BYTES = 2 * 1024 * 1024


def learn_from_projects(
    roots: list[Path | str],
    *,
    reviewed_plan: dict[str, Any] | None = None,
    expected_plan: str | None = None,
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
    if apply and (reviewed_plan is None or not expected_plan):
        raise ArchMarshalError(
            "learning_review_required",
            "Creating a learning pack requires the complete saved preview and its exact plan digest.",
        )
    if not apply and (reviewed_plan is not None or expected_plan is not None):
        raise ArchMarshalError(
            "learning_plan_unexpected",
            "Reviewed learning plans are accepted only with --apply.",
        )
    generated_at = (
        _reviewed_generated_at(reviewed_plan)
        if reviewed_plan is not None
        else datetime.now(timezone.utc)
    )
    sessions: list[dict[str, Any]] = []
    legacy_unverified_session_count = 0
    skill_metadata: dict[tuple[str, str, str], dict[str, Any]] = {}
    excluded_sources_by_project: dict[str, set[str]] = {}
    excluded_skill_keys_by_project: dict[str, set[tuple[str, str]]] = {}
    layouts: dict[str, WorkspaceLayout] = {}
    layout_observations: dict[str, dict[str, Any]] = {}
    for root in project_roots:
        layout = load_workspace_layout(root)
        layouts[str(root)] = layout
        excluded_sources = set(skill_index_exclusions(root))
        excluded_sources_by_project[str(root)] = excluded_sources
        loaded_index = load_skill_index(root)
        excluded_skill_keys: set[tuple[str, str]] = set()
        for record in (loaded_index.get("generation") or {}).get("skills", []):
            if (
                not isinstance(record, dict)
                or str(record.get("source") or "") not in excluded_sources
            ):
                continue
            manifest = record.get("manifest")
            source = manifest.get("source") if isinstance(manifest, dict) else None
            skill_id = manifest.get("id") if isinstance(manifest, dict) else None
            package_hash = source.get("package_sha256") if isinstance(source, dict) else None
            if isinstance(skill_id, str) and isinstance(package_hash, str):
                excluded_skill_keys.add((skill_id, package_hash))
        excluded_skill_keys_by_project[str(root)] = excluded_skill_keys
        for skill in collect_inventory(root).skills:
            skill_id = str(skill.get("id") or "")
            if skill_id:
                implementation_hash = str(
                    skill.get("_current_package_sha256") or skill.get("_current_skill_sha256") or ""
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
        loaded_sessions, legacy_count = _load_sessions(root, layout)
        sessions.extend(loaded_sessions)
        legacy_unverified_session_count += legacy_count
        confirmed_profile = _confirmed_workspace_layout_profile(root)
        if confirmed_profile is not None and loaded_sessions:
            digest = _canonical_digest(confirmed_profile)
            observation = layout_observations.setdefault(
                digest,
                {
                    "profile": confirmed_profile,
                    "workspace_ids": set(),
                    "evidence_refs": [],
                },
            )
            observation["workspace_ids"].add(workspace_id(root))
            observation["evidence_refs"].extend(
                {
                    "workspace_id": workspace_id(root),
                    "session": session["_session_path"],
                    "commit_sha256": session.get("_session_commit_sha256"),
                }
                for session in loaded_sessions
            )

    skill_observations: dict[tuple[str, str], set[str]] = {}
    skill_details: dict[tuple[str, str], dict[str, Any]] = {}
    tag_observations: dict[str, set[str]] = {}
    script_observations: dict[str, set[str]] = {}
    script_sources: dict[str, set[tuple[str, str]]] = {}
    usage_by_id: dict[str, set[str]] = {}
    unversioned_skill_usage_count = 0
    unreviewed_skill_usage_count = 0
    ineligible_skill_usage_count = 0
    excluded_skill_usage_count = 0
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
            source_path = usage.get("path")
            if (
                isinstance(source_path, str)
                and source_path in excluded_sources_by_project.get(project, set())
            ) or (
                isinstance(skill_id, str)
                and isinstance(implementation, str)
                and (skill_id, implementation) in excluded_skill_keys_by_project.get(project, set())
            ):
                excluded_skill_usage_count += 1
                continue
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
            script_sources.setdefault(digest, set()).add(
                (workspace_id(session["_project_root"]), path)
            )

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
                {"workspace_id": source_workspace_id, "path": path}
                for source_workspace_id, path in sorted(script_sources[digest])
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
    for digest, observation in sorted(layout_observations.items()):
        workspace_ids = sorted(observation["workspace_ids"])
        if len(workspace_ids) < 2:
            continue
        evidence_refs = sorted(
            observation["evidence_refs"],
            key=lambda item: (str(item["workspace_id"]), str(item["session"])),
        )
        preference_candidates.append(
            {
                "candidate_id": f"candidate.preference.layout.{digest[:24]}",
                "candidate_type": "preference",
                "key": "preferred.workspace_layout",
                "value": {
                    "confirmed": True,
                    "profile": observation["profile"],
                },
                "observed_projects": len(workspace_ids),
                "observed_sessions": len(evidence_refs),
                "evidence_refs": evidence_refs,
                "status": "candidate",
                "promotion_policy": "human_review_required",
                "reason": "The same explicitly confirmed layout was used in multiple projects.",
            }
        )
    preference_candidates = preference_candidates[:50]
    profile = {
        "format": LEARNING_FORMAT,
        "generated_at": generated_at.isoformat(),
        "source_project_count": len(project_roots),
        "source_session_count": len(sessions),
        "legacy_unverified_session_count": legacy_unverified_session_count,
        "unversioned_skill_usage_count": unversioned_skill_usage_count,
        "unreviewed_skill_usage_count": unreviewed_skill_usage_count,
        "ineligible_skill_usage_count": ineligible_skill_usage_count,
        "excluded_skill_usage_count": excluded_skill_usage_count,
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
    profile_bytes = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).encode("utf-8")
    profile_digest = hashlib.sha256(profile_bytes).hexdigest()
    timestamp = generated_at.strftime("%H%M%S")
    base = primary / ".agent" / "inbox" / "learning" / generated_at.strftime("%Y/%m/%d")
    if reviewed_plan is None:
        pack_dir = unique_path(base / f"{timestamp}-{profile_digest[:12]}")
    else:
        pack_dir = _reviewed_pack_dir(
            primary,
            reviewed_plan,
            generated_at=generated_at,
        )
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
    target_relative = pack_dir.relative_to(primary).as_posix()
    learning_plan = {
        "format": LEARNING_PLAN_FORMAT,
        "mode": "propose_only",
        "primary_root": str(primary),
        "source_projects": [str(item) for item in project_roots],
        "source_layouts": {str(item): layouts[str(item)].to_dict() for item in project_roots},
        "generated_at": generated_at.isoformat(),
        "target": target_relative,
        "candidate_pack_sha256": profile_digest,
        "commit_sha256": hashlib.sha256(commit_bytes).hexdigest(),
        "source_mutation": False,
    }
    learning_plan["plan_digest"] = _canonical_digest(learning_plan)
    if reviewed_plan is not None:
        _verify_reviewed_learning_plan(
            reviewed_plan,
            learning_plan,
            expected_plan=str(expected_plan),
        )
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
        "excluded_skill_usage_count": excluded_skill_usage_count,
        "target": target_relative,
        "candidate_pack_sha256": profile_digest,
        "plan_digest": learning_plan["plan_digest"],
        "learning_plan": learning_plan,
        "common_skill_candidates": common_skill_candidates,
        "preference_candidates": preference_candidates,
        "repeated_scripts": repeated_scripts,
        "notes": [
            "Learning reads only ArchMarshal session manifests, not raw project history.",
            "Candidates never mutate existing skills or global policy.",
            "Skill packages in the current exact exclusion policy do not contribute learning candidates.",
            "Only explicitly confirmed layouts repeated across multiple projects can become layout preference candidates; detected layouts are ignored.",
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


def _confirmed_workspace_layout_profile(root: Path) -> dict[str, Any] | None:
    workspace_file = root / ".agent" / "workspace.yaml"
    loaded = load_yaml_safe(workspace_file)
    if loaded.error or not isinstance(loaded.data, dict):
        return None
    metadata = loaded.data.get("layout")
    if not isinstance(metadata, dict) or metadata.get("confirmed") is not True:
        return None
    save_paths = loaded.data.get("save_paths")
    naming = loaded.data.get("naming")
    paths = loaded.data.get("paths")
    if not isinstance(save_paths, dict) or not isinstance(naming, dict):
        return None
    skill_roots = paths.get("source_skill_roots", []) if isinstance(paths, dict) else []
    return {
        "save_paths": save_paths,
        "naming": naming,
        "skill_roots": list(skill_roots) if isinstance(skill_roots, list) else [],
    }


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
    loaded_marker = read_bytes_safe(
        marker,
        max_bytes=MAX_LEARNING_COMMIT_BYTES,
        label="Learning commit marker",
    )
    try:
        if loaded_marker.error:
            raise ValueError(loaded_marker.error)
        commit = json.loads(loaded_marker.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ArchMarshalError(
            "learning_pack_invalid", "Candidate commit marker is invalid."
        ) from exc
    result = load_yaml_safe(candidate_file)
    if (
        not isinstance(commit, dict)
        or commit.get("format") != "archmarshal-learning-candidate-commit-v1"
        or commit.get("file") != "candidates.yaml"
        or commit.get("source_mutation") is not False
        or commit.get("bytes") != result.byte_count
        or commit.get("sha256") != result.sha256
    ):
        raise ArchMarshalError(
            "learning_pack_integrity_failed",
            "Candidate pack bytes do not match the commit marker.",
        )
    profile = result.data
    if (
        result.error
        or not isinstance(profile, dict)
        or profile.get("format") not in SUPPORTED_LEARNING_FORMATS
        or commit.get("candidate_format") != profile.get("format")
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
        "commit_sha256": loaded_marker.sha256,
        "commit": commit,
        "profile": profile,
    }


def _load_sessions(
    root: Path,
    layout: WorkspaceLayout | None = None,
) -> tuple[list[dict[str, Any]], int]:
    active_layout = layout or load_workspace_layout(root)
    history = active_layout.configured_dir("history")
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
                "_session_path": (marker.parent / "session.yaml").relative_to(root).as_posix(),
                "_session_commit_sha256": verified["commit_sha256"],
            }
        )
    return sessions, legacy_unverified


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reviewed_generated_at(plan: dict[str, Any]) -> datetime:
    if not isinstance(plan, dict) or plan.get("format") != LEARNING_PLAN_FORMAT:
        raise ArchMarshalError(
            "learning_plan_invalid",
            "Saved learning plan is missing or has an unsupported format.",
        )
    value = plan.get("generated_at")
    if not isinstance(value, str):
        raise ArchMarshalError("learning_plan_invalid", "Saved learning plan has no timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchMarshalError(
            "learning_plan_invalid", "Saved learning plan timestamp is invalid."
        ) from exc
    if parsed.tzinfo is None:
        raise ArchMarshalError(
            "learning_plan_invalid", "Saved learning plan timestamp must include a timezone."
        )
    return parsed


def _reviewed_pack_dir(
    primary: Path,
    plan: dict[str, Any],
    *,
    generated_at: datetime,
) -> Path:
    target = plan.get("target")
    if not isinstance(target, str):
        raise ArchMarshalError("learning_plan_invalid", "Saved learning plan has no target.")
    profile_digest = plan.get("candidate_pack_sha256")
    if not isinstance(profile_digest, str) or len(profile_digest) != 64:
        raise ArchMarshalError(
            "learning_plan_invalid", "Saved learning plan has no valid candidate digest."
        )
    relative = PurePosixPath(target)
    if relative.is_absolute() or ".." in relative.parts:
        raise ArchMarshalError("learning_plan_invalid", "Saved learning target is not relative.")
    expected_parent = PurePosixPath(".agent/inbox/learning") / generated_at.strftime("%Y/%m/%d")
    expected_name = f"{generated_at.strftime('%H%M%S')}-{profile_digest[:12]}"
    suffix = relative.name.removeprefix(expected_name)
    if (
        relative.parent != expected_parent
        or not relative.name.startswith(expected_name)
        or (suffix and (not suffix.startswith("-") or not suffix[1:].isdigit()))
    ):
        raise ArchMarshalError(
            "learning_plan_invalid",
            "Saved learning target does not match its reviewed timestamp and content digest.",
        )
    return ensure_managed_path(
        primary,
        primary.joinpath(*relative.parts),
        purpose="Reviewed learning candidate pack",
    )


def _verify_reviewed_learning_plan(
    reviewed: dict[str, Any],
    current: dict[str, Any],
    *,
    expected_plan: str,
) -> None:
    actual = _canonical_digest(
        {key: value for key, value in reviewed.items() if key != "plan_digest"}
    )
    if reviewed.get("plan_digest") != actual or expected_plan != actual:
        raise ArchMarshalError(
            "learning_plan_digest_mismatch",
            "Saved learning plan does not match the exact digest supplied for apply.",
        )
    if reviewed != current:
        raise ArchMarshalError(
            "learning_plan_stale",
            "Projects or committed evidence changed after the learning preview; review a new plan.",
        )


__all__ = [
    "LEARNING_FORMAT",
    "LEARNING_PLAN_FORMAT",
    "learn_from_projects",
    "verify_learning_pack",
]
