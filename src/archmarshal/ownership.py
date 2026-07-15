from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .errors import ArchMarshalError, require_workspace_root
from .safety import is_link_or_reparse

OWNERSHIP_FORMAT = "archmarshal-workspace-ownership-v1"
MAX_OWNERSHIP_BYTES = 64 * 1024


def workspace_id(root: Path | str) -> str:
    root_path = Path(root).resolve()
    return hashlib.sha256(
        f"archmarshal-workspace-v1\x00{root_path}".encode("utf-8")
    ).hexdigest()[:32]


def valid_ownership_marker(path: Path) -> bool:
    try:
        if (
            not path.is_file()
            or is_link_or_reparse(path)
            or path.stat().st_size > MAX_OWNERSHIP_BYTES
        ):
            return False
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ArchMarshalError):
        return False
    return (
        isinstance(marker, dict)
        and marker.get("format") == OWNERSHIP_FORMAT
        and marker.get("managed_root") == "."
        and marker.get("skill_index") in {"required", "disabled"}
        and marker.get("source_mutation") is False
        and marker.get("workspace_id") == workspace_id(path.parent.parent)
    )


def ownership_skill_index_mode(path: Path) -> str | None:
    if not valid_ownership_marker(path):
        return None
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return str(marker["skill_index"])


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
