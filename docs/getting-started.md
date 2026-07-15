# Getting Started

Use ArchMarshal as a Python CLI that Codex or a human invokes in the project.

## 1. Install

```bash
python -m pip install "git+https://github.com/yptang98/ArchMarshal.git@<reviewed-full-commit-sha>"
archmarshal --help
```

## 2. Initialize or adopt

For a new project, preview and then apply the dedicated initialization plan:

```text
archmarshal init . --tag research --pretty
archmarshal init . --tag research --expect-plan <plan_digest> --apply --pretty
```

Initialization adds the normal `.agent/` control plane and creates only the
missing project Skill scaffold paths:

```text
.agents/skills/README.md
.agents/skills/project/.gitkeep
.agents/skills/generated/.gitkeep
```

Existing files at those paths are preserved byte-for-byte. A linked ancestor,
file/directory collision, stale plan, or path that appears after preview stops
publication without rewriting the path. Ordinary adoption does not create this
scaffold implicitly, so use `init` when the project should follow the complete
layout from its first ArchMarshal-managed run.

For an existing project, preview the management overlay:

```text
archmarshal adopt . --tag research --pretty
```

After reviewing the file list and conflicts, apply it:

```text
archmarshal-start . --apply --expect-plan <plan_digest> --tag research --pretty
```

This creates a verified backup and then adds only missing control-plane files.
Existing project files, `SKILL.md`, and source skill manifests are not changed.
Skill routing metadata lives in `.agent/skill-overlays/`. Later syncs create
immutable, content-addressed generations and atomically advance only
ArchMarshal's internal `HEAD` after an exclusive lock and stale-plan check. The
digest binds the write to the exact previewed file bytes and skill-index plan;
missing or stale digests write nothing.

For a project that is already managed, type:

```text
archmarshal-start
```

ArchMarshal checks save paths, naming, memory/history rules, and then Codex can
keep using it quietly while you give normal project instructions.

If start reports a modified, removed, or restored skill, inspect the preview
before adding `--apply --expect-plan <plan_digest>`. A concurrent or stale
preview is rejected. ArchMarshal does not edit, move, or delete the source skill
during either preview or apply.
Drifted skills are also excluded from resolver activation until this review is
completed.

Skills imported from an existing project start quarantined. Adoption exposes
their optional raw `source_declared_status` and validated
`normalized_source_status` separately from `review_state` and effective
`activation_state`, and returns HEAD-bound review actions. Preview and then
apply an exact review decision before use:

```text
archmarshal skill-review . --source skills/example --decision approve \
  --expect-head <head> --pretty > skill-review.json
archmarshal skill-review . --source skills/example --decision approve \
  --expect-head <head> --plan-file skill-review.json \
  --expect-plan <plan_digest> --apply --pretty
```

Global/highest policy also requires `--allow-global-policy` in both calls.
Apply uses the exact saved generation and review timestamp from preview; it
fails before backup if the plan, generation bytes, digest, or object path differ.

An interrupted adoption is not rolled back by deleting visible paths. Inspect
and safely finish it instead:

```text
archmarshal adoption-status . --pretty
archmarshal adoption-recover . --pretty
archmarshal adoption-recover . --expect-transaction <transaction_id> \
  --expect-plan <plan_digest> --apply --pretty
```

Recovery is forward-only and create-only. Any target that no longer matches the
prepared hash blocks recovery and is preserved exactly as found.

Use `archmarshal skill-index-status . --pretty` to verify the full reachable
generation chain and inspect the process-lock state. Metadata rollback is also
preview-first: preview `skill-index-rollback --to <ancestor>`, then apply only
with the exact `--expect-head` and `--expect-plan` from that preview. It creates
a new audited generation and does not restore source skill files.

Then give normal instructions, for example:

```text
Build the release checklist.
Analyze the benchmark result.
Prepare the project report.
```

During the project, checkpoints should preserve what must survive context
compression. Summaries are indexes, not replacements: raw reports, plans,
checkpoints, notes, and history stay preserved.

## 3. End

Choose one of three explicit depths:

```text
archmarshal-end . --level quick --summary "Routine phase complete"
archmarshal-end . --level quick --summary "Routine phase complete" \
  --expect-plan <plan_digest> --apply

archmarshal-end . --level standard --summary "Release checked" \
  --step "Run tests" --script scripts/check.py
# Review, then repeat with --expect-plan <plan_digest> --apply.

archmarshal-end . --level reproducible --summary "Benchmark reproduced" \
  --step "Prepare inputs" --step "Run benchmark" \
  --script scripts/benchmark.py --command "python scripts/benchmark.py"
# Review, then repeat with --expect-plan <plan_digest> --apply.
```

The commands preview unless both `--apply` and the exact `--expect-plan` are
present. Applied records go into a new date-organized history directory and are
trusted by learning only after a final `COMMITTED.json` verifies every declared
file. Reproducible mode snapshots key scripts and does not claim readiness while
required evidence is missing; even a ready record is reference-only until its
commands are actually validated by a future execution feature.

Existing v1 closeouts have no commit marker. Learning reports them through
`legacy_unverified_session_count` but does not trust them automatically; an
explicit migration workflow is still planned.

After repeated sessions, create a review-only learning pack:

```text
archmarshal learn . --include-root ../another-project --pretty > learning-plan.json
archmarshal learn . --include-root ../another-project \
  --plan-file learning-plan.json --expect-plan <plan_digest> --apply --pretty
```

Promotion is optional. Initialize a separate user store, save its preview as
UTF-8 JSON, and apply only that complete plan:

```text
archmarshal user-store-init <store> --pretty > init-plan.json
archmarshal user-store-init <store> --plan-file init-plan.json \
  --expect-plan <plan_digest> --apply

archmarshal candidate-review . --pack <committed-pack> \
  --candidate <id> --decision accept --reason "approved evidence" \
  --user-store <store> --pretty > acceptance-plan.json
# Repeat candidate-review with the saved --plan-file, exact --expect-head,
# exact --expect-plan, and --apply.

# The reviewed draft manifest must declare promotion.candidate_id,
# candidate_digest, source_skill_id, and source_implementation_sha256 exactly.
archmarshal candidate-promote . --pack <committed-pack> \
  --candidate <id> --draft <reviewed-skill-draft> \
  --user-store <store> --pretty > promotion-plan.json
# Repeat candidate-promote with the saved --plan-file, exact --expect-head,
# exact --expect-plan, and --apply.

archmarshal-start . --task "prepare release" --user-store <store> --pretty
```

Preference candidates omit `--draft`. The store holds copies and immutable
metadata; the source project, candidate pack, and draft remain unchanged.
Rejected, deferred, or unreviewed candidates cannot be promoted.
Replacing an active record with the same Skill id or preference key also
requires `--replace-existing-skill` or `--replace-existing-preference` in both
preview and apply.

New common-Skill copies use package format v2. The content address covers file
bytes, permission and executable modes, all subdirectory modes, and empty
subdirectories. The package root remains store-owned. Non-portable Windows
names, Unicode-normalization/case collisions, links, and unusable owner
permissions are rejected rather than normalized. Committed v1 packages remain
readable and are not rewritten automatically; unapplied pre-0.12 promotion
previews must be regenerated.

## Rule

Summaries are only indexes. Keep the original reports, plans, checkpoints, and
notes preserved.

The read-only closeout recommends a depth. Writing still requires the user to
choose quick, standard, or reproducible evidence explicitly.

See [Safe Adoption And Lifecycle Recording](safe-lifecycle.md) for backup,
conflict, overlay, and no-overwrite guarantees.
