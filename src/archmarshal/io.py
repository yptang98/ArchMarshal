from __future__ import annotations

import hashlib
import math
import os
import stat
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .safety import files_below_no_links, is_link_or_reparse

PLACEHOLDER_FILES = {".gitkeep"}
MAX_YAML_BYTES = 8 * 1024 * 1024
MAX_YAML_ALIASES = 256
MAX_YAML_NODES = 100_000
MAX_YAML_DEPTH = 100


@dataclass(frozen=True)
class YamlLoadResult:
    data: Any
    error: str | None = None
    byte_count: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class StableBytesResult:
    data: bytes
    error: str | None = None
    byte_count: int | None = None
    sha256: str | None = None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> Any:
    result = load_yaml_safe(path)
    if result.error:
        raise ValueError(result.error)
    return result.data


def load_yaml_safe(path: Path) -> YamlLoadResult:
    loaded = read_bytes_safe(path, max_bytes=MAX_YAML_BYTES, label="YAML")
    if loaded.error:
        return YamlLoadResult({}, loaded.error)
    try:
        text = loaded.data.decode("utf-8")
        alias_count = sum(
            isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(text)
        )
        if alias_count > MAX_YAML_ALIASES:
            return YamlLoadResult({}, f"YAML exceeds the {MAX_YAML_ALIASES}-alias safety limit")
        data = yaml.safe_load(text) or {}
        _validate_yaml_graph(data)
        return YamlLoadResult(
            _normalize_yaml_graph(data),
            byte_count=loaded.byte_count,
            sha256=loaded.sha256,
        )
    except (UnicodeDecodeError, yaml.YAMLError, ValueError, RecursionError) as exc:
        return YamlLoadResult({}, str(exc))


def read_bytes_safe(path: Path, *, max_bytes: int, label: str = "File") -> StableBytesResult:
    try:
        if is_link_or_reparse(path):
            return StableBytesResult(b"", f"{label} path must not be a symbolic link or junction")
        path_before = path.lstat()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > max_bytes:
                return StableBytesResult(b"", f"{label} exceeds the {max_bytes}-byte safety limit")
            raw = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
        path_after = path.lstat()
        if (
            (path_before.st_dev, path_before.st_ino) != (before.st_dev, before.st_ino)
            or (path_after.st_dev, path_after.st_ino) != (before.st_dev, before.st_ino)
            or not stat.S_ISREG(before.st_mode)
            or is_link_or_reparse(path)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(raw) != after.st_size
        ):
            return StableBytesResult(b"", f"{label} changed while it was being read")
        if len(raw) > max_bytes:
            return StableBytesResult(b"", f"{label} exceeds the {max_bytes}-byte safety limit")
        return StableBytesResult(
            raw,
            byte_count=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    except OSError as exc:
        return StableBytesResult(b"", str(exc))


def _validate_yaml_graph(value: Any) -> None:
    node_count = 0

    def visit(item: Any, depth: int, ancestors: set[int]) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_YAML_NODES:
            raise ValueError(f"YAML exceeds the {MAX_YAML_NODES}-node safety limit")
        if depth > MAX_YAML_DEPTH:
            raise ValueError(f"YAML exceeds the {MAX_YAML_DEPTH}-level depth safety limit")
        if item is None or isinstance(item, (str, bool, int, date, datetime)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("YAML contains a non-finite number")
            return
        if not isinstance(item, (dict, list)):
            raise ValueError("YAML must contain only JSON-compatible mappings, lists, and scalars")
        identity = id(item)
        if identity in ancestors:
            raise ValueError("YAML contains a recursive alias")
        nested_ancestors = {*ancestors, identity}
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ValueError("YAML mapping keys must be strings")
                visit(nested, depth + 1, nested_ancestors)
        else:
            for nested in item:
                visit(nested, depth + 1, nested_ancestors)

    visit(value, 0, set())


def _normalize_yaml_graph(value: Any) -> Any:
    """Return the validated YAML graph using only deterministic JSON scalars."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize_yaml_graph(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_graph(item) for item in value]
    return value


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_placeholder(path: Path) -> bool:
    return path.name in PLACEHOLDER_FILES


def list_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(
        item
        for item in files_below_no_links(path, purpose="File inventory")
        if item.is_file() and not is_placeholder(item)
    )
