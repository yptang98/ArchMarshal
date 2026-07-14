from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ArchMarshalError
from .safety import ensure_managed_path, sha256_file

FORMAT = "archmarshal-skill-index-v1"
STATE_RELATIVE = Path(".agent/skill-overlays/.archmarshal")
HEAD_NAME = "HEAD"
LOCK_NAME = "HEAD.lock"
MAX_INDEX_SKILLS = 10_000
MAX_INDEX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_HEAD_BYTES = 128
MAX_SOURCE_LENGTH = 4096


def plan_skill_index(
    root: Path,
    discovered_skills: list[dict[str, Any]],
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    loaded = load_skill_index(root)
    expected_head = loaded["head"]
    previous_records = {
        str(record["source"]): record for record in (loaded.get("generation") or {}).get("skills", [])
    }
    if len(discovered_skills) > MAX_INDEX_SKILLS:
        raise ArchMarshalError(
            "skill_index_limit_exceeded",
            "Skill index planning exceeded the maximum number of skills.",
            details={"limit": MAX_INDEX_SKILLS, "actual": len(discovered_skills)},
        )
    current_records: dict[str, dict[str, Any]] = {}
    for skill in discovered_skills:
        source = skill.get("source")
        manifest = skill.get("manifest")
        if not isinstance(source, str) or not _is_safe_source(source):
            raise ArchMarshalError(
                "skill_index_plan_invalid",
                "Skill index planning received an unsafe source path.",
                details={"source": source},
            )
        if not isinstance(manifest, dict):
            raise ArchMarshalError(
                "skill_index_plan_invalid",
                "Skill index planning requires a manifest mapping for every skill.",
                details={"source": source},
            )
        if source in current_records:
            raise ArchMarshalError(
                "skill_index_plan_invalid",
                "Skill index planning received duplicate source paths.",
                details={"source": source},
            )
        current_records[source] = {
            "source": source,
            "state": "active",
            "manifest": _plain_manifest(manifest),
        }

    records: list[dict[str, Any]] = []
    changes: list[dict[str, str]] = []
    for source in sorted(set(previous_records) | set(current_records), key=str.casefold):
        previous = previous_records.get(source)
        current = current_records.get(source)
        if current is None:
            if previous and previous.get("state") == "removed":
                records.append(previous)
                continue
            if previous:
                records.append({**previous, "state": "removed"})
                changes.append({"kind": "removed", "source": source})
            continue
        records.append(current)
        if previous is None:
            changes.append({"kind": "added", "source": source})
        elif previous.get("state") == "removed":
            changes.append({"kind": "restored", "source": source})
        elif previous.get("manifest") != current["manifest"]:
            changes.append({"kind": "modified", "source": source})

    initializing = expected_head is None
    if initializing:
        changes.insert(0, {"kind": "initialized", "source": ""})
    changed = initializing or bool(changes)
    if not changed:
        return {
            "changed": False,
            "expected_head": expected_head,
            "digest": expected_head,
            "generation": loaded["generation"],
            "changes": [],
            "object_path": loaded.get("object_path"),
        }

    generation = {
        "format": FORMAT,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "parent": expected_head,
        "skills": records,
        "changes": changes,
    }
    payload = _object_bytes(generation)
    _ensure_object_size(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "changed": True,
        "expected_head": expected_head,
        "digest": digest,
        "generation": generation,
        "changes": changes,
        "object_path": _relative_object_path(digest),
    }


def disabled_skill_index_plan() -> dict[str, Any]:
    return {
        "changed": False,
        "expected_head": None,
        "digest": None,
        "generation": None,
        "changes": [],
        "object_path": None,
        "disabled": True,
    }


def commit_skill_index(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    if not plan.get("changed"):
        return {
            "mode": "unchanged",
            "head": plan.get("digest"),
            "changes": [],
        }
    generation = plan.get("generation")
    digest = str(plan.get("digest") or "")
    expected_head = plan.get("expected_head")
    if not isinstance(generation, dict) or not _is_sha256(digest):
        raise ArchMarshalError(
            "skill_index_plan_invalid",
            "Skill index commit requires a valid planned generation.",
        )
    payload = _object_bytes(generation)
    _ensure_object_size(payload)
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ArchMarshalError(
            "skill_index_plan_invalid",
            "Skill index generation no longer matches its planned digest.",
        )

    state_root = root / STATE_RELATIVE
    ensure_managed_path(root, state_root, purpose="Skill index state directory")
    state_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    lock_path = state_root / LOCK_NAME
    _acquire_lock(lock_path, token, expected_head)
    temporary_head = state_root / f".{HEAD_NAME}.{token}.tmp"
    published = False
    try:
        actual_head = _read_head(root)
        if actual_head != expected_head:
            raise ArchMarshalError(
                "skill_index_stale_plan",
                "Skill index HEAD changed after this plan was created.",
                details={"expected_head": expected_head, "actual_head": actual_head},
            )

        object_path = root / _relative_object_path(digest)
        ensure_managed_path(root, object_path, purpose="Skill index generation object")
        object_path.parent.mkdir(parents=True, exist_ok=True)
        if object_path.exists():
            if (
                not object_path.is_file()
                or object_path.is_symlink()
                or sha256_file(object_path) != digest
            ):
                raise ArchMarshalError(
                    "skill_index_object_collision",
                    "An existing skill index object does not match its content address.",
                    details={"path": str(object_path)},
                )
        else:
            _write_exclusive_bytes(object_path, payload)

        _write_exclusive_bytes(temporary_head, f"{digest}\n".encode("ascii"))
        actual_head = _read_head(root)
        if actual_head != expected_head:
            raise ArchMarshalError(
                "skill_index_stale_plan",
                "Skill index HEAD changed before the atomic commit.",
                details={"expected_head": expected_head, "actual_head": actual_head},
            )
        os.replace(temporary_head, state_root / HEAD_NAME)
        published = True
        _fsync_directory(state_root)
        verified = load_skill_index(root)
        if verified["head"] != digest:
            raise ArchMarshalError(
                "skill_index_commit_failed",
                "Skill index HEAD did not verify after commit.",
            )
    except BaseException:
        if published:
            try:
                _restore_head(root, state_root, expected_head, token)
            except BaseException as rollback_error:
                raise ArchMarshalError(
                    "skill_index_rollback_failed",
                    "Skill index verification failed and the previous HEAD could not be restored.",
                    details={"expected_head": expected_head, "proposed_head": digest},
                ) from rollback_error
        raise
    finally:
        temporary_head.unlink(missing_ok=True)
        _release_lock(lock_path, token)
    return {
        "mode": "committed",
        "head": digest,
        "object_path": _relative_object_path(digest).as_posix(),
        "changes": plan.get("changes") or [],
    }


def load_skill_index(root: Path) -> dict[str, Any]:
    root = root.resolve()
    state_root = root / STATE_RELATIVE
    ensure_managed_path(root, state_root, purpose="Skill index state directory")
    head = _read_head(root)
    if head is None:
        return {"format": FORMAT, "head": None, "generation": None, "object_path": None}
    object_relative = _relative_object_path(head)
    object_path = root / object_relative
    ensure_managed_path(root, object_path, purpose="Skill index generation object")
    if not object_path.is_file() or object_path.is_symlink():
        raise ArchMarshalError(
            "skill_index_object_missing",
            "Skill index HEAD points to a missing or linked generation object.",
            details={"head": head, "path": str(object_path)},
        )
    try:
        object_size = object_path.stat().st_size
    except OSError as exc:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation metadata could not be read.",
            details={"head": head, "path": str(object_path)},
        ) from exc
    if object_size > MAX_INDEX_OBJECT_BYTES:
        raise ArchMarshalError(
            "skill_index_limit_exceeded",
            "Skill index generation exceeds the safe size limit.",
            details={"limit": MAX_INDEX_OBJECT_BYTES, "actual": object_size},
        )
    if sha256_file(object_path) != head:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation bytes do not match HEAD.",
            details={"head": head, "path": str(object_path)},
        )
    try:
        generation = json.loads(object_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation is not valid UTF-8 JSON.",
            details={"head": head},
        ) from exc
    _validate_generation(generation, head)
    return {
        "format": FORMAT,
        "head": head,
        "generation": generation,
        "object_path": object_relative.as_posix(),
    }


