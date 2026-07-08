from __future__ import annotations

from pathlib import Path
from typing import Any

from .lint import lint_workspace


ACTION_BY_RULE = {
    "project.missing_workspace_yaml": "Create .agent/workspace.yaml from templates/project-basic and adjust path mappings.",
    "project.missing_agent_index": "Create .agent/INDEX.md as a short human map.",
    "project.agents_md_too_large": "Move long-lived facts into .agent/knowledge and raw history into .agent/history.",
    "project.agents_md_contains_history": "Keep AGENTS.md as a router and make history explicit-only.",
    "project.unregistered_agent_file": "Register durable files in .agent/registry.yaml or move temporary files to inbox/archive.",
    "project.report_read_policy_not_explicit": "Set report read_policy to explicit_only.",
    "project.archive_read_policy_not_never_default": "Set archive read_policy to never_default or explicit_only.",
    "project.context_module_missing_source_files": "Add source_files to the context module.",
    "project.context_module_not_registered": "Register the context module in .agent/registry.yaml.",
    "project.knowledge_without_read_policy": "Choose a task-based read policy for knowledge artifacts.",
    "project.artifact_path_missing": "Fix the registry path or create the missing artifact.",
    "project.duplicate_artifact_id": "Rename duplicate artifact ids so the registry is unambiguous.",
    "project.generated_skill_not_registered": "Register generated skills as generated_skill artifacts.",
    "skill.missing_manifest": "Add manifest.yaml to the skill directory.",
    "skill.missing_required_field": "Add the required manifest field.",
    "skill.kind_scope_mismatch": "Align skill kind and scope.",
    "skill.missing_tags": "Add tags to the skill manifest.",
    "skill.missing_triggers": "Add explicit triggers to the skill manifest.",
    "skill.missing_negative_triggers": "Add negative_triggers to reduce skill conflicts.",
    "skill.common_project_missing_reproducibility": "Make scripts/templates/references local and set reproducibility flags.",
    "skill.duplicate_name": "Rename, disable, or archive duplicate skills.",
    "skill.overlapping_trigger": "Tighten triggers or add negative_triggers for conflicting skills.",
    "skill.global_too_large": "Split procedural details out of global policy.",
    "skill.global_contains_project_fact": "Move project facts from global policy into project knowledge or context modules.",
    "skill.functional_contains_project_fact": "Move project-private details into project skills.",
}


def plan_workspace(root: Path | str) -> dict[str, Any]:
    diagnostics = lint_workspace(root)
    actions = []
    for index, diagnostic in enumerate(diagnostics, start=1):
        actions.append(
            {
                "id": f"plan-{index:03d}",
                "rule": diagnostic.rule,
                "severity": diagnostic.severity,
                "path": diagnostic.path,
                "action": ACTION_BY_RULE.get(diagnostic.rule, diagnostic.suggestion or "Review diagnostic."),
                "mode": "propose_only",
            }
        )
    return {
        "tool": "archmarshal",
        "root": str(Path(root).resolve()),
        "destructive": False,
        "apply_supported": False,
        "actions": actions,
        "notes": [
            "This plan is read-only and does not modify files.",
            "Future apply commands should require explicit confirmation and diff preview.",
        ],
    }
