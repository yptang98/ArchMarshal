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
