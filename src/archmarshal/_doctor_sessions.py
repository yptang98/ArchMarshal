from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from ._doctor_core import (
    MAX_METADATA_FILE_BYTES,
    MAX_SCAN_DEPTH,
    Report,
    display,
    is_link,
    is_sha256,
    list_directory,
    load_json,
    load_yaml,
    path_exists,
    read_file,
    report_format,
    unsafe_finding,
)
from .session import MAX_SESSION_COMMIT_BYTES, MAX_SESSION_FILES


def inspect_sessions(root: Path, report: Report) -> None:
    history = root / ".agent" / "history"
    if not path_exists(history):
        report.add(
            "session",
            "sessions_absent",
            "info",
            "absent",
            "workspace",
            display(history, root),
            "No closeout history is present.",
        )
        return
    candidates = _session_directories(history, root, report)
    if len(candidates) > report.budget.history_limit:
        report.budget.truncate("session", "workspace", display(history, root), "history_limit")
        candidates = candidates[: report.budget.history_limit]
    if not candidates:
        report.add(
            "session",
            "sessions_empty",
            "info",
            "absent",
            "workspace",
            display(history, root),
            "No closeout sessions were found within the bounded scan.",
        )
    for directory in candidates:
        _inspect_session(directory, root, report)


def _session_directories(history: Path, root: Path, report: Report) -> list[Path]:
    found: set[Path] = set()
    pending = [(history, 0)]
    while pending:
        directory, depth = pending.pop(0)
        entries = list_directory(directory, root, "session", "workspace", report)
        if entries is None:
            continue
        names = {item.name for item in entries}
        if {"session.yaml", "COMMITTED.json"} & names:
            found.add(directory)
            continue
        children = [item for item in entries if not is_link(item) and item.is_dir()]
        for item in entries:
            if is_link(item):
                unsafe_finding(report, "session", "workspace", item, root)
        if depth >= MAX_SCAN_DEPTH:
            if children:
                report.budget.truncate(
                    "session", "workspace", display(directory, root), "recursion_depth"
                )
        else:
            pending.extend((item, depth + 1) for item in children)
    return sorted(found, key=lambda item: display(item, root))


def _inspect_session(directory: Path, root: Path, report: Report) -> None:
    session_path, commit_path = directory / "session.yaml", directory / "COMMITTED.json"
    session = load_yaml(session_path, root, "session", "workspace", report)
    session_format = session.get("format") if isinstance(session, dict) else None
    if session is not None:
        report_format(
            report,
            "session",
            "workspace",
            display(session_path, root),
            session_format,
            "session",
        )
    if not path_exists(commit_path):
        classification = "legacy" if session_format == "archmarshal-session-v1" else "partial"
        report.add(
            "session",
            "session_uncommitted",
            "warning",
            classification,
            "workspace",
            display(directory, root),
            "Session has no immutable commit marker.",
        )
        report.suggest(
            "workspace",
            display(directory, root),
            classification,
            "Retain and review this session; no automatic migration or cleanup is suggested.",
        )
        return
    commit, _ = load_json(
        commit_path,
        root,
        "session",
        "workspace",
        report,
        MAX_SESSION_COMMIT_BYTES,
    )
    if commit is None:
        return
    report_format(
        report,
        "session",
        "workspace",
        display(commit_path, root),
        commit.get("format") if isinstance(commit, dict) else None,
        "session_commit",
    )
    records = commit.get("files") if isinstance(commit, dict) else None
    if (
        not isinstance(records, list)
        or len(records) > MAX_SESSION_FILES
        or commit.get("file_count") != len(records)
    ):
        _commit_error(commit_path, root, report, "session_commit_structure_invalid")
        return
    declared: set[str] = set()
    for record in records:
        if not _valid_record(record, declared):
            _commit_error(commit_path, root, report, "session_commit_record_invalid")
            return
        relative = record["path"]
        target = directory.joinpath(*PurePosixPath(relative).parts)
        raw = read_file(
            target, root, "session", "workspace", report, MAX_METADATA_FILE_BYTES
        )
        if raw is None:
            if not path_exists(target):
                _commit_error(target, root, report, "session_file_missing")
            return
        if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            _commit_error(target, root, report, "session_file_digest_mismatch")
            return
        declared.add(relative)


def _valid_record(record: object, declared: set[str]) -> bool:
    if not isinstance(record, dict):
        return False
    relative = record.get("path")
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        return False
    path = PurePosixPath(relative)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and relative not in declared
        and isinstance(record.get("bytes"), int)
        and record["bytes"] >= 0
        and is_sha256(record.get("sha256"))
    )


def _commit_error(path: Path, root: Path, report: Report, code: str) -> None:
    messages = {
        "session_commit_structure_invalid": "Session commit has an invalid bounded manifest.",
        "session_commit_record_invalid": "Session commit contains an invalid file record.",
        "session_file_missing": "A committed session file is missing.",
        "session_file_digest_mismatch": "A committed session file no longer matches its digest.",
    }
    report.add(
        "session",
        code,
        "error",
        "corrupt",
        "workspace",
        display(path, root),
        messages[code],
    )


__all__ = ["inspect_sessions"]
