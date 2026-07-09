# Project File Lifecycle

Project files are not automatically dynamic context. Most files are historical artifacts until they are distilled, registered, and promoted.

## Original Preservation Rule

Do not delete raw project history just because a summary exists.

Summaries, context modules, memory records, and closeout notes are navigation
layers. They should preserve references to the original reports, plans,
checkpoints, source files, decisions, and historical artifacts. The original
material remains explicit-only or archived so later agents can reproduce exact
details when needed.

## Lifecycle

```text
inbox
  -> raw artifact
  -> distilled knowledge
  -> context module
  -> project skill or common project skill
  -> archive
```

## File Status

```yaml
status:
  - inbox
  - raw
  - active
  - distilled
  - promoted
  - stale
  - archived
  - deleted
```

`deleted` is a registry state for external reality, not a default ArchMarshal action.

## File Kinds

```yaml
kind:
  - source_code
  - config
  - project_doc
  - knowledge
  - context_module
  - skill
  - generated_skill
  - report
  - plan
  - history
  - artifact
  - cache
```

## Read Policies

```yaml
read_policy:
  - default
  - task_based
  - on_planning
  - on_architecture_tasks
  - on_database_tasks
  - on_release_tasks
  - when_task_matches
  - explicit_only
  - never_default
```

Historical reports, history files, archives, and caches should usually be `explicit_only` or `never_default`.

## Update Policies

```yaml
update_policy:
  - human_only
  - agent_propose_diff
  - agent_can_update
  - append_only
  - generated
  - mutable_until_complete
  - immutable
```

## Promotion Rules

Promote only when content is reusable and stable enough to justify a stronger lifecycle state.

- Raw reports can become distilled knowledge.
- Distilled knowledge can become a context module.
- Repeated procedural knowledge can become a project skill.
- Cross-project procedural knowledge can become a common project skill.
- Promotion preserves the original artifact; it does not overwrite or delete it.

## Recording Depth

Do not write a heavyweight closeout summary for every project.

When a project mostly reuses existing registered skills, record only important
changes, decisions, risks, files touched, and next steps. Suggest deeper memory,
context, or skill promotion only when the work produced new reusable knowledge,
a new workflow, unregistered artifacts, or governance risk.

## Context Checkpoints

Use `archmarshal checkpoint` after context compression or summarization. A
checkpoint stores the compact state as a candidate history artifact and memory
record suggestion. It is append-only, explicit-only, and read-only by default.

## Save Paths

Project file save paths belong in `.agent/workspace.yaml` under
`save_paths.project_files`. At minimum, new projects should declare paths for:

- `checkpoints`
- `reports`
- `plans`
- `history`
- `knowledge`

Skills can use the default governed skill roots, but project files should use
explicit user-approved destinations.

## Naming

Project files should use a time-first name plus a short content hint:

```text
YYYYMMDD-HHMMSS-topic-kind.md
```

Examples:

```text
20260709-071035-release-checklist-checkpoint.md
20260709-093012-api-migration-report.md
```

The time prefix keeps history sortable. The topic slug keeps files findable
without reading every artifact.

## AGENTS.md Role

`AGENTS.md` should be short. It should route the agent to the right index, registry, context modules, and project rules.

It should not contain:

- Long history.
- Temporary plans.
- Complete audit reports.
- Large generated notes.
- Full project documentation.

## Registry Role

`.agent/registry.yaml` is the machine ledger. Every important project artifact should have:

- Stable id.
- Kind.
- Path.
- Status.
- Read policy.
- Update policy.
- Source-of-truth flag.
- Owner.
- Tags.
- Optional derivation links.

## INDEX.md Role

`.agent/INDEX.md` is the human map. It should explain where to find active knowledge, context modules, reports, plans, history, and project skills.
