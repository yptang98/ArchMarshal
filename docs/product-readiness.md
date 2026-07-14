# Product Readiness

ArchMarshal 0.6 is a safety-hardened alpha, not yet a stable product. This page
separates implemented behavior from design intent so users can decide what to
trust.

## Capability Matrix

| Area | Current state | Safety boundary | Remaining stable-release work |
|---|---|---|---|
| Existing project adoption | Implemented | Preview-first, reserved-file conflicts block, user-owned paths never replaced | Atomic multi-file transaction journal and crash recovery |
| Existing skill management | Implemented, review-first | Complete-package fingerprint; immutable generations; lock/CAS `HEAD`; source never rewritten | Explicit accept/reject/rollback CLI and safe orphan cleanup |
| Backup | Implemented | Bounded archive, byte hashes, CRC, manifest validation, no partial publish | Large-workspace benchmarks and documented retention policy |
| Restore | Implemented | Restores only into a new directory and removes partial output on failure | Guided diff/merge tooling; never add in-place restore |
| Project start | Implemented | Read-only lint plus adoption/sync preview | Share one immutable inventory snapshot for performance |
| Project closeout | Partial | Append-only unique directory; missing evidence blocks writes | Capture declared cwd/inputs/outputs/expected results and optional execution validation |
| Human project map | Partial | YAML plus `INDEX.md` remain readable | Regenerate versioned views from machine state without overwriting human notes |
| Project catalog | Implemented | Reads compact control planes, not raw history | Durable user-level catalog database and rename/move handling |
| Skill/preference learning | Candidate generation only | Deduplicated sessions; no automatic promotion | Review/accept/reject workflow, provenance, supersession, rollback |
| Lightweight global preferences | Not implemented | No silent global mutation | User-scoped accepted-preference store with budgets and explicit application |
| Dynamic context runtime | Not implemented | Resolver is advisory | Token-budgeted loader and host integrations |
| Packaging | Implemented in CI | Linux/Windows and Python 3.10–3.13 tests; clean wheel/sdist install | Signed releases, release notes, provenance, rollback drill |

## Release Gates

A stable release requires all of the following:

1. No overwrite, move, rename, or delete path for human-owned project or skill files in adoption, sync, learning, or closeout.
2. Symlink/junction/reparse escape tests on both Windows and Linux. Basic cross-platform link tests exist; native junction coverage remains.
3. Fault injection for interrupted writes, permissions, disk exhaustion, source mutation, corrupt archives, and concurrent sync. Atomic-swap, collision, lock, tamper, archive, and source-drift cases exist; permission/disk-exhaustion coverage remains.
4. Immutable overlay generations with a stale-plan compare-and-swap check. Implemented; a reviewed rollback command and stale-lock recovery command remain.
5. Statement coverage at least 85% and branch coverage at least 75%, with higher coverage for write paths.
6. Performance baselines for 10,000 files, 100 skills, and multi-project catalogs.
7. Wheel and sdist clean-install tests for every supported Python/OS boundary.
8. Version/tag consistency, signed artifacts, changelog, release checklist, and rollback documentation.

Until these gates pass, use ArchMarshal as a local governance assistant: review
plans, keep source control enabled, and treat generated reproduction scripts as
references rather than trusted executables.
