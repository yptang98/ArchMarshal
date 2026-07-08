# Agent Instructions

## Context Loading

- Read this file first.
- Then read `.agent/INDEX.md`.
- Do not scan all `.agent/` files by default.
- Historical files under `.agent/reports/`, `.agent/history/`, `.agent/archive/`, and `.agent/cache/` are explicit-only.
- Reusable knowledge lives under `.agent/knowledge/`.
- Dynamic context modules live under `.agent/context-modules/`.
- Project skills live under `.agents/skills/`.

## Skill Policy

- Global policy has the highest governance priority.
- Functional skills and common project skills are peer capability layers.
- Project-specific facts should come from project files and context modules.
- If skills conflict, report the conflict before modifying files.
