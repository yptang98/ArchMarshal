# Codex projects and external deliverables

ArchMarshal 0.19 development separates the host's working directory from external
workflow records and delivered files. Codex selects the project, chat and worktree.
ArchMarshal records explicit identities and stores selected deliverables in the
user-authorized external container. This workflow does not adopt the project or
create `.agent`, `.agents/skills`, routing files, links or background services.

## Files and identity

| Content | Location |
|---|---|
| Source, development documentation, runtime files | Actual Codex checkout/worktree |
| Task summaries, decisions, evidence and next steps | External project `history/YYYY/MM/DD/` |
| Formal reports, images, exports and packages | External project `artifacts/<event-id>/files/` |
| Conversation | Codex chat; deliverable summaries and file links only |

Use the explicitly associated Codex project root and actual working root. Retain
both paths and any known project, chat and host IDs. Never pick a different
project from a list because its name or Git remote matches. When the association
is unknown, use the explicit current working directory as the project root.

A checkout nested under the registered project is recorded as a subdirectory.
An external Git worktree must share the selected project's Git common directory
and stay within the corresponding repository subdirectory. Detached HEAD is
supported. Inherited `GIT_DIR` and related variables cannot override the explicit
path lookup. The preview pins filesystem identity, Git HEAD and branch.

The record directory combines the project directory name with a canonical-path
hash. Always use returned `record_root`; do not construct it yourself. Same-name
projects remain separate. Moving a project produces a new binding; old records
remain readable and are never silently rebound. This is path identity, not a
global project identifier across renamed directories or devices.

## Preview, record and deliver

`project-status ROOT --store-root STORE` reads the latest five records. Add
`--verify-artifacts` to check archived bytes; otherwise artifact state is
`unverified`. It reads external metadata without scanning the project's sources.

`project-record` accepts `start`, `note` or `close`, a summary, optional task and
decision/evidence/next-step entries, and repeated explicit `--artifact` paths.
Paths are portable, project-relative filenames interpreted against `--work-root`.
Only selected files are copied. Directories, linked paths, dependency/control
trees, credential filenames and colliding paths are rejected. This selection
check is not a general-purpose secret scanner for artifact contents.

Example preview, using individually passed command arguments:

```text
archmarshal project-record PROJECT --work-root WORKTREE --store-root STORE
  --kind close --summary "Report validated" --artifact reports/result.pdf
  --thread-id KNOWN_CHAT_ID --evidence "PDF pages visually checked"
```

Save the complete returned preview outside the project. Review the destination,
selected file list, sizes, hashes and recorded evidence. Repeat the same request
with `--apply --plan-file PREVIEW.json --expect-plan EXACT_DIGEST`. Preview is
read-only. Existing authorization for this exact scope is sufficient; the saved
plan does not introduce a separate approval requirement.

Publication creates a unique artifact version, copies actual bytes and verifies
them before writing the history record as the final commit marker. The record
contains the producing context, file paths, SHA-256 values and byte sizes. Its
filename binds the record's canonical content hash; this detects accidental
changes, not malicious replacement by someone who can rewrite all local evidence.
Source files and prior versions remain unchanged. There is no bidirectional sync.

Exact retry is create-only: identical output is retained, missing files may be
completed, and changed or unexpected output blocks publication. Uncommitted
bundles appear in status with a saved plan where available. Retry requires the
original reviewed plan and unchanged project/source context; offline recovery
after losing the source checkout is not implemented for this workflow. It is
distinct from native `operation-recover` journals.

Limits are 100 selected files and 64 MiB per record, 128 KiB per plan/record,
10,000 catalog entries per scan, and 128 MiB of artifact verification per status
request. Verification over budget remains explicitly `unverified`. A status limit
of 1–100 controls recent records and sampled bundle inspections. Uninspected
bundles are counted; status is not an exhaustive inventory. Long or unavailable
paths can block publication; incomplete copies are retained without a completed
history claim. No automatic pruning, migration or deduplication deletes files.

## Existing state and installed versions

The backend reads the local adapter's `codex-external-project-record-v1` history
as `legacy_unsealed` and preserves its existing `project.json` bytes. New history
uses a distinct content-addressed format. All external records explicitly state
`native_archmarshal_state: false`: they do not claim native ownership, backups,
Skill-index publication or native `end` execution.

Existing `codex-external-artifact-delivery-v1` manifests are listed under
`local_deliveries` with `legacy_unsealed_manifest` integrity. Their listed files
can be hashed within the same status read budget; this does not authenticate the
manifest or inspect unlisted files. Changed/missing files and invalid manifests
are reported without repair. Unrecognized directories remain `unmanaged_bundles`,
not interrupted native operations. No old delivery is moved or rewritten.

Existing native project state stays in place and can be diagnosed read-only.
Native `init`, `adopt`, `start`, `end` and `context-save --apply` remain available
only when the user requests that project-local control workflow. Native Skill
discovery/enabled state is separate from ArchMarshal review evidence; this
backend does not enable or install Skills or manage Codex chat state.
See [native Skill reconciliation](codex-skills.md) for the separate read-only
observation and version-checking workflow. Chat continuity uses the available
host's native resume/fork/handoff controls; external summaries only supplement it.

Installed 0.18.0 does not implement these new commands. On the current machine,
continue using the explicitly configured local `project_records.py` for records
and its returned `record_root` for authorized artifact files. Verify file bytes
before recording delivery. Never substitute the development engine for an
installed plugin, rewrite installation caches, or claim an upgrade occurred.
