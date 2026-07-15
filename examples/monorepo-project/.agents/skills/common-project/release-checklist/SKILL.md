---
name: release-checklist
description: Run a reproducible release-readiness checklist across engineering repositories.
---

# Release Checklist

Use this skill for a reproducible release readiness check across engineering repositories.

The skill may read the workspace index and release context. It writes reports or plans only.

## Procedure

1. Read `AGENTS.md` and `.agent/INDEX.md`.
2. Check the declared release context module.
3. Run local scripts from this skill directory only.
4. Emit a report under `.agent/reports/`.
5. Do not change version files, changelogs, tags, or source files.
