# Product Requirements Traceability

This document maps the core product needs to concrete ArchMarshal behavior.

## Requirement Matrix

| Need | ArchMarshal Behavior | Evidence |
|---|---|---|
| Global skills must be highest priority but lightweight. | `global_skill` is a distinct kind; the resolver returns active highest-priority global skills as required policy. Lint flags oversized global skills and global skills that appear to contain local facts. | `src/archmarshal/resolver.py`, `src/archmarshal/lint.py` |
| Functional skills can be rich but must be classified. | Functional skills require manifest tags, triggers, and negative triggers. | `schemas/skill-manifest.schema.yaml`, `skill.missing_tags`, `skill.missing_triggers`, `skill.missing_negative_triggers` |
| Common project skills are peer skills but must be reproducible. | `common_project_skill` requires local reproducibility flags, verifies declared local paths, and rejects dependency files outside the skill directory. | `skill.common_project_missing_reproducibility`, `skill.path_outside_skill_root`, `skill.local_path_missing`, `skill.dependency_file_outside_skill_root` |
| Skill dependencies should fail visibly. | Declared command dependencies are checked against the current PATH and reported as warnings when unavailable. | `skill.command_dependency_missing` |
| Skill conflicts must be visible. | Lint reports duplicate names and overlapping triggers. | `skill.duplicate_name`, `skill.overlapping_trigger`, `tests/test_archmarshal.py` |
| Project files have flexible paths. | `.agent/workspace.yaml` maps code roots, skill roots, generated skills, knowledge, context modules, and historical artifact paths. | `schemas/workspace.schema.yaml`, `src/archmarshal/inventory.py` |
| Project outputs need user-approved destinations. | `.agent/workspace.yaml` records `save_paths.project_files`; skill paths can default to governed skill roots. | `project.save_paths_missing`, `project.project_file_save_paths_missing`, `archmarshal checkpoint --save-path` |
| Project outputs need stable, human-findable names. | Project file naming uses UTC time first, then a content slug and artifact kind. | `project.project_file_naming_missing`, `checkpoint.filename`, `naming.project_files` |
| Bad project metadata should not crash the CLI. | Workspace, registry, skill manifest, and context module YAML parse fail-soft and return structured diagnostics. | `project.workspace_yaml_invalid`, `project.registry_yaml_invalid`, `skill.invalid_manifest_yaml`, `project.context_module_invalid_yaml` |
| Declared schemas must be enforced, not only documented. | Lint validates workspace, artifact registry, skill, memory-store, and memory-record YAML against packaged JSON Schemas. | `project.workspace_schema_invalid`, `memory.store_schema_invalid`, `memory.record_schema_invalid`, `src/archmarshal/schema_validation.py` |
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
| Existing projects and skills must not be damaged during adoption. | Adoption requires the exact reviewed plan digest, blocks on reserved-file conflicts, verifies a backup first, and publishes a durable create-only journal before visible control paths. Forward recovery verifies every target and never overwrites or deletes a changed path. Source skills are read-only. | `src/archmarshal/adoption.py`, `src/archmarshal/adoption_tx.py`, `src/archmarshal/safety.py`, `test_interrupted_adoption_is_forward_recoverable`, `test_adoption_recovery_preserves_changed_target` |
| Interrupted adoption must be inspectable and recoverable. | `adoption-status` reports active transaction, backup, target, lock, and skill-head state. `adoption-recover` requires that exact transaction id and plan digest, and can only complete missing create-only targets after journal, staged payload (when needed), backup, lock, and index verification. | `src/archmarshal/adoption_tx.py`, `test_adoption_recovery_requires_exact_reviewed_transaction`, `test_adoption_recovery_is_idempotent_after_receipt` |
| Existing skills need dynamic metadata without in-place rewrites. | Complete-package hashes feed immutable content-addressed generations. Sync records add/modify/remove/restore events and publishes only by exclusive lock plus expected-`HEAD` compare-and-swap; source skills and old generations are never rewritten. | `src/archmarshal/skill_index.py`, `src/archmarshal/inventory.py`, `tests/test_skill_index.py` |
| Imported Skills must not activate before review. | Adoption validates the Codex Skill package, marks imported routing metadata `needs_review`, and disables invalid packages. `skill-review` binds approve/reject to the exact package and routing subject; elevated policy requires `--allow-global-policy`. | `src/archmarshal/skill_validation.py`, `src/archmarshal/skill_review.py`, `tests/test_product_v09.py` |
| Unreviewed source drift must not activate through old routing metadata. | Commit rechecks source package hashes while holding the OS lock; resolver quarantines missing, unsafe, untracked, or changed sources in `blocked_skills`. | `skill_index_source_changed`, `src/archmarshal/resolver.py`, `test_skill_index_commit_rechecks_active_source_inside_lock` |
| Loose or history-corrupt metadata must not activate. | Once an index exists, loose overlays absent from its snapshot are quarantined; readers block during publication and verify the complete parent chain reachable from `HEAD`. Portable case/Unicode source aliases are rejected. | `src/archmarshal/skill_index.py`, `src/archmarshal/inventory.py`, `test_unindexed_overlay_is_quarantined`, `test_missing_parent_generation_prevents_resolution` |
| Skill metadata rollback must be auditable and source-preserving. | Rollback requires an ancestor digest, reviewed expected HEAD, and exact logical plan digest; it verifies the entire parent chain, backs up current index state, and publishes a new forward generation. Active target package hashes must match; source files are never restored or modified. | `archmarshal skill-index-status`, `archmarshal skill-index-rollback`, `tests/test_skill_index.py` |
| Projects should be human-findable by date and tag. | Workspace metadata records creation/adoption dates and tags; closeout records use `.agent/history/YYYY/MM/DD/<topic>-<level>-<session-key>/`. | `src/archmarshal/adoption.py`, `src/archmarshal/session.py` |
| Users need a lightweight view across projects. | `catalog` sorts project control planes by recorded creation date and supports AND-filtered tags without loading raw history. | `src/archmarshal/catalog.py`, `test_catalog_sorts_and_filters_projects_by_date_and_tags` |
| Project closeout needs three evidence levels. | `quick` records outcome, `standard` adds ordered steps and key-script hashes, and `reproducible` adds environment/dependency fingerprints, script snapshots, exact commands, and a reference run script. Apply requires the exact reviewed plan digest. A final commit manifest hashes every session file. | `archmarshal end --level`, `src/archmarshal/session.py` |
| Reproduction claims must be honest. | Closeout reports explicit evidence gaps, verifies copied script hashes, and states that commands were not executed or validated. | `reproduction_evidence_ready`, `execution_validated`, `test_reproducible_closeout_reports_missing_evidence` |
| Cross-project learning must not bloat or silently mutate the global layer. | `learn` aggregates only compact v2 session manifests whose commit marker and every declared file hash verify, reports legacy v1 sessions as unverified, caps candidate lists, and never changes global configuration. Incomplete and hash-mismatched sessions are ignored. | `src/archmarshal/learning.py`, `test_learning_creates_review_only_candidates_from_repeated_sessions`, `test_learning_reports_legacy_unverified_sessions` |
| Historical use must refer to the Skill version actually used. | Closeout records package hash, routing subject, index HEAD, and review state for each used Skill. Learning excludes unversioned, unreviewed, and rejected usage and groups evidence by the session-pinned package hash rather than the current source. | `src/archmarshal/closeout.py`, `src/archmarshal/session.py`, `test_learning_binds_usage_to_historical_package_not_current_source` |
| Candidate evidence must be reviewable and tamper-evident. | Learning writes date-organized candidate packs under an owned workspace and creates `COMMITTED.json` last. Review accepts only a verified pack under `.agent/inbox/learning/` and records compact provenance without absolute project paths. | `src/archmarshal/learning.py`, `src/archmarshal/promotion.py`, `test_learning_pack_is_commit_last_and_tamper_evident` |
| Reusable user Skills and preferences need safe cross-project state. | An explicitly initialized user store publishes validated common-Skill copies, bounded preferences, candidate decisions, and immutable generations under an OS lock and expected-HEAD CAS. Apply requires the complete saved preview; forward rollback creates a new generation. Projects and drafts remain unchanged. | `src/archmarshal/user_store.py`, `src/archmarshal/promotion.py`, `tests/test_user_store.py`, `tests/test_product_v09.py` |

## Current Product Boundary

ArchMarshal is not yet a runtime dynamic loader. It is a governance control plane that makes workspace state inspectable and safe to improve.

The safe adoption path is:

1. Preview `adopt` and inspect every proposed path, hash, and conflict.
2. Choose managed or broad project-content backup scope.
3. Apply with that exact `--expect-plan`; the durable transaction creates only
   missing control-plane files and a reviewed immutable generation. Source
   skills remain unchanged.
4. Run inventory/lint/audit/plan against the overlay.
5. Use append-only checkpoints and one of the three closeout levels.
6. Aggregate compact session evidence into committed, review-only
   skill/preference candidates.
7. Record accept/reject/defer in an isolated user store; promote only from the
   complete reviewed plan and an exact HEAD token.
8. Resolve verified user common Skills by task, or forward-roll back the store
   to an ancestor snapshot without touching any project.

ArchMarshal still has no general mutation engine. Apply-capable commands are
limited to exclusive creation of backups/managed files, durable adoption
transactions, immutable generation objects, an atomic internal `HEAD` update,
committed session records, candidate packs, and isolated user-store objects.
