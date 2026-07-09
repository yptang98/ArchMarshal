from __future__ import annotations

from pathlib import Path
from typing import Any

from .lint import lint_workspace


ACTION_BY_RULE = {
    "project.missing_workspace_yaml": "Create .agent/workspace.yaml from templates/project-basic and adjust path mappings.",
    "project.workspace_yaml_invalid": "Fix .agent/workspace.yaml syntax.",
    "project.workspace_schema_invalid": "Update .agent/workspace.yaml so it conforms to schemas/workspace.schema.yaml.",
    "project.workspace_missing_metadata": "Add workspace.name and workspace.version.",
    "project.workspace_missing_paths": "Add required project_root and agent_root path mappings.",
    "project.workspace_invalid_path_entry": "Replace invalid path entries with non-empty relative paths.",
    "project.workspace_path_outside_root": "Move the mapping under the project root or document why the external mapping is intentional.",
    "project.missing_agent_index": "Create .agent/INDEX.md as a short human map.",
    "project.agents_md_too_large": "Move long-lived facts into .agent/knowledge and raw history into .agent/history.",
    "project.agents_md_contains_history": "Keep AGENTS.md as a router and make history explicit-only.",
    "project.unregistered_agent_file": "Register durable files in .agent/registry.yaml or move temporary files to inbox/archive.",
    "project.report_read_policy_not_explicit": "Set report read_policy to explicit_only.",
    "project.registry_yaml_invalid": "Fix .agent/registry.yaml syntax.",
    "project.registry_schema_invalid": "Update .agent/registry.yaml so it conforms to schemas/artifact-registry.schema.yaml.",
    "project.archive_read_policy_not_never_default": "Set archive read_policy to never_default or explicit_only.",
    "project.context_module_missing_source_files": "Add source_files to the context module.",
    "project.context_module_not_registered": "Register the context module in .agent/registry.yaml.",
    "project.knowledge_without_read_policy": "Choose a task-based read policy for knowledge artifacts.",
    "project.artifact_path_missing": "Fix the registry path or create the missing artifact.",
    "project.duplicate_artifact_id": "Rename duplicate artifact ids so the registry is unambiguous.",
    "project.generated_skill_not_registered": "Register generated skills as generated_skill artifacts.",
    "skill.missing_manifest": "Add manifest.yaml to the skill directory.",
    "skill.invalid_manifest_yaml": "Fix the skill manifest.yaml syntax.",
    "skill.manifest_schema_invalid": "Update the skill manifest so it conforms to schemas/skill-manifest.schema.yaml.",
    "skill.missing_required_field": "Add the required manifest field.",
    "skill.kind_scope_mismatch": "Align skill kind and scope.",
    "skill.path_outside_skill_root": "Move declared skill paths back under the skill directory.",
    "skill.local_path_missing": "Create the declared local reproducibility directory under the skill path.",
    "skill.local_path_empty": "Add local reproducibility material or change the reproducibility claim.",
    "skill.dependency_file_outside_skill_root": "Copy dependency files into the skill directory or declare an external command dependency.",
    "skill.declared_dependency_file_missing": "Add the missing dependency file under the skill directory.",
    "skill.command_dependency_missing": "Install the declared command dependency or run the skill in a prepared environment.",
    "skill.memory_side_effect_undeclared": "Declare memory_effects for skills that read, write, or propose durable memory.",
    "skill.missing_tags": "Add tags to the skill manifest.",
    "skill.missing_triggers": "Add explicit triggers to the skill manifest.",
    "skill.missing_negative_triggers": "Add negative_triggers to reduce skill conflicts.",
    "skill.common_project_missing_reproducibility": "Make scripts/templates/references local and set reproducibility flags.",
    "skill.duplicate_name": "Rename, disable, or archive duplicate skills.",
    "skill.overlapping_trigger": "Tighten triggers or add negative_triggers for conflicting skills.",
    "skill.global_too_large": "Split procedural details out of global policy.",
    "skill.global_contains_project_fact": "Move project facts from global policy into project knowledge or context modules.",
    "skill.functional_contains_project_fact": "Move project-private details into project skills.",
    "memory.store_unregistered": "Register detected memory/rule locations in .agent/memory-stores.yaml.",
    "memory.store_yaml_invalid": "Fix .agent/memory-stores.yaml syntax.",
    "memory.store_missing_required_field": "Add required memory store metadata.",
    "memory.store_path_missing": "Create the filesystem memory store path or remove the stale declaration.",
    "memory.no_forget_policy": "Declare forget_policy or supersession_policy for the memory store.",
    "memory.default_blob_too_large": "Split default-loaded memory into smaller records or make retrieval task-based.",
    "memory.record_yaml_invalid": "Fix .agent/memory-records.yaml syntax.",
    "memory.record_missing_required_field": "Add required memory record metadata.",
    "memory.record_unknown_store": "Declare the referenced memory store or fix store_id.",
    "memory.record_content_missing": "Create the content file or fix content_path.",
    "memory.no_source_evidence": "Attach evidence_refs before activating or promoting the memory record.",
    "memory.generated_unreviewed": "Keep generated memory as candidate until reviewed.",
    "memory.conflicting_records": "Mark one memory record as superseded or narrow the retrieval keys.",
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
