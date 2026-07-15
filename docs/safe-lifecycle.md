# Safe Adoption And Lifecycle Recording

ArchMarshal organizes a project by adding a control plane around it. It does not
rearrange the project itself.

## Non-Negotiable Invariants

1. Existing project files and skills are human-owned.
2. Adoption never overwrites, moves, renames, or deletes a human-owned path.
3. An existing `SKILL.md` remains the behavioral source of truth.
4. Missing routing metadata is created under `.agent/skill-overlays/`, never in
   the source skill directory.
5. Preview is the default. Adoption and closeout writes require explicit
   `--apply` plus the exact SHA-256 digest of the reviewed preview.
6. Adoption verifies a backup before creating the first managed file.
7. A reserved control-file conflict blocks adoption instead of triggering a
   merge or guessed rewrite.
8. Closeout writes to a unique new directory and never edits prior sessions.
9. Reproduction records exclude environment variables and block known inline-secret patterns; selected text and scripts still require review.
10. Skill and preference promotion is review-only.
11. Skill sync creates immutable generations. The only replaced path is the
    ArchMarshal-owned `HEAD`, after backup, exclusive lock, and expected-value
    validation.
12. Directory scans never follow symbolic links, junctions, or reparse points.
13. Adoption publishes a durable journal before visible control-plane targets
    and writes a receipt last; interruption is recovered forward, never by
    deleting paths.
14. Closeout writes a hash manifest last. Incomplete or hash-mismatched sessions
    are preserved but excluded from learning.
15. Closeout and learning apply only inside a root-bound, ArchMarshal-owned
    workspace and hold one lifetime mutation lock.
16. Adopted Skills are inactive until their exact package and routing revision
    are approved. Global/highest policy requires a separate confirmation.
17. Candidate promotion writes only to an explicitly initialized user store.
    Apply requires the complete saved plan, exact plan digest, and exact HEAD.

These constraints intentionally make ArchMarshal less aggressive than a general
project formatter. A blocked or explicitly recoverable adoption is preferable
to a clever rewrite that changes a working project.

## Existing Project Adoption

The default command is a pure preview:

```bash
archmarshal adopt path/to/project --tag research --tag vision --pretty
```

The plan lists every proposed file, every discovered skill source, and any
conflict. `source_will_change` is always false.

Apply only after reviewing the plan:

```bash
archmarshal adopt path/to/project --tag research --tag vision \
  --expect-plan <plan_digest> --apply --pretty
```

The digest covers the proposed control-file bytes, project tags, backup scope,
source preconditions, and proposed skill-index generation. If the workspace or
request changes, the apply blocks and a new preview is required.

The managed backup scope contains:

- Existing ArchMarshal control files, if any.
- Existing `.agent/` files.
- Every regular file in each discovered non-root skill package (bounded by the
  same link, file-count, and byte limits used for fingerprinting).
- For a repository-root `SKILL.md`, only the entrypoint and root manifest, so a
  managed backup does not silently become a full-project backup.

Use `--backup-scope full` when the user requires a broad project-content snapshot. VCS
internals, dependency directories, virtual environments, and previous backups
are excluded. The resulting zip contains a JSON manifest with the original
relative path, byte size, and SHA-256 hash for each file; the zip is tested and
hashed before adoption continues. `.agent/backups/.gitignore` prevents backup
archives from being committed accidentally; these backups may still contain
sensitive project files and must not be shared casually.

Use `archmarshal backup-verify` to re-check every archived byte. Restore is
deliberately one-way into a new, non-existing directory; ArchMarshal never
restores over the original project. If extraction fails, the incomplete new
directory is preserved for explicit inspection. ArchMarshal does not recursively
delete it because another process may have added or replaced content there.

### Durable Adoption Transaction

After backup verification and reviewed-plan validation, ArchMarshal stages exact
payload bytes and a journal under:

```text
.agent/transactions/adoption/
|-- LOCK
|-- ACTIVE
`-- <transaction-id>/
    |-- journal.json
    |-- payloads/
    `-- COMMITTED.json
```

The journal is durable before visible control-plane targets (the already
verified backup archive is published first). The OS-lifetime lock is held for
control-plane publication. The journal binds target paths,
byte sizes, hashes, the verified backup, the reviewed plan digest, and the
skill-index plan. Each visible control file is created exclusively from a fully
staged payload; an existing exact match is accepted, while a changed, linked,
or replaced path blocks without overwrite. The immutable skill generation is
published only against its expected `HEAD`, and `COMMITTED.json` is written
last. `ACTIVE` is cleared only when its identity and bytes still match.

