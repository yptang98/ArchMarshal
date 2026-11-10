# Native Skill state and review evidence

`skill-host-status` compares a Codex `skills/list` observation with the immutable
ArchMarshal index in the actual checkout. It does not implement another Skill
loader, alter enablement, install packages, or treat approval as activation.
Four facts remain separate: native discovery/enabled state, metadata approval,
the recorded package version, and explicitly verified current file bytes.

## Observe the host

Prefer a host-provided `skills/list` result for the current working directory.
An observation envelope records `format: archmarshal-codex-skills-observation-v1`,
`host_id`, timezone-aware `observed_at`, `invalidated`, an `origin`, and the native
`response` containing `data[].cwd`, `skills` and `errors`. Record known native
server identity under `server`; do not include credentials or conversation data.

```text
archmarshal skill-host-status PROJECT --work-root WORKTREE --host-snapshot SNAPSHOT.json
```

The observation must identify the exact actual cwd and selected host. Match Skill
paths lexically; never join by name or follow a symlink to invent equivalence.
Same-name packages stay separate. Duplicate observations for one path are
ambiguous. Missing paths, discovery errors or absent snapshots remain unknown,
not disabled. The default freshness window is five minutes. Old, future-dated or
`skills/changed`-invalidated observations require refresh before alignment is
reported. Snapshot origin/time are supplied evidence, not authenticated identity.

When direct host tooling is unavailable, an explicit native Codex executable can
provide one bounded observation:

```text
archmarshal skill-host-probe WORKTREE --codex-binary ABSOLUTE_CODEX_EXECUTABLE
```

The probe opens a short-lived stdio process, sends only `initialize`, `initialized`
and `skills/list` with the selected cwd and `forceReload: true`, then closes it.
It starts no chat/turn and sends no configuration, plugin or permission changes.
Shell wrappers are rejected; on Windows select the native executable exposed by
the installed Codex package. The process has a 30-second default response deadline
(configurable 1–60), 100-message and 8 MiB total response limits, and bounded
shutdown. Unknown host action requests fail closed. No persistent listener is
created. Existing native index exclusions block this fresh probe because Codex's
reload may inspect excluded Skills; use already available host observations then.

This observes the selected CLI app-server process, not necessarily an existing
desktop chat's in-memory Skill cache. Even a fresh successful result explicitly
reports `live_runtime_verified: false` and `runtime_active: null`. It does not
prove that a Skill was invoked or that an already open chat refreshed its state.

The protocol was checked against locally generated Codex 0.146.0 JSON schemas
and the [official app-server documentation](https://learn.chatgpt.com/docs/app-server).
A real local probe discovered a temporary repository Skill and the installed,
enabled ArchMarshal 0.18.0 package without updating either installation.

## Verify an exact version

By default reconciliation reads index metadata and supplied observations only.
Repeat `--verify-source` for exact indexed, non-excluded project-relative packages
whose current bytes should be checked. Nested package boundaries, exclusions and
preserved runtime/dependency files use the same fingerprint rules as review.
The index is rechecked after inspection to detect concurrent review publication.

When an authorized installation copies a reviewed package to another location,
provide its exact receipt/destination explicitly. Do not infer the copy from its
display name or a matching version string:

```text
archmarshal skill-host-status PROJECT --host-snapshot SNAPSHOT.json
  --installed-skill SOURCE=ABSOLUTE_INSTALLED_SKILL_MD --verify-source SOURCE
```

The installed target must be a unique absolute `SKILL.md` path observed by the
host. Explicit byte verification then checks both the source and that installed
package against the reviewed package hash. An evolving authoring draft and an
unchanged approved installed version are reported independently. Unobserved or
ambiguous installed targets are not read. This inspection grants no installation
or activation permission. Linked installed packages can be observed natively but
are not followed for ArchMarshal byte verification.

An installation mapping cannot override persistent exclusions: a target inside
an excluded package is rejected before reading package bytes. When an installation
contains excluded or separately indexed project packages, their actual directory
boundaries remain outside its fingerprint, alongside boundaries mapped from the source.

Limits: 4 MiB per observation, 1,000 native Skills, 20 explicit package selections,
64 MiB/10,000 files shared across all selected source and installation checks, and
100 returned rows by default (1–1,000 configurable). Read-budget exhaustion is
explicit. Selected sources are prioritized in the output; omitted rows are counted.
Worktrees use their own existing index and never inherit approval from another
checkout merely because their Git common directory matches.
