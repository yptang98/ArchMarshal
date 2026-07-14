# Product Readiness

ArchMarshal 0.8 is a safety-hardened alpha, not yet a stable product. This page
separates implemented behavior from design intent so users can decide what to
trust.

## Capability Matrix

| Area | Current state | Safety boundary | Remaining stable-release work |
|---|---|---|---|
| Existing project adoption | Implemented, review-first | Root-bound ownership; exact plan digest; verified backup; durable create-only journal; OS lock; receipt-last forward recovery; changed targets block and are never replaced | Unify backup and adoption under one lifetime lock; handle-relative no-follow filesystem backend; orphan transaction inspection |
| Existing skill management | Immutable tracking implemented | Lock-held package recheck; immutable generations; full reachable-chain verification; reader/writer exclusion; OS lock/CAS `HEAD`; loose-overlay quarantine; exact-plan audited metadata rollback; source never rewritten | Human review/accept-reject lifecycle, ownership migration audit, and safe orphan inspection/cleanup |
| Backup | Implemented | Bounded archive, byte hashes, CRC, manifest validation, atomic exclusive publish | Large-workspace benchmarks and documented retention policy |
| Restore | Implemented | Restores only into a new directory; uncertain partial output is preserved rather than recursively deleted | Resumable restore and guided diff/merge tooling; never add in-place restore |
| Project start | Implemented | Read-only lint plus adoption/sync preview | Share one immutable inventory snapshot for performance |
| Project closeout | Partial | Exact reviewed plan/path/bytes; create-only directory; commit-last file hashes; incomplete or hash-mismatched sessions excluded from learning | Recovery/status UI; declared cwd/inputs/outputs/expected results; optional execution validation |
| Human project map | Partial | YAML plus `INDEX.md` remain readable | Regenerate versioned views from machine state without overwriting human notes |
| Project catalog | Implemented | Reads compact control planes, not raw history | Durable user-level catalog database and rename/move handling |
| Skill/preference learning | Candidate generation only | Only hash-matching committed v2 sessions are evidence; legacy v1 is counted as unverified; no automatic promotion | Explicit legacy migration, review/accept/reject workflow, provenance, supersession, rollback |
| Lightweight global preferences | Not implemented | No silent global mutation | User-scoped accepted-preference store with budgets and explicit application |
| Dynamic context runtime | Not implemented | Resolver is advisory | Token-budgeted loader and host integrations |
| Packaging | Implemented in CI | Linux/Windows and Python 3.10-3.13 tests; clean wheel/sdist install | Signed releases, release notes, provenance, rollback drill |

## Release Gates

A stable release requires all of the following:

1. No overwrite, move, rename, or delete path for human-owned project or skill files in adoption, sync, learning, or closeout.
2. Symlink/junction/reparse escape tests on both Windows and Linux. Basic cross-platform link tests and Windows reparse detection exist; native junction creation coverage remains.
3. Fault injection for interrupted writes, permissions, disk exhaustion, source mutation, corrupt archives, and concurrent sync. Atomic creation, journal/backup tampering, changed-target preservation, receipt-finalization recovery, post-publish rollback, object collision, OS lock replacement, recoverable transaction, directory-scan failure, archive, and source-race cases exist; disk-exhaustion and native permission integration coverage remain.
4. Immutable overlay generations with a stale-plan compare-and-swap check. Implemented with OS-lifetime locking, verified parent transitions, audited forward rollback, and relationship-checked released-transaction recovery; legacy lock migration tooling remains.
5. Statement coverage at least 85% and branch coverage at least 75%, with higher coverage for write paths.
6. Performance baselines for 10,000 files, 100 skills, and multi-project catalogs.
7. Wheel and sdist clean-install tests for every supported Python/OS boundary.
8. Version/tag consistency, signed artifacts, changelog, release checklist, and rollback documentation.

## Explicit Alpha Boundaries

- Safety currently targets accidental interruption, stale plans, ordinary
  concurrent ArchMarshal processes, and linked/reparse path escapes. It does not
  claim complete defense against a malicious process with permission to replace
  workspace directories during a filesystem operation; a handle-relative
  no-follow backend is still required for that claim.
- Windows file and archive contents are flushed, while directory-entry fsync is
  only available on POSIX. Therefore 0.8 does not claim a cross-platform
  power-loss durability guarantee.
- A reproducible closeout is an integrity-checked evidence capsule, not proof of
  successful execution. Generated run scripts are never executed automatically.
- Learning produces candidates only. Accepted preference state, provenance-rich
  promotion, supersession, rollback, and global budget enforcement remain future
  work.
- Commit manifests and adoption journals are integrity metadata, not signatures.
  They do not prove provenance against an actor that can coherently rewrite both
  content and its hashes.

Until these gates pass, use ArchMarshal as a local governance assistant: review
plans, keep source control enabled, and treat generated reproduction scripts as
references rather than trusted executables.
