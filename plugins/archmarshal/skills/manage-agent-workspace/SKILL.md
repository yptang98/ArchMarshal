---
name: manage-agent-workspace
description: Govern Codex projects, Skills, memory, and closeout evidence with ArchMarshal through a safety-first native plugin workflow. Use when the user asks ArchMarshal to organize, manage, adopt, initialize, inspect, diagnose, start, close, catalog, or learn from a project; manage existing or generated Skills; protect and back up existing project or Skill files; organize projects by date and tags; create reproducible records; or extract reusable Skills and user preferences.
---

# Manage with ArchMarshal

Treat this Skill as the primary product interface. Translate the user's intent
into ArchMarshal operations; do not make the user assemble CLI commands.

## Resolve the engine

If the user asks to install or update ArchMarshal itself, do not run a project
lifecycle command. Read [update.md](references/update.md) and keep the operation
outside every project root. For all project-management requests, continue with
the engine rules below.

Use `../../scripts/run_archmarshal.py`, resolved relative to this Skill's
directory. Run this stdlib-only launcher with the active Python interpreter and
pass ArchMarshal arguments as separate tokens. It uses the active interpreter
by default. When the installer created a validated, commit-scoped isolated
runtime, it reads only
`$CODEX_HOME/runtimes/archmarshal/current.json`, requires
`archmarshal-runtime-v1`, the matching engine version, a full commit SHA, and an
interpreter below that commit's runtime directory, then invokes the locked
wrapper with it. An invalid or stale runtime pointer is a stop condition.

The wrapper uses this repository's `src/` when
available, otherwise the uniquely named configured ArchMarshal Git marketplace
snapshot. It verifies the engine version, API, exact file list, and source-tree
hash against `engine.lock.json` before import. It never falls back to an ambient
installed Python package.

If the wrapper reports `archmarshal_engine_unavailable` or
any `archmarshal_engine_*mismatch`, `archmarshal_engine_*invalid`, or
`archmarshal_engine_lock_verification_failed` error, stop before mutation and
explain the required reviewed, pinned installation. Use `--bootstrap-status`
for a dependency-free identity check. During project work, never install or
upgrade dependencies automatically. A missing runtime must be repaired only by
the separately authorized installation prompt, outside every project root.

## Route the request

- Inspect health or safety: run `doctor`; add `--user-store` only when the user
  placed that store in scope. Use `inventory`, `lint`, or `audit` when the user
  asks for project-level detail beyond durable state.
- Manage a new project: preview `init`. If the user supplied a confirmed user
  store, let the preview inherit only its promoted
  `preferred.workspace_layout` profile; never treat detection as a global
  preference.
- Manage an existing project or existing Skills: run `doctor`, then preview
  `adopt`. If the user already named Skills that ArchMarshal must not manage,
  preview `adopt` with repeatable exact `--exclude-skill` paths before any
  broad inventory or doctor pass. Inspect normal and project-specific Skill locations; supply every
  nonstandard project-relative location with repeatable `--skill-root`.
  Preserve the built-in verified backup requirement.
- Respect the user's project layout before proposing ArchMarshal defaults.
  Report `layout.foundation`, `quality`, `decision`, `source`,
  `requires_confirmation`, every effective mapped path, and field provenance.
  A safe nonstandard layout is reasonable. Objective risks receive suggestions
  only; traversal, linked paths, VCS/cache/dependency/runtime boundaries, and
  file conflicts block apply.
- Start governed work: use `start` after inspecting whether initialization or
  adoption is still required.
- Finish work: use `end` with `quick`, `standard`, or `reproducible` evidence
  according to the user's requested depth. Never claim execution was reproduced
  merely because evidence was recorded.
- Find projects: use `catalog` with date/tag metadata rather than scanning raw
  histories.
- Learn reusable behavior: use `learn`, `candidate-review`, `candidate-draft`,
  and `candidate-promote` as separate review boundaries. Never mutate the
  global Skill layer automatically.

