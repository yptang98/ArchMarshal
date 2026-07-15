from __future__ import annotations

from pathlib import Path
from typing import Any

from ._doctor_core import (
    FILESYSTEM_SAFETY,
    MAX_HISTORY_LIMIT,
    Budget,
    Report,
    absolute_lexical,
    inspect_root,
)
from ._doctor_immutable import inspect_skill_index, inspect_user_store
from ._doctor_sessions import inspect_sessions
from ._doctor_workspace import (
    fill_unavailable_workspace_areas,
    inspect_control_plane,
    inspect_ownership,
    inspect_transactions,
)
from .formats import FORMAT_REGISTRY_VERSION, format_registry

DOCTOR_API_VERSION = "archmarshal-doctor-v1"


def doctor_workspace(
    root: Path | str,
    user_store: Path | str | None = None,
    history_limit: int = 20,
) -> dict[str, Any]:
    """Return a deterministic, bounded, strictly read-only health report."""
    if isinstance(history_limit, bool) or not isinstance(history_limit, int):
        raise ValueError("history_limit must be an integer")
    if not 1 <= history_limit <= MAX_HISTORY_LIMIT:
        raise ValueError(f"history_limit must be between 1 and {MAX_HISTORY_LIMIT}")

    workspace = absolute_lexical(root)
    store = absolute_lexical(user_store) if user_store is not None else None
    report = Report(Budget(history_limit))
    if inspect_root(workspace, "workspace", report):
        inspect_ownership(workspace, report)
        inspect_control_plane(workspace, report)
        inspect_transactions(workspace, report)
        inspect_skill_index(workspace, report)
        inspect_sessions(workspace, report)
    else:
        fill_unavailable_workspace_areas(workspace, report)

    if store is None:
        report.add(
            "user_store",
            "user_store_not_configured",
            "info",
            "not_configured",
            "user_store",
            ".",
            "No explicit user store was requested.",
        )
    elif inspect_root(store, "user_store", report):
        inspect_user_store(store, report)
    return _finalize(workspace, store, report)


def _finalize(workspace: Path, store: Path | None, report: Report) -> dict[str, Any]:
    findings = sorted(
        report.findings,
        key=lambda item: (
            item["area"],
            item["scope"],
            item["path"],
            item["code"],
            item["classification"],
        ),
    )
    suggestions = sorted(
        report.suggestions,
        key=lambda item: (item["scope"], item["path"], item["classification"]),
    )
    summary = {
        severity: sum(item["severity"] == severity for item in findings)
        for severity in ("error", "warning", "info")
    }
    if summary["error"]:
        state = "error"
    elif summary["warning"]:
        state = "warning"
    elif any(item["classification"] == "absent" for item in findings):
        state = "absent"
    else:
        state = "healthy"
    return {
        "api_version": DOCTOR_API_VERSION,
        "format_registry_version": FORMAT_REGISTRY_VERSION,
        "mode": "read_only",
        "workspace_root": str(workspace),
        "user_store_root": str(store) if store is not None else None,
        "state": state,
        "summary": summary,
        "budgets": report.budget.as_dict(),
        "filesystem_safety": dict(FILESYSTEM_SAFETY),
        "formats": format_registry()["formats"],
        "findings": findings,
        "retention_suggestions": suggestions,
        "source_mutation": False,
    }


__all__ = ["DOCTOR_API_VERSION", "doctor_workspace"]