def public_skill_index_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": not bool(plan.get("disabled")),
        "changed": bool(plan.get("changed")),
        "expected_head": plan.get("expected_head"),
        "proposed_head": plan.get("digest"),
        "object_path": (
            plan.get("object_path").as_posix()
            if isinstance(plan.get("object_path"), Path)
            else plan.get("object_path")
        ),
        "changes": plan.get("changes") or [],
        "commit_policy": "exclusive-lock-and-head-compare-and-swap",
        "source_mutation": False,
    }


def skill_index_summary(loaded: dict[str, Any]) -> dict[str, Any]:
    records = (loaded.get("generation") or {}).get("skills", [])
    return {
        "format": FORMAT,
        "head": loaded.get("head"),
        "object_path": loaded.get("object_path"),
        "active_skills": sum(record.get("state") == "active" for record in records),
        "removed_skills": sum(record.get("state") == "removed" for record in records),
        "immutable_objects": True,
        "head_commit": "lock_and_compare_and_swap",
    }


def _read_head(root: Path) -> str | None:
    head_path = root / STATE_RELATIVE / HEAD_NAME
    ensure_managed_path(root, head_path, purpose="Skill index HEAD")
    if not head_path.exists():
        return None
    if not head_path.is_file() or head_path.is_symlink():
        raise ArchMarshalError(
            "skill_index_head_invalid",
            "Skill index HEAD must be a regular file.",
            details={"path": str(head_path)},
        )
    try:
        if head_path.stat().st_size > MAX_HEAD_BYTES:
            raise ArchMarshalError(
                "skill_index_head_invalid",
                "Skill index HEAD exceeds the safe size limit.",
                details={"limit": MAX_HEAD_BYTES},
            )
        value = head_path.read_text(encoding="ascii").strip()
    except ArchMarshalError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ArchMarshalError(
            "skill_index_head_invalid",
            "Skill index HEAD is not readable ASCII.",
        ) from exc
    if not _is_sha256(value):
        raise ArchMarshalError(
            "skill_index_head_invalid",
            "Skill index HEAD is not a SHA-256 digest.",
        )
    return value