After a process interruption, use `adoption-status` and preview
`adoption-recover` before applying recovery with that exact transaction id and
plan digest. Recovery repeats every verification and completes only missing
create-only targets. It never removes an uncertain file or rolls the workspace
backward.

## Skill Overlay Model

For a source skill such as:

```text
.codex/skills/release-helper/SKILL.md
```

ArchMarshal may add:

```text
.agent/skill-overlays/project/release-helper-<source-hash>/manifest.yaml
```

The overlay declares kind, scope, tags, triggers, negative triggers, and the
original source path. Its source policy is:

```yaml
source:
  skill_dir: .codex/skills/release-helper
  skill_md: .codex/skills/release-helper/SKILL.md
  skill_sha256: <entrypoint-sha256>
  package_sha256: <complete-package-sha256>
  managed: false
  mutation_policy: never
```

Inventory and resolution use overlay metadata while local paths, scripts, and
behavior still resolve against the original skill directory. Scripts,
references, assets, and other regular files participate in the package
fingerprint. Linked or unexpectedly huge packages are rejected rather than
followed implicitly. This is analogous
to a module registry or package-lock overlay: identity and routing are managed
without rewriting the module implementation.

### Immutable Sync Generations

Initial adoption keeps human-readable overlay manifests. Incremental start/sync
also maintains this internal registry:

```text
.agent/skill-overlays/.archmarshal/
├─ HEAD
├─ HEAD.lock
└─ objects/sha256/<generation-digest>.json
```

A generation is a complete, canonical JSON view of active and removed skills.
It points to its parent and records `added`, `modified`, `removed`, and
`restored` changes. Every regular file in a skill package contributes to its
fingerprint, so changing a script, reference, template, or asset produces a new
generation without editing the source.

Commit order is deliberately narrow:

1. Create and verify the pre-sync backup.
2. Acquire the cross-platform OS-lifetime lock on persistent `HEAD.lock` and
   write expected/proposed transaction metadata.
3. Confirm `HEAD` still equals the preview's expected digest.
4. Re-fingerprint active source packages while holding the lock; incomplete or
   permission-denied scans block instead of implying removal.
5. Exclusively create or verify the content-addressed object and fsync its
   directory entries on POSIX.
6. Recheck `HEAD`, then atomically replace the internal pointer.
7. Reload and verify the published generation before reporting success.

If publication fails before step 6, the old `HEAD` remains authoritative. A
fully written orphan object is harmless and retained for audit; ArchMarshal has
no automatic garbage collector yet. The lock file itself persists but is empty
while idle; liveness comes from the OS-held byte/file lock, not file existence or
age. If a process exits hard, v2 transaction metadata is recovered only after
the OS lock is available and current `HEAD` equals either the recorded expected
HEAD or a fully verified proposed generation. The decision is preserved under
`.agent/skill-overlays/.archmarshal/recovery/`. Legacy, malformed, or
relationship-conflicting lock metadata remains blocked for manual review; never
delete `HEAD` or edit an object in place.

### Verified History And Metadata Rollback

`archmarshal skill-index-status` walks only the parent chain reachable from the
captured `HEAD`. It verifies every object's type, size, content hash, canonical
JSON, parent relationship, and declared snapshot changes under generation and
cumulative-byte limits. It does not enumerate or trust orphan objects.
Long output is paged with `--history-limit` and the returned continuation digest
can be supplied as `--history-from`.

`archmarshal skill-index-rollback` is preview-first. Apply requires the exact
`--expect-head` and `--expect-plan` shown by the reviewed preview. The target
must be a reachable ancestor, and the command creates a new generation whose
parent is the current HEAD; it never moves HEAD backward. Source skill files are never restored,
deleted, or rewritten. Every active target skill must already match its recorded
complete-package hash, so old routing metadata cannot silently activate newer
implementation bytes.

Rollback is not a permanent ignore/pin rule. If the workspace source still
differs from the rolled-back metadata view, the next start/sync will surface a
new reviewed add/modify/restore proposal; it will not silently apply it.

## Project Layout

New management data is small and human-readable:

```text
.agent/
├─ INDEX.md
├─ workspace.yaml
├─ registry.yaml
├─ skill-overlays/
├─ knowledge/
├─ plans/
├─ reports/
├─ history/YYYY/MM/DD/
├─ inbox/
└─ backups/
```

