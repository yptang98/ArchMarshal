# Getting Started

ArchMarshal has one simple job:

> Keep project memory findable after context gets compressed.

It is read-only by default. It prints JSON suggestions and does not modify your
project files.

## Install

```bash
python -m pip install "git+https://github.com/yptang98/ArchMarshal.git"
```

## The 3 Calls

Use these from your project root.

### 1. Check The Project

```bash
archmarshal lint . --pretty
```

Use this when starting a project or when something feels messy.

### 2. Save A Checkpoint After Context Compression

```bash
archmarshal checkpoint . --task "<what you are doing>" --summary "<what must be remembered>" --pretty
```

Example:

```bash
archmarshal checkpoint . --task "release checklist" --summary "Release checklist is drafted; CI risk remains unresolved." --pretty
```

This suggests a file name like:

```text
.agent/inbox/checkpoints/20260709-071543-release-checklist-checkpoint.md
```

If you want a specific save folder:

```bash
archmarshal checkpoint . --summary "<summary>" --save-path ".agent/history/checkpoints" --pretty
```

### 3. Close Out A Project Or Phase

```bash
archmarshal closeout . --pretty
```

If you used a skill:

```bash
archmarshal closeout . --used-skill skill.common-project.release-checklist --pretty
```

This tells you what to preserve so the project can be reproduced later.

## Optional

Ask what skill/context may help with a task:

```bash
archmarshal resolve . --task "<task>" --pretty
```

See everything ArchMarshal can see:

```bash
archmarshal inventory . --pretty
```

Get a read-only improvement plan:

```bash
archmarshal plan . --pretty
```

## Rule Of Thumb

Do not delete raw history just because you made a summary.

Summaries are indexes. Original reports, plans, checkpoints, and notes should
stay preserved in explicit-only locations.
