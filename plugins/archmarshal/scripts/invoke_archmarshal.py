#!/usr/bin/env python3
"""Invoke only the engine bytes locked to the installed ArchMarshal plugin."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

REQUIRED_ENGINE_VERSION = "0.16.1"
REQUIRED_ENGINE_API = "archmarshal-engine-api-v1"
MARKETPLACE_NAME = "archmarshal"
LOCK_FORMAT = "archmarshal-plugin-engine-lock-v1"
MAX_MARKETPLACE_LIST_BYTES = 1024 * 1024
MAX_LOCK_BYTES = 4 * 1024 * 1024
MAX_ENGINE_FILES = 10_000
MAX_ENGINE_BYTES = 512 * 1024 * 1024


class BootstrapError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _error(code: str, message: str, **details: object) -> int:
    payload: dict[str, object] = {
        "api_version": "archmarshal-plugin-bootstrap-v2",
        "tool": "archmarshal",
        "mode": "error",
        "error": {"code": code, "message": message},
    }
    if details:
        payload["error"]["details"] = details  # type: ignore[index]
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 2


def _portable_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _stable_bytes(path: Path, *, limit: int, label: str) -> bytes:
    try:
        path_before = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(path_before.st_mode):
            raise OSError("not an unlinked regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BootstrapError(
            "archmarshal_engine_lock_invalid",
            f"{label} must be an unlinked regular file.",
            path=str(path),
        ) from exc
    content = bytearray()
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            content.extend(chunk)
            if len(content) > limit:
                raise BootstrapError(
                    "archmarshal_engine_limit_exceeded",
                    f"{label} exceeds its bootstrap size limit.",
                    path=str(path),
                    limit=limit,
                )
        after = os.fstat(source.fileno())
    try:
        path_after = path.lstat()
    except OSError as exc:
        raise BootstrapError(
            "archmarshal_engine_changed",
            f"{label} disappeared during bootstrap verification.",
            path=str(path),
        ) from exc
    identity = (before.st_dev, before.st_ino)
    if (
        (path_before.st_dev, path_before.st_ino) != identity
        or (path_after.st_dev, path_after.st_ino) != identity
        or not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise BootstrapError(
            "archmarshal_engine_changed",
            f"{label} changed during bootstrap verification.",
            path=str(path),
        )
    return bytes(content)


def _read_lock(plugin_root: Path) -> dict[str, Any]:
    lock_path = plugin_root / "engine.lock.json"
    try:
        payload = json.loads(
            _stable_bytes(lock_path, limit=MAX_LOCK_BYTES, label="Plugin engine lock").decode(
                "utf-8"
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(
            "archmarshal_engine_lock_invalid",
            "The plugin engine lock is not valid bounded UTF-8 JSON.",
            path=str(lock_path),
        ) from exc
    if not isinstance(payload, dict):
        raise BootstrapError(
            "archmarshal_engine_lock_invalid",
            "The plugin engine lock must be a JSON object.",
            path=str(lock_path),
        )
    if (
        payload.get("format") != LOCK_FORMAT
        or payload.get("engine_version") != REQUIRED_ENGINE_VERSION
        or payload.get("engine_api") != REQUIRED_ENGINE_API
        or payload.get("source_root") != "src/archmarshal"
    ):
        raise BootstrapError(
            "archmarshal_engine_lock_mismatch",
            "The plugin identity and engine lock do not match.",
            required_version=REQUIRED_ENGINE_VERSION,
            required_api=REQUIRED_ENGINE_API,
        )
    files = payload.get("files")
    if (
        not isinstance(files, list)
        or not isinstance(payload.get("file_count"), int)
        or not isinstance(payload.get("content_bytes"), int)
        or payload["file_count"] != len(files)
        or len(files) > MAX_ENGINE_FILES
        or payload["content_bytes"] > MAX_ENGINE_BYTES
    ):
        raise BootstrapError(
            "archmarshal_engine_lock_invalid",
            "The plugin engine lock exceeds limits or has inconsistent totals.",
        )
    return payload


def _source_below(root: Path) -> Path | None:
    source = root / "src"
    return source if (source / "archmarshal" / "__init__.py").is_file() else None


def _marketplace_roots() -> list[Path]:
    executable = shutil.which("codex")
    if executable is None:
        return []
    try:
        completed = subprocess.run(
            [executable, "plugin", "marketplace", "list", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > MAX_MARKETPLACE_LIST_BYTES:
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    marketplaces = payload.get("marketplaces") if isinstance(payload, dict) else None
    if not isinstance(marketplaces, list):
        return []
    roots: list[Path] = []
    for item in marketplaces:
        if not isinstance(item, dict) or item.get("name") != MARKETPLACE_NAME:
            continue
        root = next(
            (
                item.get(field)
                for field in ("root", "path", "installPath")
                if isinstance(item.get(field), str)
            ),
            None,
        )
        if isinstance(root, str):
            roots.append(Path(root))
    return roots


def _configured_marketplace_source() -> Path | None:
    roots = _marketplace_roots()
    if not roots:
        return None
    unique = {str(path.resolve(strict=False)).casefold(): path for path in roots}
    if len(unique) != 1:
        raise BootstrapError(
            "archmarshal_marketplace_ambiguous",
            "Multiple configured marketplaces use the ArchMarshal identity.",
            marketplace=MARKETPLACE_NAME,
        )
    return _source_below(next(iter(unique.values())))


def _locate_engine_source(plugin_root: Path) -> tuple[Path, str]:
    repository_root = plugin_root.parents[1]
    expected_plugin = repository_root / "plugins" / "archmarshal"
    if expected_plugin.resolve(strict=False) == plugin_root.resolve(strict=False) and (
        repository_root / "pyproject.toml"
    ).is_file():
        return repository_root / "src", "checkout"
    marketplace_source = _configured_marketplace_source()
    if marketplace_source is not None:
        return marketplace_source, "marketplace"
    raise BootstrapError(
        "archmarshal_engine_unavailable",
        "The plugin could not locate its reviewed ArchMarshal marketplace engine.",
        required_version=REQUIRED_ENGINE_VERSION,
        marketplace=MARKETPLACE_NAME,
    )


def _scan_engine(source: Path) -> list[dict[str, Any]]:
    engine = source / "archmarshal"
    try:
        root_metadata = engine.lstat()
    except OSError as exc:
        raise BootstrapError(
            "archmarshal_engine_unavailable",
            "The locked ArchMarshal engine directory is missing.",
            path=str(engine),
        ) from exc
    if engine.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
        raise BootstrapError(
            "archmarshal_engine_invalid",
            "The locked ArchMarshal engine root must be an unlinked directory.",
            path=str(engine),
        )
    records: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    total = 0
    for current, directories, filenames in os.walk(engine, topdown=True, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories, key=str.casefold):
            candidate = current_path / name
            if name == "__pycache__":
                continue
            if candidate.is_symlink():
                raise BootstrapError(
                    "archmarshal_engine_link_unsupported",
                    "The locked engine contains a linked directory.",
                    path=str(candidate),
                )
            kept.append(name)
        directories[:] = kept
        for name in sorted(filenames, key=str.casefold):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = current_path / name
            content = _stable_bytes(path, limit=MAX_ENGINE_BYTES, label="Engine source file")
            relative = path.relative_to(engine).as_posix()
            key = _portable_key(relative)
            if key in seen and seen[key] != relative:
                raise BootstrapError(
                    "archmarshal_engine_portable_collision",
                    "The locked engine contains a portable path collision.",
                    first=seen[key],
                    second=relative,
                )
            seen[key] = relative
            total += len(content)
            if len(records) >= MAX_ENGINE_FILES or total > MAX_ENGINE_BYTES:
                raise BootstrapError(
                    "archmarshal_engine_limit_exceeded",
                    "The locked engine exceeds bootstrap limits.",
                )
            records.append(
                {
                    "path": relative,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
    return sorted(records, key=lambda item: (_portable_key(str(item["path"])), item["path"]))


def _bootstrap() -> dict[str, object]:
    plugin_root = Path(__file__).resolve().parents[1]
    lock = _read_lock(plugin_root)
    source, source_kind = _locate_engine_source(plugin_root)
    records = _scan_engine(source)
    canonical = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual_digest = hashlib.sha256(canonical).hexdigest()
    if (
        records != lock["files"]
        or len(records) != lock["file_count"]
        or sum(int(item["bytes"]) for item in records) != lock["content_bytes"]
        or actual_digest != lock.get("source_tree_sha256")
    ):
        raise BootstrapError(
            "archmarshal_engine_lock_verification_failed",
            "The located engine bytes do not match this installed plugin.",
            source=str(source),
            expected_sha256=lock.get("source_tree_sha256"),
            actual_sha256=actual_digest,
        )
    return {
        "source": source,
        "source_kind": source_kind,
        "engine_version": lock["engine_version"],
        "engine_api": lock["engine_api"],
        "source_tree_sha256": actual_digest,
        "file_count": len(records),
        "content_bytes": sum(int(item["bytes"]) for item in records),
        "verified": True,
    }


def _bootstrap_status(identity: dict[str, object]) -> int:
    payload = {
        "api_version": "archmarshal-plugin-bootstrap-v2",
        "tool": "archmarshal",
        "mode": "ready",
        "marketplace": MARKETPLACE_NAME,
        "dependency_imported": False,
        **{key: str(value) if isinstance(value, Path) else value for key, value in identity.items()},
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv or [])
    try:
        identity = _bootstrap()
    except BootstrapError as exc:
        return _error(exc.code, exc.message, **exc.details)
    if arguments == ["--bootstrap-status"]:
        return _bootstrap_status(identity)
    source = identity["source"]
    assert isinstance(source, Path)
    sys.path.insert(0, str(source))
    try:
        import archmarshal as engine
        from archmarshal import cli as engine_cli
    except (ImportError, ModuleNotFoundError) as exc:
        return _error(
            "archmarshal_engine_dependency_unavailable",
            "The verified engine could not import its declared Python dependencies.",
            required_version=REQUIRED_ENGINE_VERSION,
            reason=str(exc),
        )
    expected_init = (source / "archmarshal" / "__init__.py").resolve()
    actual_init = Path(str(engine.__file__)).resolve()
    if actual_init != expected_init:
        return _error(
            "archmarshal_engine_origin_mismatch",
            "Python loaded ArchMarshal from outside the verified plugin engine.",
            expected=str(expected_init),
            actual=str(actual_init),
        )
    if engine.__version__ != REQUIRED_ENGINE_VERSION:
        return _error(
            "archmarshal_engine_version_mismatch",
            "Plugin and engine versions must match before any operation.",
            required_version=REQUIRED_ENGINE_VERSION,
            actual_version=engine.__version__,
        )
    if getattr(engine, "ENGINE_API_VERSION", None) != REQUIRED_ENGINE_API:
        return _error(
            "archmarshal_engine_api_mismatch",
            "Plugin and engine API identities must match before any operation.",
            required_api=REQUIRED_ENGINE_API,
            actual_api=getattr(engine, "ENGINE_API_VERSION", None),
        )
    return int(engine_cli.main(arguments))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
