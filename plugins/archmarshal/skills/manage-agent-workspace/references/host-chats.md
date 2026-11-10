# Native chats and continuity

Use Codex's native chat history and available chat tools for resume, fork and
handoff. ArchMarshal's external summaries supplement that history with decisions,
validation and artifact paths; they are not a replacement conversation database.

- Use the current chat/project identity already provided by the host. When the
  user identifies another chat, resolve that exact target with available native
  tools and their current schemas. Read only the relevant status/context. Do not
  search unrelated projects or assume matching names identify the same chat.
- Resume or hand off only as requested. Create or fork a user-visible chat only
  when the user explicitly asks for a new/forked chat; an ordinary task or external
  `start` record does not authorize creating chats. Use the host's returned IDs,
  actual cwd and worktree association instead of inventing window identities.
- Preserve explicit decisions, constraints, unfinished steps and validation in
  concise external records. Link actual delivered files under the external
  artifact directory. Do not copy the full transcript or source tree.
- When the host changes cwd or creates a worktree, record the new actual work
  root alongside the known project root. Do not copy `.agent` ownership or review
  state into that checkout or assume it inherited another checkout's approval.
- If native resume/fork/handoff tooling is unavailable, report that limit and
  supply the known chat ID and external handoff summary. Do not parse private
  app databases, invent an undocumented hook or claim the chat was transferred.

Explicit portable ArchMarshal context export remains available through the
native project-control workflow in `portability.md` when requested. Lifecycle
hooks are optional future adapters; do not install background hooks by default.
