# Lint Rules

This is the first rule list for future `archmarshal lint` and `archmarshal audit` commands.

The MVP inventory script does not enforce every rule yet. These names are stable placeholders for report output.

## Skill Rules

```text
skill.missing_manifest
skill.missing_tags
skill.missing_triggers
skill.missing_negative_triggers
skill.global_too_large
skill.global_contains_project_fact
skill.functional_contains_project_fact
skill.common_project_missing_reproducibility
skill.script_outside_skill_path
skill.undeclared_external_dependency
skill.duplicate_name
skill.overlapping_trigger
skill.governance_implicit_enabled
```

## Project File Rules

```text
project.missing_workspace_yaml
project.missing_agent_index
project.agents_md_too_large
project.agents_md_contains_history
project.unregistered_agent_file
project.report_read_policy_not_explicit
project.archive_read_policy_not_never_default
project.inbox_file_too_old
project.generated_skill_not_registered
project.context_module_missing_source_files
project.knowledge_without_read_policy
```

## Severity Guidance

Use `error` when the structure blocks reproducibility or creates unsafe default behavior.

Use `warning` when the workspace is understandable but weaker than recommended.

Use `info` for improvement suggestions that do not affect safety or reproducibility.
