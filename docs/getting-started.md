# Getting Started

Use ArchMarshal by installing it into a Codex session. After that, type a
built-in lifecycle word and continue with normal project instructions.

## 1. Install

Paste this into Codex once:

```text
Codex, install ArchMarshal for this project:
https://github.com/yptang98/ArchMarshal

After installing, confirm it is available and show me the shortest way to start.
```

## 2. Start

For an existing project, preview the management overlay:

```text
archmarshal adopt . --tag research --pretty
```

After reviewing the file list and conflicts, apply it:

```text
archmarshal-start . --apply --tag research --pretty
```

This creates a verified backup and then adds only missing control-plane files.
Existing project files, `SKILL.md`, and source skill manifests are not changed.
Skill routing metadata lives in `.agent/skill-overlays/`.

For a project that is already managed, type:

```text
archmarshal-start
```

ArchMarshal checks save paths, naming, memory/history rules, and then Codex can
keep using it quietly while you give normal project instructions.

Then give normal instructions, for example:

```text
Build the release checklist.
Analyze the benchmark result.
Prepare the project report.
```

During the project, checkpoints should preserve what must survive context
compression. Summaries are indexes, not replacements: raw reports, plans,
checkpoints, notes, and history stay preserved.

## 3. End

Choose one of three explicit depths:

```text
archmarshal-end . --level quick --summary "Routine phase complete" --apply

archmarshal-end . --level standard --summary "Release checked" \
  --step "Run tests" --script scripts/check.py --apply

archmarshal-end . --level reproducible --summary "Benchmark reproduced" \
  --step "Prepare inputs" --step "Run benchmark" \
  --script scripts/benchmark.py --command "python scripts/benchmark.py" --apply
```

The commands preview unless `--apply` is present. Applied records go into a new
date-organized history directory. Reproducible mode snapshots key scripts and
does not claim readiness while required evidence is missing.

After repeated sessions, create a review-only learning pack:

```text
archmarshal learn . --include-root ../another-project --apply --pretty
```

## Rule

Summaries are only indexes. Keep the original reports, plans, checkpoints, and
notes preserved.

Recording depth is automatic. Routine projects should stay light; novel projects
can produce deeper memory, context, or skill candidates.

See [Safe Adoption And Lifecycle Recording](safe-lifecycle.md) for backup,
conflict, overlay, and no-overwrite guarantees.
