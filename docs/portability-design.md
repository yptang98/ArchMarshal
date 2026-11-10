# Device portability and parallel project context

Implementation target: 0.18.0. Base: de9d26a89445f9a5a2fc303a0ca7da3bbee6965f.

## Requirements

1. Review and improve existing project/Skill management without rewriting user sources.
2. Move complete Skill packages between machines, preserving explicit device provenance.
3. GitHub is the default transport; storage is pluggable and a folder transport supports
   mounted cloud drives and offline testing. Never embed credentials in portable data.
4. Concurrent versions coexist; retries are idempotent, corruption and stale plans fail
   closed, imports remain quarantined until separately reviewed.
5. Persist per-workstream context (goal, summary, decisions, next steps, risks, files,
   validation evidence), restore by project directory, and combine multiple workstreams
   without losing either original. Include explicit other checkout roots when needed.
6. Restore reports changed files, divergent branches and overlapping work. Recorded
   summaries are evidence, never higher-priority instructions or proof of execution.
7. Integrate the plugin workflow and start command, document CLI and limitations, test
   round trips, concurrent work, failure boundaries, packaging and existing regressions.

## Design

- Device identity is an explicit, immutable UUID/name profile outside project sources.
  It records self-declared origin, not hardware attestation. Copied profiles must not be
  reused as the identity of a new machine.
- Sync stores immutable content-addressed package envelopes. Separate devices and
  differing versions never share a mutable remote pointer. Select exact object IDs to
  import; never pick a winner by wall-clock timestamp. All package files and provenance
  are covered by the object digest. Transports implement list/read/create-only put.
- Use only indexed, non-excluded Skill sources; respect nested package and artifact
  boundaries. Pull materializes a new isolated inbox package with an integrity receipt,
  and never writes to an active Skill root. Activation is a separate existing review.
- Context is an append-only journal with independent stream ancestry and exact-plan
  preconditions. Records are bounded and hashed. A combined checkpoint cites source
  record IDs; source streams remain readable. Start automatically returns latest context.
- Local context stays local. Skill sync never uploads project context or whole projects.

## Verification checklist

- [x] Existing implementation review and findings
- [x] Device identity and portable envelopes
- [x] GitHub and folder transports; conflict/idempotency/tamper tests
- [x] Skill export, import, provenance retention, quarantine and reviewed activation
- [x] Context save, restore, merge and stale/overlap detection
- [x] CLI, start, doctor and plugin integration
- [x] Docs, version and engine lock
- [x] Full local tests, coverage gates, lint, package smoke and requirement audit

## Local verification

Windows / Python 3.11: 423 tests passed, 13 platform-dependent skips. Statement
coverage 87.01%, branch coverage 75.42%; every existing CI module gate passed.
Ruff, version consistency (including the previously unchecked runtime launcher),
engine-lock verification, dependency-free plugin bootstrap and example lint passed.
Wheel and sdist built, passed metadata checks and installed in clean environments.
The wheel's Python source bytes were compared against the current working tree.

`tests/test_portability.py` covers device persistence, two-device roundtrip through
quarantine/install/approval/re-export, provenance retention after edits, preserved
assets/empty directories, content conflicts, retries, exclusion boundaries,
malformed paths/manifests, HTTP authentication/error handling, concurrent create,
multiple workstreams, staleness/overlap, cross-checkout synthesis, known GitHub
address lookup, bounded retrieval, historical reads and CLI workflows.

The GitHub backend completed an authenticated read-only check against the source
repository. Write protocol behavior is covered with a controlled GitHub API fixture;
no user Skill package was uploaded to a public repository for testing. Device
labels are self-declared, and context restoration covers recorded handoffs rather
than unsaved private conversations. See portability.md for explicit resource limits.
