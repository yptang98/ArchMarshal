from __future__ import annotations

import json
from pathlib import Path

from .errors import ArchMarshalError, require_workspace_root
from .io import read_bytes_safe
from .safety import is_link_or_reparse, workspace_root_id

OWNERSHIP_FORMAT = "archmarshal-workspace-ownership-v1"
MAX_OWNERSHIP_BYTES = 64 * 1024


def workspace_id(root: Path | str) -> str:
    return workspace_root_id(root)


def _ownership_marker(path: Path) -> dict | None:
    try:
        if (
            not path.is_file()
            or is_link_or_reparse(path)
            or path.stat().st_size > MAX_OWNERSHIP_BYTES
        ):
            return None
        loaded = read_bytes_safe(path, max_bytes=MAX_OWNERSHIP_BYTES, label="Ownership")
        if loaded.error:
            return None
        marker = json.loads(loaded.data)
    except (OSError, ValueError, RecursionError, ArchMarshalError):
        return None
    valid = (
        isinstance(marker, dict)
        and marker.get("format") == OWNERSHIP_FORMAT
        and marker.get("managed_root") == "."
        and isinstance(marker.get("skill_index"), str)
        and marker["skill_index"] in {"required", "disabled"}
        and marker.get("source_mutation") is False
        and marker.get("workspace_id") == workspace_id(path.parent.parent)
    )
    return marker if valid else None


def valid_ownership_marker(path: Path) -> bool:
    return _ownership_marker(path) is not None


def ownership_skill_index_mode(path: Path) -> str | None:
    marker = _ownership_marker(path)
    return marker["skill_index"] if marker else None


def require_owned_workspace(root: Path | str, *, operation: str) -> Path:
    root_path = require_workspace_root(root)
    marker = root_path / ".agent" / "ownership.json"
    if not valid_ownership_marker(marker):
        raise ArchMarshalError(
            "workspace_ownership_required",
            f"{operation} can write only after safe ArchMarshal adoption establishes root-bound ownership.",
            details={
                "root": str(root_path),
                "ownership": str(marker),
                "source_mutation": False,
            },
        )
    return root_path


__all__ = [
    "ownership_skill_index_mode",
    "require_owned_workspace",
    "valid_ownership_marker",
    "workspace_id",
]
