"""Shared Skill activation and source-review gates."""
from __future__ import annotations

from typing import Any

from .skill_index import skill_review_subject_digest


def skill_activation_block_reason(skill: dict[str, Any]) -> str | None:
    if skill.get("_index_state") == "untracked":
        return "index_untracked"
    status = skill.get("status")
    if status not in {"active", "experimental"}:
        return f"status_{status or 'unknown'}"
    if skill.get("_source_error"):
        return "source_unsafe"
    if skill.get("_has_skill_md") is False:
        return "source_missing"
    drift = skill.get("_source_drift")
    if drift and drift != "unchanged":
        return f"source_{drift}"
    source = skill.get("source")
    source_managed = bool(source.get("managed", True)) if isinstance(source, dict) else True
    if not source_managed:
        review_state = skill.get("review_state")
        review = skill.get("review")
        if review_state == "rejected":
            return "metadata_rejected"
        if review_state != "approved" or not isinstance(review, dict):
            return "metadata_needs_review"
        if review.get("decision") != "approved":
            return "metadata_review_invalid"
        try:
            current_subject = skill_review_subject_digest(skill)
        except (TypeError, ValueError):
            return "metadata_review_invalid"
        if review.get("subject_digest") != current_subject:
            return "metadata_review_stale"
        elevated = (
            skill.get("kind") == "global_skill"
            or skill.get("scope") == "global"
            or skill.get("priority") == "highest"
        )
        if elevated and review.get("allow_global_policy") is not True:
            return "global_policy_not_approved"
    return None


def source_managed(skill: dict[str, Any]) -> bool:
    source = skill.get("source")
    return bool(source.get("managed", True)) if isinstance(source, dict) else True
