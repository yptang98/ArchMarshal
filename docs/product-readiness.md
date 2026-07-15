# Product Readiness

ArchMarshal 0.15 is a safety-hardened alpha, not yet a stable product. This page
separates implemented behavior from design intent so users can decide what to
trust.

## Capability Matrix

| Area | Current state | Safety boundary | Remaining stable-release work |
|---|---|---|---|
| Codex plugin experience | Implemented | Validated uniquely named repository marketplace and plugin manifest; natural-language Skill is the primary entrypoint; matching engine is resolved from the configured full Git marketplace snapshot and its version, API, exact file set, and source-tree hash must match the installed plugin lock; ambient package fallback is forbidden; CLI remains an internal/automation engine | Public installation telemetry, richer host-native progress UI, and signed marketplace/release provenance |
| New project initialization | Implemented, explicit | `init` uses the adoption lock, exact plan, verified backup, and durable create-only transaction; it creates only missing `.agents/skills/` guide/project/generated scaffold files and preserves every existing path | Richer project templates and host-guided first-run UX |
| Existing project adoption | Implemented, review-first | Root-bound ownership; additive default/workspace/explicit Skill roots; previewed management directories; repeatable persistent exact package exclusions pruned before content inspection; preserved VCS/cache/environment/dependency boundaries; exact managed-source paths/bytes/modes/hashes and complete backup coverage bound in the plan; one backup-through-publication lifetime lock; verified backup; durable create-only journal; receipt-last forward recovery; changed targets block and are never replaced | Handle-relative no-follow filesystem backend; orphan transaction inspection |
| Existing skill management | Implemented, quarantined and reviewed | Codex package validation; complete saved review plan binds the exact generation/HEAD; exact package+routing approval; separate global-policy confirmation; immutable generations; full reachable-chain verification; reader/writer exclusion; OS lock/CAS `HEAD`; exact-plan rollback; source never rewritten | Ownership migration audit and safe orphan inspection/cleanup |
| Backup | Implemented | Bounded archive, streamed cumulative limits and free-space reserve; one stable descriptor binds ZIP parsing, member hashes, archive size/hash, and path identity; atomic exclusive publish; managed rollback scope excludes recursive runtime/history stores | Documented retention policy and resumable large backups |
| Restore | Implemented | Exact archive/destination plan; POSIX staging stays at verified `0700` until atomic no-replace publication; Windows reports identity without claiming ACL privacy; post-publish mode failures are explicit; portable root/directory/file modes and empty directories in full backups; restores only into a new directory; optional verified full-backup ownership rebind affects only the copy | Native private Windows ACL backend; resumable restore and guided diff/merge tooling; never add in-place restore |
| Project start | Implemented | Read-only lint plus adoption/sync preview | Share one immutable inventory snapshot for performance |
| Project closeout | Partial | Exact reviewed plan/path/bytes and full session preview; ownership, evidence, recording, and execution-validation truth reported separately; create-only directory; commit-last file hashes; incomplete or hash-mismatched sessions excluded from learning | Recovery/status UI; declared cwd/inputs/outputs/expected results; optional execution validation |
| Human project map | Partial | YAML plus `INDEX.md` remain readable | Regenerate versioned views from machine state without overwriting human notes |
| Project catalog | Implemented | Reads compact control planes, not raw history | Durable user-level catalog database and rename/move handling |
| Skill/preference learning | Review, non-activating draft scaffold, and promotion implemented | Exact saved learning plan; session-pinned package hashes; privacy-preserving v3 packs; exact candidate digest/provenance; latest-decision acceptance gate; candidate draft uses a disjoint create-only envelope with `SKILL.md.draft` and commit-last baseline; promotion remains separate; no automatic activation or promotion | Explicit legacy migration, candidate supersession UI, and richer evidence explanations |
| Lightweight user preferences | Implemented in isolated store | Count/byte budgets; secret and absolute-path rejection; immutable generations; explicit replacement confirmation, application, and forward rollback | Ergonomic profile selection |
| User common-Skill store | Implemented | Root-bound ownership; v2 content addresses bind bytes, executable/permission modes, subdirectory modes, and empty subdirectories while the package root remains store-owned; portable-name collision rejection; stable descriptor verification; v1 read compatibility; complete saved-plan apply; expected-HEAD CAS; OS lock; commit-last copy; forward rollback; source/draft unchanged | Signed/exportable bundles, partial/orphan inspection, retention policy, and native host integration |
| Dynamic context runtime | Not implemented | Resolver is advisory | Token-budgeted loader and host integrations |
| CLI module loading | Implemented for built-in domains | Parsing, help, and version load only the lightweight CLI/error/version layer; each command imports its built-in domain after selection; user Skill code is never imported or executed | Measured cold-start budget and host integration profiling |
| Product health diagnostics | Implemented, read-only | `doctor` uses bounded stable reads, rejects links/reparse points, validates control schemas and current v2 package integrity, classifies corrupt/legacy/orphan/partial state, proposes retention only, and exposes a versioned durable-format registry plus truthful filesystem capabilities | Guided repair/migration plans must remain separate exact-plan workflows; no automatic cleanup |
| Scale baseline | Implemented and reproducible | Temporary fixtures only; public read/preview APIs; complete fixture bytes/modes/mtimes hashed before and after; reference covers 10,000 files, 100 Skills, and 50 projects | Heterogeneous CI history and a shared immutable inventory snapshot |
| Packaging | Implemented in CI | Linux/Windows and Python 3.10-3.13 tests; wheel/sdist clean installs on Linux/Windows at Python 3.10/3.13 boundaries; CLI version and lightweight-bootstrap checks | Signed tagged releases, provenance, and rollback drill |