The control plane also contains `ownership.json` and
`.agent/transactions/`. `workspace.yaml` records `created_on`, `adopted_on`, and
tags. `INDEX.md` is the human entrypoint. Raw history, reports, transactions,
and backups remain explicit-only.

Use `archmarshal catalog` with repeated `--include-root` and `--tag` options to
view several projects sorted by creation date. The catalog reads only their
control planes and treats repeated tags as an AND filter.

## Three Closeout Levels

### Quick

Creates `SUMMARY.md` and `session.yaml`. Use it for routine work where a concise
outcome, tags, Git state, and used-skill list are enough.

### Standard

Also creates `STEPS.md` with ordered `--step` entries and hashes for each
`--script`. Use it when another person should understand the work without
needing a complete execution capsule.

### Reproducible

Also creates `reproduction.yaml`, a reference `run.ps1` or `run.sh`, and hashed
copies of key scripts. It captures Git commit/branch/dirty paths, safe platform
and Python fingerprints, and dependency-file hashes. It is marked ready only
when summary, ordered steps, key scripts, and exact rerun commands are all
present. This is evidence completeness, not proof that the recorded commands
were executed successfully or that an external environment can be reconstructed.

Generated run scripts are never executed by ArchMarshal. They are references
that must be reviewed by a human.

All closeout levels are preview-bound by `--expect-plan`. Files are created in a
new date-organized directory and `COMMITTED.json` is created only after every
session file and selected script snapshot exists. The marker records exact
relative paths, sizes, and SHA-256 hashes. A crash before that point leaves a
visible incomplete directory for review; no cleanup routine deletes it.

## Learning Without Global Bloat

`archmarshal learn` reads only compact `session.yaml` records whose final commit
manifest and every declared file verify. It ignores incomplete and
hash-mismatched sessions and does not scan raw project history. Repeated skill
usage can become a common-project-skill
candidate; repeated tags can become user-preference candidates. Candidate lists
are capped and written to `.agent/inbox/learning/` for review.

Legacy v1 sessions predate the commit marker. They are reported as
`legacy_unverified_session_count` and are not automatically promoted to trusted
evidence. A future explicit migration may re-commit them after review.

The command never edits global skills or user configuration automatically. This
keeps the global layer small while still allowing deliberate improvement from
cross-project evidence.

## Reviewed User Skill Store

The user store is a separate ownership domain, not a hidden rewrite of a project
or a global Skill directory:

```text
<explicit-user-store>/
|-- ownership.json
|-- .archmarshal/
|   |-- HEAD
|   |-- HEAD.lock
|   `-- objects/sha256/<generation>.json
`-- packages/sha256/<package>/
    |-- SKILL.md
    |-- manifest.yaml
    `-- COMMITTED.json
```

Initialization accepts only an absent or empty real directory whose parent
already exists. `ownership.json` binds the canonical root, so copying or moving
the marker cannot claim another location. A non-empty unowned directory is
never adopted as a store. Every existing lexical path component is checked for
links/junctions before canonicalization. If external content appears during
initialization, ArchMarshal removes only its unchanged marker and leaves the
external content untouched.

Candidate review first verifies the committed learning pack under the owned
source project's `.agent/inbox/learning/`. The candidate digest and compact
session evidence are recorded as provenance. `accept`, `reject`, and `defer`
publish immutable decision generations; they do not edit the pack or project.

Promotion is separately previewed. A common Skill requires an explicit draft
that passes the Codex Skill package contract and the ArchMarshal common-project
manifest rules. The preview binds every draft file hash and its exact real
directory; apply rechecks the directory before, during, and after a commit-last
copy. Preferences are capped and reject known secrets and absolute paths. Apply
requires all three reviewed inputs:

1. the complete saved JSON preview (`--plan-file`);
2. its exact `--expect-plan` digest; and
3. its exact `--expect-head`, using `none` only when the preview says so.

The store publishes a package commit marker before the generation object and
advances only its internal `HEAD` under an OS lock and compare-and-swap. An
interrupted committed package remains an inactive orphan until a matching plan
finishes; it is never selected merely because it exists.
Private copy temporaries live in a store staging directory outside immutable
packages, so a hard interruption cannot turn an otherwise resumable package
into a package with undeclared files.

Rollback is forward-only. `user-store-rollback` copies an ancestor snapshot
into a new generation whose parent is the current HEAD. Old generations and
packages remain verifiable, while projects immediately stop resolving content
that is absent from the new active snapshot. No project or Skill draft is
restored, deleted, or rewritten.
