<p align="center">
  <img src="assets/archmarshal-cover.jpg" alt="ArchMarshal cover: scattered agent skills and project artifacts flowing into a governed control plane" width="100%">
</p>

<h1 align="center">ArchMarshal</h1>

<p align="center">
  <strong>A lightweight control plane for agent workspaces.</strong><br>
  Keep skills modular, project memory lifecycle-managed, and agents fast as your workspace grows.
</p>

<p align="center">
  <a href="https://github.com/yptang98/ArchMarshal/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/yptang98/ArchMarshal/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-teal">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Status" src="https://img.shields.io/badge/status-safety--hardened%20alpha-orange">
</p>

## Why This Exists

Agent workspaces age. Every project leaves behind skills, reports, plans, generated tools, memory snippets, and "temporary" notes that quietly become permanent. After enough projects, the agent gets heavier: global skills pile up, project memory becomes hard to scan, and overlapping skill triggers start fighting each other.

ArchMarshal exists for one core reason:

> **Agents should become sharper over time, not heavier.**

It treats skills as dynamic capability nodes, treats project memory as lifecycle-managed assets, and gives the workspace a registry, index, resolver, and closeout loop so useful context can be promoted while historical clutter stays explicit-only.

## What ArchMarshal Does

- Runs as a **Codex-native management plugin**: users ask in natural language,
  while the Python engine enforces exact plans, backups, and immutable state.
- Keeps **global skills** tiny and highest-priority.
- Lets **functional skills** grow richly with tags, triggers, and negative triggers.
- Makes **common project skills** reproducible by keeping scripts, templates, references, and dependency files inside the skill path.
- Manages **project files** through workspace path mappings, registries, read policies, and lifecycle states.
- Governs **memory stores and memory records** with ownership, privacy, evidence, review status, retrieval keys, and forget/supersession policy.
- Promotes only distilled reusable knowledge into **context modules**.
- Detects skill conflicts, missing manifests, unsafe read policies, unregistered generated skills, unregistered memory locations, and workspace mappings that could bloat context.
- Adopts existing projects through **metadata overlays**: existing `SKILL.md` and project files stay untouched.
- Initializes new projects through an exact-plan, create-only Skill scaffold;
  existing scaffold files are preserved rather than normalized in place.
- Fingerprints the **complete skill package**, so script/reference/asset drift is visible without rewriting the source.
- Records skill additions, modifications, removals, and restores as immutable, content-addressed generations with a locked compare-and-swap `HEAD`.
- Blocks drifted skill packages from resolution until reviewed, and supports verified history plus audited metadata rollback without touching source files.
- Quarantines adopted Skills until their exact package and routing metadata are
  explicitly approved; global/highest policy needs a separate confirmation.
- Records **quick**, **standard**, or **reproducible** closeouts in append-only, date-organized directories.
- Catalogs multiple projects by recorded creation date and AND-filtered tags.
- Extracts review-only common-skill and user-preference candidates from compact session manifests.
- Converts an accepted common-Skill candidate into a create-only review
  envelope whose nested package contains `SKILL.md.draft`, never an
  auto-discoverable `SKILL.md`.
- Promotes reviewed candidates into an isolated, bounded user Skill store with
  immutable packages that preserve executable modes and empty directories,
  expected-HEAD publication, provenance, and forward rollback.
- Loads built-in CLI domains on demand while keeping user Skill code strictly
  data-only until a host deliberately chooses to execute it.
- Provides a bounded, deterministic, strictly read-only `doctor` report for
  ownership, control-plane schemas, transactions, immutable generations,
  sessions, packages, formats, orphan/partial state, and filesystem capability
  truth.
- Provides Codex-facing `archmarshal-start` and `archmarshal-end` entrypoints; mutation-capable flows remain preview-first and create-only.

## Design Goals

- Keep global agent policy tiny, explicit, and highest priority.
- Separate functional skills from common project skills and project-specific skills.
- Require reusable skills to declare tags, triggers, negative triggers, dependencies, and outputs.
- Treat project files as artifacts first, not automatically loaded context.
- Promote only distilled, reusable knowledge into context modules.
- Generate reports and plans before any workspace-changing operation.
- Prefer archive over delete.

## Quick Start

