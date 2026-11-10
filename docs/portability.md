# Skill portability and parallel workstreams

ArchMarshal 0.18 adds reviewed Skill transfer between machines and persistent
workstream handoffs. Use natural language through the plugin, for example:

- "Sync this project's release Skill to my private GitHub repository."
- "Import that Skill on this computer and show its source device."
- "Save this window's backend progress, including decisions and next steps."
- "Resume the context for D:/projects/example."
- "Combine the frontend and backend handoffs and identify integration risks."

## Device identity

Each machine has a separately initialized UUID and a user-chosen label. The
default profile is `CODEX_HOME/archmarshal/device.json` (or
`~/.codex/archmarshal/device.json`). A preview does not create it.

```sh
archmarshal device-init --name work-laptop --pretty
archmarshal device-init --name work-laptop --apply --expect-plan DIGEST_FROM_PREVIEW
```

Use `--device-file` to isolate profiles in tests or separate environments. Do not
copy a device profile to another computer. A Skill snapshot includes its original
device, current exporting device and parent snapshot ID. Installed imports retain
the original source even after changes on the receiving machine. Device labels
are self-declared provenance, not hardware authentication or publisher signatures.

## GitHub sync

Use a dedicated repository you control, with an initialized default branch (for
example, create it with a README). Private repositories are recommended. GitHub
is the default backend. Authentication uses `GH_TOKEN`, `GITHUB_TOKEN`, or the
existing Git credential helper for github.com. Tokens remain in memory; portable
objects contain neither credentials nor absolute source paths. GitHub needs
repository Contents read/write permission. Repository names use `owner/repo`.

First adopt the source project and Skill through the existing reviewed workflow.
Then select the exact project-relative Skill package:

```sh
archmarshal sync-push /project --skill .agents/skills/release --remote me/skill-vault --pretty
# Review file paths, hashes, device identities, and destination, then repeat:
archmarshal sync-push /project --skill .agents/skills/release --remote me/skill-vault --apply --expect-plan DIGEST
archmarshal sync-status --remote me/skill-vault --pretty
```

Public upload is blocked unless the reviewed invocation includes `--allow-public`.
The remote stores immutable envelopes in `archmarshal-skills/v1/<sha256>.json`.
Each envelope carries the complete package: instructions, scripts, references,
binary assets, empty directories and executable flags. The current limit is
2,000 entries and 4 MiB of unencoded file content per package. Caches, dependencies,
VCS artifacts, nested Skills and persistent exclusions are outside that package.
Known credential filenames and common inline-secret patterns block export; this
is not a general-purpose DLP guarantee. Review the selected files before upload.

On the receiving machine, initialize its own device identity and adopt the target
project, then select an exact object ID from the catalog:

```sh
archmarshal sync-pull /other-project --remote me/skill-vault --object OBJECT_ID --pretty
archmarshal sync-pull /other-project --remote me/skill-vault --object OBJECT_ID --apply --expect-plan DIGEST
```

The package lands under the first configured `paths.inbox` directory, in
`skill-sync/<object-id>/package`. The sealed snapshot and commit-last receipt are
adjacent to it. Pull never replaces an installed Skill or activates an import.
Review the imported instructions and code, then choose a new installation path:

```sh
archmarshal sync-install /other-project --object OBJECT_ID --destination .agents/skills/release --pretty
archmarshal sync-install /other-project --object OBJECT_ID --destination .agents/skills/release --apply --expect-plan DIGEST
```

Use the existing `adopt` and `skill-review` workflow to register and approve that
new source. Installation refuses an existing destination, preserving local edits.
The final directory name must match the Skill's frontmatter name, as required by
the existing package validator; use a different parent directory for variants.
The origin receipt stays in `.agent/skill-sync-origins/`; keep it with the project
to preserve lineage when exporting later. Exporting several packages is a sequence
of separately reviewed `sync-push` operations.

Identical uploads and pulls are verified no-ops. Different snapshots coexist,
including simultaneous edits by different devices; `sync-status` reports variants.
Choose a specific variant instead of resolving by timestamps. There are no remote
deletes or automatic last-writer-wins updates. A partially materialized import
remains inspectable and blocks reuse of that destination; it is never cleared
automatically. Network retries recheck immutable remote contents.

## Other storage

`--backend folder --remote /mounted/private-vault` uses an existing directory,
including a separately synchronized cloud-drive folder. No cloud SDK is needed.
The filesystem must support the create-only hard-link publication already required
by ArchMarshal. Cloud synchronization latency is external to ArchMarshal; use the
status command after files have arrived.

