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

A skill node is a reusable capability with explicit metadata. The model supports
enabled, disabled, archived, upgraded, tagged, and audited states. The current
CLI discovers, validates, quarantines, reviews, tracks, resolves, inspects
history, and rolls metadata forward. Adopted packages require an approval bound
to the exact package and routing digest; a changed subject returns to review.

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
overlay's tags and triggers. Supported fields from an existing source
`manifest.yaml` are imported into the overlay without editing the source. An
invalid recognized value disables the generated metadata and marks it for
review instead of guessing an active configuration.

Adoption is a durable create-only transaction. Its reviewed plan digest binds
the exact control-file bytes, source preconditions, backup scope, and proposed
skill-index generation. After revalidation and backup verification, staged
payloads plus a journal are published under `.agent/transactions/adoption/`
before any visible target. An OS-lifetime lock serializes adoption, each target
is exclusively created or verified as an exact match, the skill generation uses
its own expected-HEAD protocol, and a transaction receipt is created last.
Recovery only completes missing targets; a changed/replaced target, journal,
backup, lock path, or index relationship stops without overwrite or deletion.
Recovery apply is a second compare-and-swap: the active transaction id and plan
digest must still equal the reviewed recovery preview while the lock is held.

`archmarshal init` is an explicit specialization of this same transaction for
new-project structure. It adds only missing `.agents/skills/README.md`,
`.agents/skills/project/.gitkeep`, and
`.agents/skills/generated/.gitkeep` targets. It does not reinterpret or rewrite
an existing Skill directory. Ordinary `adopt` remains control-plane-only so an
existing project is not expanded merely because ArchMarshal inspected it.

`ownership.json` is root-bound and declares whether the immutable Skill index is
required. That declaration must agree with the workspace management mode and
Skill roots. When required, a missing `HEAD` is an integrity failure: inventory
quarantines all loose Skill metadata and adoption refuses to create a new root
history implicitly. Repair transactions verify the complete current index even
when their Skill plan is otherwise unchanged.

After adoption, incremental sync is a small versioned module registry:

```text
.agent/skill-overlays/.archmarshal/
├─ HEAD
├─ HEAD.lock                     # persistent file; OS lock held only during publication
├─ recovery/*.json               # append-only verified crash-recovery decisions
└─ objects/sha256/<digest>.json  # immutable complete generations
```

Each generation names its parent and records active/removed skill manifests plus
added, modified, removed, and restored changes. Publication exclusively creates
the object, rechecks the expected `HEAD`, and atomically replaces only the
ArchMarshal-owned pointer. A stale plan, lock conflict, digest mismatch, unsafe
path, linked scan root, or size/count limit is a hard failure. Older and orphaned
objects are never selected unless `HEAD` names them.

Publication also re-fingerprints active sources while holding the OS process
lock. A directory permission failure is an unknown/error state, never evidence
that a skill was removed. If a writer exits unexpectedly, released v2 lock
metadata is recoverable only when current `HEAD` still equals its expected value,
or equals a fully verified proposed object. The decision is recorded before the
next transaction. Legacy or malformed lock files are never auto-cleared.

Rollback is forward-only: a new generation has the current `HEAD` as parent and
reproduces an ancestor routing snapshot while preserving tombstones. This keeps
history linear. Active target packages must match the source bytes already in
the workspace; ArchMarshal never restores implementation files.

This splits the system into two ownership domains:

```text
Human-owned source              ArchMarshal-owned control plane
project files                   workspace/index/registry
SKILL.md                        immutable skill generations + HEAD
source skill manifest           session and learning records
```

There is no automatic merge between the domains. A reserved-file collision is a
hard stop.

A third, optional domain holds explicitly promoted user-level reuse:

```text
Evidence/source domains             ArchMarshal-owned user store
committed project sessions          immutable candidate decisions
committed learning pack             immutable common-Skill packages
human-reviewed Skill draft          bounded preference records
                                    immutable generations + HEAD
```

The store never owns the three sources on the left. A complete saved promotion
plan binds the candidate digest, provenance, draft hashes or preference value,
store root, and expected HEAD. Publication copies a validated package and then
advances only the store's internal pointer. Resolver treats verified store
packages as task-triggered common-project Skills; it does not execute them or
inject them into global policy.

New immutable Skill copies use package format v2. Their content address covers
file bytes, file permission/executable state, every subdirectory mode, and
empty-subdirectory topology. The package root remains a store-owned boundary.
The portable namespace rejects Windows-reserved or invalid components, non-NFC
spelling, and Unicode/case-fold collisions before copy.
Files and commit markers are verified through stable descriptors; topology is
checked before and after content verification; `COMMITTED.json` is created
last. The v1 verifier remains separate so an old committed package is read
without applying v2-only rules or rewriting history.

## Built-in Module Loading Boundary

The CLI parser, version, and error envelope form a lightweight bootstrap.
After parsing, the selected command imports only its built-in domain module
(adoption, backup, user store, lifecycle, and so on). This is software-module
lazy loading, not dynamic execution of a Skill. Project and user Skills remain
validated data packages; ArchMarshal's resolver is advisory and never imports
their Python, shell, or other executable content.

Readers also treat skill-index publication as a critical section. They block
while the OS lock is held, verify the complete parent chain reachable from the
captured `HEAD`, and reject a head/lock race. Once `HEAD` exists, loose overlay
manifests not represented by that generation are quarantined rather than
activated. Portable case/Unicode path aliases are rejected so the same source
cannot acquire two identities on different filesystems.

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

Date-organized closeout sessions use a commit-last protocol. Session files and
selected script snapshots are exclusively created first; `COMMITTED.json` then
binds every relative path, byte count, and SHA-256 hash. Interrupted directories
are preserved for inspection but are not learning evidence. A later edit that
breaks a declared hash likewise excludes the session from learning.
This is an integrity check, not an authenticity signature: an actor able to
rewrite both files and marker remains outside the alpha threat model. Legacy v1
sessions are counted as unverified and are not silently accepted as v2 evidence.

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
8. `adopt --expect-plan ... --apply`: after an exact reviewed preview and
   verified backup, create only missing management-overlay files through a
   recoverable transaction.
9. `skill-review --plan-file ... --expect-head ... --expect-plan ... --apply`:
   publish only the exact saved immutable review generation after revalidating
   the package, routing subject, HEAD, and full plan before and after backup.
10. `end --level ... --expect-plan ... --apply`: append and commit a new quick,
   standard, or reproducible session record.
11. `learn --plan-file ... --expect-plan ... --apply`: append a bounded,
    review-only learning candidate pack only when roots, evidence, bytes, and
    target still match the complete saved preview.
12. `candidate-review`: append an accept/reject/defer decision to the isolated
    user store from a verified committed candidate.
13. `candidate-promote`: after an exact accepted decision, copy a common-Skill
    draft with explicit candidate/source lineage, or the exact preference
    candidate, into a new immutable user-store generation. Replacing an active
    id/key requires explicit type-specific confirmation.
14. `user-store-rollback`: publish a new generation from a verified ancestor
    snapshot without deleting newer history.

Mutation is capability-specific rather than a general `apply` engine. No command
can overwrite, move, rename, delete, or force-update existing project and skill
files. Promotion to user common Skills or preferences is explicit, previewed,
versioned, bounded, and reversible.
