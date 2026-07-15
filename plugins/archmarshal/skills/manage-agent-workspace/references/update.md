# Install and update ArchMarshal

Treat both installation and update as Codex plugin-state operations, never as
project governance. Do not run `adopt`, `init`, `start`, or any mutation-capable
project command during this workflow.

1. Use only `https://github.com/yptang98/ArchMarshal`. Resolve the remote default
   branch HEAD to a full Git commit SHA and require successful GitHub Actions for
   that exact commit.
2. Inspect `codex plugin marketplace list --json` and
   `codex plugin list --available --json`. Require the unique marketplace name
   `archmarshal` and plugin identity `archmarshal@archmarshal`. Stop on ambiguous
   origin or duplicate identity.
3. If the exact SHA and version are already installed and enabled, report a
   no-op. This is what makes the install command safely update-compatible.
4. For first install, add the Git marketplace at the verified full SHA, then add
   `archmarshal@archmarshal`.
5. For update, never rewrite a user-owned local checkout. For a Codex-managed Git
   snapshot, save the old origin, SHA, version, marketplace snapshot, plugin
   cache, and ArchMarshal runtime pointer below a UTC-timestamped
   `CODEX_HOME/backups/archmarshal/` directory. Exclude credentials and the full
   Codex configuration. Verify the backup before using only `codex plugin`
   remove/add commands to replace the old pinned marketplace and plugin. Restore
   the last known-good pinned version if replacement or validation fails.
6. Require the installed plugin to be enabled. Run its
   `scripts/run_archmarshal.py --bootstrap-status`; require `mode=ready`,
   `verified=true`, `marketplace=archmarshal`, `dependency_imported=false`, and
   matching plugin/engine versions. Then run read-only `doctor` against a
   nonexistent path under the system temporary directory, never the current
   project.
7. If dependencies are unavailable, create or reuse only the validated
   commit-scoped isolated runtime below `CODEX_HOME/runtimes/archmarshal/` as
   specified by the repository install prompt. Do not modify system Python.
8. Report old/new versions and SHAs, backup location, bootstrap result, doctor
   result, and whether the operation installed, updated, restored, or did
   nothing. Ask the user to start a new Codex task after a version change.

The complete standalone prompts are `INSTALL_PROMPT.md` and `UPDATE_PROMPT.md`
in the ArchMarshal repository. The install prompt is intentionally idempotent:
it performs a first install, a verified update, or a verified no-op.
