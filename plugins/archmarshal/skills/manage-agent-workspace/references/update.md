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
   no-op. Never rewrite a user-owned local checkout.
4. Keep the working version installed and enabled while staging the exact
   candidate below `CODEX_HOME/updates/archmarshal/`. Run the candidate
   launcher's dependency-free `--bootstrap-status` directly from that checkout,
   then invoke its locked wrapper directly with active Python and `-I` for
   read-only `doctor` against a nonexistent system-temporary path. Do not touch
   `current.json` or active plugin state unless both pass.
5. Prefer the active Python interpreter. Only when candidate doctor reports a
   missing dependency, prepare and validate a commit-scoped isolated runtime;
   do not publish its pointer yet and do not modify system Python.
6. For an update, use the candidate's stdlib-only `update_support.py create`
   command to build a small last-known-good capsule; do not assemble it with ad
   hoc copy commands. It records the old identity, exact ref, runtime pointer,
   `.agents/plugins/marketplace.json`, plugin directory, locked engine source,
   `pyproject.toml`, commit-last file-hash manifest, and rollback commands in
   repository-relative layout. Run its `verify` command, then verify both the
   capsule launcher and local-marketplace recovery path. Keep old runtimes in
   place and exclude credentials, marketplace history, and the complete Codex
   configuration. The old plugin remains active until this capsule and the
   candidate are both verified.
7. Use only `codex plugin` commands for the short cutover. Re-add the new
   marketplace at its full SHA, install and enable `archmarshal@archmarshal`,
   atomically publish a prepared runtime pointer only if one was required, and
   repeat bootstrap plus read-only doctor. Never manually delete plugin caches.
8. On any cutover or validation failure, restore the old runtime pointer and
   last-known-good pinned plugin/marketplace immediately. Report old/new
   versions and SHAs, capsule location, runtime choice, bootstrap result, doctor
   result, and whether the operation installed, updated, restored, or did
   nothing. If the old Git pin is unavailable, temporarily register the verified
   capsule as the recovery marketplace and reinstall the old plugin from it. A
   current task may finish under already loaded old Skill instructions; use a
   new task to load the new version.

The complete standalone prompts are `INSTALL_PROMPT.md` and `UPDATE_PROMPT.md`
in the ArchMarshal repository. The install prompt is intentionally idempotent:
it performs a first install, a verified update, or a verified no-op.
