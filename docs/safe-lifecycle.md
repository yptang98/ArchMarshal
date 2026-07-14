# Safe Adoption And Lifecycle Recording

ArchMarshal organizes a project by adding a control plane around it. It does not
rearrange the project itself.

## Non-Negotiable Invariants

1. Existing project files and skills are human-owned.
2. Adoption never overwrites, moves, renames, or deletes an existing path.
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
- Every discovered `SKILL.md`.
- Every existing source skill `manifest.yaml`.

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
