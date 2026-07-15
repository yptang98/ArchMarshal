from __future__ import annotations

from pathlib import Path
from typing import Any

from .adoption import plan_adoption
from .closeout import closeout_workspace
from .diagnostics import severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace
from .resolver import resolve_workspace, skill_activation_block_reason


def start_workspace(
    root: Path | str,
    *,
    task: str | None = None,
    user_store: Path | str | None = None,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
) -> dict[str, Any]:
    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root, inventory=inventory)
    counts = severity_counts(diagnostics)
    adoption_preview = plan_adoption(root, tags=tags, backup_scope=backup_scope)
    blocked_skill_count = sum(
        skill_activation_block_reason(skill) is not None for skill in inventory.skills
    )
    sync_required = bool(
        adoption_preview.get("configured")
        and (
            adoption_preview.get("blocked")
            or adoption_preview.get("review_required")
            or adoption_preview.get("skill_index", {}).get("changed")
        )
    )
    governance_ready = counts["error"] == 0
    payload = {
        "tool": "archmarshal",
        "stage": "start",
        "root": str(inventory.root),
        "mode": "read_only",
        "governance_ready": governance_ready,
        "project_ready": governance_ready and not sync_required and blocked_skill_count == 0,
        "sync_required": sync_required,
        "blocked_skill_count": blocked_skill_count,
        "diagnostic_summary": counts,
        "save_paths": inventory.save_paths,
        "naming": inventory.naming,
        "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
        "adoption_preview": adoption_preview if not adoption_preview["configured"] else None,
        "skill_sync_preview": adoption_preview if adoption_preview["configured"] else None,
        "codex_contract": [
            "Use ArchMarshal checkpoint after context compression.",
            "Use auto recording depth; routine skill reuse only needs important changes.",
            "Keep summaries as indexes; do not delete raw reports, plans, checkpoints, or notes.",
            "Use user-approved project file save paths.",
            "Use time-first project file names with content hints.",
            "Call ArchMarshal end when the project or phase is complete.",
        ],
        "notes": [
            "Start is read-only and does not modify files.",
            "If diagnostics are present, ask Codex to explain them before changing project files.",
            "For an unmanaged project, adoption_preview lists safe create-only setup; use start --apply only after review.",
            "For an overlay-managed project, skill_sync_preview reports new or changed source skills without rewriting them.",
        ],
    }
    if task:
        resolution = resolve_workspace(
            root,
            task,
            user_store=user_store,
            adoption_preview=adoption_preview,
        )
        payload["resolution"] = resolution
        payload["task_ready"] = bool(
            payload["project_ready"]
            and not any(item.get("task_relevant") for item in resolution["blocked_skills"])
            and resolution["adoption_transaction"].get("state") == "none"
        )
    elif user_store is not None:
        payload["notes"].append("A user store is loaded only when --task is supplied.")
    return payload


def end_workspace(root: Path | str, used_skills: list[str] | None = None) -> dict[str, Any]:
    payload = closeout_workspace(root, used_skills)
    payload["stage"] = "end"
    payload["mode"] = "read_only"
    payload["codex_contract"] = [
        "Follow recording_policy mode=auto; do not create heavy summaries for routine skill reuse.",
        "Preserve original project history before distilling memory.",
        "Review candidate memory updates before promotion.",
        "Keep generated summaries explicit-only unless reviewed.",
    ]
    return payload


__all__ = ["start_workspace", "end_workspace"]
