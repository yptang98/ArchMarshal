# Agent Memory and Skill Organization Landscape

Date: 2026-07-09

Status: research note and roadmap input, not a complete description of current ArchMarshal behavior.

Purpose: summarize current long-running-agent memory patterns, multi-project memory organization, and skill packaging practices that ArchMarshal can borrow.

Source note: external links were collected as directional references on 2026-07-09. Re-check them before relying on exact vendor behavior or product names.

## Executive Takeaways

The strongest pattern across current agent systems is not "one better memory file." It is a layered memory control plane:

1. **Directive memory**: human-authored rules such as `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, or `.continue/rules`. These should be concise, versioned, and treated as policy-like context.
2. **Session memory**: conversation history, resumable state, compaction summaries, branchable sessions, and recent working context. This helps a run continue, but is not the same as durable knowledge.
3. **Learned memory**: extracted facts, preferences, decisions, pitfalls, and procedures generated from prior work. This needs staging, consolidation, conflict handling, review, forgetting, and source evidence.
4. **Retrieval memory**: indexed stores keyed by user/org/project/task namespaces, retrieved top-k instead of loaded wholesale.
5. **Capability memory**: skills, commands, hooks, MCP tools, and workflow packages. Skills are not merely instructions; they bundle executable scripts, templates, tests, references, metadata, and trigger rules.
6. **Evidence memory**: append-only reports, task logs, acceptance records, leader decisions, and immutable versions. These should be explicit-read by default and promoted only through a closeout/review loop.

ArchMarshal already has much of the right vocabulary: skill kinds, read policies, artifact lifecycle, context modules, generated-skill registration, and closeout. The main gap is that "memory stores" and "memory records" are not first-class enough yet. ArchMarshal can become more useful by governing not only project files and skills, but also the write/read lifecycle of memories created by Codex, Claude Code, Cursor/Windsurf-like tools, MCP memory servers, and multi-agent orchestrators such as CostMarshal.

## Landscape

### Claude Code And Claude Platform

Claude Code separates human-written project memory from learned auto memory. Its docs describe two startup-loaded systems: `CLAUDE.md` files for persistent human instructions and auto memory for notes Claude writes from corrections and preferences. It distinguishes scope: project, user, org for `CLAUDE.md`; per repository, shared across worktrees for auto memory. It also warns that these are context, not enforced configuration, so hard blocking belongs in hooks. Source: https://code.claude.com/docs/en/memory

Claude's managed-agent memory model is a useful ArchMarshal reference because it treats a memory store as a workspace-scoped collection of text documents mounted as a directory in the sandbox. Individual memories are path-addressed, importable/exportable, and every change creates an immutable version. The docs also recommend many small focused files rather than a few large ones. Source: https://platform.claude.com/docs/en/managed-agents/memory

Claude Skills formalize capabilities as filesystem packages. Each skill is a directory with `SKILL.md` as entrypoint plus optional scripts, templates, examples, and validators. Claude Code also carries recently invoked skills across auto-compaction within a token budget. Source: https://code.claude.com/docs/en/skills and https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview

Borrow for ArchMarshal:

- Treat rule files as advisory context and hooks/permissions as enforcement.
- Add immutable memory versioning and point-in-time recovery metadata.
- Prefer small focused memory files with path addresses.
- Model skills as reproducible capability packages with progressive disclosure and token budgets.

### OpenAI Codex And Agents SDK

Codex memories are explicitly a local recall layer, not the source of required team guidance. OpenAI's docs say required team guidance belongs in `AGENTS.md` or checked-in documentation, while memories can carry stable preferences, workflows, tech stacks, conventions, and pitfalls. Codex stores memories under `~/.codex/memories/`, skips active or short-lived sessions, redacts secrets from generated memory fields, and exposes per-thread controls. Source: https://developers.openai.com/codex/memories

The OpenAI Agents SDK separates session memory from longer-lived agent memory. Sessions store conversation history for a specific session. Advanced SQLite sessions add branching, usage analytics, and structured queries. Sandbox Agent Memory is separate: it distills lessons from prior runs into files in the sandbox workspace, reducing exploration cost, user correction cost, and context cost for later runs. Sources: https://openai.github.io/openai-agents-python/sessions/, https://openai.github.io/openai-agents-python/sessions/advanced_sqlite_session/, https://openai.github.io/openai-agents-python/sandbox/memory/

The Agents SDK personalization cookbook shows a state-based long-term memory pattern: structured profile plus global notes are injected at the start; session notes are staged during a run; consolidation deduplicates and resolves conflicts into global memory at the end. It also records fields such as `last_updated` and `keywords`. Source: https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization

Borrow for ArchMarshal:

- Keep `AGENTS.md` and source-controlled docs as canonical rules; memories are recall hints.
- Distinguish session state, sandbox/run lessons, and stable project knowledge.
- Add candidate-memory staging plus end-of-run consolidation.
- Track branches and usage metadata for long-running sessions.

### LangGraph

LangGraph's memory model is compact and directly useful for ArchMarshal schema design: long-term memories are JSON documents in a store, organized by a custom namespace and key. Namespaces often include user or org IDs and other labels; cross-namespace search uses content filters. Source: https://docs.langchain.com/oss/python/concepts/memory

Borrow for ArchMarshal:

- Add namespace tuples such as `(org, workspace, project, memory_kind)` or `(user, project, skill_id)`.
- Separate storage key, semantic tags, and retrieval filters.
- Make cross-project retrieval explicit and filterable rather than path-scanning every workspace.

### Windsurf / Cascade

Cascade auto-generates workspace memories and stores them locally. The docs state that autogenerated memories are associated with the workspace, are not available in other workspaces, and are not committed to the repository. For durable team-shared memory, users should write it to Rules or `AGENTS.md`. Source: https://docs.devin.ai/desktop/cascade/memories

Borrow for ArchMarshal:

- Detect local-only auto memories and flag whether they are private, unshared, or should be promoted.
- Treat team-shared memory as checked-in rules/context modules, not hidden local state.
- Add diagnostics when project-critical facts exist only in local memory.

### Cursor, Continue, And Rule-Based Memory Banks

Cursor's official rules docs frame rules as persistent instructions across Project, Team, User Rules, and `AGENTS.md`. Source: https://cursor.com/docs/rules

Continue supports local `.continue/rules` files that are version-controlled and used in Agent, Chat, and Edit modes. Source: https://docs.continue.dev/customize/rules

Community Cursor Memory Bank systems add a more structured file hierarchy and hierarchical rule loading: core project files, active context, progress, process maps, and just-in-time specialized rules. This is not as authoritative as vendor docs, but it captures a common grassroots pattern: agents need a small always-on map plus task-specific memory files. Source: https://github.com/vanzan01/cursor-memory-bank

Borrow for ArchMarshal:

- Support a "memory bank" profile as a known project pattern.
- Lint always-on rules separately from task-specific context files.
- Add token-budget checks for rule banks and context modules.

### Cloudflare Agent Memory

Cloudflare's Agent Memory private beta is important because it describes a production memory service design. It uses memory profiles as isolated stores shared across sessions, agents, and users. It supports ingest, remember, recall, list, and forget. The recommended lifecycle includes bulk ingestion during compaction and direct lightweight tool calls for recall/remember/forget. Cloudflare explicitly argues against making the model design raw storage queries during task execution. Source: https://blog.cloudflare.com/introducing-agent-memory/

Borrow for ArchMarshal:

- Add memory profiles as governable entities.
- Treat compaction as a memory-ingestion hook.
- Provide constrained memory operations instead of raw filesystem spelunking.
- Require exportability and clear ownership for memory stores.

### AgentMemory, MCP Memory, And Local Knowledge Graphs

AgentMemory is a representative cross-agent memory server. It advertises MCP/HTTP integration across Claude Code, Codex CLI, Cursor, Gemini CLI, Windsurf, Aider, and others; installs native skills/hooks for some agents; and uses searchable memory instead of loading all sticky-note files into context. Its README emphasizes hybrid BM25/vector/graph retrieval and sharing one server across agents. Source: https://github.com/rohitg00/agentmemory

Codebase-Memory is a research/implementation example for code-specific memory: Tree-Sitter parses 66 languages into a SQLite knowledge graph, exposed through MCP structural tools. It reports much lower token use for code exploration while preserving most answer quality. Source: https://arxiv.org/html/2603.27277v1

Borrow for ArchMarshal:

- Recognize MCP memory servers as external stores in inventory.
- Distinguish human/project memory from code-structure memory.
- Add a `store_type` dimension: markdown, sqlite, vector, graph, MCP, managed service.
- Govern connectors and tool permissions as part of the skill/memory surface.

## CostMarshal Observations

The local CostMarshal state shows a useful orchestration memory design:

- `.costmarshal/projects/<project-id>/branch-tree.md` keeps a small project decision tree.
- Each task has a bounded `brief.md`, `branch-card.*`, `completion-report.md`, and `status.json`.
- Project reports summarize durable findings instead of relying on raw transcripts.
- `memory/events.jsonl` is append-only and records usage, results, leader self-work, quality, acceptance, failures, and reasons.
- `memory/agent-memory.json` aggregates agent-level performance by task type and acceptance.
- `memory/knowledge-index.json` contains a retrieval policy: read the small index first and attach at most one matching knowledge file unless the leader approves more.

This is very aligned with ArchMarshal's philosophy. CostMarshal's strongest idea is leader-accepted evidence, not raw transcript memory. A memory item becomes trustworthy only after an acceptance/review record exists.

Gaps ArchMarshal could help CostMarshal with:

- Promote accepted reports into reusable context modules or common-project skills.
- Register generated scripts and reports with lifecycle state.
- Detect stale task outputs and failed-worker reports that should not become default context.
- Build a cross-project knowledge index from accepted artifacts only.

## ArchMarshal Fit

ArchMarshal already has:

- Skill taxonomy: global, functional, common project, project, generated, governance.
- Skill metadata: tags, triggers, negative triggers, dependencies, outputs, permissions.
- Artifact lifecycle: inbox, raw, active, distilled, promoted, stale, archived, deleted.
- Read policies: default, task-based, planning, architecture/database/release, explicit-only, never-default.
- Context modules with source files.
- Advisory resolver and closeout summary.

Missing or under-modeled:

- First-class memory store inventory. **Initial support added:** `.agent/memory-stores.yaml`.
- Namespace model for user/org/workspace/project/task/agent memory. **Initial support added:** memory store/record namespaces.
- Memory record schema with evidence, confidence, supersession, review state, and immutable versions. **Initial support added:** `.agent/memory-records.yaml`; immutable versioning remains future work.
- Consolidation hooks: compaction, closeout, project completion, failed-run review.
- Cross-agent memory governance for MCP/HTTP memory servers.
- Conflict handling for memories, not only skills.
- Memory budgets and retrieval policies per store/module/skill.

## Recommended ArchMarshal Additions

### 1. Add `memory_store` As A Governed Object

Suggested fields:

```yaml
id: memory.codex.local
name: Codex local memories
scope: user
store_type: filesystem
path: ~/.codex/memories
namespace:
  - user
  - codex
