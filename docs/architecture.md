# Architecture

ArchMarshal is a governance layer for long-lived agent workspaces. It does not try to automate every action. Its first job is to make skills, context, and project artifacts visible, classifiable, reproducible, auditable, and eventually promotable.

## Layers

```text
User Instruction
  > Global Policy
  > Project AGENTS.md
  > Functional Skills and Common Project Skills
  > Project Skills
  > Context Modules
  > Project Knowledge
  > Historical Artifacts
```

## Global Policy Layer

Global policy is the highest-priority governance layer, but it must stay small.

It may contain:

- Skill loading principles.
- Skill conflict handling principles.
- Naming conventions.
- Global safety rules.
- General context loading rules.
- General project file governance rules.

It must not contain:

- Project-specific architecture.
- Project-specific commands.
- Business-domain facts.
- Large procedural workflows.
- Large reference material.

## Dynamic Skill Node Layer

A skill node is a reusable capability with explicit metadata. Skill nodes can be enabled, disabled, archived, upgraded, tagged, and audited.

ArchMarshal recognizes these skill kinds:

- `global_skill`
- `functional_skill`
- `common_project_skill`
- `project_skill`
- `generated_project_skill`
- `governance_skill`

Functional skills and common project skills are peers. Functional skills represent general capabilities. Common project skills represent reproducible engineering workflows.

## Project Workspace Layer

A project workspace contains source code, configuration, documentation, project skills, generated artifacts, and distilled knowledge. ArchMarshal expects each project to provide:

- `AGENTS.md` as the entry router.
- `.agent/workspace.yaml` as path mapping.
- `.agent/INDEX.md` as the human map.
- `.agent/registry.yaml` as the artifact ledger.

The path mapping allows projects to keep their own structure while still being inspectable by tooling.

## Historical Artifact Layer

Historical artifacts preserve process, evidence, reports, temporary analysis, and old plans. They are not loaded by default.

Default explicit-only directories:

- `.agent/reports/`
- `.agent/history/`
- `.agent/archive/`
- `.agent/cache/`

Historical artifacts can be promoted only through a lifecycle:

```text
inbox
  -> raw artifact
  -> distilled knowledge
  -> context module
  -> project skill or common project skill
  -> archive
```

## Context Modules

A context module is distilled, reusable project context. It is not the original report, plan, or history file.

Good context module subjects:

- Architecture boundaries.
- Database conventions.
- API contracts.
- Release rules.
- Frontend conventions.
- Security constraints.

Each module should declare read policies, negative triggers, source files, historical references, and related skills.

## Operation Model

ArchMarshal operations should mature in this order:

1. `inventory`: read and summarize.
2. `lint`: detect structural problems.
3. `audit`: explain risks and evidence.
4. `plan`: propose non-destructive changes.
5. `apply`: execute confirmed, non-destructive changes.

The MVP implements only the read-only inventory prototype.
