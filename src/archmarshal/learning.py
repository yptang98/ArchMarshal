from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any

import yaml

from .errors import ArchMarshalError, require_workspace_root
from .inventory import collect_inventory
from .io import load_yaml_safe
from .safety import create_text_exclusive, ensure_managed_path, ensure_path_within, unique_path


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
    skill_metadata: dict[tuple[str, str], dict[str, Any]] = {}
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
                skill_metadata[(str(root), skill_id)] = {
                    "id": skill_id,
                    "name": skill.get("name"),
                    "kind": skill.get("kind"),
                    "source": skill.get("_skill_dir"),
                    "project": str(root),
                    "implementation_sha256": implementation_hash,
                }
        sessions.extend(_load_sessions(root))

    skill_observations: dict[tuple[str, str], set[str]] = {}
    skill_details: dict[tuple[str, str], dict[str, Any]] = {}
    tag_observations: dict[str, set[str]] = {}
    script_observations: dict[str, set[str]] = {}
    script_sources: dict[str, set[tuple[str, str]]] = {}
    usage_by_id: dict[str, set[str]] = {}
    for session in sessions:
        session_id = f"{session['_project_root']}::{session['_session_path']}"
        project = str(session["_project_root"])
        for skill_id in {
            str(value) for value in session.get("used_skills") or [] if isinstance(value, str) and value
        }:
            metadata = skill_metadata.get((project, skill_id))
            implementation = (
                str(metadata["implementation_sha256"])
                if metadata
                else hashlib.sha256(f"{project}:{skill_id}".encode("utf-8")).hexdigest()
            )
            key = (skill_id, implementation)
            skill_observations.setdefault(key, set()).add(session_id)
            usage_by_id.setdefault(skill_id, set()).add(session_id)
            skill_details.setdefault(
                key,
                metadata
                or {
                    "id": skill_id,
                    "project": project,
                    "implementation_sha256": implementation,
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
        common_skill_candidates.append(
            {
                **metadata,
                "observed_sessions": count,
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
            "key": f"preferred_project_tag.{tag}",
            "value": tag,
            "observed_sessions": len(observed_sessions),
            "status": "candidate",
            "promotion_policy": "human_review_required",
        }
        for tag, observed_sessions in sorted(
            tag_observations.items(), key=lambda item: (-len(item[1]), item[0].casefold())
        )[:50]
        if len(observed_sessions) >= 2
    ]
    profile = {
        "format": "archmarshal-learning-candidates-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_project_count": len(project_roots),
        "source_session_count": len(sessions),
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
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = unique_path(primary / ".agent" / "inbox" / "learning" / f"{timestamp}-candidates.yaml")
    ensure_managed_path(primary, target, purpose="Learning candidate output")
    payload = {
        "tool": "archmarshal",
        "stage": "learn",
        "mode": "propose_only",
        "source_projects": [str(item) for item in project_roots],
        "source_session_count": len(sessions),
        "target": target.relative_to(primary).as_posix(),
        "common_skill_candidates": common_skill_candidates,
        "preference_candidates": preference_candidates,
        "repeated_scripts": repeated_scripts,
        "notes": [
            "Learning reads only ArchMarshal session manifests, not raw project history.",
            "Candidates never mutate existing skills or global policy.",
            "Promotion to a shared skill or user preference requires explicit human review.",
            "Usage lists are capped so the global layer can remain lightweight.",
        ],
    }
    if apply:
        create_text_exclusive(target, yaml.safe_dump(profile, sort_keys=False, allow_unicode=True))
        payload["mode"] = "candidate_pack_created"
        payload["created"] = target.relative_to(primary).as_posix()
    return payload


def _load_sessions(root: Path) -> list[dict[str, Any]]:
    history = root / ".agent" / "history"
    if not history.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for path in islice(history.rglob("session.yaml"), 10_000):
        try:
            ensure_path_within(root, path, purpose="Learning session manifest")
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                continue
        except (ArchMarshalError, OSError, ValueError):
            continue
        result = load_yaml_safe(path)
        if result.error or not isinstance(result.data, dict):
            continue
        if result.data.get("format") != "archmarshal-session-v1":
            continue
        if not all(
            isinstance(result.data.get(field, []), list)
            for field in ("used_skills", "tags", "key_scripts")
        ):
            continue
        sessions.append(
            {
                **result.data,
                "_project_root": str(root),
                "_session_path": path.relative_to(root).as_posix(),
            }
        )
    return sessions


__all__ = ["learn_from_projects"]