description: Local generated Codex recall layer
read_policy: task_based
write_policy: generated
owner: local_user
privacy: private
exportable: true
versioning: generated_state
default_token_budget: 2000
```

Add `memory_store` to workspace paths or a separate `.agent/memory-stores.yaml`. Keep external stores as pointers, not copied content.

### 2. Add `memory_record` Metadata For Promoted Knowledge

Suggested fields:

```yaml
id: mem.arch.decision-router-001
store_id: memory.project.context
kind: semantic
scope: project
namespace: [org, project, architecture]
status: candidate
content_path: .agent/knowledge/router-boundaries.md
evidence_refs:
  - report.arch-audit-2026-07-09
confidence: reviewed
review_status: pending_human
supersedes: []
last_verified: 2026-07-09
retrieval_keys:
  - router
  - architecture boundary
  - context loading
read_policy: on_architecture_tasks
ttl_days: 180
```

Do not put large freeform memory blobs directly in the registry. Store content in files or external stores and keep governance metadata in the ledger.

### 3. Extend Skill Manifests With Memory Effects

Suggested addition:

```yaml
memory_effects:
  reads:
    - memory.project.context
  writes:
    - memory.project.candidates
  consolidates:
    - memory.project.candidates
  forbidden:
    - memory.user.private
  max_retrieval_items: 3
  max_injected_tokens: 4000
