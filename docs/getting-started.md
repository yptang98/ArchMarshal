# Getting Started

Use ArchMarshal by installing it into a Codex session. After that, you type one
short lifecycle word and continue with normal project instructions.

## 1. Install Prompt

Paste this into Codex once:

```text
You are Codex working in this repository.

Install ArchMarshal from https://github.com/yptang98/ArchMarshal.

After installation, treat these user messages as ArchMarshal lifecycle shortcuts:

- archmarshal-start:
  Internally call the installed ArchMarshal start entrypoint for this project.
  Summarize whether save paths, naming policy, and memory governance are ready,
  then keep managing the project under ArchMarshal rules while I give normal
  project instructions.

- archmarshal-end:
  Internally call the installed ArchMarshal end entrypoint for this project.
  Produce a preservation and reproducibility summary. Do not modify files
  unless I explicitly approve.

During the project, if context is compressed or I ask for a checkpoint, create
an ArchMarshal checkpoint proposal. Summaries are indexes, not replacements:
never delete raw reports, plans, checkpoints, notes, or history.

At project closeout, use ArchMarshal's recording_policy. If the project mostly
reused existing skills, only record important changes, decisions, risks, and
files touched. Only suggest memory/context/skill promotion when the work created
new reusable knowledge or a new workflow.

Only show me concise results. Do not make me type long ArchMarshal commands.
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

Routine projects should produce light records. Novel projects can produce deeper
memory, context, or skill candidates.