ArchMarshal is a Codex-native management plugin. Users describe the outcome in
natural language; the plugin inspects the project, selects the safe lifecycle,
reviews exact plans, and invokes its deterministic Python engine internally.
The CLI remains available for CI, automation, and reproducible debugging, but
it is not the primary product experience.

### 1. Install the Codex plugin

```bash
codex plugin marketplace add yptang98/ArchMarshal --ref <reviewed-full-commit-sha>
codex plugin add archmarshal@personal
```

Start a new Codex task after installation so it loads the plugin Skill. The
plugin locates the matching engine inside the configured full Git marketplace
snapshot and refuses a version mismatch before mutation. Avoid an unpinned
`main` marketplace when reproducibility matters, and do not use a sparse
checkout that omits the repository `src/` directory.

### 2. Use ArchMarshal directly in Codex

Examples:

```text
用 ArchMarshal 安全接管这个已有项目和它的 Skills，先只诊断和预览。
用 ArchMarshal 开始管理这个新项目，标签是 research、python。
项目结束了，用 ArchMarshal 做认真整理，保留关键步骤和脚本哈希。
用 ArchMarshal 从最近项目中抽象一个可复用 Skill，但不要自动激活。
检查 ArchMarshal 健康状态，不要修改任何文件。
```

Codex runs the plugin workflow itself. It summarizes proposed paths, backup
scope, activation state, exact plan/HEAD tokens, and collisions before an
apply-capable change. Existing project and Skill files are never treated as
normalization targets.

### 3. What the plugin does for initialization and adoption

For a new project, preview the complete control plane and project Skill
scaffold first:

The following commands document the underlying automation contract. The plugin
normally invokes them for the user.

```bash
archmarshal init . --tag research --tag python --pretty
archmarshal init . --tag research --tag python \
  --expect-plan <plan_digest> --apply --pretty
```

This creates only missing files under `.agent/` plus
`.agents/skills/README.md`, `.agents/skills/project/.gitkeep`, and
`.agents/skills/generated/.gitkeep`. If any path already exists, ArchMarshal
preserves it; a linked ancestor or file/directory collision stops the operation.

For an existing project, preview adoption first:

```bash
archmarshal adopt . --tag research --tag python --pretty
```

Then explicitly create the management overlay:

```bash
archmarshal-start . --apply --expect-plan <plan_digest> \
  --tag research --tag python --pretty
```

ArchMarshal creates a content-verified backup before the first managed file, keeps every
existing skill in place, and stores generated routing metadata under
`.agent/skill-overlays/`. If a reserved control file already belongs to another
tool, adoption blocks instead of overwriting it. The digest must come from the
exact reviewed preview; an unreviewed or stale apply writes nothing.

For an already managed project, start also previews newly added skills and
complete-package drift. Applying an approved sync writes a new immutable skill
generation; it never replaces the source skill or an older generation:

```text
archmarshal-start
```

Use `--apply --expect-plan <plan_digest>` only after reviewing the proposed
generation and its file hashes.
Modified, removed, and restored skills are explicit plan entries. ArchMarshal
updates only its internal `HEAD` pointer under an exclusive lock and stale-plan
check; human-owned files remain untouched.

An adopted Skill is initially quarantined. Adoption output distinguishes the
optional raw `source_declared_status`, validated `normalized_source_status`,
`review_state`, and `activation_state` and supplies HEAD-bound `next_actions`;
source status is never presented as effective activation. Review the exact
package and routing revision before it can resolve:

```bash
archmarshal skill-review . --source skills/release-helper \
  --decision approve --expect-head <skill-index-head> --pretty \
  > skill-review.json
archmarshal skill-review . --source skills/release-helper \
  --decision approve --expect-head <skill-index-head> \
  --plan-file skill-review.json --expect-plan <plan_digest> --apply --pretty
```

Use `--allow-global-policy` in both commands only when intentionally approving a
global, global-scope, or highest-priority Skill. Any later package or routing
change invalidates that approval. The saved plan binds the exact `reviewed_at`,
complete immutable generation, proposed HEAD, object path, and generation byte
digest; apply never creates a new review timestamp.

If a process stops during adoption, inspect and forward-recover the durable
create-only transaction:

```bash
archmarshal adoption-status . --pretty
archmarshal adoption-recover . --pretty
archmarshal adoption-recover . --expect-transaction <transaction_id> \
  --expect-plan <plan_digest> --apply --pretty
```

Recovery verifies the original backup, journal, target hashes, lock identity,
and skill-index relationship; staged bytes are rechecked before creating a
missing target. A changed or replaced target is left untouched and blocks
recovery.

Inspect verified index history or preview an audited metadata rollback:

```bash
archmarshal skill-index-status . --pretty
archmarshal skill-index-rollback . --to <ancestor-sha256> --pretty
archmarshal skill-index-rollback . --to <ancestor-sha256> \
  --expect-head <preview-head> --expect-plan <plan_digest> \
  --reason "reviewed rollback" --apply --pretty
```

Rollback creates a new generation; it never points `HEAD` backward and never
restores source code. Any active skill in the target snapshot must still match
its complete-package hash, otherwise the operation blocks.
Rollback is not a permanent ignore rule: a later start will preview any current
source add/modify/restore difference again.

Then continue with normal work:

```text
Build the release checklist.
Refactor the API client.
Write the benchmark report.
```

During the project, checkpoints should preserve what must survive context
compression. Summaries are indexes, not replacements: raw reports, plans,
checkpoints, notes, and history stay preserved.

### 3. End

Choose the amount of evidence the project warrants:

```bash
# 1. Rough summary
archmarshal-end . --level quick --summary "Finished the release review"
archmarshal-end . --level quick --summary "Finished the release review" \
  --expect-plan <plan_digest> --apply

# 2. Careful record of steps and key scripts
archmarshal-end . --level standard --summary "Validated the release" \
  --step "Run tests" --step "Review artifacts" --script scripts/validate.py
# Repeat the same evidence with --expect-plan <plan_digest> --apply.

# 3. Reproduction-evidence capsule with hashed scripts and a reference run script
archmarshal-end . --level reproducible --summary "Reproduced benchmark A" \
  --step "Prepare data" --step "Run evaluation" --script scripts/eval.py \
  --command "python scripts/eval.py --config configs/a.yaml" --shell bash
# Repeat the same evidence with --expect-plan <plan_digest> --apply.
```

All three modes preview by default and write only to a new
`.agent/history/YYYY/MM/DD/...` directory with the exact reviewed plan digest.
`COMMITTED.json` is written last; learning ignores incomplete or hash-mismatched
sessions. Reproducible mode
reports `reproduction_evidence_ready: false` until summary, ordered steps, key
scripts, and exact commands are present. Readiness means required evidence is
present; ArchMarshal does not execute the commands or prove the result.

After multiple sessions, extract lightweight, review-only candidates:

```bash
archmarshal learn . --include-root ../another-project --pretty > learning-plan.json
archmarshal learn . --include-root ../another-project \
  --plan-file learning-plan.json --expect-plan <plan_digest> --apply --pretty
```

To reuse reviewed candidates across projects, initialize a dedicated user store
outside project roots. User-store mutation uses the complete saved preview, not
only a copied digest:

