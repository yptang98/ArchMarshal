"""Bounded, create-only primitives shared by portability and handoffs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ArchMarshalError
from .io import read_bytes_safe
from .safety import WINDOWS_RESERVED_NAMES, create_bytes_exclusive, ensure_unlinked_path

MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_FILES = 2_000
MAX_OBJECTS = 2_000


def fail(message: str, code: str = "portability_invalid") -> None:
    raise ArchMarshalError(code, message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def is_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def read(path: Path, limit: int = MAX_OBJECT_BYTES) -> bytes:
    ensure_unlinked_path(path, purpose="Portable data")
    result = read_bytes_safe(path, max_bytes=limit, label="Portable data")
    if result.error:
        fail(result.error)
    return result.data


def decode(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_OBJECT_BYTES:
        fail("Portable object exceeds the byte limit.")
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail("Duplicate JSON key in portable object.")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise ArchMarshalError("portability_invalid", "Invalid portable JSON.") from exc
    if not isinstance(value, dict):
        fail("Portable object must be a JSON mapping.")
    return value


def strict_files(directory: Path, *, limit: int = MAX_OBJECTS) -> list[Path]:
    """Reject links and incomplete scans instead of hiding journal entries."""
    ensure_unlinked_path(directory, purpose="Portable store")
    result: list[Path] = []
    count = 0
    def failed(error: OSError) -> None:
        raise error
    for current, children, files in os.walk(directory, followlinks=False, onerror=failed):
        count += len(children) + len(files)
        if count > limit:
            fail("Portable store exceeds its entry budget.")
        for name in children + files:
            ensure_unlinked_path(Path(current) / name, purpose="Portable store entry")
        result.extend(Path(current) / name for name in sorted(files))
    return result


def relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value:
        fail("Expected a bounded portable relative path.")
    parts = value.split("/")
    if any(p in {"", ".", ".."} or p.endswith((".", " "))
           or p.split(".")[0].upper() in WINDOWS_RESERVED_NAMES
           or re.search(r'[<>:"|?*\x00-\x1f]', p) for p in parts):
        fail("Unsafe or non-portable relative path.")
    if PurePosixPath(value).is_absolute():
        fail("Absolute paths are not portable.")
    return value


def portable_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def text(value: Any, label: str, limit: int = 8_000, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        fail(f"Invalid {label}; expected text up to {limit} characters.")
    if "\x00" in value:
        fail(f"NUL character in {label}.")
    return value


def checked_plan(plan: dict[str, Any], expected: str | None) -> str:
    value = digest(plan)
    if expected != value:
        fail("Preview changed or --expect-plan is missing; review a fresh preview.",
             "portability_stale_plan")
    return value


def publish(path: Path, data: bytes) -> bool:
    ensure_unlinked_path(path, purpose="Portable destination")
    if path.exists():
        if read(path) != data:
            fail("Immutable destination contains different data.", "portability_collision")
        return False
    try:
        create_bytes_exclusive(path, data)
    except FileExistsError:
        if read(path) != data:
            fail("Concurrent immutable destination differs.", "portability_collision")
        return False
    return True


def default_device_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "archmarshal/device.json"


def validate_device(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"format", "id", "name"}:
        fail("Invalid device identity.")
    if value["format"] != "archmarshal-device-v1":
        fail("Unsupported device format.")
    try:
        valid_id = str(uuid.UUID(value["id"])) == value["id"]
    except (ValueError, TypeError, AttributeError):
        valid_id = False
    if not valid_id:
        fail("Invalid device UUID.")
    text(value["name"], "device name", 100, required=True)
    return value


def device(path: Path | str | None = None) -> dict[str, str]:
    target = Path(path) if path is not None else default_device_path()
    if not target.exists():
        fail("Initialize this machine with device-init before recording or syncing.",
             "device_not_initialized")
    return validate_device(decode(read(target, 4096)))


def init_device(name: str, *, path: Path | str | None = None, apply: bool = False,
                expected_plan: str | None = None) -> dict[str, Any]:
    target = ensure_unlinked_path(Path(path) if path else default_device_path(),
                                 purpose="Device identity")
    text(name, "device name", 100, required=True)
    if target.exists():
        identity = device(target)
        if identity["name"] != name:
            fail("Device profile already exists with another name.")
        return {"mode": "unchanged", "device": identity, "path": str(target)}
    plan = {"path": str(target), "name": name, "operation": "device_init"}
    if apply:
        checked_plan(plan, expected_plan)
        identity = {"format": "archmarshal-device-v1", "id": str(uuid.uuid4()), "name": name}
        publish(target, canonical(identity))
        return {"mode": "applied", "device": identity, "path": str(target)}
    return {"mode": "preview", "plan": plan, "plan_digest": digest(plan)}
