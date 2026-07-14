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

- Keeps **global skills** tiny and highest-priority.
- Lets **functional skills** grow richly with tags, triggers, and negative triggers.
- Makes **common project skills** reproducible by keeping scripts, templates, references, and dependency files inside the skill path.
- Manages **project files** through workspace path mappings, registries, read policies, and lifecycle states.
- Governs **memory stores and memory records** with ownership, privacy, evidence, review status, retrieval keys, and forget/supersession policy.
- Promotes only distilled reusable knowledge into **context modules**.
- Detects skill conflicts, missing manifests, unsafe read policies, unregistered generated skills, unregistered memory locations, and workspace mappings that could bloat context.
- Adopts existing projects through **metadata overlays**: existing `SKILL.md` and project files stay untouched.
- Fingerprints the **complete skill package**, so script/reference/asset drift is visible without rewriting the source.
- Records **quick**, **standard**, or **reproducible** closeouts in append-only, date-organized directories.
- Catalogs multiple projects by recorded creation date and AND-filtered tags.
- Extracts review-only common-skill and user-preference candidates from compact session manifests.
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

ArchMarshal is a Python CLI that Codex or a human can invoke inside a project.

### 1. Install

```bash
python -m pip install "git+https://github.com/yptang98/ArchMarshal.git"
archmarshal --help
```

This repository is not currently a one-click Codex Skill package; the command
above installs the actual CLI from GitHub.

### 2. Start

Preview adoption first:

```bash
archmarshal adopt . --tag research --tag python --pretty
```

Then explicitly create the management overlay:

```bash
archmarshal-start . --apply --tag research --tag python --pretty
```

ArchMarshal creates a content-verified backup before the first managed file, keeps every
existing skill in place, and stores generated routing metadata under
`.agent/skill-overlays/`. If a reserved control file already belongs to another
tool, adoption blocks instead of overwriting it.

For an already managed project, start also previews newly added skills and
complete-package drift:

```text
archmarshal-start
```

Use `--apply` only to create missing overlay metadata. Changed existing overlays
are reported for review and are never replaced implicitly.

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
archmarshal-end . --level quick --summary "Finished the release review" --apply

# 2. Careful record of steps and key scripts
archmarshal-end . --level standard --summary "Validated the release" \
  --step "Run tests" --step "Review artifacts" --script scripts/validate.py --apply

# 3. Reproduction-evidence capsule with hashed scripts and a reference run script
archmarshal-end . --level reproducible --summary "Reproduced benchmark A" \
  --step "Prepare data" --step "Run evaluation" --script scripts/eval.py \
  --command "python scripts/eval.py --config configs/a.yaml" --shell bash --apply
```

All three modes preview by default and write only to a new
`.agent/history/YYYY/MM/DD/...` directory with `--apply`. Reproducible mode
reports `reproduction_evidence_ready: false` until summary, ordered steps, key
scripts, and exact commands are present. Readiness means required evidence is
present; ArchMarshal does not execute the commands or prove the result.

After multiple sessions, extract lightweight, review-only candidates:

```bash
archmarshal learn . --include-root ../another-project --apply --pretty
```

Browse projects without loading their raw history:

```bash
archmarshal catalog . --include-root ../another-project --tag research --pretty
```

That is the main workflow: install, `archmarshal-start`, normal project
instructions, `archmarshal-end`.
See [Getting Started](docs/getting-started.md) for the minimal prompts.

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
- No deletion.
- No in-place edits to adopted skills; source skills are referenced through overlays.
- No overwrite or force mode for adoption and closeout.
- No summarization that deletes or replaces original project history.
- No automatic global configuration mutation.
- No dynamic context loading runtime.

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
archmarshal resolve examples/monorepo-project --task "prepare release checklist" --pretty
archmarshal closeout examples/monorepo-project --used-skill skill.common-project.release-checklist --pretty
archmarshal-end examples/monorepo-project --used-skill skill.common-project.release-checklist --pretty
```

Preview and explicitly apply safe lifecycle writes:

```bash
archmarshal adopt path/to/existing-project --tag research --pretty
archmarshal adopt path/to/existing-project --tag research --apply --pretty
archmarshal end path/to/project --level quick --summary "Phase complete" --apply --pretty
archmarshal learn path/to/project --apply --pretty
archmarshal backup-verify path/to/backup.zip --pretty
archmarshal backup-restore path/to/backup.zip path/to/new-directory --apply --pretty
```

The compatibility wrapper still works:

```bash
python scripts/inventory.py examples/simple-project --pretty
```

`--apply` is deliberately narrow: adoption creates only missing control-plane
files after a verified backup; closeout and learning create only new append-only
artifacts. There is no overwrite, move, delete, force, or automatic promotion
path.

## Safety Rules

- Inventory, lint, audit, and plan are read-only by default.
- Adoption, closeout recording, and learning are preview-only unless `--apply` is explicit.
- Adoption backs up managed metadata and skill entry documents before writing; `--backup-scope full` creates a bounded project-content snapshot excluding VCS, dependencies, virtual environments, and prior backups.
- Existing skill sources are immutable to ArchMarshal; overlay manifests live under `.agent/skill-overlays/`.
- Reserved control-file conflicts block the whole adoption before managed files are written.
- Closeout uses unique append-only directories and verifies copied script hashes.
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
- [x] Non-mutating skill metadata overlays
- [x] Complete skill-package fingerprints and drift reporting
- [x] Content-verified backup inspection and restore-to-new-directory flow
- [x] Conflict blocking and exclusive file creation (no overwrite mode)
- [x] Quick, standard, and reproducible append-only closeout records
- [x] Hashed key-script snapshots and explicit reproducibility readiness gaps
- [x] Date- and tag-aware workspace/session organization
- [x] Read-only cross-project catalog sorted by date and filtered by tags
- [x] Review-only common-skill and user-preference learning candidates

## Research Notes

- [Agent Memory and Skill Organization Landscape](docs/agent-memory-landscape-2026.md): design direction for memory stores, memory records, retrieval budgets, and closeout-driven promotion.
- [Product Readiness](docs/product-readiness.md): honest capability matrix, safety gates, and remaining work before a stable release.

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
