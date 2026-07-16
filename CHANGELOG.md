# Changelog

## 0.15.0 - 2026-07-16

- Added repeatable exact Skill-package exclusions with immutable persistence,
  explicit per-package restoration, previewed management boundaries, and no
  backup/index/activation/learning access to excluded packages.
- Made VCS metadata, caches, virtual environments, dependency trees, and
  runtime/build outputs preserved artifact boundaries so they no longer block
  safe management of the remaining Skill source.
- Added a dedicated Codex update prompt while keeping the install prompt
  idempotent across first install, verified update, and exact-version no-op.
- Updated the Codex Skill workflow to show all proposed management directories
  first and request a preserve-or-exclude decision when artifact boundaries are
  present.
- Fixed the Skill manifest schema contract for generated portable package
  boundaries so newly adopted overlays validate and lint consistently.

## 0.14.1 - 2026-07-16

### Prompt-first installation documentation

- Replace the unusable reviewed-SHA placeholder in the primary user journey
  with a complete Codex prompt that resolves and verifies an immutable commit,
  handles first install or guarded update, protects local checkouts, backs up
  Codex-managed ArchMarshal state, and verifies bootstrap plus a read-only
  dependency smoke test without touching the current project.
- Refocus the README and getting-started guide on the Codex-native plugin
  experience. Move raw CLI detail behind maintainer documentation, correct the
  duplicated quick-start numbering and misleading UI/global-state boundaries,
  and explicitly distinguish documented CostMarshal compatibility from a
  future first-class bridge.
- Keep the public README, standalone installation prompt, and getting-started
  guide English-only, with a regression check that rejects Han-script content
  in those primary distribution documents.

### Isolated plugin runtime selection

- Add a stdlib-only plugin launcher that defaults to the active Python but can
  consume a bounded, version- and commit-scoped runtime pointer below
  `CODEX_HOME`. The launcher rejects linked, stale, malformed, out-of-root, or
  missing interpreters, invokes Python in isolated mode, and never provisions
  dependencies during project work.
- Teach the Codex Skill to use this launcher so the installation prompt can
  repair missing dependencies in an isolated runtime without changing system
  Python or any project. Engine source identity remains locked to the installed
  marketplace and never falls back to the package installed in that runtime.

## 0.14.0 - 2026-07-15

### Codex plugin identity and distribution

- Position ArchMarshal explicitly as a Codex management plugin and give its
  repository marketplace the unique `archmarshal` identity. Installation now
  uses `archmarshal@archmarshal` rather than a generic personal-marketplace
  selector.
- Add a generated plugin engine lock that binds the engine version, API, exact
  source-file set, byte counts, and source-tree hash. The wrapper verifies this
  lock before importing the engine, exposes a dependency-free bootstrap status,
  rejects ambiguous marketplaces and tampered same-version source, and never
  falls back to an ambient Python package.

### Complete Skill discovery and backup coverage

- Add repeatable, additive `--skill-root` support for project-relative
  nonstandard Skill locations and discover plugin-bundled Skills under
  `plugins/` by default. Effective source roots are recorded in the managed
  plan, while nonstandard explicit roots are recorded in the managed workspace
  so later starts preserve the discovery boundary without moving source content.
- Upgrade adoption plans to v2. Exact preview/apply now binds effective roots
  plus each planned backup file's path, bytes, portable mode, and SHA-256.
  Adoption reports per-package coverage and stops unless every file in every
  discovered Skill package is included in the verified backup. The published
  backup manifest must also match the exact reviewed source records before any
  control-plane target is created.
- Extend current project Skill package fingerprints to cover permission modes,
  so mode-only drift is visible on platforms that preserve those bits. Legacy
  v1 package and Skill-index content digests remain readable and migrate
  naturally on a later adoption. Existing source Skills and project files
  remain byte-for-byte in place.

### Honest closeout readiness

- Report workspace ownership, evidence completeness, recording authorization,
  and execution validation as separate closeout facts. Unowned projects no
  longer appear ready for recording, the exact proposed session is visible for
  review, and reference commands continue to report
  `execution_validated: false`.

## 0.13.0 - 2026-07-15

### Codex-native plugin product

- Make the Codex plugin the primary user experience. Add a validated
  repository marketplace, `archmarshal` plugin manifest, and
  `manage-agent-workspace` Skill with UI metadata, natural-language routing,
  progressive workflow/safety references, and no placeholder content.
