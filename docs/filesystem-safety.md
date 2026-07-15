# Filesystem Safety Contract

ArchMarshal treats preservation of existing project and Skill content as a
release gate. This document separates the protections implemented by the
current path-based backend from the stronger handle-relative backend required
for hostile-concurrency claims.

## Current backend

The current backend is designed for accidental interruption, stale reviewed
plans, ordinary cooperating ArchMarshal processes, and static symbolic-link,
junction, or reparse-point escapes.

It provides:

- preview-first mutation with exact plan and expected-HEAD checks;
- create-only publication for human-owned destinations;
- no in-place adoption or restore;
- link/reparse rejection and repeated path identity checks;
- exclusive leaf creation, commit-last records, bounded reads, and immutable
  internal generations;
- backups before existing-project adoption and before managed rollback work.

It does **not** provide a complete defense against another process with the
same filesystem permissions replacing an ancestor directory between a path
check and a write. `archmarshal doctor` reports this truth as
`anchored_components: false` and
`concurrent_ancestor_rebinding_protected: false`. The current write threat
model is therefore `cooperative-only`.

No implementation may silently label the current path backend as
handle-relative, race-free, or hostile-process safe.

## Required handle-relative backend

The target backend must anchor an absolute trusted root once, keep directory
handles alive for the complete operation, and accept only one validated path
component at each step. Empty components, `.`, `..`, separators, links, and
reparse points must be rejected.

The minimum capability report is:

| Capability | Meaning |
|---|---|
| `anchored_components` | Every traversed component is opened relative to a retained directory handle. |
| `nofollow_final` | The final object is opened or created without following links. |
| `atomic_create_noreplace` | A competing final leaf is never overwritten. |
| `atomic_replace` | Internal mutable pointers can be replaced relative to a retained handle. |
| `atomic_directory_noreplace` | A staged restore tree can be published only when the destination is absent. |
| `exact_open_handle_publication` | Publication names the object whose open handle was validated. |
| `file_fsync` | Successful content publication includes file durability. |
| `directory_fsync` | Successful namespace publication includes parent-directory durability. |
| `mount_beneath` | Traversal cannot escape through a mount or equivalent namespace boundary. |

Capability detection must use actual runtime support. An unavailable
capability must fail before mutation; it must never fall back to the legacy
absolute-path operation.

Errors from this layer must state at least the operation, backend, missing or
failed capability, relative path, whether mutation started, whether content was
published, whether retry is safe, whether staging was preserved, and whether
identity was verified. In particular, a directory flush failure after
publication must report `published: true`; it cannot claim that nothing was
written.

## Migration order

The backend migration is deliberately incremental, but a migrated call path
must not mix handle-relative and absolute-path mutation:

1. capability model, POSIX directory-handle backend, unsupported backend, and
   deterministic fault-injection hooks;
2. workspace and domain locks;
3. common exclusive creation, backup publication, and safe cleanup;
4. restore extraction, directory no-replace publication, modes, and ownership
   rebind;
5. Skill-index object, `HEAD`, recovery, and rollback publication;
6. user-store initialization, packages, generations, `HEAD`, and rollback;
7. adoption transaction payloads, targets, receipts, and active pointer;
8. session and learning commit-last trees;
9. a CI rule that forbids direct mutating path APIs outside the backend.

The POSIX implementation can build on runtime-supported `dir_fd` operations,
`O_DIRECTORY`, `O_NOFOLLOW`, `fstat`, `fchmod`, and `fsync`; no-replace
directory publication and stronger beneath/mount guarantees may need native
adapters. Windows requires a separately tested native handle-relative backend.
Until it exists, Windows must not claim anchored-component protection or
private-directory ACL guarantees.

## Deterministic security tests

Backend tests must inject replacement and failure at syscall boundaries rather
than depending on timing. Required cases include ancestor replacement after
anchor, link/reparse substitution at every component, competing leaf creation,
parent replacement before publish, failure before and after publish, directory
flush failure, lock-directory replacement, restore-tree replacement, and
unsupported-capability fail-closed behavior. Every case must assert which paths
changed and that unrelated existing content retained the same bytes and
metadata.
