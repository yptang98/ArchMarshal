from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ._doctor_core import (
    Report,
    display,
    is_link,
    is_sha256,
    list_directory,
    load_json,
    path_exists,
    read_file,
    report_format,
    unsafe_finding,
)
from .errors import ArchMarshalError
from .skill_index import MAX_HEAD_BYTES as SKILL_HEAD_BYTES
from .skill_index import MAX_INDEX_OBJECT_BYTES
from .skill_index import _validate_generation as _validate_skill_index_generation
from .user_store import (
    MAX_GENERATION_BYTES,
    MAX_OWNERSHIP_BYTES,
    MAX_PACKAGE_COMMIT_BYTES,
    MAX_SKILL_PACKAGE_BYTES,
    _verify_package,
)
from .user_store import (
    _validate_generation as _validate_user_store_generation,
)


@dataclass(frozen=True)
class _ChainSpec:
    area: str
    scope: str
    family: str
    object_bytes: int
    validate: Callable[[object, str], bool]
    collect_packages: bool = False


@dataclass
class _ChainResult:
    head: str | None
    reachable: set[str] = field(default_factory=set)
    packages: set[str] = field(default_factory=set)
    complete: bool = False


def inspect_skill_index(root: Path, report: Report) -> None:
    state = root / ".agent" / "skill-overlays" / ".archmarshal"
    result = _scan_chain(
        root,
        state,
        _ChainSpec(
            "skill_index",
            "workspace",
            "skill_index",
            MAX_INDEX_OBJECT_BYTES,
            _valid_skill_generation,
        ),
        report,
    )
    if result.head is None:
        report.add(
            "skill_index",
            "skill_index_absent" if not path_exists(state) else "skill_index_uninitialized",
            "info",
            "absent" if not path_exists(state) else "uninitialized",
            "workspace",
            display(state, root),
            "No Skill index state is present."
            if not path_exists(state)
            else "Skill index state exists without an active HEAD.",
        )


def inspect_user_store(root: Path, report: Report) -> None:
    ownership_path = root / "ownership.json"
    ownership, _ = load_json(
        ownership_path, root, "user_store", "user_store", report, MAX_OWNERSHIP_BYTES
    )
    if ownership is None:
        if not path_exists(ownership_path):
            report.add(
                "user_store",
                "user_store_unowned",
                "warning",
                "absent",
                "user_store",
                display(ownership_path, root),
                "User store has no ownership marker.",
            )
        return
    report_format(
        report,
        "user_store",
        "user_store",
        display(ownership_path, root),
        ownership.get("format") if isinstance(ownership, dict) else None,
        "user_store_ownership",
    )
    if not _valid_store_ownership(root, ownership):
        report.add(
            "user_store",
            "user_store_ownership_binding_invalid",
            "error",
            "corrupt",
            "user_store",
            display(ownership_path, root),
            "User-store ownership marker is not bound to this exact root.",
        )
    state = root / ".archmarshal"
    result = _scan_chain(
        root,
        state,
        _ChainSpec(
            "user_store",
            "user_store",
            "user_store_generation",
            MAX_GENERATION_BYTES,
            _valid_user_generation,
            collect_packages=True,
        ),
        report,
    )
    _inspect_packages(root, result.packages, result.complete, report)
    if result.head is None:
        report.add(
            "user_store",
            "user_store_initialized_empty",
            "info",
            "uninitialized",
            "user_store",
            display(state, root),
            "Owned user store has no active generation HEAD.",
        )


def _scan_chain(root: Path, state: Path, spec: _ChainSpec, report: Report) -> _ChainResult:
    head_path = state / "HEAD"
    head = _load_head(head_path, root, spec, report)
    result = _ChainResult(head=head, complete=head is None and not path_exists(head_path))
    objects = state / "objects" / "sha256"
    current, count = head, 0
    while current is not None and count < report.budget.history_limit:
        if current in result.reachable:
            _chain_error(report, spec, "history_cycle", head_path, root, "History has a cycle.")
            break
        result.reachable.add(current)
        path = objects / f"{current}.json"
        generation, raw = load_json(path, root, spec.area, spec.scope, report, spec.object_bytes)
        if generation is None or raw is None:
            if not path_exists(path):
                _chain_error(report, spec, "object_missing", path, root, "Generation is missing.")
            break
        if hashlib.sha256(raw).hexdigest() != current:
            _chain_error(
                report, spec, "digest_mismatch", path, root, "Object bytes do not match its name."
            )
            break
        report_format(
            report,
            spec.area,
            spec.scope,
            display(path, root),
            generation.get("format") if isinstance(generation, dict) else None,
            spec.family,
        )
        if not spec.validate(generation, current):
            _chain_error(
                report,
                spec,
                "generation_structure_invalid",
                path,
                root,
                "Generation structure or record bounds are invalid.",
            )
            break
        if spec.collect_packages:
            result.packages.update(_generation_packages(generation))
        parent = generation["parent"]
        count += 1
        if parent is None:
            result.complete, current = True, None
        elif is_sha256(parent):
            current = parent
        else:
            _chain_error(report, spec, "parent_invalid", path, root, "Parent digest is invalid.")
            break
    if current is not None and count >= report.budget.history_limit:
        report.budget.truncate(spec.area, spec.scope, display(objects, root), "history_limit")
    _classify_objects(objects, root, spec, result, report)
    return result