- Add a fail-closed plugin wrapper that uses the same checkout during
  development or locates the matching engine in Codex's configured full Git
  marketplace snapshot after installation. Plugin and engine versions must
  match exactly; no dependency is installed or upgraded automatically.
- Keep the Python CLI as the deterministic transaction engine and automation
  interface rather than presenting it as a separate primary product. The Skill
  drives health checks, preview review, backup scope, exact plan/HEAD replay,
  post-apply verification, closeout depth, and candidate learning directly from
  user intent in Codex.

### Candidate-to-draft workflow

- Add `candidate-draft`, a preview-first, exact-plan scaffold for an accepted
  common-Skill candidate. The plan binds the committed learning pack bytes,
  canonical candidate and provenance, exact accepted decision and user-store
  HEAD, absent disjoint destination, and every proposed output byte.
- Publish only a new review envelope, with `REVIEW.md` outside the nested Skill
  package and `COMMITTED.json` last. The nested package contains
  `SKILL.md.draft`, not `SKILL.md`, so an unfinished scaffold cannot be
  discovered as a Skill merely because a host scans the destination. Human
  completion, explicit rename and activation, and a separate promotion
  preview/apply remain mandatory.
- Preserve source project, learning pack, and user store byte-for-byte. A link,
  collision, stale plan/HEAD/decision, or changed evidence stops before
  publication; interrupted partial output remains inspectable and is never
  overwritten or deleted on retry.

### Read-only product health

- Add `archmarshal doctor`, a deterministic, bounded, strictly read-only report
  for ownership binding, packaged control-plane schemas, adoption
  transactions, Skill-index and user-store chains, closeout commits, current
  v2 package content/topology/mode integrity, and absent, corrupt, legacy,
  orphan, or partial state. Retention suggestions never perform an automatic
  action.
- Add a versioned durable-format registry with owner, readable/writable
  versions, legacy/migration state, and boundedness, including candidate-draft
  plan, saved preview, binding, and final commit formats.
- Report filesystem safety capabilities without overstating the current
  backend: static link/reparse rejection and stable reads are implemented, but
  handle-relative components and hostile concurrent ancestor-rebinding
  protection remain false under the cooperative-only write threat model.

### Scale and release evidence

- Add a bounded temporary-fixture benchmark for 10,000-file inventory,
  100-Skill adoption preview, and 50-project catalog reads. It hashes fixture
  bytes, modes, and mtimes before and after and fails if a benchmarked read path
  mutates the fixture.
- Document the filesystem capability contract, handle-relative backend
  migration order, deterministic security tests, measured Windows reference
  timings, and the remaining stable-release gates.

## 0.12.0 - 2026-07-15

### Safe project and Skill initialization

- Add an explicit `archmarshal init` preview/apply workflow for new projects.
  It uses the same exact-plan, verified-backup, durable create-only adoption
  transaction and creates only missing `.agents/skills/` guide, project, and
  generated-draft scaffold files. Existing paths are preserved byte-for-byte;
  file/directory conflicts and linked ancestors stop before publication.
- Report imported Skill state truthfully as source-declared status, review
  state, and effective activation state. New valid imports are quarantined
  until a separate exact-package approval, invalid imports are disabled, and
  structured next actions bind the proposed or current Skill-index HEAD.

### Safety and operability

- Verify each backup manifest, expanded member, archive size, and archive hash
  through one stable open descriptor, and reject path replacement during
  verification. Backup publication now reports bytes and hashes from the
  descriptor-bound verification of the published path.
- Publish new user common-Skill packages with a v2 content address that binds
  file bytes, executable and permission modes, subdirectory modes, and empty
  subdirectory topology. Portable path validation rejects Windows-reserved or
  invalid components plus Unicode/case-fold collisions; commit markers remain
  last and v1 packages remain verifiable without migration.
- Regenerate any unapplied pre-0.12 common-Skill promotion preview after
  upgrading. Exact-plan apply intentionally rejects its v1 package fingerprint;
  already committed v1 packages remain active and verifiable.
- Verify package files and commit markers through stable descriptors, bound
  reads, and repeat topology checks. Oversized or mismatching partial packages
  fail before content hashing and are never repaired by overwriting an existing
  path.
- Load CLI domain modules only after argument parsing and command selection.
  Help and version requests no longer initialize YAML, JSON Schema, backup,
  adoption, lifecycle, session, or user-store domains; user Skill code is still
  never imported or executed.

