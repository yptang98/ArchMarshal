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

When the project starts, type:

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

```text
archmarshal-end
```

ArchMarshal closes the project or phase with a preservation and reproducibility
summary.

## Rule

Summaries are only indexes. Keep the original reports, plans, checkpoints, and
notes preserved.

Recording depth is automatic. Routine projects should stay light; novel projects
can produce deeper memory, context, or skill candidates.
