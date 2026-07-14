from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PLACEHOLDER_FILES = {".gitkeep"}


@dataclass(frozen=True)
class YamlLoadResult:
    data: Any
    error: str | None = None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> Any:
    result = load_yaml_safe(path)
    if result.error:
        raise ValueError(result.error)
    return result.data


def load_yaml_safe(path: Path) -> YamlLoadResult:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return YamlLoadResult(yaml.safe_load(handle) or {})
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return YamlLoadResult({}, str(exc))


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_placeholder(path: Path) -> bool:
    return path.name in PLACEHOLDER_FILES


def list_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and not is_placeholder(item)
    )
