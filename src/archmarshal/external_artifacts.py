"""Inspect explicitly archived files and compatible local delivery manifests."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import ArchMarshalError
from .portable import decode, fail, portable_key, read, relative, sha
from .safety import ensure_unlinked_path

DELIVERY_FORMAT = "codex-external-artifact-delivery-v1"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_DELIVERY_FILES = 100
MAX_DELIVERY_BYTES = 64 * 1024 * 1024


def inspect_files(location: Path, entries: list[dict[str, Any]], *, verify: bool) -> list[dict[str, Any]]:
    results = []
    for entry in entries:
        path = ensure_unlinked_path(location / entry["path"], purpose="Recorded deliverable")
        state = "unverified"
        if not path.exists():
            state = "missing"
        elif verify:
            try:
                raw = read(path, entry["bytes"])
                state = "verified" if len(raw) == entry["bytes"] and sha(raw) == entry["sha256"] else "changed"
            except (ArchMarshalError, OSError):
                state = "unreadable_or_changed"
        results.append({**entry, "absolute_path": str(path), "state": state})
    return results


def inspect_local_delivery(project: Path, location: Path, directory: Path, *,
                           verify: bool, budget: list[int]) -> dict[str, Any]:
    manifest_path = directory / "MANIFEST.json"
    raw = read(manifest_path, MAX_MANIFEST_BYTES)
    manifest = decode(raw)
    if manifest.get("format") != DELIVERY_FORMAT:
        return {"path": str(directory), "state": "unrecognized_manifest"}
    if (manifest.get("project_root") != str(project) or manifest.get("record_root") != str(location)
            or manifest.get("native_archmarshal_state") is not False):
        fail("Local delivery manifest belongs to another project or storage root.", "external_delivery_invalid")
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_DELIVERY_FILES:
        fail("Invalid local delivery file list.", "external_delivery_invalid")
    names = set()
    files = []
    total = 0
    for entry in entries:
        if (not isinstance(entry, dict) or type(entry.get("bytes")) is not int or entry["bytes"] < 0
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            fail("Invalid local delivery file metadata.", "external_delivery_invalid")
        path = relative(entry.get("path"))
        key = portable_key(path)
        if key == "manifest.json" or key in names:
            fail("Local delivery has duplicate or self-referential files.", "external_delivery_invalid")
        names.add(key)
        total += entry["bytes"]
        files.append({"path": path, "bytes": entry["bytes"], "sha256": entry["sha256"]})
    if total > MAX_DELIVERY_BYTES:
        fail("Local delivery exceeds its byte budget.", "external_delivery_invalid")
    inspected = verify and total <= budget[0]
    if inspected:
        budget[0] -= total
    artifacts = inspect_files(directory, files, verify=inspected)
    return {"path": str(directory), "manifest": str(manifest_path), "manifest_sha256": sha(raw),
            "integrity": "legacy_unsealed_manifest", "native_archmarshal_state": False,
            "state": "listed_files_verified" if all(item["state"] == "verified" for item in artifacts)
            else "unverified" if all(item["state"] == "unverified" for item in artifacts) else "changed_or_missing",
            "artifacts": artifacts, "unlisted_files_checked": False}