## Release Gates

A stable release requires all of the following:

1. No overwrite, move, rename, or delete path for human-owned project or skill files in adoption, sync, learning, or closeout.
2. Symlink/junction/reparse escape tests on both Windows and Linux. Basic cross-platform link tests and Windows reparse detection exist; native junction creation coverage remains.
3. Fault injection for interrupted writes, permissions, disk exhaustion, source mutation, corrupt archives, and concurrent sync. Atomic creation, journal/backup tampering, changed-target preservation, receipt-finalization recovery, post-publish rollback, object collision, OS lock replacement, recoverable transaction, directory-scan failure, archive, and source-race cases exist; disk-exhaustion and native permission integration coverage remain.
4. Immutable overlay generations with a stale-plan compare-and-swap check. Implemented with OS-lifetime locking, verified parent transitions, audited forward rollback, and relationship-checked released-transaction recovery; legacy lock migration tooling remains.
5. Statement coverage at least 85% and branch coverage at least 75%, with
   independent non-regression floors for safety-critical write-path modules.
6. Performance baselines for 10,000 files, 100 skills, and multi-project
   catalogs. The reproducible local baseline is implemented; stable release
   still requires regression history on heterogeneous CI runners.
7. Wheel and sdist clean-install tests for every supported Python/OS boundary.
8. Version/tag consistency, signed artifacts, changelog, release checklist, and rollback documentation.

## Explicit Alpha Boundaries

- Safety currently targets accidental interruption, stale plans, ordinary
  concurrent ArchMarshal processes, and linked/reparse path escapes. It does not
  claim complete defense against a malicious process with permission to replace
  workspace directories during a filesystem operation; a handle-relative
  no-follow backend is still required for that claim. `doctor` exposes this as
  `anchored_components: false` and
  `concurrent_ancestor_rebinding_protected: false`; the migration contract is
  documented in `docs/filesystem-safety.md`.
- Windows file and archive contents are flushed, while directory-entry fsync is
  only available on POSIX. Therefore 0.12 does not claim a cross-platform
  power-loss durability guarantee.
- Python's portable mode APIs do not establish a private Windows ACL. Restore
  staging has verified identity there, but ArchMarshal does not claim staging
  confidentiality on Windows until a native ACL backend is implemented.
- A reproducible closeout is an integrity-checked evidence capsule, not proof of
  successful execution. Generated run scripts are never executed automatically.
- Learning and promotion provide integrity and explicit review, not identity
  signatures. A user must still inspect Skill behavior and scripts; ArchMarshal
  validates and copies them but never proves that executing them is safe.
- Commit manifests and adoption journals are integrity metadata, not signatures.
  They do not prove provenance against an actor that can coherently rewrite both
  content and its hashes.

Until these gates pass, use ArchMarshal as a local governance assistant: review
plans, keep source control enabled, keep generated candidate drafts outside
active Skill roots until review is complete, and treat generated reproduction
scripts as references rather than trusted executables.
