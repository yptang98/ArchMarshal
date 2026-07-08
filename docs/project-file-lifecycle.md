# Project File Lifecycle

Project files are not automatically dynamic context. Most files are historical artifacts until they are distilled, registered, and promoted.

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
