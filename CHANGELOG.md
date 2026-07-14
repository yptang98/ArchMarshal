# Changelog

## 0.7.0 - 2026-07-15

- Recheck complete skill-package hashes while holding the commit lock so source
  changes between preview and publication cannot activate stale metadata.
- Quarantine missing, unsafe, untracked, or drifted skills from resolver output
  until their source state is reviewed and synchronized.
- Add verified `skill-index-status` history and preview-first,
  expected-HEAD-gated, audited `skill-index-rollback` commands.
- Rollback creates a new immutable generation and never restores or rewrites
  source skill files; active target packages must still match their hashes.
- Replace existence-only locking with Windows/POSIX OS-lifetime locks and safely
  recover released v2 transactions only when HEAD relationships verify.
- Persist append-only lock-recovery audit records, validate parent transitions,
  reject incomplete directory scans, and fsync object directory entries on POSIX.

## 0.6.0 - 2026-07-15

- Add immutable, content-addressed skill-index generations for additions,
  modifications, removals, and restores.
- Publish the active generation through an exclusive lock, expected-HEAD
  compare-and-swap, atomic pointer update, and post-commit verification.
- Reject tampered objects, object collisions, unsafe source paths, linked or
  reparse-point scans, and excessive skill-index/file-scan sizes.
- Keep old generations after sync and keep the previous HEAD active when atomic
  publication fails.
- Integrate generation-backed skills into inventory and resolution without
  modifying source skill packages.

## 0.5.0 - 2026-07-15

- Add structured CLI errors and non-zero blocked-operation exit codes.
- Add bounded, content-verified backups and restore-to-new-directory commands.
- Reject linked control paths and external workspace scan paths.
- Fingerprint complete skill packages and report create-only sync/drift plans.
- Validate packaged memory-store and memory-record schemas.
- Deduplicate learning evidence by project, session, skill implementation, and script content.
- Require closeout evidence before writing and improve sensitive-value detection.
- Add Linux/Windows Python 3.10-3.13 CI, coverage, static checks, and clean artifact installs.
