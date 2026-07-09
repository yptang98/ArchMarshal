# Getting Started

Use ArchMarshal by asking Codex to call it. You do not need to remember command
line flags.

## 1. Install

Paste this into Codex while you are in your project:

```text
Codex, install ArchMarshal in this project from:
https://github.com/yptang98/ArchMarshal

After installing, only confirm it works. Do not modify project files.
```

## 2. Project Start

When a project starts:

```text
Codex, run archmarshal-start for this project.

Then manage this project using the ArchMarshal rules:
- preserve raw history
- checkpoint after context compression
- keep project files in user-approved save paths
- use time-first file names
```

## 3. After Context Compression

When Codex has summarized or compressed context:

```text
Codex, call ArchMarshal checkpoint.

Task: <what we are doing>
Summary: <what must be remembered>

Do not delete raw history. Show me the suggested checkpoint file path.
```

Example:

```text
Codex, call ArchMarshal checkpoint.

Task: release checklist
Summary: Release checklist is drafted; CI risk remains unresolved.

Do not delete raw history. Show me the suggested checkpoint file path.
```

## 4. At The End

When a project or phase is done:

```text
Codex, run archmarshal-end for this project.

Tell me what must be preserved so the project can be reproduced later.
Do not modify files unless I explicitly approve it.
```

## Rule

Summaries are only indexes. Keep the original reports, plans, checkpoints, and
notes preserved.
