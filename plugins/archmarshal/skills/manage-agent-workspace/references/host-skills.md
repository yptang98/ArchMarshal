# Native Skill state

Use this workflow when the user asks whether a Skill is available, enabled,
reviewed or running the expected installed version. Do not equate these states.

1. Resolve the current project and actual worktree. Prefer an available native
   `skills/list` observation for that cwd. Preserve its host identity, timestamp,
   discovery errors and `skills/changed` invalidation; never match by name alone.
2. With 0.19, run `skill-host-status` using the saved observation envelope described
   by the engine. Without an observation, report native state as unavailable.
   Do not infer enablement from ArchMarshal approval or from an installed folder.
3. When a fresh CLI observation is needed and authorized read scope permits it,
   use `skill-host-probe` with the explicit native Codex binary. It runs a bounded
   read-only stdio query, then exits. Do not use shell wrappers, change config,
   invoke a model turn or enable experimental plugin APIs to obtain the list.
   Known excluded Skills block reload; prefer existing host evidence then.
4. Read package bytes only for explicitly selected, non-excluded indexed sources
   through repeatable `--verify-source`. After an authorized installation, use
   its exact destination as `--installed-skill SOURCE=ABSOLUTE_SKILL_MD`; do not
   infer an installed copy from its name. The target must also be observed at that
   exact path. Report source draft drift separately from installed package hashes.
5. Report observation freshness, approval/block reason and byte verification as
   separate facts. A CLI app-server observation does not prove an existing desktop
   chat refreshed its cache or invoked the Skill. Keep runtime activity unknown
   unless the host separately supplies that evidence.

This workflow never enables, installs, promotes or rewrites a Skill. Any such
user-requested change follows its existing exact-plan workflow, followed by a
fresh observation and selected version verification. Saved observations and
reports belong in the authorized external record/artifact directory.
