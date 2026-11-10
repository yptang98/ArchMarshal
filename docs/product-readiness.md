# Product Readiness

ArchMarshal 0.19 is an alpha development version. This page
separates implemented behavior from design intent so users can decide what to
trust.

## Capability Matrix

| Area | Current state | Safety boundary | Remaining stable-release work |
|---|---|---|---|
| Native Skill reconciliation | Implemented, read-only | Exact cwd/host/path matching; freshness and invalidation; approval separate from native enablement; explicit bounded source/installed-copy hash verification; a short-lived CLI probe invokes no model turn or config mutation | Native desktop in-memory activation/invocation evidence; authenticated remote host observations; optional lifecycle hooks |
| Codex paths and external deliverables | Implemented in 0.19 development | Explicit project/worktree association; root/path/HEAD-bound plans; records and selected verified file copies outside source; commit-last history; compatible legacy record/delivery reads; no native adoption or host setting changes | Optional host event adapter, reviewed path migration, larger artifact streaming and offline delivery recovery |
| Interrupted local operations | Implemented | Import, installation, and closeout seal inputs before output writes; bounded stage inspection; exact-state recovery only creates missing files; modified files and changed mappings block continuation | Legacy unjournaled recovery, retention/export UI, and host-native progress presentation |
| Workstream handoff and archival | Implemented | Explicit start/save/resume/finish; last-save age and pending work; immutable archive generations preserve old IDs, parent references, heads and merge evidence; cold bodies verified on demand | Host window identity and explicit host event integration; archive export beyond bounded catalog capacity |
| Recommendation evaluation | Implemented for a fixed corpus | Match evidence required before priority ranking; token boundaries and bilingual regression fixtures; no execution claims | Representative production evaluation and semantic routing quality |
| Candidate comparison | Implemented, read-only | Duplicate/extended/complementary evidence and implementation/value conflicts remain explicit; all pack identities retained; no transferred acceptance | Cross-workspace catalog UI and reviewed candidate supersession |
| Codex plugin experience | Implemented | Validated uniquely named repository marketplace and plugin manifest; natural-language Skill is the primary entrypoint; matching engine is resolved from the configured full Git marketplace snapshot and its version, API, exact file set, and source-tree hash must match the installed plugin lock; ambient engine-package fallback is forbidden; update candidates are validated while the old version stays enabled; active Python is the dependency fast path; a bounded commit-last capsule supports rollback, while offline recovery uses a disjoint materialized marketplace copy; launchers suppress bytecode writes; runtime pointers cannot block bootstrap identity checks | Public installation telemetry, richer host-native progress UI, signed marketplace/release provenance, and a host-native atomic marketplace swap primitive |
| New project initialization | Implemented, explicit | `init` uses the adoption lock, exact plan, verified backup, and durable create-only transaction; it creates only missing `.agents/skills/` guide/project/generated scaffold files and preserves every existing path | Richer project templates and host-guided first-run UX |
| Existing project adoption | Implemented, review-first | Root-bound ownership; additive default/workspace/explicit Skill roots; previewed management directories; repeatable persistent exact package exclusions pruned before content inspection; preserved VCS/cache/environment/dependency boundaries; exact managed-source paths/bytes/modes/hashes and complete backup coverage bound in the plan; one backup-through-publication lifetime lock; verified backup; durable create-only journal; receipt-last forward recovery; changed targets block and are never replaced | Handle-relative no-follow filesystem backend; orphan transaction inspection |
| Existing skill management | Implemented, quarantined and reviewed | Codex package validation; complete saved review plan binds the exact generation/HEAD; exact package+routing approval; separate global-policy confirmation; immutable generations; full reachable-chain verification; reader/writer exclusion; OS lock/CAS `HEAD`; exact-plan rollback; source never rewritten | Ownership migration audit and safe orphan inspection/cleanup |
| Backup | Implemented | Bounded archive, streamed cumulative limits and free-space reserve; one stable descriptor binds ZIP parsing, member hashes, archive size/hash, and path identity; atomic exclusive publish; managed rollback scope excludes recursive runtime/history stores | Documented retention policy and resumable large backups |
| Restore | Implemented | Exact archive/destination plan; POSIX staging stays at verified `0700` until atomic no-replace publication; Windows reports identity without claiming ACL privacy; post-publish mode failures are explicit; portable root/directory/file modes and empty directories in full backups; restores only into a new directory; optional verified full-backup ownership rebind affects only the copy | Native private Windows ACL backend; resumable restore and guided diff/merge tooling; never add in-place restore |
| Project start | Implemented | Read-only lint plus adoption/sync preview share one root-bound operation inventory; workstream selection remains explicit | Cold-start profiling and host-native entry experience |
| Project closeout | Evidence recording and interrupted-write recovery implemented | Exact reviewed plan/path/bytes and full session preview; ownership, evidence, recording, and execution-validation truth reported separately; sealed recovery inputs; commit-last file hashes; incomplete or hash-mismatched sessions excluded from learning | Host-native status UI; declared cwd/inputs/outputs/expected results; optional execution validation |
| Human project map | Partial | YAML plus `INDEX.md` remain readable | Regenerate versioned views from machine state without overwriting human notes |
| Project catalog | Implemented | Reads compact control planes, not raw history | Durable user-level catalog database and rename/move handling |
| Skill/preference learning | Review, evidence comparison, non-activating draft scaffold, and promotion implemented | Exact saved learning plan; session-pinned package hashes; privacy-preserving v3 packs; exact candidate digest/provenance; latest-decision acceptance gate; candidate draft uses a disjoint create-only envelope with `SKILL.md.draft` and commit-last baseline; promotion remains separate; no automatic activation or promotion | Explicit legacy migration and reviewed candidate supersession UI |
| Lightweight user preferences | Implemented in isolated store | Count/byte budgets; secret and absolute-path rejection; immutable generations; explicit replacement confirmation, application, and forward rollback | Ergonomic profile selection |
| User common-Skill store | Implemented | Root-bound ownership; v2 content addresses bind bytes, executable/permission modes, subdirectory modes, and empty subdirectories while the package root remains store-owned; portable-name collision rejection; stable descriptor verification; v1 read compatibility; complete saved-plan apply; expected-HEAD CAS; OS lock; commit-last copy; forward rollback; source/draft unchanged | Signed/exportable bundles, partial/orphan inspection, retention policy, and native host integration |
| Dynamic context runtime | Not implemented | Resolver is advisory | Token-budgeted loader and host integrations |
| CLI module loading | Implemented for built-in domains | Parsing, help, and version load only the lightweight CLI/error/version layer; each command imports its built-in domain after selection; user Skill code is never imported or executed | Measured cold-start budget and host integration profiling |
| Product health diagnostics | Implemented, read-only | `doctor` uses bounded stable reads, rejects links/reparse points, validates control schemas and current v2 package integrity, classifies corrupt/legacy/orphan/partial state, proposes retention only, and exposes a versioned durable-format registry plus truthful filesystem capabilities | Guided repair/migration plans must remain separate exact-plan workflows; no automatic cleanup |
| Scale baseline | Reproducible benchmark and CI comparison implemented | Temporary fixtures only; public read/preview APIs; complete fixture bytes/modes/mtimes hashed before and after; reference covers 10,000 files, 100 Skills, and 50 projects; same-runner baseline/current comparison | Validate the new CI jobs and accumulate runner history |
| Packaging | Existing CI checks plus new provenance and rollback jobs | Linux/Windows and Python 3.10-3.13 tests; wheel/sdist clean installs on Linux/Windows at Python 3.10/3.13 boundaries; exact package file/hash comparison during isolated current/previous/current installation; attestation checks pin source ref, commit and signer workflow | Validate new remote jobs and publish only artifacts from the reviewed tagged run |

