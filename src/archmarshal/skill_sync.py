"""Portable Skill snapshots, self-declared device lineage, and quarantined imports."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from .io import load_yaml_safe
from .ownership import require_owned_workspace
from .portable import (
    MAX_FILES,
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
    sha,
    text,
    validate_device,
)
from .safety import EXCLUDED_BACKUP_PARTS, ensure_unlinked_path, is_link_or_reparse
from .session import _sensitive_text_errors
from .skill_index import load_skill_index
from .skill_validation import validate_skill_package
from .sync_transport import Transport
from .workspace_lock import workspace_mutation_lock

FORMAT = "archmarshal-skill-sync-v1"
MAX_CONTENT = 4 * 1024 * 1024
MAX_SYNC_TOTAL = 64 * 1024 * 1024


def _inbox(root: Path) -> Path:
    loaded = load_yaml_safe(root / ".agent/workspace.yaml")
    if loaded.error or not isinstance(loaded.data, dict):
        fail("Workspace configuration is invalid.")
    paths = loaded.data.get("paths", {})
    inboxes = paths.get("inbox", [".agent/inbox"]) if isinstance(paths, dict) else None
    if not isinstance(inboxes, list) or not inboxes:
        fail("Workspace inbox mapping must contain at least one relative directory.")
    return ensure_unlinked_path(root / relative(inboxes[0]), purpose="Skill inbox")


def _safe_content(path: str, raw: bytes) -> None:
    name = Path(path).name.casefold()
    if name in {".env", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_ed25519"}:
        fail("A credential filename is inside the selected package; remove it from sync scope.")
    content = raw.decode("utf-8", errors="replace")
    if "PRIVATE KEY-----" in content or _sensitive_text_errors(content=content):
        fail("Selected package appears to contain a secret; inspect it before syncing.")


def _scan(directory: Path, *, boundaries: set[Path] | None = None,
          root_entrypoint: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    ensure_unlinked_path(directory, purpose="Skill source")
    if not directory.is_dir():
        fail("Skill package directory does not exist.")
    boundaries = boundaries or set()
    files: list[dict[str, Any]] = []
    directories: list[str] = []
    seen: set[str] = set()
    total = 0
    def scan_error(error: OSError) -> None:
        raise error
    for current, children, filenames in os.walk(directory, followlinks=False, onerror=scan_error):
        parent = Path(current)
        kept = []
        for name in sorted(children):
            child = parent / name
            if (root_entrypoint or name in EXCLUDED_BACKUP_PARTS or child in boundaries
                    or (child / "SKILL.md").exists()):
                continue
            if is_link_or_reparse(child):
                fail("Linked directories cannot be synced.")
            value = relative(child.relative_to(directory).as_posix())
            key = portable_key(value)
            if key in seen:
                fail("Portable package path collision.")
            seen.add(key)
            directories.append(value)
            kept.append(name)
        children[:] = kept
        for name in sorted(filenames):
            if name in EXCLUDED_BACKUP_PARTS or (root_entrypoint and name != "SKILL.md"):
                continue
            path = parent / name
            value = relative(path.relative_to(directory).as_posix())
            key = portable_key(value)
            if key in seen:
                fail("Portable package path collision.")
            seen.add(key)
            raw = read(path, MAX_CONTENT - total)
            total += len(raw)
            _safe_content(value, raw)
            files.append({"path": value, "sha256": sha(raw), "bytes": len(raw),
                          "executable": bool(path.stat().st_mode & 0o111),
                          "content": base64.b64encode(raw).decode("ascii")})
        if len(files) + len(directories) > MAX_FILES:
            fail("Skill package exceeds its file budget.")
    if "SKILL.md" not in {item["path"] for item in files}:
        fail("Selected package must have SKILL.md.")
    return sorted(files, key=lambda item: item["path"]), sorted(directories)


def validate_snapshot(raw: bytes, key: str) -> dict[str, Any]:
    snapshot = decode(raw)
    if not is_digest(key) or digest(snapshot) != key:
        fail("Portable snapshot digest does not match its object ID.", "sync_integrity_failed")
    fields = {"format", "name", "origin", "exporter", "parents", "files", "directories"}
    if set(snapshot) != fields or snapshot["format"] != FORMAT:
        fail("Unsupported Skill snapshot format.")
    text(snapshot["name"], "Skill name", 200, required=True)
    validate_device(snapshot["origin"])
    validate_device(snapshot["exporter"])
    parents = snapshot["parents"]
    if not isinstance(parents, list) or len(parents) > 100 or not all(map(is_digest, parents)):
        fail("Invalid snapshot lineage.")
    files, directories = snapshot["files"], snapshot["directories"]
    if not isinstance(files, list) or not isinstance(directories, list):
        fail("Invalid package entries.")
    if not files or len(files) + len(directories) > MAX_FILES:
        fail("Package entry limit exceeded.")
    seen: set[str] = set()
    file_keys: set[str] = set()
    total = 0
    for value in directories:
        key_value = portable_key(relative(value))
        if key_value in seen:
            fail("Duplicate package path.")
        seen.add(key_value)
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {
            "path", "sha256", "bytes", "executable", "content"
        }:
            fail("Invalid package file record.")
        path = relative(entry["path"])
        key_value = portable_key(path)
        if key_value in seen or type(entry["executable"]) is not bool:
            fail("Duplicate path or invalid executable bit.")
        seen.add(key_value)
        file_keys.add(key_value)
        try:
            content = base64.b64decode(entry["content"], validate=True)
        except (ValueError, TypeError):
            fail("Invalid package file encoding.")
        if type(entry["bytes"]) is not int or entry["bytes"] != len(content):
            fail("Package file size mismatch.")
        if sha(content) != entry["sha256"]:
            fail("Package file digest mismatch.")
        total += len(content)
        if total > MAX_CONTENT:
            fail("Package exceeds its content budget.")
        _safe_content(path, content)
    for path in seen:
        if any(part in EXCLUDED_BACKUP_PARTS for part in path.split("/")):
            fail("Portable package contains a preserved runtime or VCS boundary.")
        if path.endswith("/skill.md"):
            fail("Nested Skills must travel as separate packages.")
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in file_keys or parent not in seen:
                fail("Invalid package directory topology.")
    if not any(entry["path"] == "SKILL.md" for entry in files):
        fail("Portable Skill has no SKILL.md.")
    return snapshot


def _origin_path(root: Path, source: str) -> Path:
    return root / ".agent/skill-sync-origins" / (digest(source) + ".json")


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
    files, directories = _scan(directory, boundaries=boundaries)
    if (files, directories) != _scan(directory, boundaries=boundaries):
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
        old_id = origin_record.get("object_id")
        old = validate_snapshot(canonical(origin_record.get("snapshot")), old_id)
    elif directory.name == "package" and receipt.exists() and original.exists():
        receipt_data = decode(read(receipt))
        old_id = receipt_data.get("object_id", "")
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
    if any(part in EXCLUDED_BACKUP_PARTS | {".agent"} for part in destination.split("/")):
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
    if receipt.get("object_id") != key or receipt.get("activation") != "quarantined":
        fail("A committed quarantine receipt is required before installation.")
    snapshot = validate_snapshot(read(quarantine / "snapshot.json"), key)
    actual_files, actual_dirs = _scan(quarantine / "package")
    expected_files = snapshot["files"]
    if os.name == "nt":
        actual_files = [{**f, "executable": False} for f in actual_files]
        expected_files = [{**f, "executable": False} for f in expected_files]
    if actual_files != expected_files or actual_dirs != snapshot["directories"]:
        fail("Quarantine files changed; inspect the sealed snapshot before installation.")
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
        if target.exists():
            fail("Install destination already exists; no files were overwritten.")
        origin_path = _origin_path(root, destination)
        if origin_path.exists():
            fail("An earlier installation receipt already owns this destination.")
        target.mkdir(parents=True)
        for name in snapshot["directories"]:
            (target / name).mkdir(parents=True, exist_ok=True)
        for entry in snapshot["files"]:
            path = target / entry["path"]
            publish(path, base64.b64decode(entry["content"], validate=True))
            if os.name != "nt":
                path.chmod(0o755 if entry["executable"] else 0o644)
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
        receipt_data = {"format": "archmarshal-sync-receipt-v1", "object_id": key,
                        "imported_by": identity, "activation": "quarantined"}
        if target.exists():
            if not (target / "RECEIPT.json").exists():
                fail("Partial import exists; preserve it and choose a fresh inbox location.")
            if decode(read(target / "RECEIPT.json")) != receipt_data:
                fail("Import receipt differs.")
            validate_snapshot(read(target / "snapshot.json"), key)
            actual_files, actual_dirs = _scan(target / "package")
            expected_files = snapshot["files"]
            if os.name == "nt":
                actual_files = [{**f, "executable": False} for f in actual_files]
                expected_files = [{**f, "executable": False} for f in expected_files]
            if actual_files != expected_files or actual_dirs != snapshot["directories"]:
                fail("Existing imported package was modified; no files were overwritten.")
            result["mode"] = "unchanged"
            return result
        target.mkdir(parents=True)
        for name in snapshot["directories"]:
            (target / "package" / name).mkdir(parents=True, exist_ok=True)
        for entry in snapshot["files"]:
            path = target / "package" / entry["path"]
            publish(path, base64.b64decode(entry["content"], validate=True))
            if os.name != "nt":
                path.chmod(0o755 if entry["executable"] else 0o644)
        publish(target / "snapshot.json", canonical(snapshot))
        publish(target / "RECEIPT.json", canonical(receipt_data))
    result["mode"] = "imported"
    return result
