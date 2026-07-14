# Lint Rules

These are the rule names currently emitted by `archmarshal lint` and surfaced by `archmarshal audit` / `archmarshal plan`.

## Skill Rules

```text
skill.missing_manifest
skill.invalid_manifest_yaml
skill.manifest_schema_invalid
skill.missing_tags
skill.missing_triggers
skill.missing_negative_triggers
skill.missing_required_field
skill.kind_scope_mismatch
skill.global_too_large
skill.global_contains_project_fact
skill.functional_contains_project_fact
skill.common_project_missing_reproducibility
skill.path_outside_skill_root
skill.local_path_missing
skill.local_path_empty
skill.dependency_file_outside_skill_root
skill.declared_dependency_file_missing
skill.command_dependency_missing
skill.memory_side_effect_undeclared
skill.overlay_source_outside_root
skill.overlay_source_missing
skill.overlay_source_changed
skill.overlay_source_untracked
skill.overlay_package_untracked
skill.overlay_source_unsafe
skill.duplicate_name
skill.overlapping_trigger
```

## Project File Rules

```text
project.missing_workspace_yaml
project.workspace_yaml_invalid
project.workspace_schema_invalid
project.workspace_missing_metadata
project.workspace_missing_paths
project.workspace_invalid_path_entry
project.workspace_path_outside_root
project.save_paths_missing
project.project_file_save_paths_missing
project.project_file_save_path_invalid
project.project_file_save_path_outside_root
project.project_file_naming_missing
project.project_file_naming_invalid
project.missing_agent_index
project.agents_md_too_large
project.agents_md_contains_history
project.unregistered_agent_file
project.workspace_path_outside_root
project.artifact_path_outside_root
project.report_read_policy_not_explicit
project.registry_yaml_invalid
project.registry_schema_invalid
project.archive_read_policy_not_never_default
project.generated_skill_not_registered
project.context_module_missing_source_files
project.context_module_invalid_yaml
project.context_module_not_registered
project.knowledge_without_read_policy
project.duplicate_artifact_id
project.artifact_path_missing
```

## Memory Rules

```text
memory.store_unregistered
memory.store_yaml_invalid
memory.store_schema_invalid
memory.store_missing_required_field
memory.store_path_missing
memory.no_forget_policy
memory.default_blob_too_large
memory.record_yaml_invalid
memory.record_schema_invalid
memory.store_path_outside_root
memory.record_content_outside_root
memory.record_missing_required_field
memory.record_unknown_store
memory.record_content_missing
memory.no_source_evidence
memory.generated_unreviewed
memory.conflicting_records
```

## Schema And Parse Rules

`project.workspace_yaml_invalid`, `project.registry_yaml_invalid`,
`skill.invalid_manifest_yaml`, and `project.context_module_invalid_yaml` mean the
file could not be parsed as YAML. ArchMarshal reports these as structured
diagnostics instead of throwing a traceback, then continues scanning what it can.

`project.workspace_schema_invalid`, `project.registry_schema_invalid`,
`skill.manifest_schema_invalid`, `memory.store_schema_invalid`, and
`memory.record_schema_invalid` mean the YAML parsed successfully but failed the
corresponding JSON Schema in `schemas/`. These diagnostics include a JSON path
such as `.agent/workspace.yaml#$.workspace.version`, the schema error message,
and a targeted suggestion.

## Severity Guidance

Use `error` when the structure blocks reproducibility or creates unsafe default behavior.

Use `warning` when the workspace is understandable but weaker than recommended.

Use `info` for improvement suggestions that do not affect safety or reproducibility.