## 0.11.0 - 2026-07-15

### Breaking safety changes

- `skill-review --apply` now requires the complete saved preview through
  `--plan-file`. The reviewed plan binds the review timestamp, complete
  immutable generation, proposed HEAD, object path and bytes, validation,
  source preconditions, and all decision inputs; apply publishes exactly that
  generation or stops before backup.

### Safety and operability

- Parse adoption recovery journals from the same bounded, stable descriptor
  bytes used for their SHA-256 check, and retain the ACTIVE marker identity so
  replacement conflicts stop finalization instead of deleting new state.
- Keep POSIX restore staging roots at verified mode `0700` while extraction is
  incomplete. Recorded root permissions are applied only after atomic
  no-replace publication, and a post-publication permission failure is reported
  as published-but-incomplete rather than being misreported as private staging.
  Windows reports stable staging identity without claiming ACL privacy.
- Stop managed adoption backups from recursively embedding prior backups,
  transactions, history, inbox, and cache while retaining rollback-critical
  control state, Skill-index recovery records, and complete non-root Skill
  packages. Full-workspace backup remains available when broad history is
  intentionally required.
- Return invalid CLI usage through the versioned JSON error contract on stderr
  with exit code 2; help and version output remain plain text.
- Add independent cross-platform statement and branch coverage floors for
  safety, adoption recovery, locking, Skill review/indexing, closeout, and
  user-store modules in the Linux/Windows CI matrix. Platform-specific lock
  branches are exercised by their native matrix side.

## 0.10.0 - 2026-07-15

### Breaking safety changes

- `learn --apply` now requires the complete saved preview through `--plan-file`
  and its exact `--expect-plan`; digest-only or unreviewed learning writes stop.
- Common-Skill drafts promoted from learning packs must declare exact candidate
  and source lineage. Replacing an active Skill id or preference key requires
  the matching type-specific replacement flag in preview and apply.
- User-store status uses the `initialized_empty` state for an owned store with
  no active generation and publishes a versioned status payload.
- Restore with `--rebind-workspace` accepts only internally scanned
  full-workspace backups that preserve portable root, directory, empty-directory,
  and file modes and contain the minimum complete ArchMarshal control plane.
- New promotion generations may only reference the latest exact acceptance in
  their parent generation; accepting and activating a candidate in one
  generation is rejected.

- Harden backup and restore boundaries: reject linked destination ancestors and
  NTFS alternate data streams, enforce actual streamed byte limits, bind apply
  to exact archive bytes and destination, and preserve incomplete output for
  inspection in private staging. Successful restore uses atomic no-replace
  directory publication, so nested extraction paths are never exposed at the
  requested destination while files are being written. An explicit
  `--rebind-workspace` restore option verifies the old
  root-bound ownership marker, backs it up inside the new copy, and atomically
  binds only the restored copy to its new root.
- Bound YAML parsing by bytes, aliases, nodes, and depth while preserving ISO
  date compatibility; recursive aliases and non-finite/complex values are
  quarantined rather than crashing project discovery.
- Parse committed learning/session YAML from the same bounded stable bytes used
  for size and SHA-256 verification, and bound reviewed-plan reads against file
  replacement or growth.
- Require the latest decision for an exact candidate digest and provenance to
  be accepted before promotion. Common-Skill drafts must declare exact
  candidate/source lineage, so a reviewed candidate cannot authorize an
  unrelated draft. Rejected and deferred candidates remain blocked.
- Require explicit type-specific confirmation before replacing an active
  user-store Skill id or preference key; the reviewed plan records that intent.
- Bind learning-pack creation to a complete saved preview and exact plan digest;
  changed evidence, candidate bytes, commit bytes, roots, or target paths stop
  before publication.
- Prevent draft/package destination overlap and keep source projects, source
  Skills, and reviewed drafts byte-for-byte unchanged across promotion.
- Resolve same-id workspace/user-store Skills deterministically with local
  workspace precedence and an explicit conflict record; newly discovered or
  restored unindexed Skills are surfaced as blocked sync work rather than
  disappearing from task resolution.
- Preserve existing project tags during incremental start, distinguish
  governance/project/task readiness, and report the narrow mutation performed
  by `start --apply` instead of describing it as read-only.
- Add discoverable user-store generation history and current decision summaries,
  PowerShell UTF-16 saved-plan compatibility, privacy-preserving learning-pack
  v3 repeated-script sources, versioned CLI JSON envelopes, and `--version`
  support on all three CLI entrypoints.
