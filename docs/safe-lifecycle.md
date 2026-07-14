# Safe Adoption And Lifecycle Recording

ArchMarshal organizes a project by adding a control plane around it. It does not
rearrange the project itself.

## Non-Negotiable Invariants

1. Existing project files and skills are human-owned.
2. Adoption never overwrites, moves, renames, or deletes a human-owned path.
3. An existing `SKILL.md` remains the behavioral source of truth.
4. Missing routing metadata is created under `.agent/skill-overlays/`, never in
   the source skill directory.
5. Preview is the default. Writes require explicit `--apply`.
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

These constraints intentionally make ArchMarshal less aggressive than a general
project formatter. A safe partial adoption is preferable to a clever rewrite
that changes a working project.

## Existing Project Adoption

The default command is a pure preview:

```bash
archmarshal adopt path/to/project --tag research --tag vision --pretty
```

The plan lists every proposed file, every discovered skill source, and any
conflict. `source_will_change` is always false.

Apply only after reviewing the plan:

```bash
archmarshal adopt path/to/project --tag research --tag vision --apply --pretty
```

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
restores over the original project.

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
2. Acquire `HEAD.lock` exclusively; an existing lock blocks the commit.
3. Confirm `HEAD` still equals the preview's expected digest.
4. Exclusively create or verify the content-addressed object.
5. Recheck `HEAD`, then atomically replace the internal pointer.
6. Reload and verify the published generation before reporting success.

If publication fails before step 5, the old `HEAD` remains authoritative. A
fully written orphan object is harmless and retained for audit; ArchMarshal has
no automatic garbage collector yet. If a process is killed hard, a stale lock
may remain. ArchMarshal intentionally does not guess that it is safe to break:
confirm no ArchMarshal process is running, preserve/copy `HEAD.lock` for audit,
and only then remove it manually before replanning. Never delete `HEAD` or edit
an object in place.

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

`workspace.yaml` records `created_on`, `adopted_on`, and tags. `INDEX.md` is the
human entrypoint. Raw history, reports, and backups remain explicit-only.

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

## Learning Without Global Bloat

`archmarshal learn` reads only compact `session.yaml` records. It does not scan
raw project history. Repeated skill usage can become a common-project-skill
candidate; repeated tags can become user-preference candidates. Candidate lists
are capped and written to `.agent/inbox/learning/` for review.

The command never edits global skills or user configuration automatically. This
keeps the global layer small while still allowing deliberate improvement from
cross-project evidence.