```bash
archmarshal user-store-init ~/.archmarshal/user-store --pretty > init-plan.json
archmarshal user-store-init ~/.archmarshal/user-store \
  --plan-file init-plan.json --expect-plan <plan_digest> --apply --pretty

archmarshal candidate-review . --pack .agent/inbox/learning/<pack> \
  --candidate <candidate-id> --decision accept \
  --reason "approved candidate evidence" \
  --user-store ~/.archmarshal/user-store --pretty > acceptance-plan.json
archmarshal candidate-review . --pack .agent/inbox/learning/<pack> \
  --candidate <candidate-id> --decision accept \
  --reason "approved candidate evidence" \
  --user-store ~/.archmarshal/user-store --plan-file acceptance-plan.json \
  --expect-head <head-or-none> --expect-plan <plan_digest> --apply --pretty

archmarshal candidate-draft . --pack .agent/inbox/learning/<pack> \
  --candidate <candidate-id> --user-store ~/.archmarshal/user-store \
  --destination ../archmarshal-drafts/<draft-envelope> \
  --pretty > candidate-draft-plan.json
archmarshal candidate-draft . --pack .agent/inbox/learning/<pack> \
  --candidate <candidate-id> --user-store ~/.archmarshal/user-store \
  --destination ../archmarshal-drafts/<draft-envelope> \
  --plan-file candidate-draft-plan.json --expect-head <exact-head> \
  --expect-plan <plan_digest> --apply --pretty
# Complete REVIEW.md, edit <draft-envelope>/<skill-name>/SKILL.md.draft,
# set manifest status to active, then rename SKILL.md.draft to SKILL.md.

archmarshal candidate-promote . --pack .agent/inbox/learning/<pack> \
  --candidate <candidate-id> --user-store ~/.archmarshal/user-store \
  --draft ../archmarshal-drafts/<draft-envelope>/<skill-name> \
  --reason "reviewed reusable workflow" \
  --pretty > promotion-plan.json
archmarshal candidate-promote . --pack .agent/inbox/learning/<pack> \
  --candidate <candidate-id> --user-store ~/.archmarshal/user-store \
  --draft ../archmarshal-drafts/<draft-envelope>/<skill-name> \
  --reason "reviewed reusable workflow" \
  --plan-file promotion-plan.json --expect-head <head-or-none> \
  --expect-plan <plan_digest> --apply --pretty

archmarshal resolve . --task "prepare release" \
  --user-store ~/.archmarshal/user-store --pretty
```

Save preview JSON as UTF-8, UTF-8 BOM, or BOM-marked UTF-16 JSON. Preference
candidates omit `--draft`. Promotion is blocked unless the latest decision for
the exact candidate digest and provenance is `accepted`. A common-Skill draft
may be written manually or scaffolded with `candidate-draft`; it must declare
this exact lineage in `manifest.yaml`. The scaffold destination must be absent,
outside both the source project and user store, and under an existing real
parent. Its commit marker records only the original baseline. Human edits are
expected and are re-hashed by the separate promotion preview.

Replacing an active record with the same Skill id or preference key requires
`--replace-existing-skill` or `--replace-existing-preference` in both preview
and apply. Without that explicit confirmation, ArchMarshal preserves the active
record and refuses to create a replacement plan.

```yaml
promotion:
  candidate_id: candidate.skill.<24-hex>
  candidate_digest: <candidate-digest-from-preview>
  source_skill_id: skill.<source-id>
  source_implementation_sha256: <source-package-sha256-from-candidate>
```

A project, candidate pack, and Skill draft remain unchanged; only immutable
files and the internal `HEAD` inside the explicitly initialized user store can
change. Use `candidate-review` for accept/reject/defer decisions and
`user-store-rollback --to <ancestor>` for a preview-first forward rollback.
`user-store-status` lists the immutable generation digests that can be selected
as rollback ancestors.

Browse projects without loading their raw history:

```bash
archmarshal catalog . --include-root ../another-project --tag research --pretty
```

That is the main workflow: install, `archmarshal-start`, normal project
instructions, `archmarshal-end`.
See [Getting Started](docs/getting-started.md) for the minimal prompts.
Maintainers should also follow the immutable [Release Process](docs/release-process.md).

Summaries are indexes, not replacements. Raw reports, plans, checkpoints, and
history should remain preserved with explicit-only read policies.

ArchMarshal does not force heavyweight summaries. The read-only closeout view
recommends a recording depth; the user explicitly chooses quick, standard, or
reproducible evidence when writing a session.

## Current Boundaries

- No GUI.
- No marketplace.
- No automatic third-party skill installation.
- No automatic project directory rewrite.
- No deletion of human-owned project or Skill files.
- No in-place edits to adopted skills; source skills are referenced through overlays.
- No overwrite or force mode for human-owned files; the only replacement is ArchMarshal's internal, backed-up `HEAD` pointer after lock/CAS validation.
- No summarization that deletes or replaces original project history.
- No automatic global configuration mutation.
- No dynamic context loading runtime.
- No execution of promoted Skill scripts; resolution is advisory.
- No claim of protection against a malicious process that can concurrently
  rewrite workspace directories; link/reparse checks and identity checks reduce
  races, but a handle-relative no-follow filesystem backend is still planned.

## Repository Layout

