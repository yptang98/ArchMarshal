# Product Requirements Traceability

This document maps the core product needs to concrete ArchMarshal behavior.

## Requirement Matrix

| Need | ArchMarshal Behavior | Evidence |
|---|---|---|
| Global skills must be highest priority but lightweight. | `global_skill` is a distinct kind and `global_skills` is a distinct workspace path. Lint flags oversized global skills and global skills that appear to contain local facts. | `src/archmarshal/lint.py`, `examples/simple-project/.agents/global/lightweight-policy/` |
| Functional skills can be rich but must be classified. | Functional skills require manifest tags, triggers, and negative triggers. | `schemas/skill-manifest.schema.yaml`, `skill.missing_tags`, `skill.missing_triggers`, `skill.missing_negative_triggers` |
| Common project skills are peer skills but must be reproducible. | `common_project_skill` requires local reproducibility flags, verifies declared local paths, and rejects dependency files outside the skill directory. | `skill.common_project_missing_reproducibility`, `skill.path_outside_skill_root`, `skill.local_path_missing`, `skill.dependency_file_outside_skill_root` |
| Skill dependencies should fail visibly. | Declared command dependencies are checked against the current PATH and reported as warnings when unavailable. | `skill.command_dependency_missing` |
| Skill conflicts must be visible. | Lint reports duplicate names and overlapping triggers. | `skill.duplicate_name`, `skill.overlapping_trigger`, `tests/test_archmarshal.py` |
| Project files have flexible paths. | `.agent/workspace.yaml` maps code roots, skill roots, generated skills, knowledge, context modules, and historical artifact paths. | `schemas/workspace.schema.yaml`, `src/archmarshal/inventory.py` |
| Project outputs need user-approved destinations. | `.agent/workspace.yaml` records `save_paths.project_files`; skill paths can default to governed skill roots. | `project.save_paths_missing`, `project.project_file_save_paths_missing`, `archmarshal checkpoint --save-path` |
| Project outputs need stable, human-findable names. | Project file naming uses UTC time first, then a content slug and artifact kind. | `project.project_file_naming_missing`, `checkpoint.filename`, `naming.project_files` |
| Bad project metadata should not crash the CLI. | Workspace, registry, skill manifest, and context module YAML parse fail-soft and return structured diagnostics. | `project.workspace_yaml_invalid`, `project.registry_yaml_invalid`, `skill.invalid_manifest_yaml`, `project.context_module_invalid_yaml` |
| Declared schemas must be enforced, not only documented. | Lint validates workspace, artifact registry, and skill manifest YAML against packaged JSON Schemas. | `project.workspace_schema_invalid`, `project.registry_schema_invalid`, `skill.manifest_schema_invalid`, `src/archmarshal/schema_validation.py` |
| Flexible paths must not silently balloon context. | Lint validates workspace metadata, required mappings, invalid entries, and warns when mappings point outside the project root. | `project.workspace_missing_metadata`, `project.workspace_path_outside_root` |
| Project memory should not become default context. | Reports, history, archive, and cache are explicit-only by policy; lint flags report/archive read policies that allow default loading. | `project.report_read_policy_not_explicit`, `project.archive_read_policy_not_never_default` |
| Summaries must not erase original project history. | Checkpoint and closeout outputs mark summaries as indexes and keep raw history explicit-only, append-only, or archive-first. | `archmarshal checkpoint`, `original_preservation_policy`, `preservation_manifest` |
| Not every project file is a dynamic module. | Raw files live in registry as artifacts; only promoted reusable content becomes context modules with `source_files`. | `schemas/artifact-registry.schema.yaml`, `project.context_module_missing_source_files` |
| Memory stores need governance too. | `.agent/memory-stores.yaml` declares memory ownership, scope, privacy, read/write policies, token budget, and forget/supersession policy. | `schemas/memory-stores.schema.yaml`, `memory.store_unregistered`, `memory.no_forget_policy` |
| Durable memories need evidence and review state. | `.agent/memory-records.yaml` tracks content path, store id, namespace, evidence refs, confidence, review status, supersession, and retrieval keys. | `schemas/memory-records.schema.yaml`, `memory.no_source_evidence`, `memory.generated_unreviewed` |
| Generated skills must be traceable. | Generated skill directories are linted against registry entries. | `project.generated_skill_not_registered` |
| The system should grow without becoming heavy. | Inventory, lint, audit, and plan are read-only and scoped by path mapping instead of loading every historical file. | `archmarshal inventory`, `archmarshal lint`, `archmarshal audit`, `archmarshal plan` |
| Dynamic modular skill loading should stay flexible. | `archmarshal resolve --task` scores skills, context modules, and memory records from triggers, tags, retrieval keys, negative triggers, and read policies without loading historical directories. | `src/archmarshal/resolver.py`, `tests/test_archmarshal.py` |
| Context compression should preserve key project state. | `archmarshal checkpoint` creates a read-only candidate checkpoint with summary, decisions, key files, next steps, and memory-record suggestions. | `src/archmarshal/checkpoint.py`, `tests/test_archmarshal.py` |
| Project completion should produce skill/memory cleanup. | `archmarshal closeout --used-skill` summarizes used skills, preservation needs, reproduction checks, missing skill references, diagnostics, and propose-only cleanup actions. | `src/archmarshal/closeout.py`, `tests/test_archmarshal.py` |
| Recording depth should be automatic and novelty-aware. | Checkpoint and closeout emit `recording_policy.mode: auto`; routine skill reuse only records important changes, while novel work can suggest memory/context/skill promotion. | `recording_policy.mode`, `recording_policy.level`, `src/archmarshal/checkpoint.py`, `src/archmarshal/closeout.py` |
| Existing projects and skills must not be damaged during adoption. | Adoption is preview-first, blocks on reserved-file conflicts, exclusively creates new paths, verifies a backup first, and represents source skills through non-mutating overlays. | `src/archmarshal/adoption.py`, `src/archmarshal/safety.py`, `test_adoption_preview_is_read_only_and_apply_uses_skill_overlays` |
| Existing skills need dynamic metadata without in-place rewrites. | Overlay manifests under `.agent/skill-overlays/` point to human-owned source directories with `managed: false` and `mutation_policy: never`; inventory resolves source-local material through the overlay. | `source` in `schemas/skill-manifest.schema.yaml`, `src/archmarshal/inventory.py` |
| Projects should be human-findable by date and tag. | Workspace metadata records creation/adoption dates and tags; closeout records use `.agent/history/YYYY/MM/DD/<time>-<topic>-<level>/`. | `src/archmarshal/adoption.py`, `src/archmarshal/session.py` |
| Users need a lightweight view across projects. | `catalog` sorts project control planes by recorded creation date and supports AND-filtered tags without loading raw history. | `src/archmarshal/catalog.py`, `test_catalog_sorts_and_filters_projects_by_date_and_tags` |
| Project closeout needs three evidence levels. | `quick` records outcome, `standard` adds ordered steps and key-script hashes, and `reproducible` adds environment/dependency fingerprints, script snapshots, exact commands, and a reference run script. | `archmarshal end --level`, `src/archmarshal/session.py` |
| Reproducible claims must be honest. | Reproducible closeout reports explicit gaps until summary, steps, scripts, and commands are present; copied script hashes are verified. | `reproducibility_ready`, `test_reproducible_closeout_reports_missing_evidence` |
| Cross-project learning must not bloat or silently mutate the global layer. | `learn` aggregates only compact session manifests, caps usage/preference lists, and writes review-only candidates without raw history or global configuration changes. | `src/archmarshal/learning.py`, `test_learning_creates_review_only_candidates_from_repeated_sessions` |

## Current Product Boundary

ArchMarshal is not yet a runtime dynamic loader. It is a governance control plane that makes workspace state inspectable and safe to improve.

The safe adoption path is:

1. Preview `adopt` and inspect every proposed path and conflict.
2. Choose managed or full backup scope.
3. Apply only missing control-plane files; source skills remain unchanged.
4. Run inventory/lint/audit/plan against the overlay.
5. Use append-only checkpoints and one of the three closeout levels.
6. Aggregate compact session evidence into review-only skill/preference candidates.

ArchMarshal still has no general mutation engine. Apply-capable commands are
limited to exclusive creation of backups, overlays, session records, and
candidate packs.
