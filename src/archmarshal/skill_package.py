"""Portable Skill encoding, exact verification, and create-only materialization."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from .portable import (
    MAX_FILES,
    decode,
    digest,
    fail,
    is_digest,
    portable_key,
    read,
    relative,
    sha,
    text,
    validate_device,
)
from .safety import EXCLUDED_BACKUP_PARTS, ensure_unlinked_path, is_link_or_reparse
from .sensitive_data import sensitive_text_errors

FORMAT = "archmarshal-skill-sync-v1"
MAX_CONTENT = 4 * 1024 * 1024


def _safe_content(path: str, raw: bytes) -> None:
    name = Path(path).name.casefold()
    if name in {".env", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_ed25519"}:
        fail("A credential filename is inside the selected package; remove it from sync scope.")
    content = raw.decode("utf-8", errors="replace")
    if "PRIVATE KEY-----" in content or sensitive_text_errors(content=content):
        fail("Selected package appears to contain a secret; inspect it before syncing.")


def scan_package(directory: Path, *, boundaries: set[Path] | None = None,
                 exact: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    ensure_unlinked_path(directory, purpose="Skill source")
    if not directory.is_dir():
        fail("Skill package directory does not exist.")
    boundaries = boundaries or set()
    files: list[dict[str, Any]] = []
    directories: list[str] = []
    seen: set[str] = set()
    total = 0
    def scan_error(error: OSError) -> None:
        raise error
    for current, children, filenames in os.walk(directory, followlinks=False, onerror=scan_error):
        parent = Path(current)
        kept = []
        if len(seen) + len(children) + len(filenames) > MAX_FILES:
            fail("Skill package exceeds its file budget.")
        for name in sorted(children):
            child = parent / name
            if not exact and (name.casefold() in EXCLUDED_BACKUP_PARTS or child in boundaries):
                continue
            if is_link_or_reparse(child):
                fail("Linked directories cannot be synced.")
            if not exact and (child / "SKILL.md").exists():
                continue
            value = relative(child.relative_to(directory).as_posix())
            key = portable_key(value)
            if key in seen:
                fail("Portable package path collision.")
            seen.add(key)
            directories.append(value)
            kept.append(name)
        children[:] = kept
        for name in sorted(filenames):
            if not exact and name.casefold() in EXCLUDED_BACKUP_PARTS:
                continue
            path = parent / name
            value = relative(path.relative_to(directory).as_posix())
            key = portable_key(value)
            if key in seen:
                fail("Portable package path collision.")
            seen.add(key)
            raw = read(path, MAX_CONTENT - total)
            total += len(raw)
            _safe_content(value, raw)
            files.append({"path": value, "sha256": sha(raw), "bytes": len(raw),
                          "executable": bool(path.stat().st_mode & 0o111),
                          "content": base64.b64encode(raw).decode("ascii")})
        if len(files) + len(directories) > MAX_FILES:
            fail("Skill package exceeds its file budget.")
    if "SKILL.md" not in {item["path"] for item in files}:
        fail("Selected package must have SKILL.md.")
    return sorted(files, key=lambda item: item["path"]), sorted(directories)


def validate_snapshot(raw: bytes, key: str) -> dict[str, Any]:
    snapshot = decode(raw)
    if not is_digest(key) or digest(snapshot) != key:
        fail("Portable snapshot digest does not match its object ID.", "sync_integrity_failed")
    fields = {"format", "name", "origin", "exporter", "parents", "files", "directories"}
    if set(snapshot) != fields or snapshot["format"] != FORMAT:
        fail("Unsupported Skill snapshot format.")
    text(snapshot["name"], "Skill name", 200, required=True)
    validate_device(snapshot["origin"])
    validate_device(snapshot["exporter"])
    parents = snapshot["parents"]
    if not isinstance(parents, list) or len(parents) > 100 or not all(map(is_digest, parents)):
        fail("Invalid snapshot lineage.")
    files, directories = snapshot["files"], snapshot["directories"]
    if not isinstance(files, list) or not isinstance(directories, list):
        fail("Invalid package entries.")
    if not files or len(files) + len(directories) > MAX_FILES:
        fail("Package entry limit exceeded.")
    seen: set[str] = set()
    file_keys: set[str] = set()
    total = 0
    for value in directories:
        key_value = portable_key(relative(value))
        if key_value in seen:
            fail("Duplicate package path.")
        seen.add(key_value)
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {
            "path", "sha256", "bytes", "executable", "content"
        }:
            fail("Invalid package file record.")
        path = relative(entry["path"])
        key_value = portable_key(path)
        if key_value in seen or type(entry["executable"]) is not bool:
            fail("Duplicate path or invalid executable bit.")
        seen.add(key_value)
        file_keys.add(key_value)
        try:
            content = base64.b64decode(entry["content"], validate=True)
        except (ValueError, TypeError):
            fail("Invalid package file encoding.")
        if type(entry["bytes"]) is not int or entry["bytes"] != len(content):
            fail("Package file size mismatch.")
        if sha(content) != entry["sha256"]:
            fail("Package file digest mismatch.")
        total += len(content)
        if total > MAX_CONTENT:
            fail("Package exceeds its content budget.")
        _safe_content(path, content)
    exact_directories = set(directories)
    for path in directories + [entry["path"] for entry in files]:
        folded = portable_key(path)
        if any(part in EXCLUDED_BACKUP_PARTS for part in folded.split("/")):
            fail("Portable package contains a preserved runtime or VCS boundary.")
        if folded.endswith("/skill.md"):
            fail("Nested Skills must travel as separate packages.")
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if portable_key(parent) in file_keys or parent not in exact_directories:
                fail("Invalid package directory topology.")
    if not any(entry["path"] == "SKILL.md" for entry in files):
        fail("Portable Skill has no SKILL.md.")
    return snapshot


def verify_package(directory: Path, snapshot: dict[str, Any]) -> None:
    """Verify every entry in a materialized package, including normally excluded ones."""
    actual_files, actual_dirs = scan_package(directory, exact=True)
    expected_files = snapshot["files"]
    if os.name == "nt":
        actual_files = [{**f, "executable": False} for f in actual_files]
        expected_files = [{**f, "executable": False} for f in expected_files]
    if actual_files != expected_files or actual_dirs != snapshot["directories"]:
        fail("Imported package was modified; no files were overwritten.")


def materialize_package(directory: Path, snapshot: dict[str, Any]) -> None:
    """Write validated sealed bytes into a new directory, preserving executable bits."""
    ensure_unlinked_path(directory, purpose="Skill destination")
    directory.mkdir(parents=True)
    for name in snapshot["directories"]:
        (directory / name).mkdir(parents=True, exist_ok=True)
    for entry in snapshot["files"]:
        path = directory / entry["path"]
        from .safety import create_bytes_exclusive
        create_bytes_exclusive(path, base64.b64decode(entry["content"], validate=True),
                               mode=0o755 if entry["executable"] else 0o644)
