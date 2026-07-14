from __future__ import annotations

from pathlib import Path
from typing import Any

from .adoption import plan_adoption
from .closeout import closeout_workspace
from .diagnostics import severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace


def start_workspace(root: Path | str) -> dict[str, Any]:
    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)
    counts = severity_counts(diagnostics)
    adoption_preview = None
    if not inventory.files["workspace_yaml"]["exists"]:
        adoption_preview = plan_adoption(root)
    return {
        "tool": "archmarshal",
        "stage": "start",
        "root": str(inventory.root),
        "mode": "read_only",
        "project_ready": counts["error"] == 0,
        "diagnostic_summary": counts,
        "save_paths": inventory.save_paths,
        "naming": inventory.naming,
        "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
        "adoption_preview": adoption_preview,
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
        ],
    }


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
