from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .inventory import collect_inventory
from .io import load_yaml_safe
from .safety import create_text_exclusive, unique_path


def learn_from_projects(
    roots: list[Path | str],
    *,
    apply: bool = False,
) -> dict[str, Any]:
    project_roots = [Path(item).resolve() for item in roots]
    if not project_roots:
        raise ValueError("At least one project root is required.")
    sessions: list[dict[str, Any]] = []
    skill_metadata: dict[str, dict[str, Any]] = {}
    for root in project_roots:
        if not root.is_dir():
            raise ValueError(f"Project root is not a directory: {root}")
        for skill in collect_inventory(root).skills:
            skill_id = str(skill.get("id") or "")
            if skill_id:
                skill_metadata[skill_id] = {
                    "id": skill_id,
                    "name": skill.get("name"),
                    "kind": skill.get("kind"),
                    "source": skill.get("_skill_dir"),
                    "project": str(root),
                }
        sessions.extend(_load_sessions(root))

    skill_counts = Counter(
        str(skill_id)
        for session in sessions
        for skill_id in session.get("used_skills") or []
        if skill_id
    )
    tag_counts = Counter(
        str(tag)
        for session in sessions
        for tag in session.get("tags") or []
        if tag
    )
    script_counts = Counter(
        str(script.get("path"))
        for session in sessions
        for script in session.get("key_scripts") or []
        if isinstance(script, dict) and script.get("path")
    )
    common_skill_candidates = []
    for skill_id, count in skill_counts.most_common():
        metadata = skill_metadata.get(skill_id, {"id": skill_id})
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
            "path": path,
            "observed_sessions": count,
            "suggestion": "Review as a reusable common-project skill script.",
        }
        for path, count in script_counts.most_common()
        if count >= 2
    ]
    preference_candidates = [
        {
            "key": f"preferred_project_tag.{tag}",
            "value": tag,
            "observed_sessions": count,
            "status": "candidate",
            "promotion_policy": "human_review_required",
        }
        for tag, count in tag_counts.most_common(50)
        if count >= 2
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
            {"id": skill_id, "sessions": count}
            for skill_id, count in skill_counts.most_common(50)
        ],
    }
    primary = project_roots[0]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = unique_path(primary / ".agent" / "inbox" / "learning" / f"{timestamp}-candidates.yaml")
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
    for path in history.rglob("session.yaml"):
        result = load_yaml_safe(path)
        if result.error or not isinstance(result.data, dict):
            continue
        if result.data.get("format") != "archmarshal-session-v1":
            continue
        sessions.append(result.data)
    return sessions


__all__ = ["learn_from_projects"]
