from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .errors import ArchMarshalError

if os.name == "nt":
    from ctypes import wintypes

    class _Win32FindStreamData(ctypes.Structure):
        _fields_ = [
            ("stream_size", ctypes.c_longlong),
            ("stream_name", ctypes.c_wchar * 296),
        ]

EXCLUDED_BACKUP_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
MAX_BACKUP_FILES = 100_000
MAX_BACKUP_CONTENT_BYTES = 20 * 1024 * 1024 * 1024
MAX_BACKUP_MANIFEST_BYTES = 32 * 1024 * 1024
BACKUP_DISK_RESERVE_BYTES = 64 * 1024 * 1024
MAX_SKILL_FILES = 10_000
MAX_SKILL_CONTENT_BYTES = 1024 * 1024 * 1024
MAX_DIRECTORY_SCAN_FILES = 100_000
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{index}" for index in range(1, 10)},
    *{f"LPT{index}" for index in range(1, 10)},
}


def workspace_root_id(root: Path | str) -> str:
    root_path = Path(root).resolve(strict=False)
    return hashlib.sha256(
        f"archmarshal-workspace-v1\x00{root_path}".encode("utf-8")
    ).hexdigest()[:32]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_regular_file(
    root: Path,
    path: Path,
    *,
    purpose: str = "File",
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Fingerprint one in-root regular file through a no-follow descriptor."""
    directory = root.resolve()
    if not directory.is_dir() or is_link_or_reparse(root):
        raise ArchMarshalError(
            "fingerprint_root_invalid",
            f"{purpose} root must be a real directory, not a symbolic link.",
            details={"path": str(root)},
        )
    if is_link_or_reparse(path):
        raise ArchMarshalError(
            "fingerprint_symlink_unsupported",
            f"{purpose} must not be a symbolic link or reparse point.",
            details={"path": str(path)},
        )
    resolved = ensure_path_within(directory, path, purpose=purpose)
    relative = resolved.relative_to(directory).as_posix()
    try:
        path_before = resolved.lstat()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise ArchMarshalError(
            "fingerprint_source_changed",
            f"{purpose} could not be opened as an unlinked regular file.",
            details={"path": relative},
        ) from exc
    digest = hashlib.sha256()
    byte_count = 0
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if max_bytes is not None and before.st_size > max_bytes:
            raise ArchMarshalError(
                "fingerprint_limit_exceeded",
                f"{purpose} exceeds the remaining {max_bytes}-byte fingerprint limit.",
                details={"path": relative},
            )
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            if max_bytes is not None and byte_count + len(chunk) > max_bytes:
                raise ArchMarshalError(
                    "fingerprint_limit_exceeded",
                    f"{purpose} exceeded the remaining fingerprint limit while reading.",
                    details={"path": relative},
                )
            digest.update(chunk)
            byte_count += len(chunk)
        after = os.fstat(source.fileno())
    try:
        path_after = resolved.lstat()
    except OSError as exc:
        raise ArchMarshalError(
            "fingerprint_source_changed",
            f"{purpose} disappeared while it was being fingerprinted.",
            details={"path": relative},
        ) from exc
    descriptor_identity = (before.st_dev, before.st_ino)
    if (
        (path_before.st_dev, path_before.st_ino) != descriptor_identity
        or (path_after.st_dev, path_after.st_ino) != descriptor_identity
        or not stat.S_ISREG(before.st_mode)
        or is_link_or_reparse(resolved)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
        or stat.S_IMODE(path_before.st_mode) != stat.S_IMODE(path_after.st_mode)
        or byte_count != after.st_size
    ):
        raise ArchMarshalError(
            "fingerprint_source_changed",
            f"{purpose} changed while it was being fingerprinted.",
            details={"path": relative},
        )
    _reject_named_data_streams(resolved, purpose=purpose)
    return {
        "path": relative,
        "bytes": byte_count,
        "mode": stat.S_IMODE(after.st_mode) & 0o777,
        "sha256": digest.hexdigest(),
    }


def is_link_or_reparse(path: Path) -> bool:
    """Detect POSIX links and Windows reparse points without following them."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ArchMarshalError(
            "path_metadata_unreadable",
            "Path metadata could not be inspected safely.",
            details={"path": str(path)},
        ) from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(reparse_flag and attributes & reparse_flag)


def _named_data_streams(path: Path) -> list[str]:
    if os.name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FindFirstStreamW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.INT,
        ctypes.POINTER(_Win32FindStreamData),
        wintypes.DWORD,
    ]
    kernel32.FindFirstStreamW.restype = wintypes.HANDLE
    kernel32.FindNextStreamW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Win32FindStreamData),
    ]
    kernel32.FindNextStreamW.restype = wintypes.BOOL
    kernel32.FindClose.argtypes = [wintypes.HANDLE]
    kernel32.FindClose.restype = wintypes.BOOL
    data = _Win32FindStreamData()
    handle = kernel32.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in {1, 38, 50}:  # unsupported filesystem or no streams
            return []
        raise ArchMarshalError(
            "path_stream_inspection_failed",
            "Windows data streams could not be inspected safely.",
            details={"path": str(path), "winerror": error},
        )
    streams: list[str] = []
    try:
        while True:
            name = str(data.stream_name)
            if name and not name.startswith("::$"):
                streams.append(name)
            if kernel32.FindNextStreamW(handle, ctypes.byref(data)):
                continue
            error = ctypes.get_last_error()
            if error != 38:
                raise ArchMarshalError(
                    "path_stream_inspection_failed",
                    "Windows data stream enumeration failed before completion.",
                    details={"path": str(path), "winerror": error},
                )
            break
    finally:
        kernel32.FindClose(handle)
    return streams


def _reject_named_data_streams(path: Path, *, purpose: str) -> None:
    streams = _named_data_streams(path)
    if streams:
        raise ArchMarshalError(
            "named_stream_unsupported",
            f"{purpose} contains an NTFS named data stream that is not part of portable content.",
            details={"path": str(path), "streams": streams[:20]},
        )