## Release Gates

A stable release requires all of the following:

1. No overwrite, move, rename, or delete path for human-owned project or skill files in adoption, sync, learning, or closeout.
2. Symlink/junction/reparse escape tests on both Windows and Linux. Basic cross-platform link tests and Windows reparse detection exist; native junction creation coverage remains.
3. Fault injection for interrupted writes, permissions, disk exhaustion, source mutation, corrupt archives, and concurrent sync. The dedicated Linux CI job mounts a bounded tmpfs and tests real ENOSPC/EACCES before and after staging, then verifies reviewed recovery. Cross-platform unit and integration runs remain required; Windows ACL-specific fault drills are not claimed.
4. Immutable overlay generations with a stale-plan compare-and-swap check. Implemented with OS-lifetime locking, verified parent transitions, audited forward rollback, and relationship-checked released-transaction recovery; legacy lock migration tooling remains.
5. Statement coverage at least 85% and branch coverage at least 75%, with
   independent non-regression floors for safety-critical write-path modules.
6. Performance baselines for 10,000 files, 100 skills, and multi-project
   catalogs. CI compares 0.18.0 and current measurements on each Linux/Windows runner
   and retains results. Long-term runner history is still accumulating.
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
  only available on POSIX. Therefore 0.19 does not claim a cross-platform
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
