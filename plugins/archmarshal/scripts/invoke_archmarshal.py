#!/usr/bin/env python3
"""Invoke the matching ArchMarshal engine for the Codex plugin."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_ENGINE_VERSION = "0.13.0"
MARKETPLACE_NAME = "personal"
MAX_MARKETPLACE_LIST_BYTES = 1024 * 1024


def _error(code: str, message: str, **details: str) -> int:
    payload: dict[str, object] = {
        "api_version": "archmarshal-plugin-bootstrap-v1",
        "tool": "archmarshal",
        "mode": "error",
        "error": {"code": code, "message": message},
    }
    if details:
        payload["error"]["details"] = details  # type: ignore[index]
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 2


def _source_below(root: Path) -> Path | None:
    source = root / "src"
    return source if (source / "archmarshal" / "__init__.py").is_file() else None


def _configured_marketplace_source() -> Path | None:
    executable = shutil.which("codex")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "plugin", "marketplace", "list", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > MAX_MARKETPLACE_LIST_BYTES:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    marketplaces = payload.get("marketplaces") if isinstance(payload, dict) else None
    if not isinstance(marketplaces, list):
        return None
    for item in marketplaces:
        if not isinstance(item, dict) or item.get("name") != MARKETPLACE_NAME:
            continue
        root = item.get("root")
        if isinstance(root, str):
            return _source_below(Path(root))
    return None


def _add_engine_source() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    repository_root = plugin_root.parents[1]
    source = _source_below(repository_root) or _configured_marketplace_source()
    if source is not None:
        sys.path.insert(0, str(source))


def main(argv: list[str] | None = None) -> int:
    _add_engine_source()
    try:
        from archmarshal import __version__
        from archmarshal.cli import main as archmarshal_main
    except (ImportError, ModuleNotFoundError) as exc:
        return _error(
            "archmarshal_engine_unavailable",
            "The Codex plugin requires the reviewed ArchMarshal Python engine.",
            required_version=REQUIRED_ENGINE_VERSION,
            reason=str(exc),
        )
    if __version__ != REQUIRED_ENGINE_VERSION:
        return _error(
            "archmarshal_engine_version_mismatch",
            "Plugin and engine versions must match before any operation.",
            required_version=REQUIRED_ENGINE_VERSION,
            actual_version=__version__,
        )
    return int(archmarshal_main(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
