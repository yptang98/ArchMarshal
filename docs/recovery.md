# Interrupted operations and context capacity

New sync imports, Skill installations, and closeout recordings seal their inputs
under `.agent/operations/<id>` before creating outputs. The journal records exact
bytes, destinations, file modes, commit order, and the reviewed plan. A completion
marker is written last. Staging and completed evidence are retained.

```sh
archmarshal operation-status PROJECT
archmarshal operation-recover PROJECT --operation ID
archmarshal operation-recover PROJECT --operation ID --apply --expect-plan DIGEST
```

Status distinguishes `unprepared`, `prepared`, `writing`, `commit-written`,
`complete`, and `blocked`. Recovery works from local sealed bytes, including the
original selected script snapshots; it does not need the remote sync service.
It rechecks ownership, mappings, exclusions, input hashes, and current output
state under the workspace mutation lock. Only missing outputs are created.
Modified files, unexpected entries, links, and changed mappings stop replay.
Review the new preview if another window changes state after your first preview.

An unprepared journal has not authorized any destination writes. Retry the
original reviewed operation to finish staging. Partial operations from 0.18 or
earlier have no sealed journal and are preserved for manual inspection; recovery
does not guess their intended bytes. Journals are local integrity evidence, not
cryptographic authentication against an administrator who can rewrite the store.

Each operation is bounded to 3,000 files, 64 MiB of staged contents, and a 2 MiB
manifest. Catalog status enumerates at most 1,000 operation IDs and inspects 20 by
default (`--limit` can change this), with a 160 MiB aggregate read budget. Omitted
IDs are reported explicitly; select an exact ID to inspect it. No automatic cleanup
deletes recovery evidence. File modes preserve executable bits and private markers;
the operating system's restrictive umask is respected.

## Context generations

When a context index approaches 1,000 records or 16 MiB of record bodies, preview
an archive before saving more handoffs:

```sh
archmarshal context-archive PROJECT
archmarshal context-archive PROJECT --apply --expect-plan DIGEST
archmarshal context-read PROJECT --record OLD_RECORD_ID
```

Archiving atomically seals the old generation and starts a new active index.
Every workstream's terminal record carries forward, including completed streams.
Original pointers and checkpoint files remain in place; old IDs and embedded
merge evidence still work. Normal resume reads the active generation and retained
heads. Cold record bodies are hash-verified when explicitly read or integrated,
so a normal health check does not claim to have reverified every cold body.

The archive catalog has separate bounds: 1,000 generations, 2 MiB per manifest,
and 32 MiB overall. Archiving cannot reclaim space when every record is already
a terminal stream head. It reports this honestly instead of dropping streams.
At the catalog limit, preserve/export the workspace history; unlimited archival
and automatic pruning are not implemented.

Archive publication is one create-only atomic commit. A failure before that commit
leaves the old active index selected; retry the same reviewed archive. A successful
commit invalidates outstanding save plans, which must be previewed again. Temporary
archive writes are kept outside the catalog so an interrupted temporary write
cannot masquerade as a published generation.
