from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .diagnostics import severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace
from .planner import plan_workspace


def closeout_workspace(root: Path | str, used_skills: list[str] | None = None) -> dict[str, Any]:
    used_skills = used_skills or []
    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)
    skill_index = {
        str(skill.get("id") or skill.get("name")): skill
        for skill in inventory.skills
        if skill.get("id") or skill.get("name")
    }
    matched = [skill_index[item] for item in used_skills if item in skill_index]
    missing = [item for item in used_skills if item not in skill_index]
    plan = plan_workspace(root)
    return {
        "tool": "archmarshal",
        "root": str(inventory.root),
        "used_skills": [
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "kind": skill.get("kind"),
                "path": skill.get("_skill_dir"),
                "tags": skill.get("tags") or [],
            }
            for skill in matched
        ],
        "missing_used_skills": missing,
        "skill_counts_by_kind": dict(Counter(str(skill.get("kind")) for skill in inventory.skills)),
        "diagnostic_summary": severity_counts(diagnostics),
        "cleanup_actions": plan["actions"],
        "candidate_memory_updates": _candidate_memory_updates(inventory.to_dict()),
        "review_questions": [
            "Did any temporary report contain durable knowledge worth promoting?",
            "Did any repeated workflow deserve a project skill or common project skill?",
            "Did any selected skill have overlapping triggers or missing negative triggers?",
            "Can any generated skill be archived, registered, or promoted?",
        ],
        "notes": [
            "Closeout is read-only and does not archive, promote, or modify files.",
            "Use this after project work to keep skills and project memory from accumulating silently.",
        ],
    }


def _candidate_memory_updates(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    known_evidence = {
        str(ref)
        for record in inventory["memory_records"]
        for ref in (record.get("evidence_refs") or [])
    }
    for artifact in inventory["artifacts"]:
        artifact_id = str(artifact.get("id", ""))
        kind = artifact.get("kind")
        status = artifact.get("status")
        if kind not in {"report", "plan", "history"}:
            continue
        if status not in {"raw", "active", "distilled"}:
            continue
        if artifact_id in known_evidence:
            continue
        candidates.append(
            {
                "source_artifact": artifact_id,
                "source_path": artifact.get("path"),
                "candidate_status": "needs_review",
                "suggested_target": "memory_record_candidate",
                "reason": "Explicit-only artifact may contain durable learning; review before promotion.",
            }
        )
    return candidates