def fingerprint_directory(
    root: Path,
    *,
    purpose: str = "Directory",
    entrypoint_only: bool = False,
) -> dict[str, Any]:
    """Return a deterministic content fingerprint without following links.

    Relative paths, file sizes, permission bits, and file bytes are covered by the digest.  The
    scan is bounded so a malformed or unexpectedly huge skill cannot make a
    routine start operation consume unbounded resources.
    """
    directory = root.resolve()
    if not directory.is_dir() or is_link_or_reparse(root):
        raise ArchMarshalError(
            "fingerprint_root_invalid",
            f"{purpose} must be a real directory, not a symbolic link.",
            details={"path": str(root)},
        )
    records: list[dict[str, Any]] = []
    total_bytes = 0
    if entrypoint_only:
        entrypoint = directory / "SKILL.md"
        paths = [entrypoint] if entrypoint.exists() or is_link_or_reparse(entrypoint) else []
    else:
        paths = _walk_files_no_links(
            directory,
            reject_links=True,
            purpose=purpose,
            max_files=MAX_SKILL_FILES + 1,
        )
    for path in paths:
        if is_link_or_reparse(path):
            raise ArchMarshalError(
                "fingerprint_symlink_unsupported",
                f"{purpose} contains a symbolic link; ArchMarshal will not follow it implicitly.",
                details={"path": str(path)},
            )
        if not path.is_file():
            continue
        resolved = ensure_path_within(directory, path, purpose=f"{purpose} file")
        relative = resolved.relative_to(directory).as_posix()
        if entrypoint_only and relative != "SKILL.md":
            continue
        if len(records) >= MAX_SKILL_FILES:
            raise ArchMarshalError(
                "fingerprint_limit_exceeded",
                f"{purpose} exceeds the {MAX_SKILL_FILES}-file fingerprint limit.",
                details={"path": str(directory)},
            )
        digest_builder = hashlib.sha256()
        path_before = resolved.lstat()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if total_bytes + before.st_size > MAX_SKILL_CONTENT_BYTES:
                raise ArchMarshalError(
                    "fingerprint_limit_exceeded",
                    f"{purpose} exceeds the {MAX_SKILL_CONTENT_BYTES}-byte fingerprint limit.",
                    details={"path": str(directory)},
                )
            byte_count = 0
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                if total_bytes + byte_count + len(chunk) > MAX_SKILL_CONTENT_BYTES:
                    raise ArchMarshalError(
                        "fingerprint_limit_exceeded",
                        f"{purpose} exceeds the {MAX_SKILL_CONTENT_BYTES}-byte fingerprint limit.",
                        details={"path": str(directory)},
                    )
                digest_builder.update(chunk)
                byte_count += len(chunk)
            after = os.fstat(source.fileno())
        path_after = resolved.lstat()
        identity_before = (path_before.st_dev, path_before.st_ino)
        descriptor_identity = (before.st_dev, before.st_ino)
        identity_after = (path_after.st_dev, path_after.st_ino)
        if (
            identity_before != descriptor_identity
            or identity_after != descriptor_identity
            or not stat.S_ISREG(before.st_mode)
            or is_link_or_reparse(resolved)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
            or stat.S_IMODE(path_before.st_mode) != stat.S_IMODE(path_after.st_mode)
            or byte_count != after.st_size
        ):
            raise ArchMarshalError(
                "fingerprint_source_changed",
                f"{purpose} changed while it was being fingerprinted.",
                details={"path": relative},
            )
        _reject_named_data_streams(resolved, purpose=purpose)
        total_bytes += byte_count
        records.append(
            {
                "path": relative,
                "bytes": byte_count,
                "mode": stat.S_IMODE(after.st_mode) & 0o777,
                "sha256": digest_builder.hexdigest(),
            }
        )

    aggregate = hashlib.sha256()
    for record in records:
        aggregate.update(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        aggregate.update(b"\n")
    return {
        "sha256": aggregate.hexdigest(),
        "file_count": len(records),
        "content_bytes": total_bytes,
        "files": records,
    }


def ensure_path_within(root: Path, path: Path, *, purpose: str) -> Path:
    """Resolve existing symlinks/junctions and reject paths outside ``root``."""
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ArchMarshalError(
            "unsafe_path_escape",
            f"{purpose} resolves outside the workspace root.",
            details={
                "root": str(resolved_root),
                "path": str(path),
                "resolved_path": str(resolved_path),
            },
        ) from exc
    return resolved_path


def ensure_managed_path(root: Path, path: Path, *, purpose: str) -> Path:
    """Reject linked control-path components even when they stay inside root."""
    root = root.resolve()
    candidate = path if path.is_absolute() else root / path
    try:
        lexical = candidate.absolute().relative_to(root)
    except ValueError as exc:
        raise ArchMarshalError(
            "unsafe_path_escape",
            f"{purpose} is outside the workspace root.",
            details={"root": str(root), "path": str(path)},
        ) from exc
    current = root
    for part in lexical.parts:
        current = current / part
        if current.exists() and is_link_or_reparse(current):
            raise ArchMarshalError(
                "unsafe_managed_link",
                f"{purpose} crosses a symbolic link or junction.",
                details={"path": str(current), "resolved_path": str(current.resolve())},
            )
    return ensure_path_within(root, candidate, purpose=purpose)


def ensure_unlinked_path(path: Path | str, *, purpose: str) -> Path:
    """Reject every existing link/reparse component in an explicit lexical path."""
    candidate = Path(path).expanduser()
    candidate = candidate if candidate.is_absolute() else candidate.absolute()
    for component in [*reversed(candidate.parents), candidate]:
        if is_link_or_reparse(component):
            raise ArchMarshalError(
                "unsafe_path_link",
                f"{purpose} crosses a symbolic link or junction.",
                details={"path": str(component)},
            )
    return candidate


def create_text_exclusive(path: Path, content: str) -> None:
    """Durably publish a complete UTF-8 file without replacing a path.

    Bytes are first flushed through a private file in the target directory and
    then exposed with a hard-link create.  A write failure therefore cannot
    leave a partial file at the user-visible destination.  Hard-link support is
    deliberately required: falling back to a streaming copy would reintroduce
    a process-crash window with a partial destination.
    """
    create_bytes_exclusive(path, content.encode("utf-8"))


def create_bytes_exclusive(
    path: Path,
    content: bytes,
    *,
    mode: int = 0o644,
    temporary_directory: Path | None = None,
) -> None:
    """Durably publish complete bytes with create-only, same-filesystem semantics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_link_or_reparse(path.parent):
        raise ArchMarshalError(
            "unsafe_managed_link",
            "Create-only destination parent must not be a symbolic link or junction.",
            details={"path": str(path.parent)},
        )
    temporary_parent = temporary_directory or path.parent
    temporary_parent.mkdir(parents=True, exist_ok=True)
    if is_link_or_reparse(temporary_parent) or not temporary_parent.is_dir():
        raise ArchMarshalError(
            "unsafe_managed_link",
            "Create-only staging directory must be a real directory.",
            details={"path": str(temporary_parent)},
        )
    temporary = temporary_parent / f".am-{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, mode)
    identity = _descriptor_identity(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        fsync_directory(path.parent)
    finally:
        if _unlink_created_path(temporary, identity):
            fsync_directory(temporary_parent)


def fsync_directory(path: Path) -> None:
    """Persist a directory entry where the platform exposes directory fsync."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _descriptor_identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    return metadata.st_dev, metadata.st_ino


def _unlink_created_path(path: Path, identity: tuple[int, int]) -> bool:
    """Remove only the exact directory entry created by this process.

    If another actor replaced the path, leave it untouched.  A platform that
    cannot provide a useful file identity also fails closed and leaves the
    private orphan for later inspection.
    """
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if identity[1] == 0 or (metadata.st_dev, metadata.st_ino) != identity:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free path next to {path}")


def create_backup(
    root: Path,
    files: Iterable[Path],
    destination: Path,
    *,
    reason: str,
    scope: str = "selection",
) -> dict[str, Any]:
    """Create a verified zip snapshot before a managed write.

    Only regular files below ``root`` are accepted. The zip contains a manifest
    with original paths, sizes, and hashes, and is re-opened for integrity
    verification before the caller proceeds.
    """
    root = root.resolve()
    if scope not in {"selection", "managed_workspace", "full_workspace"}:
        raise ArchMarshalError(
            "backup_scope_invalid",
            "Backup scope must be selection, managed_workspace, or full_workspace.",
        )
    ensure_managed_path(root, destination, purpose="Backup destination")
    destination = unique_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if scope == "full_workspace":
        full_files, directory_records, root_mode = _scan_full_backup(root)
        backup_inputs: Iterable[Path] = full_files
    else:
        backup_inputs = files
        directory_records = []
        root_mode = None
    selected: dict[str, Path] = {}
    estimated_bytes = 0
    for item in backup_inputs:
        if is_link_or_reparse(item):
            raise ArchMarshalError(
                "backup_symlink_unsupported",
                "Backup source is a symbolic link; ArchMarshal will not follow it implicitly.",
                details={"path": str(item)},
            )
        resolved = item.resolve()
        if not resolved.is_file():
            continue
        _reject_named_data_streams(resolved, purpose="Backup source")
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ArchMarshalError(
                "backup_source_escape",
                "Backup source resolves outside the workspace root.",
                details={"path": str(item), "resolved_path": str(resolved)},
            ) from exc
        if backup_relative_is_excluded(relative):
            continue
        if relative not in selected:
            if len(selected) >= MAX_BACKUP_FILES:
                raise ArchMarshalError(
                    "backup_limit_exceeded",
                    f"Backup exceeds the {MAX_BACKUP_FILES}-file safety limit.",
                )
            estimated_bytes += resolved.stat().st_size
            if estimated_bytes > MAX_BACKUP_CONTENT_BYTES:
                raise ArchMarshalError(
                    "backup_limit_exceeded",
                    f"Backup exceeds the {MAX_BACKUP_CONTENT_BYTES}-byte safety limit.",
                )
        selected[relative] = resolved

    free_bytes = shutil.disk_usage(destination.parent).free
    required_free = estimated_bytes + BACKUP_DISK_RESERVE_BYTES
    if free_bytes < required_free:
        raise ArchMarshalError(
            "backup_space_insufficient",
            "The backup destination does not have enough free space for a safe snapshot.",
            details={"required_bytes": required_free, "free_bytes": free_bytes},
        )

    temporary = destination.parent / f".am-backup-{uuid.uuid4().hex}.tmp"
    temporary_identity: tuple[int, int] | None = None
    records: list[dict[str, Any]] = []
    actual_total_bytes = 0
    next_space_check = 0
    try:
        descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        temporary_identity = _descriptor_identity(descriptor)
        with os.fdopen(descriptor, "w+b") as raw_archive:
            with zipfile.ZipFile(
                raw_archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for relative, path in sorted(selected.items()):
                    digest = hashlib.sha256()
                    byte_count = 0
                    path_before = path.lstat()
                    flags = (
                        os.O_RDONLY
                        | getattr(os, "O_BINARY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    try:
                        source_descriptor = os.open(path, flags)
                    except OSError as exc:
                        raise ArchMarshalError(
                            "backup_source_changed",
                            "A backup source could not be opened without following a replacement.",
                            details={"path": relative},
                        ) from exc
                    with os.fdopen(source_descriptor, "rb") as source:
                        before = os.fstat(source.fileno())
                        with archive.open(f"files/{relative}", "w", force_zip64=True) as target:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                projected_total = actual_total_bytes + byte_count + len(chunk)
                                if projected_total > MAX_BACKUP_CONTENT_BYTES:
                                    raise ArchMarshalError(
                                        "backup_limit_exceeded",
                                        "Backup sources exceeded the total content safety limit while reading.",
                                        details={"path": relative},
                                    )
                                if projected_total >= next_space_check:
                                    free_now = shutil.disk_usage(destination.parent).free
                                    if free_now < BACKUP_DISK_RESERVE_BYTES + len(chunk):
                                        raise ArchMarshalError(
                                            "backup_space_insufficient",
                                            "Free space fell below the backup safety reserve while sources were being streamed.",
                                            details={
                                                "required_reserve_bytes": BACKUP_DISK_RESERVE_BYTES,
                                                "free_bytes": free_now,
                                                "path": relative,
                                            },
                                        )
                                    next_space_check = projected_total + 64 * 1024 * 1024
                                target.write(chunk)
                                digest.update(chunk)
                                byte_count += len(chunk)
                        after = os.fstat(source.fileno())
                    path_after = path.lstat()
                    if (
                        (path_before.st_dev, path_before.st_ino)
                        != (before.st_dev, before.st_ino)
                        or (path_after.st_dev, path_after.st_ino)
                        != (before.st_dev, before.st_ino)
                        or not stat.S_ISREG(before.st_mode)
                        or is_link_or_reparse(path)
                        or before.st_size != after.st_size
                        or before.st_mtime_ns != after.st_mtime_ns
                        or byte_count != after.st_size
                    ):
                        raise ArchMarshalError(
                            "backup_source_changed",
                            "A source file changed while the backup was being created.",
                            details={"path": relative},
                        )
                    _reject_named_data_streams(path, purpose="Backup source")
                    records.append(
                        {
                            "path": relative,
                            "bytes": byte_count,
                            "sha256": digest.hexdigest(),
                            "mode": stat.S_IMODE(before.st_mode) & 0o777,
                        }
                    )
                    actual_total_bytes += byte_count
                if scope == "full_workspace":
                    rescanned_files, rescanned_directories, rescanned_root_mode = (
                        _scan_full_backup(root)
                    )
                    if (
                        {path.relative_to(root).as_posix() for path in rescanned_files}
                        != {record["path"] for record in records}
                        or rescanned_directories != directory_records
                        or rescanned_root_mode != root_mode
                    ):
                        raise ArchMarshalError(
                            "backup_source_changed",
                            "Full-workspace contents changed while the backup was being created.",
                        )
                manifest = {
                    "format": "archmarshal-backup-v1",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "reason": reason,
                    "scope": scope,
                    "project_root": str(root),
                    "file_count": len(records),
                    "files": records,
                }
                if scope == "full_workspace":
                    manifest.update(
                        {
                            "root_mode": root_mode,
                            "directory_count": len(directory_records),
                            "directories": directory_records,
                        }
                    )
                archive.writestr(
                    "ARCHMARSHAL-BACKUP.json",
                    json.dumps(manifest, indent=2, sort_keys=True),
                )
            raw_archive.flush()
            os.fsync(raw_archive.fileno())
        verify_backup(temporary)
        os.link(temporary, destination)
        fsync_directory(destination.parent)
        published_verification = verify_backup(destination)
    finally:
        if temporary_identity is not None:
            _unlink_created_path(temporary, temporary_identity)

    return {
        "path": destination.relative_to(root).as_posix(),
        "file_count": published_verification["file_count"],
        "content_bytes": published_verification["content_bytes"],
        "file_preview": records[:100],
        "file_preview_truncated": len(records) > 100,
        "bytes": published_verification["archive_bytes"],
        "sha256": published_verification["sha256"],
        "verified": True,
    }


def verify_backup(path: Path | str) -> dict[str, Any]:
    archive_path = Path(path).expanduser().absolute()
    try:
        if is_link_or_reparse(archive_path):
            raise OSError("archive path is linked or reparse-backed")
        path_before = archive_path.lstat()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(archive_path, flags)
    except OSError as exc:
        raise ArchMarshalError(
            "backup_not_found",
            f"Backup archive does not exist as a regular unlinked file: {archive_path}",
        ) from exc
    try:
        archive_handle = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    try:
        descriptor_before = os.fstat(archive_handle.fileno())
        descriptor_identity = (descriptor_before.st_dev, descriptor_before.st_ino)
        if (
            not stat.S_ISREG(descriptor_before.st_mode)
            or (path_before.st_dev, path_before.st_ino) != descriptor_identity
        ):
            raise ArchMarshalError(
                "backup_archive_changed",
                "Backup archive identity changed while it was being opened.",
                details={"path": str(archive_path)},
            )
        with zipfile.ZipFile(archive_handle, "r") as archive:
            names = archive.namelist()
            if len(names) > MAX_BACKUP_FILES + 1:
                raise ArchMarshalError(
                    "backup_limit_exceeded",
                    "Backup contains too many archive members.",
                )
            if len(names) != len(set(names)) or names.count("ARCHMARSHAL-BACKUP.json") != 1:
                raise ArchMarshalError(
                    "backup_integrity_failed",
                    "Backup contains duplicate archive member names.",
                )
            manifest_info = archive.getinfo("ARCHMARSHAL-BACKUP.json")
            if manifest_info.file_size > MAX_BACKUP_MANIFEST_BYTES:
                raise ArchMarshalError(
                    "backup_limit_exceeded",
                    "Backup manifest exceeds the safety limit.",
                )
            try:
                manifest_raw = archive.read("ARCHMARSHAL-BACKUP.json")
                manifest = json.loads(manifest_raw.decode("utf-8"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArchMarshalError(
                    "backup_manifest_invalid",
                    "Backup manifest is missing or invalid.",
                ) from exc
            if not isinstance(manifest, dict) or manifest.get("format") != "archmarshal-backup-v1":
                raise ArchMarshalError(
                    "backup_manifest_invalid",
                    "Backup manifest has an unsupported format.",
                )
            records = manifest.get("files")
            scope = manifest.get("scope", "selection")
            if scope not in {"selection", "managed_workspace", "full_workspace"}:
                raise ArchMarshalError(
                    "backup_manifest_invalid",
                    "Backup manifest declares an unsupported scope.",
                )
            if not isinstance(records, list) or manifest.get("file_count") != len(records):
                raise ArchMarshalError(
                    "backup_manifest_invalid",
                    "Backup manifest file_count does not match its file list.",
                )
            if len(records) > MAX_BACKUP_FILES:
                raise ArchMarshalError(
                    "backup_limit_exceeded",
                    f"Backup declares more than {MAX_BACKUP_FILES} files.",
                )
            directory_records: list[dict[str, Any]] = []
            if scope == "full_workspace":
                directory_value = manifest.get("directories")
                root_mode_value = manifest.get("root_mode")
                if (
                    not isinstance(directory_value, list)
                    or manifest.get("directory_count") != len(directory_value)
                    or not _is_portable_mode(root_mode_value)
                ):
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Full-workspace backup directory metadata is missing or invalid.",
                    )
                if len(records) + len(directory_value) > MAX_BACKUP_FILES:
                    raise ArchMarshalError(
                        "backup_limit_exceeded",
                        f"Backup declares more than {MAX_BACKUP_FILES} filesystem entries.",
                )
                directory_records = directory_value
            seen: set[str] = set()
            portable_seen: set[str] = set()
            total_bytes = 0
            for record in records:
                if not isinstance(record, dict):
                    raise ArchMarshalError(
                        "backup_manifest_invalid", "Backup manifest contains a non-object file record."
                    )
                relative = _safe_backup_relative(str(record.get("path", "")))
                if scope == "full_workspace" and _excluded_backup_relative(
                    Path(*PurePosixPath(relative).parts)
                ):
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Full-workspace backup declares a path reserved for exclusion.",
                        details={"path": relative},
                    )
                if relative in seen:
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Backup manifest contains a duplicate path.",
                        details={"path": relative},
                    )
                seen.add(relative)
                portable_key = relative.casefold()
                if portable_key in portable_seen:
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Backup paths collide on case-insensitive filesystems.",
                        details={"path": relative},
                    )
                portable_seen.add(portable_key)
                member = f"files/{relative}"
                try:
                    info = archive.getinfo(member)
                except KeyError as exc:
                    raise ArchMarshalError(
                        "backup_integrity_failed",
                        "Backup is missing a declared file.",
                        details={"path": relative},
                    ) from exc
                expected_bytes = record.get("bytes")
                expected_hash = record.get("sha256")
                expected_mode = record.get("mode")
                if not isinstance(expected_bytes, int) or expected_bytes < 0:
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Backup file size must be a non-negative integer.",
                        details={"path": relative},
                    )
                if total_bytes + expected_bytes > MAX_BACKUP_CONTENT_BYTES:
                    raise ArchMarshalError(
                        "backup_limit_exceeded",
                        f"Backup expands beyond the {MAX_BACKUP_CONTENT_BYTES}-byte safety limit.",
                    )
                if not isinstance(expected_hash, str) or not _is_sha256(expected_hash):
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Backup file hash must be a SHA-256 hex digest.",
                        details={"path": relative},
                    )
                if (scope == "full_workspace" or expected_mode is not None) and not (
                    _is_portable_mode(expected_mode)
                ):
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Backup file mode must contain only portable permission bits.",
                        details={"path": relative},
                    )
                digest = hashlib.sha256()
                actual_bytes = 0
                with archive.open(info, "r") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        actual_bytes += len(chunk)
                        if total_bytes + actual_bytes > MAX_BACKUP_CONTENT_BYTES:
                            raise ArchMarshalError(
                                "backup_limit_exceeded",
                                "Backup content exceeded the safety limit while reading.",
                            )
                if (
                    info.is_dir()
                    or info.file_size != expected_bytes
                    or actual_bytes != expected_bytes
                    or digest.hexdigest() != expected_hash
                ):
                    raise ArchMarshalError(
                        "backup_integrity_failed",
                        "Backup file does not match its manifest.",
                        details={"path": relative},
                    )
                total_bytes += actual_bytes
                if total_bytes > MAX_BACKUP_CONTENT_BYTES:
                    raise ArchMarshalError(
                        "backup_limit_exceeded",
                        f"Backup expands beyond the {MAX_BACKUP_CONTENT_BYTES}-byte safety limit.",
                    )
            directory_seen: set[str] = set()
            for record in directory_records:
                if (
                    not isinstance(record, dict)
                    or set(record) != {"path", "mode"}
                    or not isinstance(record.get("path"), str)
                    or not _is_portable_mode(record.get("mode"))
                ):
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Full-workspace backup contains invalid directory metadata.",
                    )
                relative = _safe_backup_relative(record["path"])
                if _excluded_backup_relative(Path(*PurePosixPath(relative).parts)):
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Full-workspace backup declares an excluded directory.",
                        details={"path": relative},
                    )
                portable_key = relative.casefold()
                if relative in directory_seen or portable_key in portable_seen:
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Backup directory paths are duplicated or collide portably.",
                        details={"path": relative},
                    )
                directory_seen.add(relative)
                portable_seen.add(portable_key)
            for relative in directory_seen:
                parent_relative = PurePosixPath(relative).parent.as_posix()
                if parent_relative != "." and parent_relative not in directory_seen:
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Backup directory metadata omits an ancestor directory.",
                        details={"path": relative, "missing_parent": parent_relative},
                    )
            for relative in seen:
                parent_relative = PurePosixPath(relative).parent.as_posix()
                if (
                    scope == "full_workspace"
                    and parent_relative != "."
                    and parent_relative not in directory_seen
                ):
                    raise ArchMarshalError(
                        "backup_manifest_invalid",
                        "Full-workspace backup omits a file parent directory.",
                        details={"path": relative, "missing_parent": parent_relative},
                    )
            allowed = {"ARCHMARSHAL-BACKUP.json", *{f"files/{item}" for item in seen}}
            extras = set(names) - allowed
            if extras:
                raise ArchMarshalError(
                    "backup_integrity_failed",
                    "Backup contains undeclared file members.",
                    details={"members": sorted(extras)},
                )
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ArchMarshalError(
            "backup_integrity_failed", "Backup is not a valid ZIP archive."
        ) from exc
    else:
        try:
            archive_handle.seek(0)
            archive_digest = hashlib.sha256()
            archive_bytes = 0
            for chunk in iter(lambda: archive_handle.read(1024 * 1024), b""):
                archive_digest.update(chunk)
                archive_bytes += len(chunk)
            descriptor_after = os.fstat(archive_handle.fileno())
            path_after = archive_path.lstat()
        except OSError as exc:
            raise ArchMarshalError(
                "backup_archive_changed",
                "Backup archive changed or disappeared during verification.",
                details={"path": str(archive_path)},
            ) from exc
        if (
            (descriptor_after.st_dev, descriptor_after.st_ino) != descriptor_identity
            or (path_after.st_dev, path_after.st_ino) != descriptor_identity
            or is_link_or_reparse(archive_path)
            or descriptor_before.st_size != descriptor_after.st_size
            or descriptor_before.st_mtime_ns != descriptor_after.st_mtime_ns
            or archive_bytes != descriptor_after.st_size
        ):
            raise ArchMarshalError(
                "backup_archive_changed",
                "Backup archive identity or bytes changed during verification.",
                details={"path": str(archive_path)},
            )
        archive_sha256 = archive_digest.hexdigest()
    finally:
        archive_handle.close()
    return {
        "path": str(archive_path),
        "file_count": len(records),
        "content_bytes": total_bytes,
        "archive_bytes": archive_bytes,
        "sha256": archive_sha256,
        "verified": True,
        "manifest": manifest,
    }


