# 0.19 improvement plan

Baseline: released `v0.18.0` (`3451901`). This work develops the next version;
publishing a new release is a separate action.

## Recovery and capacity

- [x] Seal recovery inputs before sync import, Skill installation, or closeout writes.
- [x] Provide read-only stage/progress diagnostics and preview-bound recovery.
- [x] Resume missing files only; preserve and report changed files, unexpected entries,
  changed mappings, invalid ownership, and incomplete journals.
- [x] Archive context generations without deleting records or losing exact-ID reads,
  parent references, current heads, or merge evidence.
- [x] Exercise interruption, retry, concurrent changes, and capacity boundaries.

Evidence: [sealed journals](../src/archmarshal/operation_journal.py),
[reviewed recovery](../src/archmarshal/operation_recovery.py),
[archive storage](../src/archmarshal/context_store.py),
[recovery tests](../tests/test_operation_recovery.py), and
[archive tests](../tests/test_context_archive.py). Tests include offline replay,
changed source/output preservation, corrupt inputs, stale plans, archive capacity,
cold reads, and display names distinct from Skill frontmatter names.

## Workstream handoff

- [x] Provide explicit start/resume/save/finish guidance and stable stream selection.
- [x] Show last save, unfinished next steps, and stale evidence on entry and closeout.
- [x] Document the host boundary: no claim of automatic chat capture or invisible saves.
- [x] Verify a second process can recover state, decisions, and the next action.

Evidence: [workstream entry](../src/archmarshal/workstream.py),
[handoff persistence](../src/archmarshal/handoff.py),
[workstream tests](../tests/test_workstream.py), and
[parallel-device/CLI tests](../tests/test_portability.py), plus
[fresh-process handoff](../tests/test_workstream_process.py).
This validates the explicit persistence and selection contract, not a host UI or
automatic window identity. See [the workflow](workstream-and-reuse.md).

## Skill reuse quality

- [x] Add task fixtures and measure recommendation precision/omissions.
- [x] Explain recommendation evidence and promotion requirements.
- [x] Detect duplicate learning candidates without discarding their evidence.

Evidence: [26-task bilingual corpus](../tests/fixtures/recommendation_tasks.json),
[evaluator](../scripts/evaluate_recommendations.py),
[matching and shared-inventory tests](../tests/test_recommendation_quality.py),
and [candidate evidence tests](../tests/test_candidate_evidence.py).
The curated corpus is regression evidence; representative production quality
measurement remains future work. Candidate comparison preserves every pack ID and
does not transfer acceptance between candidates.

## Performance and structure

- [x] Reuse one operation-scoped inventory where repeated scans currently occur.
- [x] Extract cohesive storage/adoption/CLI responsibilities with preserved contracts.
- [x] Turn the existing scale benchmark into a bounded CI regression check.