def _validate_generation(generation: object, head: str) -> None:
    if not isinstance(generation, dict) or generation.get("format") != FORMAT:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation has an unsupported format.",
            details={"head": head},
        )
    if hashlib.sha256(_object_bytes(generation)).hexdigest() != head:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation canonical content does not match HEAD.",
            details={"head": head},
        )
    parent = generation.get("parent")
    if parent is not None and (not isinstance(parent, str) or not _is_sha256(parent)):
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation has an invalid parent digest.",
        )
    records = generation.get("skills")
    if not isinstance(records, list):
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation skills must be a list.",
        )
    if len(records) > MAX_INDEX_SKILLS:
        raise ArchMarshalError(
            "skill_index_limit_exceeded",
            "Skill index generation exceeds the safe skill-count limit.",
            details={"limit": MAX_INDEX_SKILLS, "actual": len(records)},
        )
    changes = generation.get("changes")
    if not isinstance(changes, list) or len(changes) > MAX_INDEX_SKILLS + 1:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation contains an invalid change list.",
        )
    for change in changes:
        if (
            not isinstance(change, dict)
            or change.get("kind")
            not in {"initialized", "added", "modified", "removed", "restored"}
            or not isinstance(change.get("source"), str)
            or (
                change.get("kind") != "initialized"
                and not _is_safe_source(str(change.get("source")))
            )
            or (change.get("kind") == "initialized" and change.get("source") != "")
        ):
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index generation contains an invalid change record.",
            )
    seen: set[str] = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("source"), str)
            or record.get("state") not in {"active", "removed"}
            or not isinstance(record.get("manifest"), dict)
        ):
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index generation contains an invalid skill record.",
            )
        source = str(record["source"])
        if not _is_safe_source(source):
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index generation contains an unsafe source path.",
                details={"source": source},
            )
        if source in seen:
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index generation contains duplicate source paths.",
                details={"source": source},
            )
        source_metadata = record["manifest"].get("source")
        skill_dir = source_metadata.get("skill_dir") if isinstance(source_metadata, dict) else None
        skill_md = source_metadata.get("skill_md") if isinstance(source_metadata, dict) else None
        if (
            skill_dir != source
            or not isinstance(skill_md, str)
            or not _is_safe_source(skill_md)
            or PurePosixPath(skill_md).parent.as_posix() != source
        ):
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index manifest source metadata does not match its record path.",
                details={"source": source},
            )
        seen.add(source)


