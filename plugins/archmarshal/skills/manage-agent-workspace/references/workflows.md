# ArchMarshal Codex Workflow Reference

Use the plugin launcher at `../../scripts/run_archmarshal.py`; it selects the
active Python or a validated commit-scoped runtime and then calls the locked
wrapper. All paths below are arguments, not instructions for the user to type.

## Read-only intake

1. Run `doctor <root> [--user-store <store>] --pretty`.
2. Run `inventory`, `lint`, and `audit` when source layout and policy findings
   are relevant.
3. Keep absent, corrupt, legacy, orphan, partial, and truncated states distinct.
   `doctor` never repairs them.

## New or existing project

- New: preview `init <root> [--tag <tag>] --pretty`; apply only with the exact
  `--expect-plan` and the chosen backup scope. Supply `--user-store` only when
  the user placed a promoted layout profile in scope.
- Existing: when the user already named Skills that must stay unmanaged,
  preview `adopt` first with repeatable exact `--exclude-skill` package paths so
  those subtrees are pruned before content inspection. Otherwise preview after
  doctor. Report every package directory prepared for management, every
  excluded package, quarantine state, effective source root, backup scope,
  complete managed-source backup coverage, and proposed control path before
  apply. Add each nonstandard project-relative source root with repeatable
  `--skill-root`; the exact apply must replay the same roots and selection
  arguments.
- For project layout, report `foundation`, `quality`, `decision`, `source`,
  `requires_confirmation`, field provenance, all evidence, issues,
  recommendations, and `human_review.mapped_paths`. A nonstandard but safe
  confirmed layout is reasonable and remains unchanged. Detection is
  read-only evidence until the user confirms the exact plan.
- Translate confirmed choices to repeatable `--save-path kind=relative/path`
  plus `--naming-strategy`, `--timezone`, `--date-partition`, and
  `--timestamp-format`. Never pass an unsafe path merely to match a user's old
  convention; explain the blocker and suggest a safe nearby path without
  moving existing content.
- When preview reports preserved `.git`/VCS metadata, caches, virtual
  environments, dependency trees, or related artifact boundaries, do not enter
  or remove them. Unless the user already chose, ask whether to preserve those
  artifacts and manage the remaining Skill source or exclude the whole Skill.
  Preservation is the default and does not block adoption.
- Exact exclusions persist in immutable Skill-index history. A later run keeps
  them without repeated flags. Restore management only with explicit exact
  `--manage-skill`; then require fresh fingerprint, backup, and review.
- Start: call `start` after the control plane is healthy. Use the task text only
  for read-only Skill/context resolution.
- Never treat `start` as permission to reorganize arbitrary project files.

## Closeout levels

- `quick`: outcome and lightweight session evidence.
- `standard`: ordered steps and hashes of selected key scripts.
- `reproducible`: environment/dependency fingerprints, exact commands, copied
  script snapshots, and a reference run script.

Preview `end --level ...` first and apply with its exact plan. Report
`workspace_owned`, `evidence_ready`, `recording_ready`, and
`execution_validated` separately, and inspect the exact `session_preview`.

## Learning and candidate lifecycle

1. Preview `learn`; save the complete JSON outside the project and apply with
   `--plan-file` plus exact `--expect-plan`.
2. Review a committed pack with `candidate-review`; accept/reject/defer is an
   immutable user-store decision and requires the exact saved preview and HEAD.
3. For an accepted common-Skill candidate, preview `candidate-draft` to an
   absent destination outside source project and user store. Apply with the
   complete preview, exact plan, and exact HEAD.
4. Confirm the envelope contains `REVIEW.md`, nested `manifest.yaml`, nested
   `SKILL.md.draft`, and final `COMMITTED.json`. Do not rename automatically.
5. After a human completes the checklist, explicitly renames to `SKILL.md`, and
   activates the manifest, preview `candidate-promote` against the nested
   package. Apply only the exact saved promotion plan.
6. Preference candidates skip draft creation but retain exact acceptance,
   replacement, plan, and HEAD requirements.
7. A `preferred.workspace_layout` candidate is eligible only after the same
   explicitly confirmed profile appears in multiple projects with committed
   session evidence. Detected layouts and one-off choices remain local.

## Restore and rollback

- Restore only into a new absent directory. Never simulate in-place restore.
- Rebind ownership only for a verified full backup and only in the restored
  copy.
- Skill-index and user-store rollback are forward publications from a verified
  ancestor; they never restore, delete, or rewrite source Skill files.
- Run status/doctor before and after. Keep uncertain orphan or partial state.

## Exact-preview storage

Use a system temporary file for saved previews unless the user approves a
durable evidence path. Preserve bytes exactly; do not reconstruct a smaller
plan from displayed fields. Remove temporary preview files only when they were
created by the current operation and removal is safe and authorized by the host
workflow.
