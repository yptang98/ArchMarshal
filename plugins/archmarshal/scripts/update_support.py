#!/usr/bin/env python3
"""Create and verify a minimal last-known-good ArchMarshal update capsule."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CAPSULE_FORMAT = "archmarshal-update-capsule-v1"
CAPSULE_MANIFEST = "CAPSULE.json"
ALLOWED_REPOSITORIES = {
    "https://github.com/yptang98/archmarshal",
    "https://github.com/yptang98/archmarshal.git",
}
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAX_FILES = 10_000
MAX_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
WINDOWS_REPARSE_POINT = 0x0400
REQUIRED_FILES = {
    ".agents/plugins/marketplace.json",
    "plugins/archmarshal/.codex-plugin/plugin.json",
    "plugins/archmarshal/engine.lock.json",
    "plugins/archmarshal/scripts/run_archmarshal.py",
    "pyproject.toml",
    "src/archmarshal/__init__.py",
}


class CapsuleError(Exception):
    """A bounded capsule operation could not be completed safely."""


def _portable_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT)


def _unlinked_directory(path: Path, *, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CapsuleError(f"{label} could not be inspected: {path}") from exc
    if path.is_symlink() or _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise CapsuleError(f"{label} must be an unlinked directory: {path}")
    return metadata


def _stable_bytes(path: Path, *, limit: int, label: str) -> tuple[bytes, int]:
    try:
        before_path = path.lstat()
        if path.is_symlink() or _is_reparse(before_path) or not stat.S_ISREG(
            before_path.st_mode
        ):
            raise OSError("not an unlinked regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CapsuleError(f"{label} must be an unlinked regular file: {path}") from exc

    content = bytearray()
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            content.extend(chunk)
            if len(content) > limit:
                raise CapsuleError(f"{label} exceeds its size limit: {path}")
        after = os.fstat(source.fileno())

    try:
        after_path = path.lstat()
    except OSError as exc:
        raise CapsuleError(f"{label} disappeared while it was read: {path}") from exc
    identity = (before.st_dev, before.st_ino)
    if (
        (before_path.st_dev, before_path.st_ino) != identity
        or (after_path.st_dev, after_path.st_ino) != identity
        or path.is_symlink()
        or _is_reparse(after_path)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise CapsuleError(f"{label} changed while it was read: {path}")
    return bytes(content), stat.S_IMODE(after.st_mode)


def _safe_relative(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CapsuleError("Capsule paths must be non-empty portable relative paths.")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CapsuleError(f"Unsafe capsule path: {value!r}")
    return path


def _ensure_directory(path: Path) -> None:
    if os.path.lexists(path):
        _unlinked_directory(path, label="Capsule directory")
        return
    parent = path.parent
    if parent != path:
        _ensure_directory(parent)
    try:
        path.mkdir()
    except FileExistsError:
        _unlinked_directory(path, label="Capsule directory")


def _write_exclusive(path: Path, content: bytes, *, mode: int) -> None:
    _ensure_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, mode or 0o600)
    except OSError as exc:
        raise CapsuleError(f"Capsule output must be new and writable: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(path, mode or 0o600)
    except Exception:
        raise


def _record_file(
    source: Path,
    capsule: Path,
    relative: str,
    *,
    seen: dict[str, str],
    records: list[dict[str, Any]],
    total: list[int],
) -> None:
    portable = _safe_relative(relative).as_posix()
    key = _portable_key(portable)
    if key in seen:
        raise CapsuleError(
            f"Portable capsule path collision: {seen[key]!r} and {portable!r}"
        )
    content, mode = _stable_bytes(source, limit=MAX_BYTES, label="Capsule source file")
    if len(records) >= MAX_FILES or total[0] + len(content) > MAX_BYTES:
        raise CapsuleError("Capsule file or byte limit exceeded.")
    _write_exclusive(capsule / Path(*portable.split("/")), content, mode=mode)
    seen[key] = portable
    total[0] += len(content)
    records.append(
        {
            "path": portable,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "mode": mode,
        }
    )


def _record_tree(
    source: Path,
    capsule: Path,
    prefix: str,
    *,
    seen: dict[str, str],
    records: list[dict[str, Any]],
    total: list[int],
) -> None:
    _unlinked_directory(source, label="Capsule source tree")
    for current, directories, filenames in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories, key=lambda item: (_portable_key(item), item)):
            candidate = current_path / name
            metadata = candidate.lstat()
            if candidate.is_symlink() or _is_reparse(metadata):
                raise CapsuleError(f"Linked source directory is not supported: {candidate}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise CapsuleError(f"Unexpected source entry: {candidate}")
            if name != "__pycache__":
                kept.append(name)
        directories[:] = kept
        for name in sorted(filenames, key=lambda item: (_portable_key(item), item)):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = current_path / name
            relative_source = path.relative_to(source).as_posix()
            relative = f"{prefix}/{relative_source}"
            _record_file(
                path,
                capsule,
                relative,
                seen=seen,
                records=records,
                total=total,
            )


def _normalize_repository(value: str) -> str:
    if not isinstance(value, str):
        raise CapsuleError("The old repository identity must be a string.")
    normalized = value.strip().rstrip("/").casefold()
    if normalized not in ALLOWED_REPOSITORIES:
        raise CapsuleError("The old repository is not the official ArchMarshal origin.")
    return value.strip().rstrip("/")


def _backup_root(codex_home: Path) -> Path:
    _unlinked_directory(codex_home, label="CODEX_HOME")
    root = codex_home / "backups" / "archmarshal"
    _ensure_directory(root)
    return root


def _output_path(codex_home: Path, output: Path | None, commit: str) -> Path:
    root = _backup_root(codex_home).resolve(strict=True)
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate = root / f"{stamp}-{commit[:12]}"
    else:
        candidate = output.expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
    if candidate.parent.resolve(strict=False) != root:
        raise CapsuleError("The capsule must be a direct child of CODEX_HOME/backups/archmarshal.")
    if os.path.lexists(candidate):
        raise CapsuleError(f"The capsule destination already exists: {candidate}")
    return candidate


def create_capsule(
    *,
    codex_home: Path,
    marketplace_root: Path,
    plugin_root: Path,
    old_repository: str,
    old_commit: str,
    old_version: str,
    output: Path | None = None,
    runtime_pointer: Path | None = None,
) -> dict[str, Any]:
    if COMMIT_RE.fullmatch(old_commit) is None:
        raise CapsuleError("The old commit must be a full lowercase Git SHA.")
    if VERSION_RE.fullmatch(old_version) is None:
        raise CapsuleError("The old version must use major.minor.patch form.")
    repository = _normalize_repository(old_repository)
    marketplace = marketplace_root.expanduser().resolve(strict=True)
    plugin = plugin_root.expanduser().resolve(strict=True)
    _unlinked_directory(marketplace, label="Old marketplace root")
    _unlinked_directory(plugin, label="Old plugin root")
    capsule = _output_path(codex_home.expanduser().resolve(strict=True), output, old_commit)
    unresolved_capsule = capsule.resolve(strict=False)
    if unresolved_capsule.is_relative_to(marketplace) or unresolved_capsule.is_relative_to(
        plugin
    ):
        raise CapsuleError("The capsule destination must be outside every source tree.")
    capsule.mkdir()

    seen: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    total = [0]
    _record_file(
        marketplace / ".agents" / "plugins" / "marketplace.json",
        capsule,
        ".agents/plugins/marketplace.json",
        seen=seen,
        records=records,
        total=total,
    )
    _record_tree(
        plugin,
        capsule,
        "plugins/archmarshal",
        seen=seen,
        records=records,
        total=total,
    )
    _record_tree(
        marketplace / "src" / "archmarshal",
        capsule,
        "src/archmarshal",
        seen=seen,
        records=records,
        total=total,
    )
    _record_file(
        marketplace / "pyproject.toml",
        capsule,
        "pyproject.toml",
        seen=seen,
        records=records,
        total=total,
    )
    pointer = runtime_pointer or codex_home / "runtimes" / "archmarshal" / "current.json"
    if os.path.lexists(pointer):
        _record_file(
            pointer,
            capsule,
            "runtime/current.json",
            seen=seen,
            records=records,
            total=total,
        )

    records.sort(key=lambda item: (_portable_key(str(item["path"])), item["path"]))
    manifest = {
        "format": CAPSULE_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "old": {
            "repository": repository,
            "commit": old_commit,
            "version": old_version,
        },
        "rollback": {
            "pinned_marketplace": [
                "codex plugin remove archmarshal@archmarshal",
                "codex plugin marketplace remove archmarshal",
                f"codex plugin marketplace add yptang98/ArchMarshal --ref {old_commit}",
                "codex plugin add archmarshal@archmarshal",
            ],
            "temporary_capsule_marketplace": [
                "cd <verified-capsule-directory>",
                "codex plugin marketplace add .",
                "codex plugin add archmarshal@archmarshal",
            ],
            "runtime_pointer": (
                "runtime/current.json" if "runtime/current.json" in seen.values() else None
            ),
        },
        "files": records,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    _write_exclusive(capsule / CAPSULE_MANIFEST, manifest_bytes, mode=0o600)
    verified = verify_capsule(capsule)
    return {
        "api_version": "archmarshal-update-support-v1",
        "mode": "capsule_created",
        "capsule": str(capsule),
        "file_count": verified["file_count"],
        "content_bytes": verified["content_bytes"],
        "verified": True,
    }


def _manifest(capsule: Path) -> dict[str, Any]:
    raw, _ = _stable_bytes(
        capsule / CAPSULE_MANIFEST,
        limit=MAX_MANIFEST_BYTES,
        label="Capsule manifest",
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapsuleError("The capsule manifest is not bounded UTF-8 JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "format",
        "created_at",
        "old",
        "rollback",
        "files",
    }:
        raise CapsuleError("The capsule manifest has unexpected or missing fields.")
    if payload.get("format") != CAPSULE_FORMAT or not isinstance(payload.get("created_at"), str):
        raise CapsuleError("The capsule manifest identity is invalid.")
    old = payload.get("old")
    if not isinstance(old, dict) or set(old) != {"repository", "commit", "version"}:
        raise CapsuleError("The capsule old-version identity is invalid.")
    _normalize_repository(old.get("repository", ""))
    if COMMIT_RE.fullmatch(str(old.get("commit", ""))) is None or VERSION_RE.fullmatch(
        str(old.get("version", ""))
    ) is None:
        raise CapsuleError("The capsule old-version identity is invalid.")
    rollback = payload.get("rollback")
    expected_rollback = {
        "pinned_marketplace": [
            "codex plugin remove archmarshal@archmarshal",
            "codex plugin marketplace remove archmarshal",
            f"codex plugin marketplace add yptang98/ArchMarshal --ref {old['commit']}",
            "codex plugin add archmarshal@archmarshal",
        ],
        "temporary_capsule_marketplace": [
            "cd <verified-capsule-directory>",
            "codex plugin marketplace add .",
            "codex plugin add archmarshal@archmarshal",
        ],
        "runtime_pointer": (
            "runtime/current.json"
            if any(
                isinstance(item, dict) and item.get("path") == "runtime/current.json"
                for item in payload.get("files", [])
            )
            else None
        ),
    }
    if rollback != expected_rollback:
        raise CapsuleError("The capsule rollback contract is invalid.")
    if not isinstance(payload.get("files"), list) or len(payload["files"]) > MAX_FILES:
        raise CapsuleError("The capsule file list is invalid or exceeds its limit.")
    return payload


def verify_capsule(capsule: Path) -> dict[str, Any]:
    root = capsule.expanduser().resolve(strict=True)
    _unlinked_directory(root, label="Capsule root")
    manifest = _manifest(root)
    expected: dict[str, dict[str, Any]] = {}
    total = 0
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256", "mode"}:
            raise CapsuleError("A capsule file record is invalid.")
        portable = _safe_relative(item.get("path", "")).as_posix()
        key = _portable_key(portable)
        if key in expected:
            raise CapsuleError(f"Duplicate portable capsule path: {portable}")
        size = item.get("bytes")
        mode = item.get("mode")
        digest = item.get("sha256")
        if (
            not isinstance(size, int)
            or size < 0
            or not isinstance(mode, int)
            or mode < 0
            or mode > 0o7777
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise CapsuleError(f"Invalid capsule file metadata: {portable}")
        content, actual_mode = _stable_bytes(
            root / Path(*portable.split("/")), limit=MAX_BYTES, label="Capsule file"
        )
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise CapsuleError(f"Capsule file hash mismatch: {portable}")
        if actual_mode != mode:
            raise CapsuleError(f"Capsule file mode mismatch: {portable}")
        total += size
        if total > MAX_BYTES:
            raise CapsuleError("Capsule byte limit exceeded.")
        expected[key] = item

    paths = {str(item["path"]) for item in expected.values()}
    missing = sorted(REQUIRED_FILES - paths)
    if missing:
        raise CapsuleError(f"Capsule is missing required files: {missing}")

    actual: set[str] = set()
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories:
            candidate = current_path / name
            metadata = candidate.lstat()
            if candidate.is_symlink() or _is_reparse(metadata):
                raise CapsuleError(f"Capsule contains a linked directory: {candidate}")
        for name in filenames:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative != CAPSULE_MANIFEST:
                actual.add(_portable_key(relative))
    if actual != set(expected):
        raise CapsuleError("Capsule contains missing or uncommitted extra files.")

    return {
        "api_version": "archmarshal-update-support-v1",
        "mode": "capsule_verified",
        "capsule": str(root),
        "old": manifest["old"],
        "file_count": len(expected),
        "content_bytes": total,
        "verified": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="Create and verify a new update capsule.")
    create.add_argument("--codex-home", type=Path, required=True)
    create.add_argument("--marketplace-root", type=Path, required=True)
    create.add_argument("--plugin-root", type=Path, required=True)
    create.add_argument("--old-repository", required=True)
    create.add_argument("--old-commit", required=True)
    create.add_argument("--old-version", required=True)
    create.add_argument("--output", type=Path)
    create.add_argument("--runtime-pointer", type=Path)
    verify = subparsers.add_parser("verify", help="Verify a committed update capsule.")
    verify.add_argument("capsule", type=Path)
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        if args.command == "create":
            payload = create_capsule(
                codex_home=args.codex_home,
                marketplace_root=args.marketplace_root,
                plugin_root=args.plugin_root,
                old_repository=args.old_repository,
                old_commit=args.old_commit,
                old_version=args.old_version,
                output=args.output,
                runtime_pointer=args.runtime_pointer,
            )
        else:
            payload = verify_capsule(args.capsule)
    except (CapsuleError, OSError) as exc:
        print(
            json.dumps(
                {
                    "api_version": "archmarshal-update-support-v1",
                    "mode": "error",
                    "error": {"code": "archmarshal_update_capsule_invalid", "message": str(exc)},
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