```text
ArchMarshal/
├─ README.md
├─ docs/
│  ├─ architecture.md
│  ├─ getting-started.md
│  ├─ skill-taxonomy.md
│  ├─ project-file-lifecycle.md
│  ├─ product-requirements.md
│  └─ lint-rules.md
├─ schemas/
│  ├─ workspace.schema.yaml
│  ├─ skill-manifest.schema.yaml
│  ├─ artifact-registry.schema.yaml
│  ├─ memory-stores.schema.yaml
│  └─ memory-records.schema.yaml
├─ scripts/
│  └─ inventory.py
├─ src/
│  └─ archmarshal/
├─ tests/
├─ templates/
│  ├─ project-basic/
│  ├─ skill-functional/
│  ├─ skill-common-project/
│  └─ context-module/
└─ examples/
   ├─ simple-project/
   ├─ monorepo-project/
   └─ audit-report.md
```

## Core Model

```text
Global Policy Layer
        |
Dynamic Skill Node Layer
        |
Project Workspace Layer
        |
Historical Artifact Layer
```

The layers are deliberately asymmetric. Global policy should be small and stable. Skills can be numerous, but must be classifiable and reproducible. Project workspaces can be messy, so ArchMarshal gives them registries, indexes, and lifecycle rules instead of pretending every file is a module.

## Developer CLI Reference

Most users should ask Codex to call ArchMarshal. This section is only for
maintainers who want to run the underlying CLI directly.

Install locally for development:

```bash
pip install -e .
```

Run read-only commands:

```bash
archmarshal-start examples/simple-project --pretty
archmarshal inventory examples/simple-project --pretty
archmarshal lint examples/simple-project --pretty
archmarshal audit examples/simple-project --pretty
archmarshal plan examples/simple-project --pretty
archmarshal skill-index-status examples/simple-project --pretty
archmarshal resolve examples/monorepo-project --task "prepare release checklist" --pretty
archmarshal closeout examples/monorepo-project --used-skill skill.common-project.release-checklist --pretty
archmarshal-end examples/monorepo-project --used-skill skill.common-project.release-checklist --pretty
```

Preview and explicitly apply safe lifecycle writes:

```bash
archmarshal adopt path/to/existing-project --tag research --pretty
archmarshal adopt path/to/existing-project --tag research \
  --expect-plan <plan_digest> --apply --pretty
archmarshal adoption-status path/to/existing-project --pretty
archmarshal adoption-recover path/to/existing-project \
  --expect-transaction <transaction_id> --expect-plan <plan_digest> --apply --pretty
archmarshal end path/to/project --level quick --summary "Phase complete" --pretty
archmarshal end path/to/project --level quick --summary "Phase complete" \
  --expect-plan <plan_digest> --apply --pretty
archmarshal learn path/to/project --pretty > learning-plan.json
archmarshal learn path/to/project --plan-file learning-plan.json \
  --expect-plan <plan_digest> --apply --pretty
archmarshal backup-verify path/to/backup.zip --pretty
archmarshal backup-restore path/to/backup.zip path/to/new-directory --pretty
archmarshal backup-restore path/to/backup.zip path/to/new-directory \
  --expect-plan <plan_digest> --apply --pretty
archmarshal backup-restore path/to/backup.zip path/to/new-managed-copy \
  --rebind-workspace --pretty
# Rebind accepts only a backup created by adoption/start with --backup-scope full.
# Repeat with --rebind-workspace, the exact --expect-plan, and --apply.
archmarshal skill-index-rollback path/to/project --to <ancestor-sha256> --pretty
archmarshal skill-index-rollback path/to/project --to <ancestor-sha256> \
  --expect-head <preview-head> --expect-plan <plan_digest> --apply --pretty
archmarshal user-store-status path/to/user-store --pretty
archmarshal doctor path/to/project --user-store path/to/user-store --pretty
archmarshal candidate-review path/to/project --pack <committed-pack> \
  --candidate <candidate-id> --decision accept --user-store <store> --pretty
archmarshal candidate-draft path/to/project --pack <committed-pack> \
  --candidate <candidate-id> --user-store <store> \
  --destination path/to/absent-draft-envelope --pretty
```

The compatibility wrapper still works:

```bash
python scripts/inventory.py examples/simple-project --pretty
```

