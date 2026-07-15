# Update ArchMarshal in Codex

Copy the complete prompt below into Codex. This is the dedicated update
command. The idempotent prompt in `INSTALL_PROMPT.md` remains valid for both a
first installation and an update.

<!-- BEGIN UPDATE PROMPT -->
```text
Update the installed ArchMarshal management plugin to the latest verified version from https://github.com/yptang98/ArchMarshal. If ArchMarshal is not installed, safely perform a first installation instead, using the same verification and rollback rules.

This is a Codex plugin-state operation, not a project-governance operation. Do not run ArchMarshal against the current project. Do not clone into the current project, create a virtual environment there, save plans there, or modify any project or Skill file. Changes are limited to Codex-managed ArchMarshal marketplace/plugin state and ArchMarshal-specific backup or isolated-runtime directories under CODEX_HOME.

Complete the work yourself under these safety constraints; do not hand the steps back to me:

1. Confirm that `codex plugin`, Git, and Python 3.10-3.13 are available. Reuse existing Git/GitHub authentication. Never request, print, copy, or write tokens, passwords, cookies, SSH private keys, or the complete Codex configuration.
2. Resolve the remote default branch HEAD to a full 40-character commit SHA and confirm that GitHub Actions CI succeeded for that exact SHA. Use only that immutable SHA; do not update to an unpinned branch.
3. Inspect `codex plugin marketplace list --json` and `codex plugin list --available --json`. Require the unique marketplace name `archmarshal`, plugin identity `archmarshal@archmarshal`, and repository origin above. Stop without changing anything on ambiguous origin, duplicate identity, or an unexpected same-named marketplace.
4. Record the installed state. If the exact SHA and version are already installed and enabled, make no redundant change and report a verified no-op. If no prior installation exists, use the first-install path from `INSTALL_PROMPT.md`.
5. If the marketplace is a user-owned local checkout, never delete, move, reset, pull, or rewrite it; report that automatic replacement is refused. For a Codex-managed Git snapshot, create a UTC-timestamped backup below `CODEX_HOME/backups/archmarshal/` containing only the old repository identity, full SHA, version, related ArchMarshal marketplace snapshot, plugin cache, and runtime pointer. Do not copy credentials or the complete Codex configuration. Verify the backup before replacement.
6. Replace only through `codex plugin` commands: remove the old `archmarshal@archmarshal` plugin and its Codex-managed marketplace after backup, re-add `yptang98/ArchMarshal` pinned with `--ref` to the verified full SHA, then add `archmarshal@archmarshal`. Do not directly delete plugin caches or edit Codex configuration files. If replacement fails, restore the last known-good pinned plugin/marketplace from the verified backup and report the restoration.
7. Require `archmarshal@archmarshal` to be installed and enabled. Locate its `scripts/run_archmarshal.py` and run `--bootstrap-status`. Identity verification succeeds only with `mode=ready`, `verified=true`, `marketplace=archmarshal`, `dependency_imported=false`, and matching plugin/engine versions.
8. Run read-only `doctor` through the same launcher against a nonexistent path under the system temporary directory, never the current project. If dependencies are missing, do not modify system Python. Create a commit-scoped isolated runtime only below `CODEX_HOME/runtimes/archmarshal/` according to the repository installer contract, install only the pinned `pyproject.toml` wheel dependency closure, retain the pip report, run `pip check`, and atomically publish a validated `archmarshal-runtime-v1` pointer. Never install the ArchMarshal engine itself as an ambient package or build untrusted source dependencies.
9. Report whether the result was installed, updated, unchanged, refused, or restored; include old/new full SHAs and versions, backup location, bootstrap verification, and doctor result. After a version change, remind me to start a new Codex task before invoking ArchMarshal.
```
<!-- END UPDATE PROMPT -->
