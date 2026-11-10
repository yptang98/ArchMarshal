# Workstream handoff and Skill reuse

Use `context-start PROJECT --task TEXT` to inspect current workstreams. The result
shows last-save time, age, unfinished next steps, and evidence that needs review.
An explicit `--stream NAME` selects one stream; the same filter is available on
`context-resume`. With multiple active streams, choose the intended one. Suggested
names are deterministic hints, not host window IDs or implicit stream assignments.

Before switching windows or compressing context, preview `context-save ROOT
--stream NAME --summary TEXT` with decisions, reasons, constraints, rejected options,
next steps, evidence, and relevant file paths. Apply the exact preview digest.
Finish through `context-finish ROOT --stream NAME --summary TEXT`; this appends a
completed record. `end` reports streams that remain active. Nothing automatically
captures private conversations or saves unobserved work.

Skill recommendation now requires a declared trigger or tag match before priority
affects ranking. Latin triggers use token boundaries, while CJK phrases can match
within continuous text. Matches explain trigger/tag evidence and the priority
contribution. Required global policy remains a separate result. This is deterministic
matching, not semantic intent detection or a guarantee that a Skill is appropriate.

Run `python scripts/evaluate_recommendations.py` for the fixed English/Chinese task
corpus. It reports unexpected suggestions, omissions, precision, and recall and
fails on changed expectations. Corpus results are regression evidence, not a
measurement of production recommendation quality.

For overlapping learning packs:

```sh
archmarshal candidate-compare ROOT --pack FIRST_PACK --pack SECOND_PACK
```

The read-only comparison groups common Skills by ID and implementation digest,
and preferences by key/value. It retains every member's pack/candidate identity,
unions pinned evidence references, and distinguishes duplicate, extended,
complementary, and conflicting evidence. Compare at most 20 explicit project
learning packs, bounded to 32 MiB and 5,000 candidates. No pack or review decision
is merged, replaced, or deleted. A different implementation remains a separate variant.

Candidate review/promotion previews explain claimed observations and actual pinned
references separately. Promotion still requires acceptance of the exact candidate
digest and provenance, a reviewed Skill draft where applicable, the current user-store
HEAD, and explicit replacement confirmation when needed. Repeated evidence does not
prove successful execution, and acceptance never transfers between candidate digests.