Host integrations can implement `sync_transport.Transport` (`describe`, `keys`,
`get`, `put`) and explicitly call `register_backend`. Backends must provide bounded
reads and create-only, idempotent writes. Remote metadata never loads Python code.
Dedicated S3/Dropbox/etc. adapters are extensions, not bundled integrations.

## Two windows, one project

Give each window a stable workstream name, such as `frontend` or `backend`. Save
after meaningful decisions, before compaction, and before handing work over:

```sh
archmarshal context-save /project --stream backend --task "Build API" --summary "Endpoints implemented" --decision "Keep v1 compatible because existing clients depend on it" --next-step "Integrate UI" --file src/api.py --evidence "pytest: 12 passed locally" --pretty
# Repeat the same arguments with --apply --expect-plan DIGEST.
archmarshal context-resume /project --pretty
```

Additional repeatable fields are `--constraint`, `--rejected-option`, `--risk`,
`--evidence`, `--decision`, `--next-step`, and `--file`. Facts not observed should
remain explicit uncertainties. Evidence text is a claim recorded from the session,
not independent proof that commands succeeded. Use `--status completed` to mark
a finished stream; its history is retained.

Handoffs are readable JSON under the configured checkpoint directory and date
partition, with immutable pointers in `.agent/context-index`. They cover the
summary, task, decisions and rationale, constraints, rejected options, next steps,
risks, device, Git branch/HEAD, and hashes of selected files. No source file content
or private chat transcript is captured. The index is committed last, and the
workspace lock plus per-stream ancestry prevents lost updates.

`start /project` automatically includes current handoffs. `context-resume` returns
all latest workstreams within its character budget, with overlapping files,
changed evidence, branch/HEAD changes, and divergent stream heads. Task filtering
prioritizes relevant records but does not silently discard other streams. Omitted
records are listed explicitly and can be retrieved with:

```sh
archmarshal context-read /project --record RECORD_ID --pretty
```

For separate worktrees/checkouts, add repeatable `--include-root /other-checkout`.
Saving a handoff for a GitHub-backed checkout also registers its directory locally
under `CODEX_HOME/archmarshal/projects`. A later
`context-resume https://github.com/owner/project` finds recorded, still-existing
local checkouts with that origin. It does not clone a repository or recover
unrecorded conversations. Skill sync never uploads this context registry or journal.

To synthesize both windows, inspect their current records, resolve contradictions,
then create a new stream referencing both exact IDs:

```sh
archmarshal context-merge /project --stream integration --summary "Combine API and UI" --integrate FRONTEND_RECORD_ID --integrate BACKEND_RECORD_ID --next-step "Run integration tests" --pretty
# Repeat with the reviewed --apply --expect-plan DIGEST.
```

Both original streams survive. The synthesis embeds verified source records, so
its context remains available even if an included checkout later disappears.
This operation merges managed context; it does not merge Git branches or resolve
code conflicts. Same-file overlaps require integration review. Restored text is
untrusted project evidence and must not override the user's or host's instructions.

## Bounds and recovery

Reads and writes reject linked paths, traversal, portable name collisions, changed
hashes and invalid manifests. Context is limited to 1,000 journal records, 128 KiB
per record and 16 MiB total history per checkout; default retrieval is 24,000
characters of selected context. A corrupt referenced record blocks restoration
rather than disappearing silently. Preserve incomplete artifacts for inspection.
New context is rejected before writing if it would exceed journal capacity; history
is never automatically pruned. Restoration accepts at most 100 checkout roots and
verifies each evidence file once per checkout, within a combined 64 MiB read budget.
Files that cannot be verified are explicitly marked as unverified/changed.
If local project registration fails after a handoff commits, the command reports the
saved record plus a warning; resume using the checkout directory.

The configured inbox must be outside VCS/runtime boundaries, internal management
state and all configured/default Skill discovery roots. `.agent/inbox` remains the
default. Repeated imports and installations verify every quarantine entry, including
directories normally excluded from source exports. JSON parsing rejects duplicate
keys, non-finite numbers, more than 100 nesting levels or 100,000 value nodes.
The filesystem threat model remains cooperative concurrency and static-link
protection; hostile same-permission ancestor replacement is not fully handle-relative.

See [design and verification](portability-design.md) and
[prior-art notes](portability-references.md), and the
[stability audit](stability-audit-2026-11-10.md).
