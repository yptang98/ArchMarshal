# Prior-art review (2026-11-10)

These are design references, not installed dependencies. No third-party Skill
instructions were activated and no implementation code was copied.

- [Serena memories](https://github.com/oraios/serena/blob/main/docs/02-usage/045_memories.md):
  project-local readable memory, explicit references and progressive disclosure.
  Applied here as a bounded project index, omitted-record references and exact
  `context-read` retrieval. ArchMarshal keeps immutable evidence rather than
  silently editing a single shared summary.
- [forest/agent-skills session-handoff](https://github.com/forest/agent-skills/blob/main/plugins/session-handoff/skills/session-handoff/SKILL.md):
  preserve observed facts, decision rationale, active constraints, rejected options
  and unresolved work. Applied as structured handoff fields and plugin guidance;
  domain-specific sections are not copied into the generic project contract.
- [TreatTrick/skill-sync](https://github.com/TreatTrick/skill-sync):
  local review, a user-owned GitHub vault, content-addressed packages and explicit
  conflicts. ArchMarshal uses immutable version objects with device lineage and
  quarantined import, retaining multiple variants rather than choosing a winner.
- [GitHub repository contents API](https://docs.github.com/en/rest/repos/contents)
  and [Git trees API](https://docs.github.com/en/rest/git/trees): primary protocol
  references for bounded tree discovery, pinned blob reads and create-only uploads.

The combination specifically addresses two independent needs: transferring reusable
capabilities between devices, and restoring the decisions of parallel project work.
Project context is never bundled into a Skill upload implicitly.

## Stability review follow-up

- [Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx)
  and its [reference validator](https://github.com/agentskills/agentskills/blob/main/skills-ref/src/skills_ref/validator.py)
  separate the package entrypoint and optional resources. This review retained
  ArchMarshal's existing compatibility contract and consolidated package validation;
  adopting a reference implementation would not replace filesystem or trust checks.
- [KitsuneKode/session-handoff](https://github.com/KitsuneKode/agent-skills/blob/main/skills/session-handoff/SKILL.md)
  emphasizes key-file references, ordered next actions, task alignment and checking
  stale state before continuation. ArchMarshal already records these fields; the
  audit added shared evidence verification and a cumulative read budget.
- Revisited TreatTrick/skill-sync's explicit conflict/recovery model and forest's
  fact-based handoffs. Kept immutable versions and commit-last receipts; strengthened
  quarantine boundary checks, failure reporting and capacity preflight. No external
  hooks were installed, and no third-party code or Skill instructions were executed.

These are workflow references, not evidence that either implementation is secure.
See [the audit findings](stability-audit-2026-11-10.md) for locally reproduced issues.
