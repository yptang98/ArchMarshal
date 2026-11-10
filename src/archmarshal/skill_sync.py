"""Portable Skill snapshots, self-declared device lineage, and quarantined imports."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .io import load_yaml_safe
from .ownership import require_owned_workspace
from .portable import (
    canonical,
    checked_plan,
    decode,
    device,
    digest,
    fail,
    is_digest,
    portable_key,
    publish,
    read,
    relative,
)
from .safety import EXCLUDED_BACKUP_PARTS, ensure_unlinked_path
from .skill_index import load_skill_index
from .skill_package import (
    FORMAT,
    materialize_package,
    scan_package,
    validate_snapshot,
    verify_package,
)
from .skill_paths import SKILL_PATH_FIELDS, SKILL_ROOT_CANDIDATES
from .skill_validation import validate_skill_package
from .sync_transport import Transport
from .workspace_lock import workspace_mutation_lock

MAX_SYNC_TOTAL = 64 * 1024 * 1024


def _inbox(root: Path) -> Path:
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


def _origin_path(root: Path, source: str) -> Path:
    return root / ".agent/skill-sync-origins" / (digest(source) + ".json")


def _validate_receipt(receipt: dict[str, Any], key: str) -> None:
    from .portable import validate_device
    if (set(receipt) != {"format", "object_id", "imported_by", "activation"}
            or receipt["format"] != "archmarshal-sync-receipt-v1"
            or receipt["object_id"] != key or receipt["activation"] != "quarantined"):
        fail("A committed quarantine receipt is required before installation.")
    validate_device(receipt["imported_by"])


def snapshot_skill(root: Path | str, source: str, *, device_path: Path | str | None = None
                   ) -> dict[str, Any]:
    root = ensure_unlinked_path(Path(root), purpose="Skill workspace")
    with workspace_mutation_lock(root, operation="sync_snapshot"):
        return _snapshot_skill(root, source, device_path=device_path)


def _snapshot_skill(root: Path, source: str, *, device_path: Path | str | None
                    ) -> dict[str, Any]:
    root = require_owned_workspace(root, operation="Skill sync")
    source = relative(source)
    loaded = load_skill_index(root)
    generation = loaded.get("generation") or {}
    excluded = generation.get("selection", {}).get("excluded_packages", [])
    if any(portable_key(source) == portable_key(value) or
           portable_key(source).startswith(portable_key(value) + "/") for value in excluded):
        fail("Selected Skill is excluded from management.")
    records = generation.get("skills", [])
    record = next((r for r in records if r["source"] == source and r["state"] == "active"), None)
    if record is None:
        fail("Adopt the selected Skill into the verified index before syncing.")
    boundaries = {root / r["source"] for r in records if r["source"] != source}
    boundaries.update(root / value for value in excluded)
    identity = device(device_path)
    directory = root / source
    files, directories = scan_package(directory, boundaries=boundaries)
    if (files, directories) != scan_package(directory, boundaries=boundaries):
        fail("Skill source changed while preparing the portable snapshot.", "sync_source_changed")
    snapshot = {"format": FORMAT, "name": str(record["manifest"].get("name") or directory.name),
                "origin": identity, "exporter": identity, "parents": [],
                "files": files, "directories": directories}
    # An imported package keeps its original origin even after local modifications.
    receipt = directory.parent / "RECEIPT.json"
    original = directory.parent / "snapshot.json"
    origin_path = _origin_path(root, source)
    old = None
    old_id = None
    if origin_path.exists():
        origin_record = decode(read(origin_path))
        if (set(origin_record) != {"format", "source", "object_id", "snapshot"}
                or origin_record["format"] != "archmarshal-sync-origin-v1"
                or origin_record["source"] != source):
            fail("Invalid Skill installation origin record.")
        old_id = origin_record.get("object_id")
        old = validate_snapshot(canonical(origin_record.get("snapshot")), old_id)
    elif directory.name == "package" and receipt.exists() and original.exists():
        receipt_data = decode(read(receipt))
        old_id = receipt_data.get("object_id", "")
        _validate_receipt(receipt_data, old_id)
        old = validate_snapshot(read(original), old_id)
    if old is not None:
        snapshot["name"] = old["name"]
        snapshot["origin"] = old["origin"]
        snapshot["parents"] = [old_id]
        if os.name == "nt":
            modes = {f["path"]: f for f in old["files"]}
            for entry in files:
                previous = modes.get(entry["path"], {})
                if previous.get("sha256") == entry["sha256"]:
                    entry["executable"] = previous["executable"]
    validate_snapshot(canonical(snapshot), digest(snapshot))
    return snapshot


def sync_status(store: Transport) -> dict[str, Any]:
    objects = []
    names: dict[str, list[str]] = {}
    total = 0
    for key in store.keys():
        raw = store.get(key)
        total += len(raw)
        if total > MAX_SYNC_TOTAL:
            fail("Sync catalog exceeds the read budget; use a smaller repository.")
        snapshot = validate_snapshot(raw, key)
        objects.append({"object_id": key, "name": snapshot["name"],
                        "origin": snapshot["origin"], "exporter": snapshot["exporter"],
                        "parents": snapshot["parents"], "file_count": len(snapshot["files"])})
        names.setdefault(snapshot["name"], []).append(key)
    return {"mode": "read_only", "remote": store.describe(), "objects": objects,
            "variants": {name: keys for name, keys in names.items() if len(keys) > 1},
            "selection_policy": "explicit-object-id; no last-writer-wins"}


def push_skill(root: Path | str, source: str, store: Transport, *,
               device_path: Path | str | None = None, allow_public: bool = False,
               apply: bool = False, expected_plan: str | None = None) -> dict[str, Any]:
    snapshot = snapshot_skill(root, source, device_path=device_path)
    key = digest(snapshot)
    remote = store.describe()
    plan = {"operation": "sync_push", "source": source, "root": str(Path(root).resolve()),
            "remote": remote, "object_id": key, "allow_public": allow_public}
    result = {"mode": "preview", "plan": plan, "plan_digest": digest(plan),
              "name": snapshot["name"], "origin": snapshot["origin"],
              "exporter": snapshot["exporter"],
              "files": [{k: v for k, v in entry.items() if k != "content"}
                        for entry in snapshot["files"]],
              "public_upload_blocked": remote.get("public") is True and not allow_public}
    if apply:
        checked_plan(plan, expected_plan)
        if result["public_upload_blocked"]:
            fail("Public upload requires explicit --allow-public.", "sync_public_review_required")
        # Bytes in this snapshot were re-read for this exact preview digest.
        changed = store.put(key, canonical(snapshot))
        result["mode"] = "uploaded" if changed else "unchanged"
    return result


def install_skill(root: Path | str, key: str, destination: str, *,
                  apply: bool = False, expected_plan: str | None = None) -> dict[str, Any]:
    """Copy a reviewed quarantine package to a new explicit source, retaining lineage.

    Existing ArchMarshal adoption and exact package review remain separate operations.
    """
    root = require_owned_workspace(ensure_unlinked_path(Path(root), purpose="Skill workspace"),
                                   operation="Skill sync install")
    relative(destination)
    if any(part in EXCLUDED_BACKUP_PARTS | {".agent"}
           for part in portable_key(destination).split("/")):
        fail("Install destination crosses a reserved project boundary.")
    target = ensure_unlinked_path(root / destination, purpose="Reviewed Skill destination")
    if target.exists():
        fail("Install destination already exists; choose a new directory.")
    generation = load_skill_index(root).get("generation") or {}
    for excluded in generation.get("selection", {}).get("excluded_packages", []):
        if (portable_key(destination) == portable_key(excluded)
                or portable_key(destination).startswith(portable_key(excluded) + "/")):
            fail("Install destination is excluded from management.")
    if not is_digest(key):
        fail("Invalid snapshot object ID.")
    quarantine = _inbox(root) / "skill-sync" / key
    receipt = decode(read(quarantine / "RECEIPT.json"))
    _validate_receipt(receipt, key)
    snapshot = validate_snapshot(read(quarantine / "snapshot.json"), key)
    verify_package(quarantine / "package", snapshot)
    validation = validate_skill_package(quarantine / "package", enforce_folder_name=False)
    if not validation["valid"]:
        fail("Imported Skill fails the existing package validation contract.")
    if target.name != validation["frontmatter"]["name"]:
        fail("Destination directory name must match the Skill frontmatter name.")
    # Review is bound to the sealed snapshot, not an edited quarantine working copy.
    plan = {"operation": "sync_install", "root": str(root), "object_id": key,
            "destination": destination, "origin": snapshot["origin"],
            "file_digests": [{"path": entry["path"], "sha256": entry["sha256"]}
                             for entry in snapshot["files"]]}
    result = {"mode": "preview", "plan": plan, "plan_digest": digest(plan),
              "activation": "requires-adoption-and-skill-review", "source": destination}
    if not apply:
        return result
    checked_plan(plan, expected_plan)
    with workspace_mutation_lock(root, operation="sync_install"):
        # Rebuild the preview under the mutation lock, including exclusions and inbox mapping.
        locked_plan = install_skill(root, key, destination)["plan"]
        checked_plan(locked_plan, expected_plan)
        if target.exists():
            fail("Install destination already exists; no files were overwritten.")
        origin_path = _origin_path(root, destination)
        if origin_path.exists():
            fail("An earlier installation receipt already owns this destination.")
        materialize_package(target, snapshot)
        publish(origin_path, canonical({"format": "archmarshal-sync-origin-v1", "source": destination,
                                       "object_id": key, "snapshot": snapshot}))
    result["mode"] = "installed"
    return result


def pull_skill(root: Path | str, key: str, store: Transport, *,
               device_path: Path | str | None = None, apply: bool = False,
               expected_plan: str | None = None) -> dict[str, Any]:
    root = require_owned_workspace(ensure_unlinked_path(Path(root), purpose="Skill workspace"),
                                   operation="Skill sync import")
    identity = device(device_path)
    snapshot = validate_snapshot(store.get(key), key)
    inbox = _inbox(root)
    target = ensure_unlinked_path(inbox / "skill-sync" / key, purpose="Skill quarantine")
    plan = {"operation": "sync_pull", "remote": store.describe(), "object_id": key,
            "destination": str(target), "device": identity, "activation": "quarantined"}
    result = {"mode": "preview", "plan": plan, "plan_digest": digest(plan),
              "name": snapshot["name"], "origin": snapshot["origin"],
              "package": str(target / "package"), "activation": "quarantined",
              "files": [{k: v for k, v in entry.items() if k != "content"}
                        for entry in snapshot["files"]]}
    if not apply:
        return result
    checked_plan(plan, expected_plan)
    with workspace_mutation_lock(root, operation="sync_pull"):
        require_owned_workspace(root, operation="Skill sync import")
        if _inbox(root) != inbox:
            fail("Workspace inbox mapping changed; review a fresh preview.", "portability_stale_plan")
        receipt_data = {"format": "archmarshal-sync-receipt-v1", "object_id": key,
                        "imported_by": identity, "activation": "quarantined"}
        if target.exists():
            if not (target / "RECEIPT.json").exists():
                fail("Partial import exists; preserve it and choose a fresh inbox location.")
            if decode(read(target / "RECEIPT.json")) != receipt_data:
                fail("Import receipt differs.")
            validate_snapshot(read(target / "snapshot.json"), key)
            verify_package(target / "package", snapshot)
            result["mode"] = "unchanged"
            return result
        target.mkdir(parents=True)
        materialize_package(target / "package", snapshot)
        publish(target / "snapshot.json", canonical(snapshot))
        publish(target / "RECEIPT.json", canonical(receipt_data))
    result["mode"] = "imported"
    return result
