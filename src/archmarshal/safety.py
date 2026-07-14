from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    destination = unique_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    selected: dict[str, Path] = {}
    for item in files:
        resolved = item.resolve()
        if not resolved.is_file():
            continue
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"Backup source escapes project root: {resolved}") from exc
        if any(part in EXCLUDED_BACKUP_PARTS for part in Path(relative).parts):
            continue
        if relative.startswith(".agent/backups/"):
            continue
        selected[relative] = resolved

    records = [
        {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for relative, path in sorted(selected.items())
    ]
    manifest = {
        "format": "archmarshal-backup-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "project_root": str(root),
        "file_count": len(records),
        "files": records,
    }
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ARCHMARSHAL-BACKUP.json", json.dumps(manifest, indent=2, sort_keys=True))
        for relative, path in sorted(selected.items()):
            archive.write(path, f"files/{relative}")

    with zipfile.ZipFile(destination, "r") as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            destination.unlink(missing_ok=True)
            raise OSError(f"Backup verification failed at {bad_member}")

    return {
        "path": destination.relative_to(root).as_posix(),
        "file_count": len(records),
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "verified": True,
    }


def files_for_full_backup(root: Path) -> list[Path]:
    root = root.resolve()
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in EXCLUDED_BACKUP_PARTS for part in path.relative_to(root).parts)
        and not path.relative_to(root).as_posix().startswith(".agent/backups/")
    ]


__all__ = [
    "create_backup",
    "create_text_exclusive",
    "files_for_full_backup",
    "sha256_file",
    "unique_path",
]
