from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


PLACEHOLDER_FILES = {".gitkeep"}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


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

