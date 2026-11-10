"""Bounded adoption backup selection and package-coverage evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ArchMarshalError
from .safety import (
    EXCLUDED_BACKUP_PARTS,
    MAX_DIRECTORY_SCAN_FILES,
    files_below_no_links,
    is_link_or_reparse,
)

MANAGED_BACKUP_EXCLUDED_AGENT_ROOTS = frozenset(
    {
        "backups",
        "cache",
        "history",
        "inbox",
        "transactions",
        "operations",
        "context-staging",
    }
)


def managed_backup_files(
    root: Path,
    skills: list[dict[str, Any]],
    *,
    excluded_packages: list[str] | None = None,
    reserved_files: tuple[str, ...] = (),
) -> list[Path]:
    files: set[Path] = set()
    for relative in reserved_files:
        candidate = root / relative
        if candidate.is_file():
            files.add(candidate)
    agent_root = root / ".agent"
    if agent_root.exists():
        if is_link_or_reparse(agent_root) or not agent_root.is_dir():
            # Preserve the previous fail-closed behavior for the managed root.
            files_below_no_links(agent_root, purpose="Managed metadata backup")
        agent_files: list[Path] = []
        for child in sorted(agent_root.iterdir(), key=lambda path: path.name.casefold()):
            if child.name.casefold() in MANAGED_BACKUP_EXCLUDED_AGENT_ROOTS:
                continue
            if is_link_or_reparse(child):
                # The bounded scanner never followed linked children. Keep that
                # behavior while avoiding traversal of excluded runtime stores.
                continue
            if child.is_file():
                if len(agent_files) >= MAX_DIRECTORY_SCAN_FILES:
                    raise ArchMarshalError(
                        "directory_scan_limit_exceeded",
                        "Managed metadata backup exceeds the bounded file scan limit.",
                        details={"path": str(agent_root)},
                    )
                agent_files.append(child)
            elif child.is_dir():
                remaining = MAX_DIRECTORY_SCAN_FILES - len(agent_files)
                agent_files.extend(
                    files_below_no_links(
                        child,
                        purpose="Managed metadata backup",
                        max_files=remaining,
                    )
                )
        files.update(path for path in agent_files if path.is_file())
    boundaries = {(root / PurePosixPath(str(skill["source"]))).resolve() for skill in skills}
    boundaries.update(
        (root / PurePosixPath(source)).resolve() for source in (excluded_packages or [])
    )
    for skill in skills:
        source_dir = root / skill["source"]
        if source_dir.resolve() == root:
            for name in ("SKILL.md", "manifest.yaml"):
                candidate = source_dir / name
                if candidate.is_file():
                    files.add(candidate)
        else:
            files.update(
                path
                for path in files_below_no_links(
                    source_dir,
                    purpose="Source skill package backup",
                    max_files=10_000,
                    excluded_parts=EXCLUDED_BACKUP_PARTS,
                    excluded_directories=[
                        boundary
                        for boundary in boundaries
                        if boundary != source_dir.resolve()
                        and source_dir.resolve() in boundary.parents
                    ],
                )
                if path.is_file()
            )
    return sorted(files)


def skill_backup_coverage(
    root: Path,
    skills: list[dict[str, Any]],
    backup_records: list[dict[str, Any]],
) -> dict[str, Any]:
    by_path = {
        str(record["path"]): record
        for record in backup_records
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    packages: list[dict[str, Any]] = []
    all_complete = True
    for skill in skills:
        source = str(skill["source"])
        package_files = skill.get("package_files")
        if not isinstance(package_files, list):
            package_files = []
        expected_paths = []
        for record in package_files:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                continue
            expected_paths.append(record["path"] if source == "." else f"{source}/{record['path']}")
        covered = [by_path[path] for path in expected_paths if path in by_path]
        missing = sorted(set(expected_paths) - set(by_path), key=str.casefold)
        manifest_source = skill.get("manifest", {}).get("source", {})
        expected_count = (
            manifest_source.get("package_file_count") if isinstance(manifest_source, dict) else None
        )
        complete = (
            isinstance(expected_count, int)
            and expected_count == len(expected_paths)
            and not missing
            and len(covered) == len(expected_paths)
        )
        all_complete = all_complete and complete
        coverage_bytes = json.dumps(
            covered,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        packages.append(
            {
                "source": source,
                "package_sha256": manifest_source.get("package_sha256")
                if isinstance(manifest_source, dict)
                else None,
                "expected_file_count": expected_count,
                "planned_backup_file_count": len(covered),
                "backup_records_sha256": hashlib.sha256(coverage_bytes).hexdigest(),
                "missing": missing,
                "complete": complete,
                "boundary": "workspace-root-entrypoint-only"
                if source == "."
                else "skill-directory",
            }
        )
    return {
        "format": "archmarshal-skill-backup-coverage-v1",
        "complete": all_complete,
        "discovered_package_count": len(skills),
        "covered_package_count": sum(item["complete"] for item in packages),
        "packages": sorted(packages, key=lambda item: str(item["source"]).casefold()),
        "source_mutation": False,
    }