`--apply` is deliberately narrow: adoption creates missing control-plane files
through a durable transaction after a verified backup, skill sync creates
immutable generation objects and atomically advances an internal `HEAD`, and
closeout/learning create append-only artifacts. Adoption and closeout additionally
require the exact digest of a reviewed preview. There is no overwrite, move,
delete, force, or automatic promotion path for human-owned project or skill files.

## Safety Rules

- Inventory, lint, audit, and plan are read-only by default.
- Adoption and closeout require both explicit `--apply` and the exact reviewed
  `--expect-plan`; learning additionally requires the complete saved preview.
- Adoption backs up the relevant managed control state and complete non-root skill packages before writing; root skills remain entrypoint-only. Managed backups never recursively embed prior backups, transactions, history, inbox, or cache. `--backup-scope full` creates a bounded project-content snapshot excluding VCS, dependencies, virtual environments, and prior backups, and preserves portable root/directory/file modes plus empty directories.
- Backup verification binds ZIP parsing, expanded file hashes, archive size,
  and archive hash to one stable descriptor and rejects path replacement.
- Existing skill sources are immutable to ArchMarshal; overlay manifests live under `.agent/skill-overlays/`.
- Skill sync uses immutable content-addressed objects, an exclusive `HEAD.lock`, and an expected-`HEAD` compare-and-swap; stale or concurrent plans fail without changing the active generation.
- `HEAD.lock` uses an OS-lifetime lock. Released v2 transaction metadata is recovered only after expected/proposed/current HEAD validation and is recorded under the internal recovery audit directory; legacy or malformed locks remain blocked.
- Resolver output quarantines source-missing, unsafe, untracked, or drifted skills instead of suggesting them for activation.
- Imported/adopted Skills also remain quarantined until validation and an exact
  `skill-review` approval; package or routing drift makes that approval stale.
- Directory scans do not follow symlinks, junctions, or Windows reparse points and enforce file/package bounds.
- Candidate draft scaffolding creates only an absent, disjoint envelope,
  publishes `COMMITTED.json` last, preserves partial output after interruption,
  and emits `SKILL.md.draft` so an external host cannot discover the unfinished
  scaffold as a Skill.
- `doctor` is strictly read-only and retention suggestions always set
  `automatic_action: false`. Its capability report explicitly states that the
  current backend is not handle-relative and does not claim protection from a
  same-permission process replacing ancestor directories during a write.
- Reserved control-file conflicts block the whole adoption before managed files are written.
- Closeout uses unique append-only directories, verifies copied script hashes,
  and writes `COMMITTED.json` last; incomplete or hash-mismatched sessions are
  not learned.
- Closeout and learning apply only inside a root-bound owned workspace and share
  a lifetime mutation lock with adoption and Skill review.
- The user store refuses non-empty unowned roots, is bound to its canonical
  location, copies only validated common-project Skill packages, rejects linked
  paths and sensitive/absolute preference values, and publishes by immutable
  generation plus expected-HEAD compare-and-swap.
- New user-store Skill packages use a v2 digest over file bytes, modes,
  executable state, subdirectory modes, and empty-subdirectory topology. The
  package root remains store-owned; existing v1 packages stay read-compatible
  and are never rewritten for migration.
- Atomic create-only publication requires same-filesystem hard-link support and
  fails closed when the filesystem cannot provide it.
- Environment variables are not captured. Known inline-secret patterns are blocked, but user-selected text and script snapshots may still contain sensitive material and require review.
- YAML inputs fail softly: bad workspace, registry, skill, or context module YAML becomes a structured diagnostic.
- Workspace, registry, skill, memory-store, and memory-record schemas are enforced during lint.
- Workspace save paths distinguish default skill roots from user-approved project file destinations.
- Resolve is advisory and loads only matched skill/context metadata.
- Closeout summarizes used skills and cleanup actions after project work.
- Checkpoint records compact state after context compression without modifying files.
- Summaries and memory candidates must point back to preserved original material.
- Global, functional, common-project, project, and generated skill roots are separately mapped.
- Historical artifact directories are explicit-read only:
  - `.agent/reports/`
  - `.agent/history/`
  - `.agent/archive/`
  - `.agent/cache/`
