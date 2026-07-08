# ArchMarshal

ArchMarshal is a lightweight control plane for agent workspaces. It treats skills as dynamic capability nodes, project skills as reproducible engineering workflows, and project files as lifecycle-managed artifacts.

The first version is intentionally conservative: it defines structure, schemas, templates, examples, and read-only inventory, lint, audit, and plan behavior before any apply-style automation exists.

## Goals

- Keep global agent policy tiny, explicit, and highest priority.
- Separate functional skills from common project skills and project-specific skills.
- Require reusable skills to declare tags, triggers, negative triggers, dependencies, and outputs.
- Treat project files as artifacts first, not automatically loaded context.
- Promote only distilled, reusable knowledge into context modules.
- Generate reports and plans before any workspace-changing operation.
- Prefer archive over delete.

## Non-Goals For MVP

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

## Suggested CLI Shape

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

## MVP Acceptance Checklist

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