def restore_backup(
    path: Path | str,
    destination: Path | str,
    *,
    apply: bool = False,
    expected_plan: str | None = None,
    rebind_workspace: bool = False,
) -> dict[str, Any]:
    verification = verify_backup(path)
    archive_path = Path(path).expanduser().resolve()
    target_root = ensure_unlinked_path(destination, purpose="Restore destination")
    source_root_value = verification["manifest"].get("project_root")
    if isinstance(source_root_value, str) and Path(source_root_value).is_absolute():
        source_root = Path(source_root_value).resolve(strict=False)
        try:
            target_root.resolve(strict=False).relative_to(source_root)
        except ValueError:
            pass
        else:
            raise ArchMarshalError(
                "restore_destination_overlaps_source",
                "Restore destination must not be the source project or a directory inside it.",
                details={
                    "source_root": str(source_root),
                    "destination": str(target_root),
                },
            )
    parent = target_root.parent
    if not parent.exists() or not parent.is_dir():
        raise ArchMarshalError(
            "restore_parent_not_found",
            "Restore destination parent must be an existing directory.",
            details={"parent": str(parent)},
        )
    if target_root.exists() or is_link_or_reparse(target_root):
        raise ArchMarshalError(
            "restore_destination_exists",
            "Restore destination must not already exist; ArchMarshal never restores over files.",
            details={"destination": str(target_root)},
        )
    records = verification["manifest"]["files"]
    directory_records = verification["manifest"].get("directories") or []
    root_mode = verification["manifest"].get("root_mode")
    rebind_plan = (
        _plan_restored_workspace_rebind(verification, archive_path, target_root)
        if rebind_workspace
        else None
    )
    parent_metadata = parent.stat()
    parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
    restore_plan = {
        "format": "archmarshal-backup-restore-plan-v1",
        "archive": str(archive_path),
        "archive_bytes": verification["archive_bytes"],
        "archive_sha256": verification["sha256"],
        "manifest_sha256": hashlib.sha256(
            json.dumps(
                verification["manifest"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "destination": str(target_root),
        "file_count": len(records),
        "content_bytes": verification["content_bytes"],
        "file_preview": records[:100],
        "file_preview_truncated": len(records) > 100,
        "overwrite": False,
        "workspace_rebind": rebind_plan,
    }
    plan_digest = hashlib.sha256(
        json.dumps(
            restore_plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "tool": "archmarshal",
        "stage": "backup_restore",
        "mode": "propose_only",
        "archive": str(archive_path),
        "archive_sha256": verification["sha256"],
        "destination": str(target_root.resolve(strict=False)),
        "verified": True,
        "file_count": len(records),
        "content_bytes": verification["content_bytes"],
        "file_preview": records[:100],
        "file_preview_truncated": len(records) > 100,
        "overwrite": False,
        "workspace_rebind": {
            "requested": rebind_workspace,
            "performed": False,
            "source_root": rebind_plan.get("source_root") if rebind_plan else None,
            "new_workspace_id": rebind_plan.get("new_workspace_id")
            if rebind_plan
            else None,
        },
        "plan_digest": plan_digest,
        "apply_precondition": "--expect-plan <plan_digest> --apply",
        "notes": [
            "Restore always targets a new directory and never modifies the source project.",
            "File contents are re-hashed while extracting.",
            "A failed restore is preserved for inspection; ArchMarshal never recursively deletes a path that another process may have changed.",
        ],
    }
    if not apply:
        return payload
    if expected_plan is None:
        payload["mode"] = "review_required"
        payload["notes"].append(
            "Restore apply requires --expect-plan from this exact preview; nothing was created."
        )
        return payload
    if expected_plan != plan_digest:
        payload["mode"] = "blocked"
        payload["expected_plan"] = expected_plan
        payload["actual_plan"] = plan_digest
        payload["notes"].append(
            "The archive bytes, manifest, or destination changed after review; nothing was created."
        )
        return payload

    archive_path_metadata = archive_path.lstat()
    with archive_path.open("rb") as archive_handle:
        archive_descriptor_metadata = os.fstat(archive_handle.fileno())
        archive_path_after = archive_path.lstat()
        descriptor_identity = (
            archive_descriptor_metadata.st_dev,
            archive_descriptor_metadata.st_ino,
        )
        if (
            (archive_path_metadata.st_dev, archive_path_metadata.st_ino)
            != descriptor_identity
            or (archive_path_after.st_dev, archive_path_after.st_ino) != descriptor_identity
            or is_link_or_reparse(archive_path)
        ):
            raise ArchMarshalError(
                "restore_archive_changed",
                "Backup archive identity changed after review; restore was stopped.",
            )
        archive_digest = hashlib.sha256()
        archive_bytes = 0
        for chunk in iter(lambda: archive_handle.read(1024 * 1024), b""):
            archive_digest.update(chunk)
            archive_bytes += len(chunk)
        if (
            archive_bytes != verification["archive_bytes"]
            or archive_digest.hexdigest() != verification["sha256"]
        ):
            raise ArchMarshalError(
                "restore_archive_changed",
                "Backup archive bytes changed after verification; restore was stopped.",
            )
        archive_handle.seek(0)
        extraction_root = parent / f".amr-{uuid.uuid4().hex[:8]}"
        published = False
        target_identity: tuple[int, int] | None = None
        root_mode_applied = False
        try:
            extraction_root.mkdir(mode=0o700, exist_ok=False)
            os.chmod(extraction_root, 0o700)
            parent_after = parent.stat()
            target_metadata = extraction_root.lstat()
            if (
                (parent_after.st_dev, parent_after.st_ino) != parent_identity
                or is_link_or_reparse(parent)
                or is_link_or_reparse(extraction_root)
                or not extraction_root.is_dir()
                or (
                    os.name != "nt"
                    and (stat.S_IMODE(target_metadata.st_mode) & 0o777) != 0o700
                )
            ):
                raise ArchMarshalError(
                    "restore_destination_replaced",
                    "Private restore staging identity or mode changed during creation; extraction was stopped.",
                    details={"staging": str(extraction_root)},
                )
            target_identity = (target_metadata.st_dev, target_metadata.st_ino)
            resolved_target = extraction_root.resolve()
            for directory_record in sorted(
                directory_records,
                key=lambda item: (
                    len(PurePosixPath(item["path"]).parts),
                    item["path"].casefold(),
                ),
            ):
                relative = _safe_backup_relative(directory_record["path"])
                directory_target = extraction_root.joinpath(
                    *PurePosixPath(relative).parts
                )
                ensure_path_within(
                    resolved_target,
                    directory_target,
                    purpose="Restore directory",
                )
                ensure_managed_path(
                    resolved_target,
                    directory_target.parent,
                    purpose="Restore directory parent",
                )
                directory_target.mkdir(mode=0o700, exist_ok=False)
            with zipfile.ZipFile(archive_handle, "r") as archive:
                for record in records:
                    current_target_metadata = extraction_root.lstat()
                    if (
                        (current_target_metadata.st_dev, current_target_metadata.st_ino)
                        != target_identity
                        or is_link_or_reparse(extraction_root)
                    ):
                        raise ArchMarshalError(
                            "restore_destination_replaced",
                            "Private restore staging was replaced during extraction; writing was stopped.",
                            details={"staging": str(extraction_root)},
                        )
                    relative = _safe_backup_relative(str(record["path"]))
                    target = extraction_root.joinpath(*PurePosixPath(relative).parts)
                    ensure_path_within(resolved_target, target, purpose="Restore target")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    ensure_managed_path(
                        resolved_target,
                        target.parent,
                        purpose="Restore target parent",
                    )
                    digest = hashlib.sha256()
                    byte_count = 0
                    with archive.open(f"files/{relative}", "r") as source, target.open(
                        "xb"
                    ) as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            output.write(chunk)
                            digest.update(chunk)
                            byte_count += len(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    mode = record.get("mode")
                    if isinstance(mode, int) and not isinstance(mode, bool):
                        os.chmod(target, mode)
                    if byte_count != record["bytes"] or digest.hexdigest() != record["sha256"]:
                        raise ArchMarshalError(
                            "restore_integrity_failed",
                            "Restored file does not match the backup manifest.",
                            details={"path": relative},
                        )
            if rebind_plan is not None:
                rebind_result = _apply_restored_workspace_rebind(extraction_root, rebind_plan)
                payload["workspace_rebind"].update(rebind_result)
            for directory_record in sorted(
                directory_records,
                key=lambda item: (
                    -len(PurePosixPath(item["path"]).parts),
                    item["path"].casefold(),
                ),
            ):
                directory_target = extraction_root.joinpath(
                    *PurePosixPath(directory_record["path"]).parts
                )
                os.chmod(directory_target, directory_record["mode"])
                if os.name != "nt" and (
                    stat.S_IMODE(directory_target.lstat().st_mode) & 0o777
                ) != directory_record["mode"]:
                    raise ArchMarshalError(
                        "restore_mode_failed",
                        "A restored directory mode could not be reproduced safely.",
                        details={"path": directory_record["path"]},
                    )
            if target_root.exists() or is_link_or_reparse(target_root):
                raise ArchMarshalError(
                    "restore_destination_exists",
                    "Restore destination appeared before publication; private staging was preserved.",
                    details={
                        "destination": str(target_root),
                        "staging": str(extraction_root),
                        "published": False,
                    },
                )
            _publish_directory_exclusive(extraction_root, target_root)
            published = True
            fsync_directory(parent)
            if _is_portable_mode(root_mode):
                _apply_published_root_mode(target_root, target_identity, root_mode)
                root_mode_applied = True
        except BaseException as exc:
            if published:
                raise ArchMarshalError(
                    "restore_published_incomplete",
                    "Restore content was atomically published, but final permission metadata did not complete; the new destination was preserved for explicit recovery.",
                    details={
                        "destination": str(target_root),
                        "published": True,
                        "staging": None,
                        "root_mode_requested": root_mode,
                        "root_mode_applied": root_mode_applied,
                        "destination_identity_verified": _path_has_identity(
                            target_root, target_identity
                        ),
                    },
                ) from exc
            staging_private = _private_restore_staging(
                extraction_root,
                target_identity,
            )
            staging_identity_verified = _path_has_identity(
                extraction_root,
                target_identity,
            )
            if isinstance(exc, ArchMarshalError) and exc.code == "restore_destination_exists":
                exc.details.update(
                    {
                        "published": False,
                        "staging_identity_verified": staging_identity_verified,
                        "staging_private": staging_private,
                        "staging_mode": 0o700 if staging_private and os.name != "nt" else None,
                    }
                )
                raise
            raise ArchMarshalError(
                "restore_incomplete",
                (
                    "Restore stopped before publication; incomplete private staging was preserved for review."
                    if staging_private
                    else (
                        "Restore stopped before publication; staging identity was preserved, but private permissions could not be verified on this platform."
                        if staging_identity_verified
                        else "Restore stopped before publication; staging was preserved, but its identity and private permissions could not be verified."
                    )
                ),
                details={
                    "staging": str(extraction_root),
                    "destination": str(target_root),
                    "published": False,
                    "staging_identity_verified": staging_identity_verified,
                    "staging_private": staging_private,
                    "staging_mode": 0o700 if staging_private and os.name != "nt" else None,
                },
            ) from exc

    payload["mode"] = "restored"
    payload["created"] = str(target_root.resolve())
    return payload


def _path_has_identity(path: Path, identity: tuple[int, int] | None) -> bool:
    if identity is None:
        return False
    try:
        metadata = path.lstat()
        return (
            not is_link_or_reparse(path)
            and stat.S_ISDIR(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == identity
        )
    except (ArchMarshalError, OSError):
        return False


def _private_restore_staging(path: Path, identity: tuple[int, int] | None) -> bool:
    if not _path_has_identity(path, identity):
        return False
    if os.name == "nt":
        # Python's chmod/mkdir mode does not establish a private Windows ACL.
        # Never claim confidentiality that this backend cannot verify.
        return False
    try:
        return (stat.S_IMODE(path.lstat().st_mode) & 0o777) == 0o700
    except OSError:
        return False


def _apply_published_root_mode(
    target_root: Path,
    expected_identity: tuple[int, int],
    root_mode: int,
) -> None:
    """Apply the recorded root mode only after atomic no-replace publication."""
    if os.name == "nt":
        if not _path_has_identity(target_root, expected_identity):
            raise ArchMarshalError(
                "restore_destination_replaced",
                "Published restore destination changed before final mode application.",
            )
        os.chmod(target_root, root_mode)
        if not _path_has_identity(target_root, expected_identity):
            raise ArchMarshalError(
                "restore_destination_replaced",
                "Published restore destination changed during final mode application.",
            )
        return

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(target_root, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(before.st_mode)
            or (before.st_dev, before.st_ino) != expected_identity
        ):
            raise ArchMarshalError(
                "restore_destination_replaced",
                "Published restore destination changed before final mode application.",
            )
        os.fchmod(descriptor, root_mode)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != expected_identity
            or (stat.S_IMODE(after.st_mode) & 0o777) != root_mode
            or not _path_has_identity(target_root, expected_identity)
        ):
            raise ArchMarshalError(
                "restore_mode_failed",
                "The published root directory mode could not be reproduced safely.",
            )
    finally:
        os.close(descriptor)


def _publish_directory_exclusive(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing a concurrent destination."""
    if os.name == "nt":
        try:
            os.rename(source, destination)
        except FileExistsError as exc:
            raise ArchMarshalError(
                "restore_destination_exists",
                "Restore destination appeared before atomic publication.",
                details={"destination": str(destination), "staging": str(source)},
            ) from exc
        return
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:
        raise ArchMarshalError(
            "restore_atomic_publish_unsupported",
            "This platform cannot atomically publish a restore without replacement.",
            details={"staging": str(source)},
        )
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {17, 39}:
        raise ArchMarshalError(
            "restore_destination_exists",
            "Restore destination appeared before atomic publication.",
            details={"destination": str(destination), "staging": str(source)},
        )
    raise ArchMarshalError(
        "restore_atomic_publish_failed",
        "Private restore staging could not be atomically published.",
        details={"destination": str(destination), "staging": str(source), "errno": error},
    )


def _plan_restored_workspace_rebind(
    verification: dict[str, Any],
    archive_path: Path,
    target_root: Path,
) -> dict[str, Any]:
    relative = ".agent/ownership.json"
    records = verification["manifest"].get("files") or []
    matches = [record for record in records if record.get("path") == relative]
    source_root = verification["manifest"].get("project_root")
    record_paths = {
        str(record.get("path")) for record in records if isinstance(record, dict)
    }
    required_control_files = {
        ".agent/ownership.json",
        ".agent/workspace.yaml",
        ".agent/INDEX.md",
        ".agent/registry.yaml",
    }
    if (
        verification["manifest"].get("scope") != "full_workspace"
        or not records
        or any(
            not isinstance(record.get("mode"), int)
            or isinstance(record.get("mode"), bool)
            for record in records
        )
        or not required_control_files.issubset(record_paths)
        or len(matches) != 1
        or not isinstance(source_root, str)
        or not Path(source_root).is_absolute()
    ):
        raise ArchMarshalError(
            "restore_rebind_unavailable",
            "Workspace rebind requires a complete full-workspace backup with the minimum ArchMarshal control plane.",
        )
    with zipfile.ZipFile(archive_path, "r") as archive:
        try:
            marker_bytes = archive.read(f"files/{relative}")
            marker = json.loads(marker_bytes.decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchMarshalError(
                "restore_rebind_unavailable",
                "The backed-up workspace ownership marker is invalid.",
            ) from exc
    expected_fields = {
        "format",
        "workspace_id",
        "managed_root",
        "skill_index",
        "source_mutation",
    }
    if (
        not isinstance(marker, dict)
        or set(marker) != expected_fields
        or marker.get("format") != "archmarshal-workspace-ownership-v1"
        or marker.get("workspace_id") != workspace_root_id(source_root)
        or marker.get("managed_root") != "."
        or marker.get("skill_index") not in {"required", "disabled"}
        or marker.get("source_mutation") is not False
        or hashlib.sha256(marker_bytes).hexdigest() != matches[0].get("sha256")
    ):
        raise ArchMarshalError(
            "restore_rebind_unavailable",
            "The backed-up ownership marker does not exactly match the backup source root.",
        )
    new_marker = {**marker, "workspace_id": workspace_root_id(target_root)}
    if marker["skill_index"] == "required":
        head_path = ".agent/skill-overlays/.archmarshal/HEAD"
        if head_path not in record_paths:
            raise ArchMarshalError(
                "restore_rebind_unavailable",
                "Indexed workspace rebind requires the active Skill index HEAD.",
            )
        with zipfile.ZipFile(archive_path, "r") as archive:
            try:
                head = archive.read(f"files/{head_path}").decode("ascii").strip()
            except (KeyError, UnicodeDecodeError) as exc:
                raise ArchMarshalError(
                    "restore_rebind_unavailable",
                    "The backed-up Skill index HEAD is invalid.",
                ) from exc
        object_path = f".agent/skill-overlays/.archmarshal/objects/sha256/{head}.json"
        if not _is_sha256(head) or object_path not in record_paths:
            raise ArchMarshalError(
                "restore_rebind_unavailable",
                "The full backup does not contain the active Skill index generation.",
            )
    new_bytes = (
        json.dumps(new_marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return {
        "format": "archmarshal-restored-workspace-rebind-v1",
        "source_root": str(Path(source_root).resolve(strict=False)),
        "source_workspace_id": marker["workspace_id"],
        "new_workspace_id": new_marker["workspace_id"],
        "ownership_path": relative,
        "old_sha256": matches[0]["sha256"],
        "new_sha256": hashlib.sha256(new_bytes).hexdigest(),
        "new_marker": new_marker,
        "backup_path": (
            f".agent/backups/ownership-rebind-{verification['sha256'][:12]}.zip"
        ),
        "source_mutation": False,
    }


def _apply_restored_workspace_rebind(
    target_root: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    marker = target_root / str(plan["ownership_path"])
    ensure_managed_path(target_root, marker, purpose="Restored ownership marker")
    if (
        not marker.is_file()
        or is_link_or_reparse(marker)
        or sha256_file(marker) != plan["old_sha256"]
    ):
        raise ArchMarshalError(
            "restore_rebind_source_changed",
            "Restored ownership marker changed before rebind; the original marker was preserved.",
        )
    new_bytes = (
        json.dumps(plan["new_marker"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if hashlib.sha256(new_bytes).hexdigest() != plan["new_sha256"]:
        raise ArchMarshalError(
            "restore_rebind_plan_invalid",
            "Workspace rebind marker bytes do not match the reviewed restore plan.",
        )
    backup = create_backup(
        target_root,
        [marker],
        target_root / str(plan["backup_path"]),
        reason="Preserve the restored root-bound ownership marker before explicit rebind.",
    )
    before = marker.lstat()
    before_identity = (before.st_dev, before.st_ino)
    temporary = marker.parent / f".am-ownership-rebind-{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    temporary_identity = _descriptor_identity(descriptor)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(new_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        after = marker.lstat()
        if (
            (after.st_dev, after.st_ino) != before_identity
            or is_link_or_reparse(marker)
            or sha256_file(marker) != plan["old_sha256"]
        ):
            raise ArchMarshalError(
                "restore_rebind_source_changed",
                "Restored ownership marker changed after backup; the rebind was stopped.",
            )
        os.replace(temporary, marker)
        replaced = True
        fsync_directory(marker.parent)
    finally:
        if not replaced:
            _unlink_created_path(temporary, temporary_identity)
    if sha256_file(marker) != plan["new_sha256"]:
        raise ArchMarshalError(
            "restore_rebind_commit_failed",
            "Rebound workspace ownership marker did not verify after publication.",
        )
    return {
        "performed": True,
        "backup": backup,
        "source_files_modified": False,
    }


def _safe_backup_relative(value: str) -> str:
    path = PurePosixPath(value)
    unsafe_component = any(
        not part
        or part in {".", ".."}
        or part.endswith((" ", "."))
        or ":" in part
        or "\x00" in part
        or part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        for part in path.parts
    )
    if not value or path.is_absolute() or unsafe_component or "\\" in value:
        raise ArchMarshalError(
            "backup_manifest_invalid",
            "Backup manifest contains an unsafe relative path.",
            details={"path": value},
        )
    return path.as_posix()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _is_portable_mode(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 0o777
    )


def files_for_full_backup(root: Path) -> list[Path]:
    return _scan_full_backup(root)[0]


def backup_relative_is_excluded(relative: Path | str) -> bool:
    """Return whether the backup policy intentionally omits a relative path."""
    path = Path(*PurePosixPath(str(relative).replace("\\", "/")).parts)
    return _excluded_backup_relative(path)


def _scan_full_backup(root: Path) -> tuple[list[Path], list[dict[str, Any]], int]:
    """Scan an exact portable full-workspace snapshot without following links."""
    root = root.resolve()
    if not root.is_dir() or is_link_or_reparse(root):
        raise ArchMarshalError(
            "backup_source_invalid",
            "Full-workspace backup root must be a real directory.",
            details={"path": str(root)},
        )
    _reject_named_data_streams(root, purpose="Project backup root")
    root_mode = stat.S_IMODE(root.lstat().st_mode) & 0o777
    files: list[Path] = []
    directories_found: list[dict[str, Any]] = []

    def fail_scan(error: OSError) -> None:
        raise ArchMarshalError(
            "directory_scan_failed",
            "Project backup could not be scanned completely.",
            details={"path": str(getattr(error, "filename", None) or root)},
        ) from error

    for current, directories, filenames in os.walk(
        root,
        topdown=True,
        onerror=fail_scan,
        followlinks=False,
    ):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories, key=str.casefold):
            candidate = current_path / name
            relative = candidate.relative_to(root)
            relative_posix = relative.as_posix()
            if _excluded_backup_relative(relative):
                continue
            if is_link_or_reparse(candidate):
                raise ArchMarshalError(
                    "backup_symlink_unsupported",
                    "Full-workspace backup contains a linked directory; it cannot be called complete safely.",
                    details={"path": str(candidate)},
                )
            metadata = candidate.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise ArchMarshalError(
                    "backup_source_unsupported",
                    "Full-workspace backup contains a non-directory traversal entry.",
                    details={"path": str(candidate)},
                )
            _reject_named_data_streams(candidate, purpose="Project backup directory")
            if len(files) + len(directories_found) >= MAX_BACKUP_FILES:
                raise ArchMarshalError(
                    "backup_limit_exceeded",
                    f"Backup exceeds the {MAX_BACKUP_FILES}-entry safety limit.",
                )
            directories_found.append(
                {
                    "path": relative_posix,
                    "mode": stat.S_IMODE(metadata.st_mode) & 0o777,
                }
            )
            kept.append(name)
        directories[:] = kept
        for name in sorted(filenames, key=str.casefold):
            candidate = current_path / name
            relative = candidate.relative_to(root)
            if _excluded_backup_relative(relative):
                continue
            if is_link_or_reparse(candidate):
                raise ArchMarshalError(
                    "backup_symlink_unsupported",
                    "Full-workspace backup contains a linked file; it cannot be called complete safely.",
                    details={"path": str(candidate)},
                )
            metadata = candidate.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ArchMarshalError(
                    "backup_source_unsupported",
                    "Full-workspace backup contains a non-regular file.",
                    details={"path": str(candidate)},
                )
            _reject_named_data_streams(candidate, purpose="Project backup file")
            if len(files) + len(directories_found) >= MAX_BACKUP_FILES:
                raise ArchMarshalError(
                    "backup_limit_exceeded",
                    f"Backup exceeds the {MAX_BACKUP_FILES}-entry safety limit.",
                )
            files.append(candidate)
    return files, directories_found, root_mode


def _excluded_backup_relative(relative: Path) -> bool:
    parts = relative.parts
    return any(part in EXCLUDED_BACKUP_PARTS for part in parts) or (
        len(parts) >= 2 and parts[0] == ".agent" and parts[1] == "backups"
    )


def files_below_no_links(
    directory: Path,
    *,
    purpose: str,
    max_files: int = MAX_DIRECTORY_SCAN_FILES,
) -> list[Path]:
    if is_link_or_reparse(directory):
        raise ArchMarshalError(
            "unsafe_managed_link",
            f"{purpose} root must not be a symbolic link or junction.",
            details={"path": str(directory)},
        )
    return _walk_files_no_links(
        directory.resolve(),
        reject_links=False,
        purpose=purpose,
        max_files=max_files,
    )


def _walk_files_no_links(
    directory: Path,
    *,
    reject_links: bool,
    purpose: str,
    max_files: int,
) -> list[Path]:
    files: list[Path] = []

    def fail_scan(error: OSError) -> None:
        raise ArchMarshalError(
            "directory_scan_failed",
            f"{purpose} could not be scanned completely; no absence inference is safe.",
            details={"path": str(getattr(error, "filename", None) or directory)},
        ) from error

    for current, directories, filenames in os.walk(
        directory,
        topdown=True,
        onerror=fail_scan,
        followlinks=False,
    ):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories, key=str.casefold):
            candidate = current_path / name
            if is_link_or_reparse(candidate):
                if reject_links:
                    raise ArchMarshalError(
                        "fingerprint_symlink_unsupported",
                        f"{purpose} contains a linked directory; ArchMarshal will not follow it.",
                        details={"path": str(candidate)},
                    )
                continue
            kept.append(name)
        directories[:] = kept
        for name in sorted(filenames, key=str.casefold):
            candidate = current_path / name
            if is_link_or_reparse(candidate):
                if reject_links:
                    raise ArchMarshalError(
                        "fingerprint_symlink_unsupported",
                        f"{purpose} contains a linked file; ArchMarshal will not follow it.",
                        details={"path": str(candidate)},
                    )
                continue
            _reject_named_data_streams(candidate, purpose=purpose)
            if len(files) >= max_files:
                raise ArchMarshalError(
                    "directory_scan_limit_exceeded",
                    f"{purpose} exceeds the {max_files}-file scan limit.",
                    details={"path": str(directory)},
                )
            files.append(candidate)
    return files


__all__ = [
    "create_backup",
    "backup_relative_is_excluded",
    "create_bytes_exclusive",
    "create_text_exclusive",
    "ensure_path_within",
    "ensure_managed_path",
    "ensure_unlinked_path",
    "fingerprint_directory",
    "fingerprint_regular_file",
    "files_below_no_links",
    "files_for_full_backup",
    "fsync_directory",
    "is_link_or_reparse",
    "sha256_file",
    "restore_backup",
    "unique_path",
    "verify_backup",
    "workspace_root_id",
]