def _acquire_lock(lock_path: Path, token: str, expected_head: str | None) -> None:
    content = json.dumps(
        {
            "token": token,
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expected_head": expected_head,
        },
        sort_keys=True,
    ).encode("utf-8")
    try:
        _write_exclusive_bytes(lock_path, content)
    except FileExistsError as exc:
        raise ArchMarshalError(
            "skill_index_locked",
            "Another skill index commit holds HEAD.lock; no state was changed.",
            details={"path": str(lock_path)},
        ) from exc


def _release_lock(lock_path: Path, token: str) -> None:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if payload.get("token") == token:
        lock_path.unlink(missing_ok=True)


def _restore_head(
    root: Path,
    state_root: Path,
    expected_head: str | None,
    token: str,
) -> None:
    head_path = state_root / HEAD_NAME
    rollback_head = state_root / f".{HEAD_NAME}.{token}.rollback.tmp"
    try:
        if expected_head is None:
            head_path.unlink(missing_ok=True)
        else:
            _write_exclusive_bytes(rollback_head, f"{expected_head}\n".encode("ascii"))
            os.replace(rollback_head, head_path)
        _fsync_directory(state_root)
        if _read_head(root) != expected_head:
            raise ArchMarshalError(
                "skill_index_rollback_failed",
                "The previous skill index HEAD did not verify after rollback.",
                details={"expected_head": expected_head},
            )
    finally:
        rollback_head.unlink(missing_ok=True)


def _write_exclusive_bytes(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _plain_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


def _object_bytes(generation: dict[str, Any]) -> bytes:
    return (
        json.dumps(generation, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _ensure_object_size(payload: bytes) -> None:
    if len(payload) > MAX_INDEX_OBJECT_BYTES:
        raise ArchMarshalError(
            "skill_index_limit_exceeded",
            "Skill index generation exceeds the safe size limit.",
            details={"limit": MAX_INDEX_OBJECT_BYTES, "actual": len(payload)},
        )


def _relative_object_path(digest: str) -> Path:
    return STATE_RELATIVE / "objects" / "sha256" / f"{digest}.json"


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_safe_source(value: str) -> bool:
    if not value or len(value) > MAX_SOURCE_LENGTH or "\\" in value or "\x00" in value:
        return False
    if value == ".":
        return True
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and "." not in path.parts
        and path.as_posix() == value
    )


__all__ = [
    "commit_skill_index",
    "disabled_skill_index_plan",
    "load_skill_index",
    "plan_skill_index",
    "public_skill_index_plan",
    "skill_index_summary",
]
