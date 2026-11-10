"""Immutable context generations with bounded hot reads and exact cold lookup.

An archive commit seals the previous index and carries its terminal records into
the next generation. Original indexes and record files remain in place.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .portable import decode, digest, fail, is_digest, read, relative, strict_files
from .safety import ensure_unlinked_path, workspace_root_id

INDEX = Path(".agent/context-index")
ARCHIVES = Path(".agent/context-archives")
ARCHIVE_FORMAT = "archmarshal-context-archive-v1"
MAX_GENERATIONS = 1_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 32 * 1024 * 1024
MAX_GENERATION_RECORDS = 1_000


def index_path(number: int) -> Path:
    return INDEX if number == 0 else Path(f".agent/context-index-g{number:06d}")


def validate_pointer(pointer: Any, key: str) -> None:
    if (not isinstance(pointer, dict) or set(pointer) != {"format", "path", "id"}
            or not is_digest(key) or pointer.get("id") != key
            or pointer.get("format") != "archmarshal-context-pointer-v1"):
        fail("Invalid context index pointer.")
    relative(pointer["path"])


def archive_catalog(root: Path) -> list[dict[str, Any]]:
    directory = ensure_unlinked_path(root / ARCHIVES, purpose="Context archive catalog")
    if not directory.exists():
        return []
    paths = sorted(strict_files(directory, limit=MAX_GENERATIONS))
    result = []
    total = 0
    previous = None
    for number, path in enumerate(paths, start=1):
        if path.parent != directory or path.name != f"{number:06d}.json":
            fail("Context archive catalog has an unexpected entry or generation gap.")
        raw = read(path, MAX_ARCHIVE_BYTES)
        total += len(raw)
        if total > MAX_CATALOG_BYTES:
            fail("Context archive catalog exceeds its byte budget.")
        value = decode(raw)
        if (set(value) != {"format", "workspace", "generation", "previous", "pointers", "heads"}
                or value["format"] != ARCHIVE_FORMAT or value["workspace"] != workspace_root_id(root)
                or type(value["generation"]) is not int or value["generation"] != number
                or value["previous"] != previous or not isinstance(value["pointers"], dict)
                or not value["pointers"] or len(value["pointers"]) > MAX_GENERATION_RECORDS
                or not isinstance(value["heads"], list) or not value["heads"]
                or not all(is_digest(key) for key in value["heads"])
                or len(set(value["heads"])) != len(value["heads"])):
            fail("Invalid or relocated context archive generation.")
        for key, pointer in value["pointers"].items():
            validate_pointer(pointer, key)
        if any(key not in value["pointers"] for key in value["heads"]):
            fail("Archive generation has a missing terminal pointer.")
        previous = digest(value)
        result.append(value)
    return result


def active_pointers(root: Path, *, max_records: int,
                     catalog: list[dict[str, Any]] | None = None
                     ) -> tuple[dict[str, dict[str, Any]], set[str], list[dict[str, Any]]]:
    catalog = archive_catalog(root) if catalog is None else catalog
    anchors = set(catalog[-1]["heads"]) if catalog else set()
    pointers = {key: catalog[-1]["pointers"][key] for key in anchors} if catalog else {}
    if len(pointers) > max_records:
        fail("Context terminal records exceed the capacity; archive cannot discard stream heads.")
    directory = ensure_unlinked_path(root / index_path(len(catalog)), purpose="Active context index")
    if directory.exists():
        for path in strict_files(directory, limit=max_records - len(pointers)):
            if path.parent != directory or path.suffix != ".json" or not is_digest(path.stem):
                fail("Unexpected context index entry.")
            pointer = decode(read(path, 8192))
            validate_pointer(pointer, path.stem)
            if path.stem in pointers:
                fail("Active index duplicates an archived terminal record.")
            pointers[path.stem] = pointer
    return pointers, anchors, catalog


def read_pointer(root: Path, pointer: dict[str, Any], *, max_bytes: int,
                 validate: Callable[[dict[str, Any]], None]) -> tuple[dict[str, Any], int]:
    validate_pointer(pointer, pointer.get("id", ""))
    raw = read(root / relative(pointer["path"]), max_bytes)
    record = decode(raw)
    if digest(record) != pointer["id"]:
        fail("Context record was modified.", "context_integrity_failed")
    validate(record)
    return record, len(raw)


def load_records(root: Path, *, max_records: int, max_bytes: int, max_record_bytes: int,
                  validate: Callable[[dict[str, Any]], None]
                  ) -> dict[str, dict[str, Any]]:
    if max_bytes < 0 or max_records < 0:
        fail("Context history exceeds its read budget or record limit; preview context-archive.")
    pointers, anchors, catalog = active_pointers(root, max_records=max_records)
    records = {}
    total = 0
    for key, pointer in sorted(pointers.items()):
        record, size = read_pointer(root, pointer, max_bytes=max_record_bytes, validate=validate)
        total += size
        if total > max_bytes:
            fail("Context history exceeds the read budget; preview context-archive.")
        records[key] = record
    for key, record in records.items():
        parent = record["parent"]
        if not parent:
            continue
        if parent in records:
            if records[parent]["stream"] != record["stream"]:
                fail("Context stream has a mismatched parent.")
        elif key not in anchors or not any(parent in item["pointers"] for item in catalog):
            fail("Context stream has a missing or mismatched parent.")
    return records


def exact_record(root: Path, record_id: str, *, max_record_bytes: int,
                  validate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    if not is_digest(record_id):
        fail("Invalid context record ID.")
    catalog = archive_catalog(root)
    pointers, _, _ = active_pointers(root, max_records=MAX_GENERATION_RECORDS, catalog=catalog)
    pointer = pointers.get(record_id)
    if pointer is None:
        for item in reversed(catalog):
            if record_id in item["pointers"]:
                pointer = item["pointers"][record_id]
                break
    if pointer is None:
        fail("Requested context record is not in this project's verified index.",
             "context_record_not_found")
    record, _ = read_pointer(root, pointer, max_bytes=max_record_bytes, validate=validate)
    return record
