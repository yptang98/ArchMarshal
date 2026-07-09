# Getting Started

Use ArchMarshal by installing it into a Codex session. After that, you type one
short lifecycle word and continue with normal project instructions.

## 1. Install Prompt

Paste this into Codex once:

```text
Codex, install ArchMarshal for this project:
https://github.com/yptang98/ArchMarshal

After installing it, let me use two short lifecycle words:

- When I type `archmarshal-start`, run ArchMarshal's project-start check for
  the current project. Check save paths, naming, and memory/history rules, then
  keep using ArchMarshal quietly while I give normal project instructions.

- When I type `archmarshal-end`, run ArchMarshal's project closeout for the
  current project or phase. Give me a preservation and reproducibility summary.
  Do not modify files unless I explicitly approve.

During the project, if context is compressed or I ask for a checkpoint, create
an ArchMarshal checkpoint proposal. Summaries are indexes, not replacements:
never delete raw reports, plans, checkpoints, notes, or history.

Use automatic recording depth. If the project mostly reuses existing skills,
keep the record light. Only go deeper when the work creates new reusable
knowledge, a new workflow, or a real governance risk.

Keep the output concise. Do not make me type long ArchMarshal commands.
```

## 2. Start

When the project starts, type:

```text
archmarshal-start
```

Then give normal instructions, for example:

```text
Build the release checklist.
Analyze the benchmark result.
Prepare the project report.
```

Codex should use ArchMarshal in the background when checkpoints or closeout are
needed.

## 3. End

```text
archmarshal-end
```

## Rule

Summaries are only indexes. Keep the original reports, plans, checkpoints, and
notes preserved.

Recording depth is automatic. Routine projects should stay light; novel projects
can produce deeper memory, context, or skill candidates.
