from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .errors import ArchMarshalError

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
MAX_SKILL_FILES = 10_000
MAX_SKILL_CONTENT_BYTES = 1024 * 1024 * 1024
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{index}" for index in range(1, 10)},
    *{f"LPT{index}" for index in range(1, 10)},
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_directory(
    root: Path,
    *,
    purpose: str = "Directory",
    entrypoint_only: bool = False,
) -> dict[str, Any]:
    """Return a deterministic content fingerprint without following links.

    Relative paths, file sizes, and file bytes are covered by the digest.  The
    scan is bounded so a malformed or unexpectedly huge skill cannot make a
    routine start operation consume unbounded resources.
    """
    directory = root.resolve()
    if not directory.is_dir() or root.is_symlink():
        raise ArchMarshalError(
            "fingerprint_root_invalid",
            f"{purpose} must be a real directory, not a symbolic link.",
            details={"path": str(root)},
        )
    records: list[dict[str, Any]] = []
    total_bytes = 0
    for path in _walk_files_no_links(directory, reject_links=True, purpose=purpose):
        if path.is_symlink():
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
        size_before = resolved.stat().st_size
        if len(records) >= MAX_SKILL_FILES:
            raise ArchMarshalError(
                "fingerprint_limit_exceeded",
                f"{purpose} exceeds the {MAX_SKILL_FILES}-file fingerprint limit.",
                details={"path": str(directory)},
            )
        if total_bytes + size_before > MAX_SKILL_CONTENT_BYTES:
            raise ArchMarshalError(
                "fingerprint_limit_exceeded",
                f"{purpose} exceeds the {MAX_SKILL_CONTENT_BYTES}-byte fingerprint limit.",
                details={"path": str(directory)},
            )
        digest_builder = hashlib.sha256()
        with resolved.open("rb") as source:
            before = os.fstat(source.fileno())
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest_builder.update(chunk)
            after = os.fstat(source.fileno())
        size_after = after.st_size
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ArchMarshalError(
                "fingerprint_source_changed",
                f"{purpose} changed while it was being fingerprinted.",
                details={"path": relative},
            )
        total_bytes += size_after
        records.append(
            {"path": relative, "bytes": size_after, "sha256": digest_builder.hexdigest()}
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
        if current.exists() and current.is_symlink():
            raise ArchMarshalError(
                "unsafe_managed_link",
                f"{purpose} crosses a symbolic link or junction.",
                details={"path": str(current), "resolved_path": str(current.resolve())},
            )
    return ensure_path_within(root, candidate, purpose=purpose)


def create_text_exclusive(path: Path, content: str) -> None:
    """Create a UTF-8 file without ever replacing an existing path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


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
) -> dict[str, Any]:
    """Create a verified zip snapshot before a managed write.

    Only regular files below ``root`` are accepted. The zip contains a manifest
    with original paths, sizes, and hashes, and is re-opened for integrity
    verification before the caller proceeds.
    """
    root = root.resolve()
    ensure_managed_path(root, destination, purpose="Backup destination")
    destination = unique_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    selected: dict[str, Path] = {}
    estimated_bytes = 0
    for item in files:
        if item.is_symlink():
            raise ArchMarshalError(
                "backup_symlink_unsupported",
                "Backup source is a symbolic link; ArchMarshal will not follow it implicitly.",
                details={"path": str(item)},
            )
        resolved = item.resolve()
        if not resolved.is_file():
            continue
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ArchMarshalError(
                "backup_source_escape",
                "Backup source resolves outside the workspace root.",
                details={"path": str(item), "resolved_path": str(resolved)},
            ) from exc
        if any(part in EXCLUDED_BACKUP_PARTS for part in Path(relative).parts):
            continue
        if relative.startswith(".agent/backups/"):
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
    required_free = estimated_bytes + 64 * 1024 * 1024
    if free_bytes < required_free:
        raise ArchMarshalError(
            "backup_space_insufficient",
            "The backup destination does not have enough free space for a safe snapshot.",
            details={"required_bytes": required_free, "free_bytes": free_bytes},
        )

    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    records: list[dict[str, Any]] = []
    published = False
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, path in sorted(selected.items()):
                digest = hashlib.sha256()
                byte_count = 0
                with path.open("rb") as source:
                    before = os.fstat(source.fileno())
                    with archive.open(f"files/{relative}", "w", force_zip64=True) as target:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            target.write(chunk)
                            digest.update(chunk)
                            byte_count += len(chunk)
                            if byte_count > MAX_BACKUP_CONTENT_BYTES:
                                raise ArchMarshalError(
                                    "backup_limit_exceeded",
                                    "A backup source exceeded the total content safety limit while reading.",
                                    details={"path": relative},
                                )
                    after = os.fstat(source.fileno())
                if (
                    before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or byte_count != after.st_size
                ):
                    raise ArchMarshalError(
                        "backup_source_changed",
                        "A source file changed while the backup was being created.",
                        details={"path": relative},
                    )
                records.append(
                    {"path": relative, "bytes": byte_count, "sha256": digest.hexdigest()}
                )
            manifest = {
                "format": "archmarshal-backup-v1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "project_root": str(root),
                "file_count": len(records),
                "files": records,
            }
            archive.writestr(
                "ARCHMARSHAL-BACKUP.json",
                json.dumps(manifest, indent=2, sort_keys=True),
            )
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        verification = verify_backup(temporary)
        try:
            os.link(temporary, destination)
        except OSError:
            with temporary.open("rb") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
        published = True
        verify_backup(destination)
    except BaseException:
        if published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "path": destination.relative_to(root).as_posix(),
        "file_count": verification["file_count"],
        "content_bytes": verification["content_bytes"],
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "verified": True,
    }


def verify_backup(path: Path | str) -> dict[str, Any]:
    archive_path = Path(path).expanduser()
    if not archive_path.is_file():
        raise ArchMarshalError(
            "backup_not_found",
            f"Backup archive does not exist: {archive_path}",
        )
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
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
            seen: set[str] = set()
            portable_seen: set[str] = set()
            total_bytes = 0
            for record in records:
                if not isinstance(record, dict):
                    raise ArchMarshalError(
                        "backup_manifest_invalid", "Backup manifest contains a non-object file record."
                    )
                relative = _safe_backup_relative(str(record.get("path", "")))
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
    return {
        "path": str(archive_path.resolve()),
        "file_count": len(records),
        "content_bytes": total_bytes,
        "archive_bytes": archive_path.stat().st_size,
        "sha256": sha256_file(archive_path),
        "verified": True,
        "manifest": manifest,
    }


def restore_backup(
    path: Path | str,
    destination: Path | str,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    verification = verify_backup(path)
    archive_path = Path(path).expanduser().resolve()
    target_root = Path(destination).expanduser()
    parent = target_root.parent
    if not parent.exists() or not parent.is_dir():
        raise ArchMarshalError(
            "restore_parent_not_found",
            "Restore destination parent must be an existing directory.",
            details={"parent": str(parent)},
        )
    if target_root.exists() or target_root.is_symlink():
        raise ArchMarshalError(
            "restore_destination_exists",
            "Restore destination must not already exist; ArchMarshal never restores over files.",
            details={"destination": str(target_root)},
        )
    records = verification["manifest"]["files"]
    payload = {
        "tool": "archmarshal",
        "stage": "backup_restore",
        "mode": "propose_only",
        "archive": str(archive_path),
        "destination": str(target_root.resolve(strict=False)),
        "verified": True,
        "file_count": len(records),
        "content_bytes": verification["content_bytes"],
        "overwrite": False,
        "notes": [
            "Restore always targets a new directory and never modifies the source project.",
            "File contents are re-hashed while extracting and the new directory is removed on failure.",
        ],
    }
    if not apply:
        return payload

    created_root = False
    try:
        target_root.mkdir(exist_ok=False)
        created_root = True
        resolved_target = target_root.resolve()
        with zipfile.ZipFile(archive_path, "r") as archive:
            for record in records:
                relative = _safe_backup_relative(str(record["path"]))
                target = target_root.joinpath(*PurePosixPath(relative).parts)
                ensure_path_within(resolved_target, target, purpose="Restore target")
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                byte_count = 0
                with archive.open(f"files/{relative}", "r") as source, target.open("xb") as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(chunk)
                        digest.update(chunk)
                        byte_count += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if byte_count != record["bytes"] or digest.hexdigest() != record["sha256"]:
                    raise ArchMarshalError(
                        "restore_integrity_failed",
                        "Restored file does not match the backup manifest.",
                        details={"path": relative},
                    )
    except BaseException:
        if created_root:
            shutil.rmtree(target_root, ignore_errors=True)
        raise

    payload["mode"] = "restored"
    payload["created"] = str(target_root.resolve())
    return payload


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


def files_for_full_backup(root: Path) -> list[Path]:
    root = root.resolve()
    return [
        path
        for path in _walk_files_no_links(root, reject_links=False, purpose="Project backup")
        if path.is_file()
        and not any(part in EXCLUDED_BACKUP_PARTS for part in path.relative_to(root).parts)
        and not path.relative_to(root).as_posix().startswith(".agent/backups/")
    ]


def files_below_no_links(directory: Path, *, purpose: str) -> list[Path]:
    if directory.is_symlink():
        raise ArchMarshalError(
            "unsafe_managed_link",
            f"{purpose} root must not be a symbolic link or junction.",
            details={"path": str(directory)},
        )
    return _walk_files_no_links(directory.resolve(), reject_links=False, purpose=purpose)


def _walk_files_no_links(
    directory: Path,
    *,
    reject_links: bool,
    purpose: str,
) -> list[Path]:
    files: list[Path] = []
    for current, directories, filenames in os.walk(directory, topdown=True, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories, key=str.casefold):
            candidate = current_path / name
            if candidate.is_symlink():
                if reject_links:
                    raise ArchMarshalError(
                        "fingerprint_symlink_unsupported",
                        f"{purpose} contains a linked directory; ArchMarshal will not follow it.",
                        details={"path": str(candidate)},
                    )
                continue
            kept.append(name)
        directories[:] = kept
        files.extend(current_path / name for name in sorted(filenames, key=str.casefold))
    return files


__all__ = [
    "create_backup",
    "create_text_exclusive",
    "ensure_path_within",
    "ensure_managed_path",
    "fingerprint_directory",
    "files_below_no_links",
    "files_for_full_backup",
    "sha256_file",
    "restore_backup",
    "unique_path",
    "verify_backup",
]
