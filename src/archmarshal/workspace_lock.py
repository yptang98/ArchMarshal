from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .errors import ArchMarshalError, require_workspace_root
from .safety import is_link_or_reparse


@dataclass(frozen=True)
class WorkspaceMutationLock:
    root: Path
    root_identity: tuple[int, int]
    path: Path
    handle: BinaryIO
    lock_identity: tuple[int, int]
    operation: str

    def verify(self) -> None:
        try:
            root_metadata = self.root.stat()
            lock_metadata = self.path.lstat()
        except OSError as exc:
            raise ArchMarshalError(
                "workspace_lock_replaced",
                "Workspace root or lifecycle lock disappeared during a mutation.",
                details={"root": str(self.root), "lock": str(self.path)},
            ) from exc
        if (
            (root_metadata.st_dev, root_metadata.st_ino) != self.root_identity
            or is_link_or_reparse(self.path)
            or (lock_metadata.st_dev, lock_metadata.st_ino) != self.lock_identity
        ):
            raise ArchMarshalError(
                "workspace_lock_replaced",
                "Workspace root or lifecycle lock identity changed during a mutation.",
                details={"root": str(self.root), "lock": str(self.path)},
            )


@contextmanager
def workspace_mutation_lock(
    root: Path | str,
    *,
    operation: str,
) -> Iterator[WorkspaceMutationLock]:
    """Serialize ArchMarshal mutations without creating a pre-backup project file."""
    root_path = require_workspace_root(root)
    root_metadata = root_path.stat()
    root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    lock_path = _lock_path(root_path, root_identity)
    handle = lock_path.open("a+b", buffering=0)
    acquired = False
    try:
        if is_link_or_reparse(lock_path):
            raise ArchMarshalError(
                "workspace_lock_invalid",
                "Workspace lifecycle lock must be a regular file.",
                details={"path": str(lock_path)},
            )
        if not _try_os_lock(handle):
            raise ArchMarshalError(
                "workspace_mutation_locked",
                "Another ArchMarshal mutation is active for this workspace.",
                details={"root": str(root_path), "operation": operation},
            )
        acquired = True
        metadata = os.fstat(handle.fileno())
        held = WorkspaceMutationLock(
            root=root_path,
            root_identity=root_identity,
            path=lock_path,
            handle=handle,
            lock_identity=(metadata.st_dev, metadata.st_ino),
            operation=operation,
        )
        held.verify()
        yield held
        held.verify()
    finally:
        if acquired:
            _unlock_os_lock(handle)
        handle.close()


def _lock_path(root: Path, identity: tuple[int, int]) -> Path:
    uid = str(os.getuid()) if hasattr(os, "getuid") else "windows-user"
    state = Path(tempfile.gettempdir()) / f"archmarshal-workspace-locks-{uid}"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if is_link_or_reparse(state) or not state.is_dir():
        raise ArchMarshalError(
            "workspace_lock_invalid",
            "Workspace lifecycle lock directory is linked or invalid.",
            details={"path": str(state)},
        )
    if hasattr(os, "getuid") and state.stat().st_uid != os.getuid():
        raise ArchMarshalError(
            "workspace_lock_invalid",
            "Workspace lifecycle lock directory is owned by another user.",
            details={"path": str(state)},
        )
    key = hashlib.sha256(
        f"archmarshal-workspace-lock-v1\x00{root}\x00{identity[0]}\x00{identity[1]}".encode(
            "utf-8"
        )
    ).hexdigest()
    path = state / f"{key}.lock"
    if path.exists() and (not path.is_file() or is_link_or_reparse(path)):
        raise ArchMarshalError(
            "workspace_lock_invalid",
            "Workspace lifecycle lock path is not a regular file.",
            details={"path": str(path)},
        )
    return path


def _try_os_lock(handle: BinaryIO) -> bool:
    try:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_os_lock(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


__all__ = ["WorkspaceMutationLock", "workspace_mutation_lock"]
