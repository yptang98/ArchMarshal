# Product Requirements Traceability

This document maps the core product needs to concrete ArchMarshal behavior.

## Requirement Matrix

| Need | ArchMarshal Behavior | Evidence |
|---|---|---|
| Global skills must be highest priority but lightweight. | `global_skill` is a distinct kind and `global_skills` is a distinct workspace path. Lint flags oversized global skills and global skills that appear to contain local facts. | `src/archmarshal/lint.py`, `examples/simple-project/.agents/global/lightweight-policy/` |
| Functional skills can be rich but must be classified. | Functional skills require manifest tags, triggers, and negative triggers. | `schemas/skill-manifest.schema.yaml`, `skill.missing_tags`, `skill.missing_triggers`, `skill.missing_negative_triggers` |
| Common project skills are peer skills but must be reproducible. | `common_project_skill` requires local reproducibility flags and can keep scripts, templates, references, and tests under the skill directory. | `skill.common_project_missing_reproducibility`, `examples/monorepo-project/.agents/skills/common-project/release-checklist/` |
| Skill conflicts must be visible. | Lint reports duplicate names and overlapping triggers. | `skill.duplicate_name`, `skill.overlapping_trigger`, `tests/test_archmarshal.py` |
| Project files have flexible paths. | `.agent/workspace.yaml` maps code roots, skill roots, generated skills, knowledge, context modules, and historical artifact paths. | `schemas/workspace.schema.yaml`, `src/archmarshal/inventory.py` |
| Project memory should not become default context. | Reports, history, archive, and cache are explicit-only by policy; lint flags report/archive read policies that allow default loading. | `project.report_read_policy_not_explicit`, `project.archive_read_policy_not_never_default` |
| Not every project file is a dynamic module. | Raw files live in registry as artifacts; only promoted reusable content becomes context modules with `source_files`. | `schemas/artifact-registry.schema.yaml`, `project.context_module_missing_source_files` |
| Generated skills must be traceable. | Generated skill directories are linted against registry entries. | `project.generated_skill_not_registered` |
| The system should grow without becoming heavy. | Inventory, lint, audit, and plan are read-only and scoped by path mapping instead of loading every historical file. | `archmarshal inventory`, `archmarshal lint`, `archmarshal audit`, `archmarshal plan` |
| Dynamic modular skill loading should stay flexible. | `archmarshal resolve --task` scores skills and context modules from triggers, tags, negative triggers, and read policies without loading historical directories. | `src/archmarshal/resolver.py`, `tests/test_archmarshal.py` |
| Project completion should produce skill/memory cleanup. | `archmarshal closeout --used-skill` summarizes used skills, missing skill references, current diagnostics, and propose-only cleanup actions. | `src/archmarshal/closeout.py`, `tests/test_archmarshal.py` |

## Current Product Boundary

ArchMarshal is not yet a runtime dynamic loader. It is a governance control plane that makes workspace state inspectable and safe to improve.

The intended adoption path is:

1. Add workspace mapping and registry.
2. Run inventory to see actual skill and artifact state.
3. Run lint to find structural risks.
4. Run audit to summarize why the risks matter.
5. Run plan to produce non-destructive cleanup proposals.
6. Add apply only after review, diff preview, and archive-first behavior are stable.
