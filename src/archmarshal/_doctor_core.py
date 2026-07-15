from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ArchMarshalError
from .formats import DURABLE_FORMATS, find_format
from .io import load_yaml_safe, read_bytes_safe
from .safety import is_link_or_reparse

MAX_HISTORY_LIMIT = 100
MAX_DIRECTORY_ENTRIES = 2_048
MAX_SCAN_BYTES = 128 * 1024 * 1024
MAX_METADATA_FILE_BYTES = 64 * 1024 * 1024
MAX_SCAN_DEPTH = 8

FILESYSTEM_SAFETY = {
    "static_link_reparse_rejection": True,
    "stable_file_identity_reads": True,
    "anchored_components": False,
    "concurrent_ancestor_rebinding_protected": False,
    "write_threat_model": "cooperative-only",
    "doctor_writes": False,
}


@dataclass
class Budget:
    history_limit: int
    entries: int = 0
    bytes: int = 0
    truncations: list[dict[str, str]] = field(default_factory=list)

    def entry(self, area: str, scope: str, path: str) -> bool:
        if self.entries >= MAX_DIRECTORY_ENTRIES:
            self.truncate(area, scope, path, "directory_entry_limit")
            return False
        self.entries += 1
        return True

    def content(self, size: int, area: str, scope: str, path: str) -> bool:
        if size > MAX_METADATA_FILE_BYTES:
            self.truncate(area, scope, path, "metadata_file_byte_limit")
            return False
        if self.bytes + size > MAX_SCAN_BYTES:
            self.truncate(area, scope, path, "cumulative_byte_limit")
            return False
        self.bytes += size
        return True

    def truncate(self, area: str, scope: str, path: str, reason: str) -> None:
        item = {"area": area, "scope": scope, "path": path, "reason": reason}
        if item not in self.truncations:
            self.truncations.append(item)

    def as_dict(self) -> dict[str, Any]:
        truncations = sorted(
            self.truncations,
            key=lambda item: (item["area"], item["scope"], item["path"], item["reason"]),
        )
        return {
            "limits": {
                "history_generations": self.history_limit,
                "directory_entries": MAX_DIRECTORY_ENTRIES,
                "cumulative_bytes": MAX_SCAN_BYTES,
                "metadata_file_bytes": MAX_METADATA_FILE_BYTES,
                "recursion_depth": MAX_SCAN_DEPTH,
            },
            "used": {"directory_entries": self.entries, "bytes": self.bytes},
            "truncated": bool(truncations),
            "truncations": truncations,
        }


@dataclass
class Report:
    budget: Budget
    findings: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    _finding_keys: set[tuple[str, str, str, str, str]] = field(default_factory=set)
    _suggestion_keys: set[tuple[str, str, str]] = field(default_factory=set)

    def add(
        self,
        area: str,
        code: str,
        severity: str,
        classification: str,
        scope: str,
        path: str,
        message: str,
        **details: Any,
    ) -> None:
        key = area, code, classification, scope, path
        if key in self._finding_keys:
            return
        self._finding_keys.add(key)
        item: dict[str, Any] = {
            "area": area,
            "code": code,
            "severity": severity,
            "classification": classification,
            "scope": scope,
            "path": path,
            "message": message,
        }
        item.update({key: value for key, value in details.items() if value is not None})
        self.findings.append(item)

    def suggest(self, scope: str, path: str, classification: str, message: str) -> None:
        key = scope, path, classification
        if key in self._suggestion_keys:
            return
        self._suggestion_keys.add(key)
        self.suggestions.append(
            {
                "scope": scope,
                "path": path,
                "classification": classification,
                "suggestion": message,
                "automatic_action": False,
            }
        )


def absolute_lexical(value: Path | str) -> Path:
    candidate = Path(value).expanduser()
    return Path(os.path.abspath(candidate if candidate.is_absolute() else Path.cwd() / candidate))


