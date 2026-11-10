"""Workspace boundaries and immutable provenance policy for Skill transfers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import load_yaml_safe
from .portable import digest, fail, portable_key, relative
from .safety import EXCLUDED_BACKUP_PARTS, ensure_unlinked_path
from .skill_paths import SKILL_PATH_FIELDS, SKILL_ROOT_CANDIDATES


def inbox_path(root: Path) -> Path:
    loaded = load_yaml_safe(root / ".agent/workspace.yaml")
    if loaded.error or not isinstance(loaded.data, dict):
        fail("Workspace configuration is invalid.")
    paths = loaded.data.get("paths", {})
    inboxes = paths.get("inbox", [".agent/inbox"]) if isinstance(paths, dict) else None
    if not isinstance(inboxes, list) or not inboxes:
        fail("Workspace inbox mapping must contain at least one relative directory.")
    value = relative(inboxes[0])
    folded = portable_key(value)
    if any(part in EXCLUDED_BACKUP_PARTS for part in folded.split("/")):
        fail("Skill inbox crosses a reserved runtime or VCS boundary.")
    if folded == ".agent" or (folded.startswith(".agent/")
                              and not folded.startswith(".agent/inbox/")
                              and folded != ".agent/inbox"):
        fail("Skill inbox must not overlap internal management state.")
    roots = list(SKILL_ROOT_CANDIDATES)
    for field in (*SKILL_PATH_FIELDS, "source_skill_roots"):
        declared = paths.get(field, [])
        if isinstance(declared, str):
            declared = [declared]
        if not isinstance(declared, list) or not all(isinstance(p, str) for p in declared):
            fail("Invalid Skill discovery mapping.")
        roots.extend(declared)
    for skill_root in roots:
        normalized = skill_root.strip().replace("\\", "/").strip("/")
        # A root-level SKILL.md is entrypoint-only in adoption, not a recursive discovery root.
        if normalized == ".":
            continue
        boundary = portable_key(relative(normalized))
        if (folded == boundary or folded.startswith(boundary + "/")
                or boundary.startswith(folded + "/")):
            fail("Skill inbox overlaps a Skill discovery boundary.")
    return ensure_unlinked_path(root / value, purpose="Skill inbox")


def origin_path(root: Path, source: str) -> Path:
    return root / ".agent/skill-sync-origins" / (digest(source) + ".json")


def validate_receipt(receipt: dict[str, Any], key: str) -> None:
    from .portable import validate_device
    if (set(receipt) != {"format", "object_id", "imported_by", "activation"}
            or receipt["format"] != "archmarshal-sync-receipt-v1"
            or receipt["object_id"] != key or receipt["activation"] != "quarantined"):
        fail("A committed quarantine receipt is required before installation.")
    validate_device(receipt["imported_by"])