- Stream Skill fingerprints through descriptor identity and post-read checks,
  enforce entrypoint-only scans for root Skills, and expand regression coverage
  for source growth, archive replacement, Windows junctions/streams, unsafe
  topology, review bypass, resolver conflict, and restored-workspace rebind.

## 0.9.0 - 2026-07-15

- Quarantine every Skill discovered during adoption until its exact package and
  routing subject pass Codex Skill package validation and an explicit
  `skill-review` decision. Elevated global/highest policy needs a separate
  confirmation; any package or routing change invalidates the approval.
- Hold a root-bound OS-lifetime workspace lock across backup, adoption, Skill
  review, closeout, and learning publication. Closeout and learning writes now
  require a valid ownership marker and cannot claim an unmanaged project.
- Pin each recorded Skill use to its package hash, routing digest, index HEAD,
  and review state. Cross-project learning uses the historical session-bound
  package rather than attributing old evidence to current source bytes;
  unreviewed, rejected, drifted, or otherwise resolver-blocked usage is excluded
  from promotion evidence.
- Commit date-organized learning packs with a final hash marker and reject
  incomplete, moved-outside-workspace, linked, or tampered candidate evidence.
  Persist only root-bound workspace identities and relative evidence paths in
  packs rather than absolute project paths.
- Add an isolated, root-bound user Skill store with immutable Skill packages,
  immutable generation history, bounded preferences, OS locking, exact-plan and
  expected-HEAD publication, commit-last package copies, and forward-only
  rollback. Linked path components and concurrent initialization claims are
  rejected; crash-orphan staging files cannot contaminate a package. Human
  project and draft sources are never modified.
- Add `candidate-review` and `candidate-promote` workflows. Apply requires the
  complete saved preview plus its exact plan digest and HEAD token; common Skill
  promotion binds the exact reviewed draft directory, while preference
  promotion binds the exact committed candidate value.
- Keep raw candidate decision/promotion primitives internal so the supported
  activation path always begins with a verified committed learning pack.
- Add optional user-store resolution to `resolve` and task-aware `start`.
  Verified user common Skills remain task-triggered and project Skills take no
  mutation dependency on the store.
- Validate Skill frontmatter, package layout, optional UI metadata, bounded file
  sizes, and progressive-disclosure directories without executing Skill scripts.
- Add end-to-end and fault-boundary tests covering unowned workspaces,
  unreviewed/global activation, package drift, evidence tampering, stale plans,
  concurrent locks, source preservation, cross-project promotion, and rollback.

## 0.8.0 - 2026-07-15

- Bind adoption and closeout writes to the exact reviewed preview through a
  SHA-256 plan digest; `--apply` without `--expect-plan` writes nothing.
- Add a durable, create-only adoption journal with staged payload hashes,
  backup verification, an OS-lifetime lock, forward recovery, and a receipt
  written last. Recovery never overwrites or deletes a changed target.
- Add `adoption-status` and preview-first `adoption-recover` commands for
  inspecting and completing interrupted adoption transactions. Apply requires
  the exact reviewed transaction id and plan digest.
- Atomically publish new files and backups through exclusive hard links, fsync
  data and POSIX directory entries, and preserve uncertain restore/closeout
  output instead of recursively deleting paths that another process may own.
- Validate the complete reachable skill-index chain, quarantine loose
  unindexed overlays, block readers during publication, reject portable path
  aliases, and verify persistent lock-file identity throughout commits.
- Preserve supported source-manifest routing fields in generated overlays;
  invalid imported metadata is disabled and marked for review while source
  files remain unchanged.
- Add a root-bound ownership marker whose Skill-index mode must agree with the
  workspace; required indexes never fall back to loose metadata when HEAD is
  missing, including during unchanged repair-transaction recovery.
- Commit closeout sessions with a final hash manifest. Learning ignores
  incomplete or hash-mismatched sessions, reports legacy v1 sessions as
  unverified rather than silently trusting them, and keeps reproducible
  closeouts reference-only until execution validation exists.
- Bind metadata rollback to both the reviewed HEAD and exact logical rollback
  plan, preventing a reviewed ancestor/reason from being swapped at apply time.
- Add fault-injection tests for interrupted adoption, changed targets, journal
  and backup tampering, receipt-finalization recovery, lock replacement,
  incomplete sessions, and concurrent file replacement.

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
