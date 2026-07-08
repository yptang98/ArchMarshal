from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic, severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace


def audit_workspace(root: Path | str) -> dict[str, Any]:
    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)
    counts = severity_counts(diagnostics)
    return {
        "tool": "archmarshal",
        "root": str(inventory.root),
        "summary": {
            "errors": counts["error"],
            "warnings": counts["warning"],
            "infos": counts["info"],
            "artifact_count": len(inventory.artifacts),
            "skill_count": len(inventory.skills),
            "context_module_count": len(inventory.context_modules),
            "unregistered_agent_file_count": len(inventory.unregistered_agent_files),
        },
        "risk_model": _risk_model(diagnostics),
        "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
    }


def _risk_model(diagnostics: list[Diagnostic]) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    rules = {diagnostic.rule for diagnostic in diagnostics}
    if any(rule.startswith("skill.") for rule in rules):
        risks.append(
            {
                "area": "skill-routing",
                "risk": "Skill selection may be ambiguous, incomplete, or unreproducible.",
            }
        )
    if any(rule.startswith("project.report") or rule.startswith("project.archive") for rule in rules):
        risks.append(
            {
                "area": "context-bloat",
                "risk": "Historical artifacts may leak into default context.",
            }
        )
    if "project.unregistered_agent_file" in rules:
        risks.append(
            {
                "area": "workspace-memory",
                "risk": "Agent workspace files are accumulating outside the registry.",
            }
        )
    if "project.context_module_missing_source_files" in rules:
        risks.append(
            {
                "area": "context-traceability",
                "risk": "Promoted context lacks provenance back to source files.",
            }
        )
    if not risks:
        risks.append(
            {
                "area": "baseline",
                "risk": "No obvious governance risks detected by current rules.",
            }
        )
    return risks