def inspect_root(path: Path, scope: str, report: Report) -> bool:
    area = "user_store" if scope == "user_store" else "ownership"
    linked, unreadable = first_unsafe_component(path)
    if linked is not None or unreadable is not None:
        classification = "unsafe" if linked else "unreadable"
        report.add(
            area,
            f"{scope}_{'link_rejected' if linked else 'metadata_unreadable'}",
            "error",
            classification,
            scope,
            ".",
            "The inspection root could not be traversed safely.",
            unsafe_component=str(linked) if linked else None,
            unreadable_component=str(unreadable) if unreadable else None,
        )
        return False
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        report.add(area, f"{scope}_absent", "info", "absent", scope, ".", "Root absent.")
        return False
    except OSError:
        report.add(
            area,
            f"{scope}_metadata_unreadable",
            "error",
            "unreadable",
            scope,
            ".",
            "Root metadata unreadable.",
        )
        return False
    if not stat.S_ISDIR(metadata.st_mode):
        report.add(
            area,
            f"{scope}_not_directory",
            "error",
            "unsafe",
            scope,
            ".",
            "Root is not a real directory.",
        )
        return False
    return True


def read_file(
    path: Path,
    root: Path,
    area: str,
    scope: str,
    report: Report,
    max_bytes: int,
) -> bytes | None:
    unsafe = unsafe_between(root, path)
    if unsafe is not None:
        unsafe_finding(report, area, scope, unsafe, root)
        return None
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        metadata_unreadable(report, area, scope, path, root)
        return None
    if not stat.S_ISREG(metadata.st_mode):
        metadata_unreadable(report, area, scope, path, root)
        return None
    if metadata.st_size > max_bytes:
        report.add(
            area,
            f"{area}_metadata_oversized",
            "error",
            "unreadable",
            scope,
            display(path, root),
            "Metadata exceeds its format-specific read limit.",
            limit=max_bytes,
            actual=metadata.st_size,
        )
        return None
    if not report.budget.content(metadata.st_size, area, scope, display(path, root)):
        report.add(
            area,
            f"{area}_scan_budget_exhausted",
            "warning",
            "truncated",
            scope,
            display(path, root),
            "Doctor byte budget exhausted before this metadata read.",
        )
        return None
    loaded = read_bytes_safe(path, max_bytes=max_bytes, label="Doctor metadata")
    if loaded.error:
        metadata_unreadable(report, area, scope, path, root)
        return None
    return loaded.data


def load_json(
    path: Path,
    root: Path,
    area: str,
    scope: str,
    report: Report,
    max_bytes: int,
) -> tuple[Any | None, bytes | None]:
    raw = read_file(path, root, area, scope, report, max_bytes)
    if raw is None:
        return None, None
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, ValueError):
        report.add(
            area,
            f"{area}_json_corrupt",
            "error",
            "corrupt",
            scope,
            display(path, root),
            "Metadata is not valid UTF-8 JSON.",
        )
        return None, raw


def load_yaml(
    path: Path,
    root: Path,
    area: str,
    scope: str,
    report: Report,
) -> Any | None:
    unsafe = unsafe_between(root, path)
    if unsafe is not None:
        unsafe_finding(report, area, scope, unsafe, root)
        return None
    try:
        size = path.lstat().st_size
    except FileNotFoundError:
        return None
    except OSError:
        metadata_unreadable(report, area, scope, path, root)
        return None
    if not report.budget.content(size, area, scope, display(path, root)):
        report.add(
            area,
            f"{area}_scan_budget_exhausted",
            "warning",
            "truncated",
            scope,
            display(path, root),
            "Doctor byte budget exhausted before this YAML read.",
        )
        return None
    loaded = load_yaml_safe(path)
    if loaded.error:
        if is_link(path):
            classification = "unsafe"
        elif any(
            token in loaded.error.casefold()
            for token in ("permission", "access", "denied", "changed while", "no such file")
        ):
            classification = "unreadable"
        else:
            classification = "corrupt"
        suffix = "link_rejected" if classification == "unsafe" else f"yaml_{classification}"
        report.add(
            area,
            f"{area}_{suffix}",
            "error",
            classification,
            scope,
            display(path, root),
            "Metadata is not safe, bounded UTF-8 YAML.",
        )
        return None
    return loaded.data


