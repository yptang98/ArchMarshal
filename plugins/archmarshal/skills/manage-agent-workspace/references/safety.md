# ArchMarshal Plugin Safety Reference

## Non-negotiable invariants

- Existing project and Skill content is human-owned.
- Preview and read paths must not create directories or metadata.
- Existing-project adoption must retain its verified backup gate.
- Every managed Skill source file must have complete planned backup coverage;
  source-root declarations and each managed file path/bytes/mode/hash are bound
  into the exact reviewed plan.
- Exact package exclusions are project-relative, repeatable, and persisted in
  immutable history. They are evaluated before package content is read. An
  excluded package is not backed up, indexed, activated, learned from, or
  modified, and only an explicit exact manage request reverses the boundary.
- Standard VCS metadata, caches, virtual environments, dependency trees, and
  runtime/build artifacts are reported as preserved boundaries. Their paths
  may be shown, but their contents are not entered, backed up, indexed, learned
  from, moved, or deleted.
- Existing safe project layouts are mapped, not normalized. Detection never
  grants permission to move content or to publish a user preference. Unsafe
  destinations outside the project, across links/reparse points, inside
  VCS/cache/dependency/runtime boundaries, or colliding with files fail closed.
- A nested Skill package is a separate module boundary. Excluding it also
  excludes its bytes from any managed parent package fingerprint and backup.
- Apply must replay the complete reviewed plan and exact concurrency tokens.
- User-owned destinations are create-only; a collision is not a merge request.
- Partial, legacy, orphan, and corrupt state is retained for review. No doctor
  or plugin path performs automatic cleanup, migration, or repair.
- Imported Skills remain quarantined until exact package/routing approval.
- Candidate draft output is non-activating and contains `SKILL.md.draft`.
- Global policy and active record replacement require explicit, type-specific
  confirmation.

## Verification before apply

Record or compare the target tree's existing paths, file hashes, modes, and
mtimes in proportion to risk. Check:

- canonical ownership root;
- link, junction, and reparse rejection;
- plan digest and complete preview identity;
- expected Skill-index or user-store HEAD;
- committed pack/session/package hashes;
- destination absence and disjointness;
- backup descriptor-bound verification where required;
- additive effective Skill roots, exact Skill selection, preserved artifact
  boundaries, and `skill_backup_coverage.complete`;
- the complete effective layout, per-field provenance, confirmation state,
  timezone/date partition, and every human-review mapped path;
- activation and review state for every affected Skill.

After apply, confirm that only reviewed create-only or internal immutable paths
changed. A failure after publication must be reported as published/partial; do
not claim that nothing changed.

## Current filesystem boundary

The current backend protects against static link/reparse escapes, stale plans,
ordinary cooperating ArchMarshal processes, and many interruption/collision
cases. It is path-based, not a complete handle-relative backend. It does not
claim protection from a malicious same-permission process replacing ancestor
directories between validation and mutation. Use `doctor.filesystem_safety` as
the machine-readable truth and never silently upgrade this claim.

Windows does not yet have a native handle-relative/ACL backend. POSIX directory
fsync and no-replace behavior do not imply cross-platform power-loss or privacy
guarantees.

## Reporting

Tell the user:

- what was read;
- what exact scope is proposed;
- whether a backup exists and verified;
- which existing paths remain unchanged;
- what was created, if anything;
- whether a commit marker or HEAD was published;
- any partial state and its safe next action;
- any capability boundary relevant to the requested security claim.