def _load_head(path: Path, root: Path, spec: _ChainSpec, report: Report) -> str | None:
    if not path_exists(path):
        return None
    raw = read_file(path, root, spec.area, spec.scope, report, SKILL_HEAD_BYTES)
    try:
        value = raw.decode("ascii").strip() if raw is not None else ""
    except UnicodeDecodeError:
        value = ""
    if not is_sha256(value):
        _chain_error(report, spec, "head_invalid", path, root, "HEAD is not a SHA-256 digest.")
        return None
    return value


def _classify_objects(
    directory: Path,
    root: Path,
    spec: _ChainSpec,
    result: _ChainResult,
    report: Report,
) -> None:
    entries = list_directory(directory, root, spec.area, spec.scope, report)
    for path in entries or []:
        if is_link(path):
            unsafe_finding(report, spec.area, spec.scope, path, root)
            continue
        digest = path.name[:-5] if path.name.endswith(".json") else None
        if not is_sha256(digest):
            report.add(
                spec.area,
                f"{spec.area}_object_name_invalid",
                "warning",
                "partial",
                spec.scope,
                display(path, root),
                "Object entry has no canonical generation name.",
            )
            _retain(report, spec.scope, path, root, "partial_object")
        elif result.complete and digest not in result.reachable:
            report.add(
                spec.area,
                f"{spec.area}_orphan_immutable_object",
                "warning",
                "orphan_immutable_object",
                spec.scope,
                display(path, root),
                "Immutable generation is unreachable from the complete active chain.",
            )
            _retain(report, spec.scope, path, root, "orphan_immutable_object")


def _inspect_packages(root: Path, referenced: set[str], complete: bool, report: Report) -> None:
    packages = root / ".archmarshal" / "packages" / "sha256"
    entries = list_directory(packages, root, "user_store", "user_store", report)
    for package in entries or []:
        relative = display(package, root)
        if is_link(package):
            unsafe_finding(report, "user_store", "user_store", package, root)
            continue
        try:
            metadata = package.lstat()
        except OSError:
            metadata = None
        if metadata is None or not stat.S_ISDIR(metadata.st_mode):
            report.add(
                "user_store",
                "user_store_package_path_invalid",
                "error",
                "corrupt",
                "user_store",
                relative,
                "Immutable package entry is not a real directory.",
            )
            continue
        digest = package.name if is_sha256(package.name) else None
        if digest is None:
            report.add(
                "user_store",
                "user_store_package_name_invalid",
                "error",
                "corrupt",
                "user_store",
                relative,
                "Package directory name is not a SHA-256 digest.",
            )
        commit_path = package / "COMMITTED.json"
        if not path_exists(commit_path):
            report.add(
                "user_store",
                "user_store_partial_package",
                "warning",
                "partial_package",
                "user_store",
                relative,
                "Package has no final commit marker.",
            )
            _retain(report, "user_store", package, root, "partial_package")
            continue
        commit, _ = load_json(
            commit_path,
            root,
            "user_store",
            "user_store",
            report,
            MAX_PACKAGE_COMMIT_BYTES,
        )
        if commit is not None:
            _inspect_package_commit(commit, digest, commit_path, root, report)
            _verify_v2_package(root, package, digest, commit, report)
        if digest is not None and complete and digest not in referenced:
            report.add(
                "user_store",
                "user_store_orphan_package",
                "warning",
                "orphan_package",
                "user_store",
                relative,
                "Committed package is unreferenced by the complete generation chain.",
            )
            _retain(report, "user_store", package, root, "orphan_package")


def _inspect_package_commit(
    commit: object, digest: str | None, path: Path, root: Path, report: Report
) -> None:
    value = commit.get("format") if isinstance(commit, dict) else None
    report_format(
        report, "user_store", "user_store", display(path, root), value, "user_skill_package"
    )
    if value == "archmarshal-user-skill-package-v2" and (
        digest is None
        or commit.get("package_sha256") != digest
        or commit.get("snapshot_format") != "archmarshal-user-skill-snapshot-v2"
        or commit.get("source_mutation") is not False
    ):
        report.add(
            "user_store",
            "user_store_package_commit_invalid",
            "error",
            "corrupt",
            "user_store",
            display(path, root),
            "V2 package commit is not bound to its package directory.",
        )