Evidence: [lifecycle](../src/archmarshal/lifecycle.py),
[adoption backup](../src/archmarshal/adoption_backup.py),
[candidate CLI](../src/archmarshal/candidate_cli.py),
[performance gate](../scripts/check_benchmark.py), and
[gate tests](../tests/test_benchmark_gate.py). The new Linux/Windows scale jobs
passed on Linux and Windows in [remote CI](https://github.com/yptang98/ArchMarshal/actions/runs/42828262520).

## Release assurance

- [x] Prepare verifiable build provenance and artifact attestation in release automation.
- [x] Exercise rollback with explicit byte/hash verification.
- [x] Run real filesystem permission and disk-capacity failure integration tests in CI.
- [x] Refresh product readiness, version metadata, engine lock, and user documentation.
- [x] Repeat full-suite and package checks after the native Skill reconciliation changes.

Each completed item must link to implementation and repeatable validation. Limits,
unsupported legacy recovery, and platform-specific checks must be stated explicitly.

Evidence: [provenance workflow](../.github/workflows/provenance.yml),
[isolated rollback drill](../scripts/package_rollback_drill.py),
[real filesystem integration](../tests/integration/test_filesystem_failures.py),
[release procedure](release-process.md), and [readiness boundaries](product-readiness.md).
Local Windows/Python 3.11 wheel and sdist clean installs and the
0.19.0/0.18.0/0.19.0 rollback passed. On commit `db35774`, remote Linux/Windows
rollback and package checks passed, the [real Linux filesystem job](https://github.com/yptang98/ArchMarshal/actions/runs/42828262520/job/129871171296)
ran all four ENOSPC/EACCES cases without skips, and [build attestation](https://github.com/yptang98/ArchMarshal/actions/runs/42828253497)
verified signatures, source ref, commit and signer workflow. The disposable tmpfs
is bounded at 128 MiB so fixture adoption retains the unchanged 64 MiB backup reserve
before the test deliberately fills it. The first remote run also exposed Linux
path/fixture differences and missing sync rejection coverage; follow-up commits
address these without reducing coverage floors. See [PR checks](https://github.com/yptang98/ArchMarshal/pull/2/checks)
for the full matrix at the current head. No new release is published.

The current native-compatibility snapshot passed Windows / Python 3.11 verification:
full suite 614 passed and 19 skipped, including fresh-process handoff, external
delivery and a real Codex protocol integration. Statement coverage is 87.89% and branch coverage 77.01%; every
existing and newly added module gate passed. Wheel/sdist metadata, clean installs
and the current/0.18/current rollback passed again. Fresh same-runner 0.18.0/current scale
comparisons passed for 10,000 files, 100 Skills and 50 projects, with unchanged
fixture hashes. These local results complement the remote evidence above; they
do not substitute for checking the full matrix at the current PR head.

## Codex compatibility direction (user steering, 2026-11-10)

Source, development documentation and runtime files remain in the actual Codex
project/worktree. The earlier default source location under
`D:\archmarshal\projects` is cancelled. The user's latest instruction places
**real formal deliverables** under the matching external record directory's
`artifacts` versions. Chat contains a summary and file links, not the sole copy.
Existing files and native management state remain in place.

The local `D:\archmarshal\tools\project_records.py` adapter records the registered
project root, actual working root and known Codex project/chat IDs. Its external
records are **not** native ArchMarshal ownership, backup, Skill-index or closeout
commits. Installed 0.18.0 diagnostics remain read-only under this workflow.
Native adoption and lifecycle writes are no longer a default action.

Current implementation and remaining milestone:

- [x] Resolve project and checkout identity from explicit Codex context and Git;
  retain separate registered-root and actual-worktree bindings, and report unknown
  associations instead of guessing from a shared remote URL or directory name.
- [x] Add an explicit external storage backend with exact-plan path binding and
  compatibility tests. Preserve existing root-bound native state rather than
  copying ownership markers into another checkout or reinterpreting old records.
- [x] Keep Skill authoring/review/version evidence separate from native discovery and
  enabled state. Verify the host-visible installed version after an authorized
  activation; no second implicit Skill loader or global promotion.
- [x] Copy selected deliverable bytes into immutable external artifact versions;
  record source paths, hashes, sizes, producing chat and validation evidence.
  Publish the history record only after every selected file verifies. Preserve
  partial copies and changed output; exact-plan retries never overwrite them.
- [x] Route chat resume/fork/handoff through available native host tools in the Skill workflow. External
  summaries supplement project decisions and evidence. Native lifecycle hooks,
  if supported and reviewed, are a later optional adapter with bounded work and
  duplicate-event handling, not a prerequisite for the first milestone.

Acceptance scenarios must cover a normal project, a detached worktree, two chats
in one project, two same-name projects, a changed project location, an unavailable
external record directory, and a Skill whose native enabled state differs from
ArchMarshal review state. Preserve the original release-assurance work above;
native compatibility is the newly prioritized product direction.

Implemented in [project binding](../src/archmarshal/project_context.py),
[external records and delivery](../src/archmarshal/external_records.py), and
[regression tests](../tests/test_external_records.py).
See [the external project workflow](codex-projects.md) for the current limits.
This is development code; it does not update the installed 0.18.0 plugin or
silently migrate existing `.agent` state. Native Skill reconciliation is implemented
in [host observations](../src/archmarshal/host_skills.py) and the
[bounded CLI probe](../src/archmarshal/codex_skill_probe.py), with
[tests](../tests/test_host_skills.py) and [usage boundaries](codex-skills.md).
Native enablement is observed separately from metadata approval and explicitly
selected source/installed-copy hashes. A real Codex 0.146.0 probe discovered a
temporary repository Skill and enabled ArchMarshal 0.18.0. It does not prove an
existing desktop chat refreshed or invoked those Skills. Chat routing is guidance
for host-provided tools, not a new conversation runtime; optional lifecycle hooks
remain unimplemented. Local delivery manifest compatibility is covered by the
external-record tests and does not relabel old files as native commits.

Official references checked for this design:
[worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees),
[Skill discovery](https://learn.chatgpt.com/docs/build-skills),
[AGENTS.md precedence](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
and [lifecycle hooks](https://learn.chatgpt.com/docs/hooks).
