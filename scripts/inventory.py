#!/usr/bin/env python3
"""Read-only ArchMarshal workspace inventory.

This prototype intentionally does not modify files. It scans a project root for
the expected ArchMarshal/Codex governance files and prints a compact JSON
summary that later lint and audit commands can consume.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_AGENT_DIRS = [
    ".agent/knowledge",
    ".agent/context-modules",
    ".agent/reports",
    ".agent/plans",
    ".agent/history",
    ".agent/inbox",
    ".agent/archive",
    ".agent/cache",
    ".agents/skills",
]

IGNORED_PLACEHOLDERS = {".gitkeep"}


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(
        1
        for item in path.rglob("*")
        if item.is_file() and item.name not in IGNORED_PLACEHOLDERS
    )


def list_manifest_dirs(skill_root: Path, root: Path) -> list[dict[str, str]]:
    if not skill_root.exists():
        return []
    manifests = []
    for manifest in skill_root.rglob("manifest.yaml"):
        manifests.append(
            {
                "manifest": rel(manifest, root),
                "skill_dir": rel(manifest.parent, root),
            }
        )
    return sorted(manifests, key=lambda item: item["manifest"])


def inventory(root: Path) -> dict[str, object]:
    root = root.resolve()
    agent_root = root / ".agent"
    skills_root = root / ".agents" / "skills"

    expected_files = {
        "agents_md": root / "AGENTS.md",
        "workspace_yaml": agent_root / "workspace.yaml",
        "index_md": agent_root / "INDEX.md",
        "registry_yaml": agent_root / "registry.yaml",
    }

    expected_dirs = {
        item: {
            "exists": (root / item).exists(),
            "file_count": count_files(root / item),
        }
        for item in DEFAULT_AGENT_DIRS
    }

    return {
        "tool": "archmarshal-inventory",
        "version": "0.1.0",
        "root": str(root),
        "files": {
            key: {
                "path": rel(path, root),
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.exists() else 0,
            }
            for key, path in expected_files.items()
        },
        "directories": expected_dirs,
        "skill_manifests": list_manifest_dirs(skills_root, root),
        "notes": [
            "Read-only scan; no files were modified.",
            "Historical artifact directories should not be loaded by default.",
            "Use this output as input to future lint, audit, and plan commands.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ArchMarshal inventory")
    parser.add_argument("root", nargs="?", default=".", help="Project root to scan")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    result = inventory(Path(args.root))
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
