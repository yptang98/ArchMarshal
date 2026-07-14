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
- `.agent/memory-stores.yaml` as the governed memory-store ledger.
- `.agent/memory-records.yaml` as the evidence-backed memory-record ledger.
- `.agents/global/` for lightweight global policy skills.
- `.agents/skills/functional/` for reusable functional skills.
- `.agents/skills/common-project/` for reproducible engineering workflow skills.
- `.agents/skills/generated/` for generated skills that must be registered before use.

The path mapping allows projects to keep their own structure while still being inspectable by tooling.

### Adoption Overlay

Existing projects use an overlay rather than a rewrite. ArchMarshal discovers
skill sources in common project-local roots and writes generated metadata under
`.agent/skill-overlays/<kind>/`. Each overlay points at the original skill
directory with `managed: false` and `mutation_policy: never`. Inventory resolves
local scripts and references against the source directory while routing uses the
overlay's tags and triggers.

After adoption, incremental sync is a small versioned module registry:

```text
.agent/skill-overlays/.archmarshal/
├─ HEAD
├─ HEAD.lock                     # exists only during publication
└─ objects/sha256/<digest>.json  # immutable complete generations
```

Each generation names its parent and records active/removed skill manifests plus
added, modified, removed, and restored changes. Publication exclusively creates
the object, rechecks the expected `HEAD`, and atomically replaces only the
ArchMarshal-owned pointer. A stale plan, lock conflict, digest mismatch, unsafe
path, linked scan root, or size/count limit is a hard failure. Older and orphaned
objects are never selected unless `HEAD` names them.

This splits the system into two ownership domains:

```text
Human-owned source              ArchMarshal-owned control plane
project files                   workspace/index/registry
SKILL.md                        immutable skill generations + HEAD
source skill manifest           session and learning records
```

There is no automatic merge between the domains. A reserved-file collision is a
hard stop.

Workspace, registry, and skill manifest YAML files are parsed fail-soft. Invalid
YAML becomes a structured lint diagnostic, and valid YAML is checked against the
packaged JSON Schemas before downstream rules run.

`save_paths` records where new material should be preserved. Skill save paths
can follow ArchMarshal defaults because skills live under governed skill roots.
Project files are different: checkpoints, reports, plans, history, knowledge,
and inbox artifacts should use user-approved destinations so reproduction
materials do not drift into hidden implicit locations.

Project file naming is also governed. The default strategy is
`time_topic_kind`: UTC timestamp first, then a short task/content slug, then the
artifact kind. This makes raw history sortable by time and recognizable by
project content.

## Historical Artifact Layer

Historical artifacts preserve process, evidence, reports, temporary analysis, and old plans. They are not loaded by default.

Summaries never replace original history. A distilled knowledge file, context
module, memory record, or checkpoint is an index into preserved evidence. Raw
reports, plans, checkpoints, and history should remain retrievable through
explicit-only paths so later agents can reproduce detail without reloading every
artifact by default.

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

## Memory Stores And Records

Memory stores are governed pointers to places where durable or candidate memories live. They may be local files, markdown folders, SQLite databases, MCP stores, vector stores, or managed services. ArchMarshal records store ownership, privacy, read/write policy, exportability, versioning, and forget/supersession behavior.

Memory records are small metadata entries that point to content and evidence. They track namespace, retrieval keys, confidence, review status, supersession, and read policy. This keeps memory promotion explicit: generated notes stay candidates until reviewed, and reviewed memory can be resolved by task without loading every historical artifact.

## Operation Model

ArchMarshal operations should mature in this order:

1. `inventory`: read and summarize.
2. `lint`: detect structural problems.
3. `audit`: explain risks and evidence.
4. `plan`: propose non-destructive changes.
5. `resolve`: advise which skills and context modules fit a task.
6. `checkpoint`: preserve compact state after context compression as a read-only candidate record.
7. `closeout`: summarize used skills, preservation needs, and reproduction evidence after project work.
8. `adopt --apply`: after preview and verified backup, create only missing management-overlay files.
9. `end --level ... --apply`: append a new quick, standard, or reproducible session record.
10. `learn --apply`: append a bounded, review-only learning candidate pack.

Mutation is capability-specific rather than a general `apply` engine. No command
can overwrite, move, rename, delete, or force-update existing project and skill
files. Promotion to shared skills or user preferences remains a human-reviewed
operation.