- New non-source artifacts should first enter `.agent/inbox/`.
- `AGENTS.md` is an entry router, not a history dump.
- `.agent/registry.yaml` is the machine ledger.
- `.agent/memory-stores.yaml` declares governed memory stores.
- `.agent/memory-records.yaml` declares reviewed or candidate memory records.
- `.agent/INDEX.md` is the human map.

## What Ships Today

- [x] README
- [x] Architecture documentation
- [x] Skill taxonomy documentation
- [x] Project file lifecycle documentation
- [x] Workspace schema
- [x] Skill manifest schema
- [x] Artifact registry schema
- [x] Lint-time schema validation for workspace, registry, and skill manifests
- [x] Basic project template
- [x] Functional skill template
- [x] Common project skill template
- [x] Context module template
- [x] Simple project example
- [x] Monorepo project example
- [x] Read-only inventory script
- [x] Lint rule list
- [x] Audit report sample
- [x] Executable CLI package
- [x] Validated Codex plugin, repository marketplace, and natural-language management Skill
- [x] Codex-facing `archmarshal-start` and `archmarshal-end` entrypoints
- [x] Governance lint rules
- [x] Fail-soft YAML parsing for workspace, registry, skill manifests, and context modules
- [x] Read-only remediation plan output
- [x] Task-based skill/context resolver
- [x] Read-only context checkpoint output for post-compression summaries
- [x] User-recorded project file save paths
- [x] Project closeout skill/memory summary
- [x] Closeout preservation manifest and reproduction checklist
- [x] Memory store and memory record governance
- [x] Memory-aware resolve and closeout candidate output
- [x] Tests for clean examples, missing entry files, skill conflicts, and historical read policy
- [x] Preview-first, backup-before-write adoption for existing projects
- [x] Exact-plan, create-only project Skill scaffold initialization
- [x] Reviewed-plan binding and durable forward-recoverable adoption transactions
- [x] Non-mutating skill metadata overlays
- [x] Complete skill-package fingerprints and drift reporting
- [x] Immutable skill generations with add/modify/remove/restore history and lock/CAS publication
- [x] Verified generation history and audited, source-preserving metadata rollback
- [x] OS-lifetime process locks with relationship-checked crash recovery records
- [x] Content-verified backup inspection and restore-to-new-directory flow
- [x] Conflict blocking and exclusive file creation (no overwrite mode)
- [x] Quick, standard, and reproducible append-only closeout records
- [x] Commit-last closeout manifests with learning-time integrity verification
- [x] Hashed key-script snapshots and explicit reproducibility readiness gaps
- [x] Date- and tag-aware workspace/session organization
- [x] Read-only cross-project catalog sorted by date and filtered by tags
- [x] Review-only common-skill and user-preference learning candidates
- [x] Codex Skill package validation and exact-package approval/rejection
- [x] Root-bound lifetime locks across workspace mutations
- [x] Session-pinned Skill package and routing evidence
- [x] Commit-last, tamper-evident learning packs
- [x] Isolated immutable user Skill/preference store with forward rollback
- [x] Explicit candidate decision and promotion workflow
- [x] Accepted-candidate to non-activating, create-only Skill draft envelope
- [x] User-store-aware task resolution and project start
- [x] User Skill package v2 with mode and empty-directory preservation
- [x] Lazy built-in CLI module loading for fast help/version bootstrap
- [x] Bounded read-only doctor and durable format registry
- [x] Reproducible 10,000-file / 100-Skill / multi-project scale benchmark

## Research Notes

- [Agent Memory and Skill Organization Landscape](docs/agent-memory-landscape-2026.md): design direction for memory stores, memory records, retrieval budgets, and closeout-driven promotion.
- [Product Readiness](docs/product-readiness.md): honest capability matrix, safety gates, and remaining work before a stable release.
- [CLI Contract](docs/cli-contract.md): versioned JSON envelopes, streams, and exit codes for automation.
- [Filesystem Safety Contract](docs/filesystem-safety.md): current threat model, capability truth, and handle-relative backend migration gates.
- [Performance Baselines](docs/performance.md): bounded read-only benchmark method, reference results, and scaling work.

## Development

```bash
python -m pytest
```

When running without installing the package:

```bash
$env:PYTHONPATH='src'
python -m archmarshal lint examples/simple-project --pretty
```

## License

MIT
