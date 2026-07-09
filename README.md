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
  <img alt="Status" src="https://img.shields.io/badge/status-governance%20prototype-orange">
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
- Promotes only distilled reusable knowledge into **context modules**.
- Detects skill conflicts, missing manifests, unsafe read policies, unregistered generated skills, and workspace mappings that could bloat context.
- Provides read-only `inventory`, `lint`, `audit`, `plan`, `resolve`, and `closeout` commands before any apply-style automation exists.

## Design Goals

- Keep global agent policy tiny, explicit, and highest priority.
- Separate functional skills from common project skills and project-specific skills.
- Require reusable skills to declare tags, triggers, negative triggers, dependencies, and outputs.
- Treat project files as artifacts first, not automatically loaded context.
- Promote only distilled, reusable knowledge into context modules.
- Generate reports and plans before any workspace-changing operation.
- Prefer archive over delete.

## Quick Start

Open Codex in the workspace you want to inspect and paste:

```text
Install ArchMarshal from https://github.com/yptang98/ArchMarshal and run a read-only governance check on this workspace.

Please:
1. Install it with:
   python -m pip install "git+https://github.com/yptang98/ArchMarshal.git"
2. Confirm the CLI works:
   archmarshal --help
3. Run:
   archmarshal inventory . --pretty
   archmarshal lint . --pretty
   archmarshal audit . --pretty
   archmarshal plan . --pretty
4. Do not modify my existing project files. Only report diagnostics, risks, and suggested next steps.
```

For a quick smoke test inside this repository:

```bash
archmarshal inventory examples/simple-project --pretty
archmarshal lint examples/simple-project --pretty
archmarshal audit examples/simple-project --pretty
archmarshal resolve examples/monorepo-project --task "prepare release checklist" --pretty
archmarshal closeout examples/monorepo-project --used-skill skill.common-project.release-checklist --pretty
```

## Current Boundaries

- No GUI.
- No marketplace.
- No automatic third-party skill installation.
- No automatic project directory rewrite.
- No deletion.
- No automatic global configuration mutation.
- No dynamic context loading runtime.

## Repository Layout

```text
ArchMarshal/
├─ README.md
├─ docs/
│  ├─ architecture.md
│  ├─ skill-taxonomy.md
│  ├─ project-file-lifecycle.md
│  ├─ product-requirements.md
│  └─ lint-rules.md
├─ schemas/
│  ├─ workspace.schema.yaml
│  ├─ skill-manifest.schema.yaml
│  └─ artifact-registry.schema.yaml
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

## CLI

Install locally for development:

```bash
pip install -e .
```

Run read-only commands:

```bash
archmarshal inventory examples/simple-project --pretty
archmarshal lint examples/simple-project --pretty
archmarshal audit examples/simple-project --pretty
archmarshal plan examples/simple-project --pretty
archmarshal resolve examples/monorepo-project --task "prepare release checklist" --pretty
archmarshal closeout examples/monorepo-project --used-skill skill.common-project.release-checklist --pretty
```

The compatibility wrapper still works:

```bash
python scripts/inventory.py examples/simple-project --pretty
```

`apply` is intentionally not implemented. It should never be introduced before plan output, diff preview, and non-destructive defaults are stable.

## Safety Rules

- Inventory, lint, audit, and plan are read-only by default.
- Resolve is advisory and loads only matched skill/context metadata.
- Closeout summarizes used skills and cleanup actions after project work.
- Global, functional, common-project, project, and generated skill roots are separately mapped.
- Historical artifact directories are explicit-read only:
  - `.agent/reports/`
  - `.agent/history/`
  - `.agent/archive/`
  - `.agent/cache/`
- New non-source artifacts should first enter `.agent/inbox/`.
- `AGENTS.md` is an entry router, not a history dump.
- `.agent/registry.yaml` is the machine ledger.
- `.agent/INDEX.md` is the human map.

## What Ships Today

- [x] README
- [x] Architecture documentation
- [x] Skill taxonomy documentation
- [x] Project file lifecycle documentation
- [x] Workspace schema
- [x] Skill manifest schema
- [x] Artifact registry schema
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
- [x] Governance lint rules
- [x] Read-only remediation plan output
- [x] Task-based skill/context resolver
- [x] Project closeout skill/memory summary
- [x] Tests for clean examples, missing entry files, skill conflicts, and historical read policy

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