hooks:
  on_start:
    - resolve_context
  on_closeout:
    - propose_memory_diff
mcp:
  servers:
    - agentmemory
```

This lets ArchMarshal answer: "If I invoke this skill, what memory might it read or mutate?"

### 4. Add Memory Lint Rules

High-value diagnostics:

- `memory.store_unregistered`: detected `.codex/memories`, `.claude`, `.cursor/rules`, `.continue/rules`, `.windsurf/rules`, or MCP memory config not declared.
- `memory.default_blob_too_large`: always-loaded memory/rule file exceeds a budget.
- `memory.no_source_evidence`: promoted memory lacks source report or decision record.
- `memory.local_only_team_fact`: project-critical fact exists only in private local memory.
- `memory.conflicting_records`: two active records assert incompatible guidance.
- `memory.generated_unreviewed`: generated memory is active without review status.
- `memory.no_forget_policy`: store lacks deletion/supersession/export policy.
- `skill.memory_side_effect_undeclared`: skill can write memory but manifest does not say so.

### 5. Upgrade `resolve` To Return A Memory Bundle

Current `resolve` suggests skills and context modules. Add:

```json
{
  "suggested_memory_records": [
    {
      "id": "mem.arch.decision-router-001",
      "score": 7,
      "store_id": "memory.project.context",
      "reason": "architecture tag + on_architecture_tasks policy",
      "inject": false,
      "read_first": true
    }
  ],
  "memory_budget": {
    "max_records": 5,
    "max_tokens": 6000,
    "prefer_reviewed": true
  }
}
```

Default should be "read index first, inject only selected memory," mirroring the CostMarshal knowledge-index policy.

### 6. Upgrade `closeout` Into A Memory Promotion Gate

At closeout, produce candidate diffs rather than mutating memory:

- What did we learn?
- Which source artifacts prove it?
- Is it project-specific, user preference, workflow/procedural, code-structure, or historical?
- Does it supersede older memory?
- Should it become a context module, project skill, common-project skill, or archive-only note?

This is the cleanest place to adapt the "semantic commit" idea: identify non-local conflicts before updating a natural-language memory store. Source: https://arxiv.org/abs/2504.09283

## Suggested Roadmap

1. **Inventory adapters**: detect common rule/memory locations for Codex, Claude Code, Cursor, Continue, Windsurf/Cascade, and local `.agent` memory.
2. **Schema v2**: add `memory_store`, `memory_record`, memory namespaces, confidence, evidence refs, supersession, privacy, and token budget.
3. **Lint pack**: implement the memory diagnostics above without touching files.
4. **Resolve bundle**: make `archmarshal resolve` return skills, context modules, and memory records under a shared budget.
5. **Closeout candidates**: make `archmarshal closeout` emit `candidate_memory_updates` with source evidence and conflict warnings.
6. **CostMarshal bridge**: import accepted CostMarshal reports/events into ArchMarshal registry as evidence-backed memory candidates.
7. **MCP store registry**: inventory MCP memory servers and record their permissions, store types, exportability, and read/write effects.

## Anti-Patterns To Guard Against

- One giant memory file loaded every session.
- Raw transcripts as default context.
- Generated memories treated as source of truth without review.
- Hidden local memory containing team-critical decisions.
- Memory stores with no export/forget/supersede path.
- Skills that silently update durable memory.
- Cross-project recall with no namespace filter.
- Codebase structure memory mixed with user preferences and project decisions.

## Best Borrowable Design For ArchMarshal

The most practical model is:

```text
AGENTS.md / rules
  -> route to indices, not history

.agent/memory-stores.yaml
  -> declares local, repo, MCP, and managed memory stores

.agent/registry.yaml
  -> records promoted memory artifacts and source evidence

.agent/knowledge-index.yaml
  -> small retrieval map with token budgets and namespace filters

.agent/context-modules/
  -> reviewed, source-backed project knowledge

.agent/skills/
  -> reproducible capability packages with declared memory effects

.agent/reports/ and CostMarshal task reports
  -> explicit-only evidence; closeout proposes promotion
```

This preserves ArchMarshal's core principle: agents should become sharper over time, not heavier. The sharper path is not more always-on context; it is governed memory promotion, indexed retrieval, scoped skills, and explicit evidence.
