#!/usr/bin/env python3
"""Resolve an optional Codex-owned ArchMarshal runtime, then invoke the locked engine."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

RUNTIME_FORMAT = "archmarshal-runtime-v1"
REQUIRED_ENGINE_VERSION = "0.16.0"
MAX_POINTER_BYTES = 16 * 1024
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
WINDOWS_REPARSE_POINT = 0x0400


class RuntimePointerError(Exception):
    pass


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _error(message: str, *, pointer: Path) -> int:
    payload = {
        "api_version": "archmarshal-runtime-launcher-v1",
        "tool": "archmarshal",
        "mode": "error",
        "error": {
            "code": "archmarshal_runtime_invalid",
            "message": message,
            "details": {"pointer": str(pointer)},
        },
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 2


def _is_unlinked_regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    is_reparse = bool(
        getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
    )
    return not path.is_symlink() and not is_reparse and stat.S_ISREG(metadata.st_mode)


def _read_pointer(pointer: Path, runtime_root: Path) -> Path:
    try:
        runtime_metadata = runtime_root.lstat()
    except OSError as exc:
        raise RuntimePointerError("The runtime root could not be inspected.") from exc
    runtime_is_reparse = bool(
        getattr(runtime_metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
    )
    if (
        runtime_root.is_symlink()
        or runtime_is_reparse
        or not stat.S_ISDIR(runtime_metadata.st_mode)
    ):
        raise RuntimePointerError("The runtime root must be an unlinked directory.")
    if not _is_unlinked_regular_file(pointer):
        raise RuntimePointerError("The runtime pointer must be an unlinked regular file.")
    try:
        raw = pointer.read_bytes()
    except OSError as exc:
        raise RuntimePointerError("The runtime pointer could not be read.") from exc
    if len(raw) > MAX_POINTER_BYTES:
        raise RuntimePointerError("The runtime pointer exceeds its size limit.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimePointerError("The runtime pointer is not bounded UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimePointerError("The runtime pointer must be a JSON object.")

    if set(payload) != {"format", "commit", "engine_version", "python"}:
        raise RuntimePointerError("The runtime pointer has unexpected or missing fields.")
    commit = payload.get("commit")
    engine_version = payload.get("engine_version")
    python_value = payload.get("python")
    if (
        payload.get("format") != RUNTIME_FORMAT
        or not isinstance(commit, str)
        or COMMIT_RE.fullmatch(commit) is None
        or engine_version != REQUIRED_ENGINE_VERSION
        or not isinstance(python_value, str)
        or not python_value
    ):
        raise RuntimePointerError("The runtime pointer identity is invalid or stale.")

    expected_root = runtime_root / commit
    raw_interpreter = Path(python_value).expanduser()
    try:
        runtime_metadata = expected_root.lstat()
        runtime_is_reparse = bool(
            getattr(runtime_metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        )
        if (
            expected_root.is_symlink()
            or runtime_is_reparse
            or not stat.S_ISDIR(runtime_metadata.st_mode)
        ):
            raise RuntimePointerError(
                "The commit-scoped runtime must be an unlinked directory."
            )
        if not _is_unlinked_regular_file(raw_interpreter):
            raise RuntimePointerError(
                "The runtime interpreter must be an unlinked regular file."
            )
        resolved_root = expected_root.resolve(strict=True)
        interpreter = raw_interpreter.resolve(strict=True)
        interpreter.relative_to(resolved_root)
    except RuntimePointerError:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimePointerError(
            "The runtime interpreter must exist below its commit-scoped directory."
        ) from exc
    return interpreter


def _interpreter() -> tuple[Path, Path]:
    runtime_root = _codex_home() / "runtimes" / "archmarshal"
    pointer = runtime_root / "current.json"
    if not os.path.lexists(pointer):
        return Path(sys.executable), pointer
    return _read_pointer(pointer, runtime_root), pointer


def main(arguments: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    wrapper = Path(__file__).resolve().with_name("invoke_archmarshal.py")
    try:
        interpreter, pointer = _interpreter()
    except RuntimePointerError as exc:
        return _error(str(exc), pointer=_codex_home() / "runtimes/archmarshal/current.json")
    try:
        completed = subprocess.run(
            [str(interpreter), "-I", str(wrapper), *args], check=False
        )
    except OSError as exc:
        return _error(
            f"The selected Python interpreter could not start: {exc}", pointer=pointer
        )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
