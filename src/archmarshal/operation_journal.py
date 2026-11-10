"""Sealed inputs and create-only progress for recoverable local operations.

Callers validate their domain policy before preparing or replaying a journal.
Journals are integrity records, not signatures against a local administrator.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .ownership import require_owned_workspace
from .portable import canonical, decode, digest, fail, is_digest, portable_key, read, relative, sha
from .safety import create_bytes_exclusive, ensure_unlinked_path, workspace_root_id

FORMAT = "archmarshal-operation-v1"
DONE_FORMAT = "archmarshal-operation-complete-v1"
MAX_FILES = 3_000
MAX_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_OPERATIONS = 1_000
MAX_STATUS_BYTES = 160 * 1024 * 1024
KINDS = {"sync_pull", "sync_install", "closeout"}


def operation_directory(root: Path, operation_id: str) -> Path:
    if not is_digest(operation_id):
        fail("Invalid recovery operation ID.", "operation_invalid")
    return ensure_unlinked_path(root / ".agent/operations" / operation_id,
                                purpose="Recovery journal")


def file_records(files: dict[str, tuple[bytes, int]]) -> list[dict[str, Any]]:
    return [{"path": path, "sha256": sha(content), "bytes": len(content), "mode": mode,
             "payload": f"{index:04d}"}
            for index, (path, (content, mode)) in enumerate(sorted(files.items()))]


def _validate(root: Path, journal: dict[str, Any], operation_id: str) -> None:
    if (set(journal) != {"format", "workspace", "kind", "destination", "files", "directories",
                         "commit_files", "metadata", "reviewed_plan"}
            or journal["format"] != FORMAT or digest(journal) != operation_id
            or journal["workspace"] != workspace_root_id(root)
            or not isinstance(journal["kind"], str) or journal["kind"] not in KINDS
            or not is_digest(journal["reviewed_plan"])
            or not isinstance(journal["metadata"], dict)):
        fail("Invalid or relocated recovery journal.", "operation_invalid")
    destination = relative(journal["destination"])
    records, directories, commits = (journal[k] for k in ("files", "directories", "commit_files"))
    if (not isinstance(records, list) or not records or len(records) > MAX_FILES
            or not isinstance(directories, list) or len(directories) > MAX_FILES
            or not isinstance(commits, list) or not commits
            or not all(isinstance(p, str) for p in commits)):
        fail("Recovery journal exceeds its structure limit.", "operation_invalid")
    names: set[str] = set()
    total = 0
    file_names = set()
    for index, entry in enumerate(records):
        if (not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes", "mode", "payload"}
                or entry["payload"] != f"{index:04d}"
                or not is_digest(entry["sha256"]) or type(entry["bytes"]) is not int
                or entry["bytes"] < 0 or type(entry["mode"]) is not int
                or entry["mode"] not in {0o600, 0o644, 0o755}):
            fail("Invalid recovery file record.", "operation_invalid")
        path = relative(entry["path"])
        key = portable_key(path)
        if key in names:
            fail("Colliding recovery paths.", "operation_invalid")
        file_names.add(path)
        names.add(key)
        total += entry["bytes"]
    if total > MAX_BYTES:
        fail("Recovery inputs exceed the byte budget.", "operation_invalid")
    for path in directories:
        key = portable_key(relative(path))
        if key in names or not (path == destination or path.startswith(destination + "/")):
            fail("Invalid recovery directory topology.", "operation_invalid")
        names.add(key)
    if destination not in directories or len(set(commits)) != len(commits):
        fail("Recovery destination or commit order is invalid.", "operation_invalid")
    for path in commits:
        if relative(path) not in file_names:
            fail("Recovery commit file is undeclared.", "operation_invalid")
    folded_files = {portable_key(p) for p in file_names}
    for path in [*file_names, *directories]:
        for parent in Path(path).parents:
            if portable_key(parent.as_posix()) in folded_files:
                fail("Recovery file is an ancestor of another entry.", "operation_invalid")


def prepare_operation(root: Path, *, kind: str, destination: str,
                      files: dict[str, tuple[bytes, int]], directories: list[str],
                      commit_files: list[str], metadata: dict[str, Any], reviewed_plan: str
                      ) -> dict[str, Any]:
    """Seal all bytes before creating any output. The workspace lock must be held."""
    require_owned_workspace(root, operation="Prepare recovery")
    # Include all output ancestors inside the owned destination tree.
    dirs = {destination, *directories}
    for path in files:
        for parent in Path(path).parents:
            value = parent.as_posix()
            if value == destination or value.startswith(destination + "/"):
                dirs.add(value)
    journal = {"format": FORMAT, "workspace": workspace_root_id(root), "kind": kind,
               "destination": destination, "files": file_records(files),
               "directories": sorted(dirs), "commit_files": commit_files,
               "metadata": metadata, "reviewed_plan": reviewed_plan}
    operation_id = digest(journal)
    _validate(root, journal, operation_id)
    raw = canonical(journal)
    if len(raw) > MAX_JOURNAL_BYTES:
        fail("Recovery manifest exceeds its byte budget.", "operation_invalid")
    directory = operation_directory(root, operation_id)
    # A failure while staging has no output-side effects. Retrying the exact plan
    # can reuse complete identical blobs; different bytes always fail closed.
    for entry in journal["files"]:
        path = directory / "payloads" / entry["payload"]
        data = files[entry["path"]][0]
        _seal(path, data)
    _seal(directory / "JOURNAL.json", raw)
    return journal


def _seal(path: Path, content: bytes) -> None:
    ensure_unlinked_path(path, purpose="Recovery payload")
    if path.exists():
        if read(path, MAX_BYTES) != content:
            fail("Recovery staging contains different bytes.", "operation_invalid")
    else:
        create_bytes_exclusive(path, content, mode=0o600)


def load_operation(root: Path, operation_id: str, *, verify_inputs: bool = True) -> dict[str, Any]:
    directory = operation_directory(root, operation_id)
    journal = decode(read(directory / "JOURNAL.json", MAX_JOURNAL_BYTES))
    _validate(root, journal, operation_id)
    # Validate the whole sealed input set before any replay or progress claim.
    if verify_inputs:
        for entry in journal["files"]:
            payload(root, journal, entry, operation_id=operation_id)
    return journal


def payload(root: Path, journal: dict[str, Any], entry: dict[str, Any], *,
            operation_id: str | None = None) -> bytes:
    path = operation_directory(root, operation_id or digest(journal)) / "payloads" / entry["payload"]
    content = read(path, entry["bytes"] + 1)
    if len(content) != entry["bytes"] or sha(content) != entry["sha256"]:
        fail("Sealed recovery input has changed.", "operation_invalid")
    return content


def observe_operation(root: Path, journal: dict[str, Any]) -> dict[str, Any]:
    """Read only, with exact output scope and bounded byte/entry scans."""
    present, missing, conflicts = [], [], []
    allowed = {entry["path"] for entry in journal["files"]} | set(journal["directories"])
    for entry in journal["files"]:
        path = ensure_unlinked_path(root / entry["path"], purpose="Recovery output")
        if not path.exists():
            missing.append(entry["path"])
        elif (not path.is_file() or path.stat().st_size != entry["bytes"]
              or sha(read(path, entry["bytes"] + 1)) != entry["sha256"]
              or (os.name != "nt" and path.stat().st_mode & 0o111 != entry["mode"] & 0o111)
              or (os.name != "nt" and entry["mode"] == 0o600 and path.stat().st_mode & 0o077)):
            conflicts.append(entry["path"])
        else:
            present.append(entry["path"])
    missing_dirs = []
    for value in journal["directories"]:
        path = ensure_unlinked_path(root / value, purpose="Recovery directory")
        if not path.exists():
            missing_dirs.append(value)
        elif not path.is_dir():
            conflicts.append(value)
    destination = ensure_unlinked_path(root / journal["destination"], purpose="Recovery scope")
    count = 0
    def failed(error: OSError) -> None:
        raise error
    if destination.is_dir():
        for current, children, files in os.walk(destination, followlinks=False, onerror=failed):
            count += len(children) + len(files)
            if count > MAX_FILES * 2:
                fail("Recovery output exceeds the entry budget.", "operation_invalid")
            for name in sorted(children + files):
                path = ensure_unlinked_path(Path(current) / name, purpose="Recovery scope entry")
                value = path.relative_to(root).as_posix()
                if value not in allowed:
                    conflicts.append(value)
    operation_id = digest(journal)
    done = operation_directory(root, operation_id) / "DONE.json"
    completed = False
    if done.exists():
        completed = decode(read(done, 4096)) == {"format": DONE_FORMAT, "operation": operation_id}
        if not completed:
            fail("Recovery completion marker is invalid.", "operation_invalid")
    stage = ("blocked" if conflicts else "complete" if completed and not missing and not missing_dirs
             else "commit-written" if not missing and not missing_dirs
             else "writing" if present else "prepared")
    return {"operation_id": operation_id, "kind": journal["kind"], "stage": stage,
            "destination": journal["destination"], "present": sorted(present),
            "missing": sorted(missing), "missing_directories": sorted(missing_dirs),
            "conflicts": sorted(set(conflicts)), "completed_marker": completed}


def write_missing(root: Path, journal: dict[str, Any], *,
                  verify_lock: Callable[[], None]) -> None:
    """Replay only missing verified outputs. Caller holds lock and checks domain policy."""
    state = observe_operation(root, journal)
    if state["conflicts"]:
        fail("Recovery found modified or unexpected files; all were preserved.", "operation_conflict")
    for value in journal["directories"]:
        verify_lock()
        ensure_unlinked_path(root / value, purpose="Recovery directory").mkdir(
            parents=True, exist_ok=True)
    entries = {entry["path"]: entry for entry in journal["files"]}
    operation_id = digest(journal)
    commits = journal["commit_files"]
    order = [p for p in entries if p not in commits] + commits
    for value in order:
        verify_lock()
        if value not in state["missing"]:
            continue
        path = ensure_unlinked_path(root / value, purpose="Recovery output")
        entry = entries[value]
        content = payload(root, journal, entry, operation_id=operation_id)
        create_bytes_exclusive(path, content, mode=entry["mode"],
                               temporary_directory=operation_directory(root, operation_id) / "tmp")
        verify_lock()
    current = observe_operation(root, journal)
    if current["missing"] or current["missing_directories"] or current["conflicts"]:
        fail("Recovery output changed during replay.", "operation_conflict")


def complete_operation(root: Path, journal: dict[str, Any]) -> None:
    operation_id = digest(journal)
    _seal(operation_directory(root, operation_id) / "DONE.json",
          canonical({"format": DONE_FORMAT, "operation": operation_id}))


def operation_status(root: Path | str, operation_id: str | None = None, *, limit: int = 20
                      ) -> dict[str, Any]:
    root = require_owned_workspace(ensure_unlinked_path(Path(root), purpose="Recovery workspace"),
                                   operation="Recovery status")
    if type(limit) is not int or not 1 <= limit <= MAX_OPERATIONS:
        fail("Operation status limit must be between 1 and 1000.")
    if operation_id:
        ids = [operation_id]
    else:
        base = ensure_unlinked_path(root / ".agent/operations", purpose="Recovery journals")
        ids = []
        if base.exists():
            for path in base.iterdir():
                if len(ids) >= MAX_OPERATIONS:
                    fail("Recovery catalog exceeds its entry budget; select an operation ID.")
                ids.append(path.name)
    states = []
    ids = sorted(ids)
    omitted = ids[limit:]
    remaining = MAX_STATUS_BYTES
    for key in ids[:limit]:
        directory = operation_directory(root, key)
        if not directory.is_dir():
            fail("Recovery operation directory is missing or invalid.", "operation_invalid")
        if not (directory / "JOURNAL.json").exists():
            states.append({"operation_id": key, "stage": "unprepared",
                           "next_action": "Retry the original reviewed operation to finish staging."})
        else:
            if remaining < MAX_JOURNAL_BYTES:
                omitted.append(key)
                continue
            remaining -= MAX_JOURNAL_BYTES
            journal = load_operation(root, key, verify_inputs=False)
            cost = 2 * sum(entry["bytes"] for entry in journal["files"])
            if cost > remaining:
                omitted.append(key)
                continue
            remaining -= cost
            for entry in journal["files"]:
                payload(root, journal, entry, operation_id=key)
            states.append(observe_operation(root, journal))
    return {"mode": "read_only", "operations": states, "omitted": sorted(omitted),
            "truncated": bool(omitted), "read_budget_bytes": MAX_STATUS_BYTES,
            "recovery": "operation-recover ROOT --operation ID; review before --apply --expect-plan"}