Read [workflows.md](references/workflows.md) before an apply-capable lifecycle,
candidate, restore, or rollback operation. Read
[safety.md](references/safety.md) before adopting existing content, resolving a
collision, handling partial state, or making any security claim.

## Enforce preview-first changes

1. Inspect the target and run the relevant preview.
2. Summarize proposed paths, backup scope, activation state, expected HEAD,
   conflicts, effective Skill roots, every `prepared_management_packages`
   directory, every excluded package, preserved artifact boundaries, complete
   backup coverage, effective layout and naming policy, layout evidence and
   recommendations, and whether any existing bytes could change. Do not
   describe a package as accepted for management before showing this boundary.
   Project configuration has priority over current explicit choices, then a
   confirmed user profile, then read-only detection, then defaults.
3. If `boundary_confirmation_required` is true and the user has not already
   chosen a policy, ask whether to preserve the reported cache/repository
   artifacts and manage the remaining Skill source, or exclude that entire
   Skill. Preservation is the default. Never inspect or remove those artifacts
   merely to make adoption pass.
4. Apply only when the user's request authorizes that concrete change and every
   required exact plan/HEAD token is available. Ask when destination, evidence
   depth, replacement, or scope would materially change the result.
   A detected layout requires confirmation through the exact reviewed plan.
5. Save complete reviewed preview JSON in a system temporary directory or a
   user-approved path outside the project. Do not add plan files to the project
   merely to drive apply.
6. Re-run through the wrapper with the complete saved preview and exact tokens.
7. Verify the result with `doctor`, the relevant status command, and a source
   tree or version-control diff. Report partial output explicitly.

## Protect user content

- Never delete, move, rename, normalize, or overwrite an existing user-owned
  project or Skill path.
- Treat `--skill-root` as additive. Never point it outside the project or into
  `.agent`, and never continue unless `skill_backup_coverage.complete` is true
  for every discovered package.
- Treat repeatable `--exclude-skill` as an exact project-relative package
  boundary, never as a glob or name match. Excluded packages must remain unread,
  unbacked-up, unindexed, unlearned, and unmodified. The immutable selection
  persists across later runs. Use exact `--manage-skill` only when the user
  explicitly restores management; never clear exclusions as a side effect.
- Preserve `.git`, `.hg`, `.svn`, caches, virtual environments, dependency
  trees, and build/runtime artifacts reported by the preview. They are outside
  the managed package fingerprint and backup. Do not delete or move them.
- Treat nested Skill packages as module boundaries. An excluded child Skill
  must not contribute bytes to a managed parent package fingerprint, backup,
  index, or learning evidence.
- Treat a collision, linked/reparse component, stale plan, stale HEAD, changed
  source, corrupt state, or uncertain ownership as a stop condition.
- Preserve interrupted partial output for inspection; never retry by clearing
  or replacing it.
- Keep imported Skills quarantined until exact package and routing review.
- Generate candidate scaffolds only through `candidate-draft`. Confirm the
  result contains `SKILL.md.draft`, not `SKILL.md`; require human completion,
  explicit rename, active manifest status, and a separate promotion preview.
- Keep global Skills lightweight. Promote reusable project behavior into the
  isolated user store only from repeated committed evidence and an exact
  accepted decision.
- Promote a layout habit only when the same explicitly confirmed profile is
  evidenced in multiple projects. Never learn a detected or single-project
  layout automatically.
- State the current threat model honestly: static links/reparse points and
  cooperative concurrency are covered; hostile same-permission ancestor
  replacement is not handle-relative yet.
- For closeout, report `workspace_owned`, `evidence_ready`, `recording_ready`,
  and `execution_validated` separately. Review `session_preview`; never turn a
  complete evidence capsule into an execution-success claim.

Prefer concise outcome updates in the user's language. Expose raw commands only
when the user asks for automation or debugging details.
