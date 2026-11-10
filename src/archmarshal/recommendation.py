"""Deterministic task matching with inspectable evidence."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .skill_activation import skill_activation_block_reason, source_managed


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("-", " ").replace("_", " ")
    return " ".join(value.split())


def contains(text: str, needle: str) -> bool:
    value = normalize(needle)
    if not value:
        return False
    # Latin triggers use token boundaries; CJK phrases still match contiguous text.
    left = r"(?<![a-z0-9])" if value[0].isascii() and value[0].isalnum() else ""
    right = r"(?![a-z0-9])" if value[-1].isascii() and value[-1].isalnum() else ""
    return re.search(left + re.escape(value) + right, normalize(text)) is not None


def match_skills(skills: list[dict[str, Any]], task_text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for skill in skills:
        if (
            skill.get("status") not in {"active", "experimental"}
            or skill_activation_block_reason(skill) is not None
        ):
            continue
        negative_matches = [
            item for item in skill.get("negative_triggers") or [] if contains(task_text, str(item))
        ]
        if negative_matches:
            continue
        trigger_matches = [
            item for item in skill.get("triggers") or [] if contains(task_text, str(item))
        ]
        tag_matches = [item for item in skill.get("tags") or [] if contains(task_text, str(item))]
        if not trigger_matches and not tag_matches:
            continue
        priority_bonus = {"highest": 3, "high": 2, "normal": 0, "low": -1}.get(
            str(skill.get("priority") or "normal"), 0
        )
        score = len(trigger_matches) * 3 + len(tag_matches) + priority_bonus
        matches.append(
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "kind": skill.get("kind"),
                "scope": skill.get("scope"),
                "priority": skill.get("priority") or "normal",
                "score": score,
                "path": skill.get("_skill_dir"),
                "metadata_path": skill.get("_overlay_manifest_path") or skill.get("_manifest_path"),
                "source_managed": source_managed(skill),
                "origin": "workspace",
                "trigger_matches": trigger_matches,
                "tag_matches": tag_matches,
                "match_evidence": {"trigger_weight": 3, "tag_weight": 1,
                                   "priority_bonus": priority_bonus,
                                   "rule": "At least one declared trigger or tag must match; priority only ranks matches."},
                "implementation_sha256": skill.get("_current_package_sha256"),
            }
        )
    return sorted(matches, key=lambda item: (-item["score"], str(item["id"])))