def _verify_v2_package(
    root: Path,
    package: Path,
    digest: str | None,
    commit: object,
    report: Report,
) -> None:
    if not isinstance(commit, dict) or commit.get("format") != "archmarshal-user-skill-package-v2":
        return
    content_bytes = commit.get("content_bytes")
    manifest_digest = commit.get("manifest_digest")
    relative = display(package, root)
    if (
        digest is None
        or not isinstance(content_bytes, int)
        or isinstance(content_bytes, bool)
        or not 0 <= content_bytes <= MAX_SKILL_PACKAGE_BYTES
        or not is_sha256(manifest_digest)
    ):
        report.add(
            "user_store",
            "user_store_package_integrity_failed",
            "error",
            "corrupt",
            "user_store",
            relative,
            "V2 package cannot be verified within its declared integrity bounds.",
        )
        return
    if not report.budget.content(content_bytes, "user_store", "user_store", relative):
        report.add(
            "user_store",
            "user_store_package_integrity_truncated",
            "warning",
            "truncated",
            "user_store",
            relative,
            "Doctor byte budget was exhausted before package-content verification.",
        )
        return
    try:
        _verify_package(root, digest, manifest_digest)
    except ArchMarshalError as exc:
        report.add(
            "user_store",
            "user_store_package_integrity_failed",
            "error",
            "corrupt",
            "user_store",
            relative,
            "V2 package content, topology, modes, or manifest binding failed verification.",
            cause=exc.code,
        )
        return
    report.add(
        "user_store",
        "user_store_package_integrity_verified",
        "info",
        "current",
        "user_store",
        relative,
        "V2 package content, topology, modes, and manifest binding are verified.",
    )


def _chain_error(
    report: Report,
    spec: _ChainSpec,
    suffix: str,
    path: Path,
    root: Path,
    message: str,
) -> None:
    report.add(
        spec.area,
        f"{spec.area}_{suffix}",
        "error",
        "corrupt",
        spec.scope,
        display(path, root),
        message,
    )


def _retain(report: Report, scope: str, path: Path, root: Path, classification: str) -> None:
    report.suggest(
        scope,
        display(path, root),
        classification,
        "Retain until provenance and recovery value are reviewed; no automatic removal is proposed.",
    )


def _generation_packages(generation: dict[str, Any]) -> set[str]:
    return {
        value
        for record in generation.get("common_skills", [])[:500]
        if isinstance(record, dict)
        and is_sha256(value := record.get("package_sha256"))
    }


def _valid_skill_generation(value: object, digest: str) -> bool:
    shallow = (
        isinstance(value, dict)
        and set(value) == {"format", "created_at", "parent", "skills", "changes"}
        and value.get("format") == "archmarshal-skill-index-v1"
        and isinstance(value.get("created_at"), str)
        and bool(value["created_at"])
        and (value.get("parent") is None or is_sha256(value.get("parent")))
        and isinstance(value.get("skills"), list)
        and len(value["skills"]) <= 10_000
        and isinstance(value.get("changes"), list)
        and len(value["changes"]) <= 10_001
    )
    if not shallow:
        return False
    try:
        _validate_skill_index_generation(value, digest)
    except ArchMarshalError:
        return False
    return True


def _valid_user_generation(value: object, digest: str) -> bool:
    expected = {
        "format",
        "created_at",
        "parent",
        "common_skills",
        "preferences",
        "candidate_decisions",
        "operation",
    }
    shallow = (
        isinstance(value, dict)
        and set(value) == expected
        and value.get("format") == "archmarshal-user-store-generation-v1"
        and isinstance(value.get("created_at"), str)
        and bool(value["created_at"])
        and (value.get("parent") is None or is_sha256(value.get("parent")))
        and isinstance(value.get("common_skills"), list)
        and len(value["common_skills"]) <= 500
        and isinstance(value.get("preferences"), list)
        and len(value["preferences"]) <= 100
        and isinstance(value.get("candidate_decisions"), list)
        and len(value["candidate_decisions"]) <= 10_000
        and isinstance(value.get("operation"), dict)
    )
    if not shallow:
        return False
    try:
        _validate_user_store_generation(value, digest)
    except ArchMarshalError:
        return False
    return True


def _valid_store_ownership(root: Path, marker: object) -> bool:
    store_id = hashlib.sha256(f"archmarshal-user-store-v1\x00{root}".encode()).hexdigest()[:32]
    return (
        isinstance(marker, dict)
        and marker.get("store_id") == store_id
        and marker.get("managed_root") == "."
        and isinstance(marker.get("created_at"), str)
        and bool(marker["created_at"])
        and marker.get("source_mutation") is False
    )


__all__ = ["inspect_skill_index", "inspect_user_store"]