def list_directory(
    path: Path,
    root: Path,
    area: str,
    scope: str,
    report: Report,
) -> list[Path] | None:
    unsafe = unsafe_between(root, path)
    if unsafe is not None:
        unsafe_finding(report, area, scope, unsafe, root)
        return None
    try:
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise NotADirectoryError
        entries = sorted(path.iterdir(), key=lambda item: item.name)
    except FileNotFoundError:
        return None
    except (NotADirectoryError, OSError):
        report.add(
            area,
            f"{area}_directory_unreadable",
            "error",
            "unreadable",
            scope,
            display(path, root),
            "State directory could not be enumerated safely.",
        )
        return None
    accepted: list[Path] = []
    for entry in entries:
        if not report.budget.entry(area, scope, display(entry, root)):
            break
        accepted.append(entry)
    return accepted


def report_format(
    report: Report,
    area: str,
    scope: str,
    path: str,
    value: object,
    expected_family: str,
) -> None:
    actual, classification = find_format(value)
    expected = next((item for item in DURABLE_FORMATS if item.family == expected_family), None)
    if actual is not None and actual.family != expected_family:
        classification = "unsupported"
    severity = "info" if classification == "current" else "warning"
    if classification in {"missing", "unknown", "unsupported"}:
        severity = "error"
    messages = {
        "current": "Durable record uses the current writable format.",
        "legacy": "Durable record uses a recognized legacy format; no migration was attempted.",
        "unsupported": "Durable record uses an unsupported format for this location.",
        "unknown": "Durable record uses an unknown format.",
        "missing": "Durable record has no format identifier.",
    }
    report.add(
        area,
        f"{expected_family}_format_{classification}",
        severity,
        classification,
        scope,
        path,
        messages[classification],
        format=value if isinstance(value, str) else None,
        format_family=expected_family,
        owner=expected.owner if expected else None,
        readable_versions=list(expected.readable_versions) if expected else [],
        writable_versions=list(expected.writable_versions) if expected else [],
        migration_status=expected.migration_status if expected else "unknown",
    )


def first_unsafe_component(path: Path) -> tuple[Path | None, Path | None]:
    for component in [*reversed(path.parents), path]:
        try:
            component.lstat()
            if is_link_or_reparse(component):
                return component, None
        except FileNotFoundError:
            continue
        except (OSError, ArchMarshalError):
            return None, component
    return None, None


def unsafe_between(root: Path, path: Path) -> Path | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path
    current = root
    for part in ("", *relative.parts):
        current = current if not part else current / part
        try:
            if is_link_or_reparse(current):
                return current
        except ArchMarshalError:
            return current
    return None


def unsafe_finding(report: Report, area: str, scope: str, path: Path, root: Path) -> None:
    report.add(
        area,
        f"{area}_link_rejected",
        "error",
        "unsafe",
        scope,
        display(path, root),
        "A symbolic link or reparse point was rejected and not traversed.",
    )


def metadata_unreadable(
    report: Report, area: str, scope: str, path: Path, root: Path
) -> None:
    report.add(
        area,
        f"{area}_metadata_unreadable",
        "error",
        "unreadable",
        scope,
        display(path, root),
        "Metadata could not be read as a stable regular file.",
    )


def is_link(path: Path) -> bool:
    try:
        return is_link_or_reparse(path)
    except ArchMarshalError:
        return True


def path_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except (FileNotFoundError, OSError):
        return False


def display(path: Path, root: Path) -> str:
    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
    return value or "."


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


__all__ = [
    "FILESYSTEM_SAFETY",
    "MAX_HISTORY_LIMIT",
    "MAX_METADATA_FILE_BYTES",
    "MAX_SCAN_DEPTH",
    "Budget",
    "Report",
    "absolute_lexical",
    "display",
    "inspect_root",
    "is_link",
    "is_sha256",
    "list_directory",
    "load_json",
    "load_yaml",
    "path_exists",
    "read_file",
    "report_format",
    "unsafe_finding",
]
