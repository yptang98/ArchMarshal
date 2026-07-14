from __future__ import annotations

from pathlib import Path
from typing import Any


class ArchMarshalError(Exception):
    """Expected user-facing error with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


def require_workspace_root(root: Path | str) -> Path:
    candidate = Path(root).expanduser()
    if not candidate.exists() or not candidate.is_dir():
        raise ArchMarshalError(
            "workspace_not_found",
            f"Workspace root is not an existing directory: {candidate}",
            details={"root": str(candidate)},
        )
    return candidate.resolve()


__all__ = ["ArchMarshalError", "require_workspace_root"]
