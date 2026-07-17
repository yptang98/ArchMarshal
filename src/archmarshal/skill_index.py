from __future__ import annotations

import hashlib
import json
import os
import socket
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NamedTuple

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .errors import ArchMarshalError, require_workspace_root
from .safety import (
    EXCLUDED_BACKUP_PARTS,
    create_backup,
    create_bytes_exclusive,
    ensure_managed_path,
    fingerprint_directory,
    fingerprint_directory_matches,
    is_link_or_reparse,
    sha256_file,
)
from .workspace_lock import workspace_mutation_lock

FORMAT = "archmarshal-skill-index-v1"
SELECTION_FORMAT = "archmarshal-skill-selection-v1"
STATE_RELATIVE = Path(".agent/skill-overlays/.archmarshal")
HEAD_NAME = "HEAD"
LOCK_NAME = "HEAD.lock"
LOCK_FORMAT = "archmarshal-skill-index-lock-v2"
MAX_INDEX_SKILLS = 10_000
MAX_INDEX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_HEAD_BYTES = 128
MAX_SOURCE_LENGTH = 4096
MAX_HISTORY_GENERATIONS = 10_000
MAX_HISTORY_BYTES = 256 * 1024 * 1024
MAX_LOCK_BYTES = 16 * 1024
MAX_ROLLBACK_REASON_LENGTH = 500
WINDOWS_RESERVED_SOURCE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{index}" for index in range(1, 10)},
    *{f"LPT{index}" for index in range(1, 10)},
}


class _HeldLock(NamedTuple):
    path: Path
    handle: BinaryIO
    token: str
    identity: tuple[int, int]


def plan_skill_index(
    root: Path,
    discovered_skills: list[dict[str, Any]],
    *,
    created_at: str | None = None,
    excluded_packages: list[str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    loaded = load_skill_index(root)
    expected_head = loaded["head"]
    previous_records = {
        str(record["source"]): record
        for record in (loaded.get("generation") or {}).get("skills", [])
    }
    previous_exclusions = _generation_exclusions(loaded.get("generation"))
    desired_exclusions = (
        previous_exclusions
        if excluded_packages is None
        else _validated_exclusions(excluded_packages)
    )
    excluded_keys = {_portable_source_key(source) for source in desired_exclusions}
    if len(discovered_skills) > MAX_INDEX_SKILLS:
        raise ArchMarshalError(
            "skill_index_limit_exceeded",
            "Skill index planning exceeded the maximum number of skills.",
            details={"limit": MAX_INDEX_SKILLS, "actual": len(discovered_skills)},
        )
    current_records: dict[str, dict[str, Any]] = {}
    current_source_keys: set[str] = set()
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
        source_key = _portable_source_key(source)
        if source_key in current_source_keys:
            raise ArchMarshalError(
                "skill_index_plan_invalid",
                "Skill index planning received duplicate portable source paths.",
                details={"source": source},
            )
        current_records[source] = {
            "source": source,
            "state": "active",
            "manifest": _plain_manifest(manifest),
        }
        if previous_records.get(source):
            current_records[source]["manifest"] = _carry_review_decision(
                previous_records[source].get("manifest"),
                current_records[source]["manifest"],
            )
        current_source_keys.add(source_key)

    records: list[dict[str, Any]] = []
    for source in sorted(set(previous_records) | set(current_records), key=str.casefold):
        previous = previous_records.get(source)
        current = current_records.get(source)
        if current is None:
            if previous and _portable_source_key(source) in excluded_keys:
                records.append(previous)
                continue
            if previous and previous.get("state") == "removed":
                records.append(previous)
                continue
            if previous:
                records.append({**previous, "state": "removed"})
            continue
        records.append(current)

    initializing = expected_head is None
    changes = _diff_skill_records(
        list(previous_records.values()),
        records,
        initializing=initializing,
        previous_exclusions=previous_exclusions,
        current_exclusions=desired_exclusions,
    )
    changed = initializing or bool(changes)
    if not changed:
        return {
            "changed": False,
            "expected_head": expected_head,
            "digest": expected_head,
            "generation": loaded["generation"],
            "changes": [],
            "object_path": loaded.get("object_path"),
            "source_precondition_policy": "active-match-and-removed-absent",
            "source_preconditions": _source_preconditions(
                records,
                include_removed=True,
                excluded_packages=desired_exclusions,
            ),
        }

    generation = {
        "format": FORMAT,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "parent": expected_head,
        "skills": records,
        "changes": changes,
        "selection": {
            "format": SELECTION_FORMAT,
            "excluded_packages": desired_exclusions,
        },
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
        "source_precondition_policy": "active-match-and-removed-absent",
        "source_preconditions": _source_preconditions(
            records,
            include_removed=True,
            excluded_packages=desired_exclusions,
        ),
    }


def skill_index_exclusions(root: Path | str) -> list[str]:
    """Return the active exact-package exclusions from verified immutable state."""
    root_path = require_workspace_root(root)
    return _generation_exclusions(load_skill_index(root_path).get("generation"))


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


def skill_review_subject_digest(manifest: dict[str, Any]) -> str:
    """Bind a review to exact package and routing metadata, excluding the decision itself."""
    subject = _plain_manifest(
        {key: value for key, value in manifest.items() if not str(key).startswith("_")}
    )
    subject.pop("review", None)
    subject.pop("review_state", None)
    return hashlib.sha256(
        json.dumps(
            subject,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _carry_review_decision(
    previous: object,
    current: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(previous, dict):
        return current
    review = previous.get("review")
    if not isinstance(review, dict):
        return current
    decision = review.get("decision")
    if decision not in {"approved", "rejected"}:
        return current
    subject_digest = skill_review_subject_digest(current)
    if review.get("subject_digest") != subject_digest:
        return current
    carried = _plain_manifest(current)
    carried["review_state"] = decision
    carried["review"] = _plain_manifest(review)
    return carried


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
    _validate_generation(generation, digest)
    preconditions = _validate_source_preconditions(plan, generation)

    state_root = root / STATE_RELATIVE
    ensure_managed_path(root, state_root, purpose="Skill index state directory")
    state_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    lock_path = state_root / LOCK_NAME
    held_lock = _acquire_lock(root, lock_path, token, expected_head, digest)
    temporary_head = state_root / f".{HEAD_NAME}.{token}.tmp"
    published = False
    try:
        _verify_held_lock(held_lock)
        actual_head = _read_head(root)
        if actual_head != expected_head:
            raise ArchMarshalError(
                "skill_index_stale_plan",
                "Skill index HEAD changed after this plan was created.",
                details={"expected_head": expected_head, "actual_head": actual_head},
            )
        _validate_planned_transition(root, digest, generation, expected_head)
        _verify_source_preconditions(root, preconditions)

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
        _fsync_directory_chain(object_path.parent, state_root)

        _write_exclusive_bytes(temporary_head, f"{digest}\n".encode("ascii"))
        _verify_held_lock(held_lock)
        actual_head = _read_head(root)
        if actual_head != expected_head:
            raise ArchMarshalError(
                "skill_index_stale_plan",
                "Skill index HEAD changed before the atomic commit.",
                details={"expected_head": expected_head, "actual_head": actual_head},
            )
        os.replace(temporary_head, state_root / HEAD_NAME)
        published = True
        _verify_held_lock(held_lock)
        _fsync_directory(state_root)
        verified = _load_skill_index(root, allow_locked=True)
        if verified["head"] != digest:
            raise ArchMarshalError(
                "skill_index_commit_failed",
                "Skill index HEAD did not verify after commit.",
            )
        _verify_held_lock(held_lock)
    except BaseException:
        if published:
            try:
                _restore_head(root, state_root, expected_head, digest, token)
            except BaseException as rollback_error:
                raise ArchMarshalError(
                    "skill_index_rollback_failed",
                    "Skill index verification failed and the previous HEAD could not be restored.",
                    details={"expected_head": expected_head, "proposed_head": digest},
                ) from rollback_error
        raise
    finally:
        temporary_head.unlink(missing_ok=True)
        _release_lock(held_lock)
    return {
        "mode": "committed",
        "head": digest,
        "object_path": _relative_object_path(digest).as_posix(),
        "changes": plan.get("changes") or [],
    }


def load_skill_index(root: Path) -> dict[str, Any]:
    return _load_skill_index(root, allow_locked=False)


def _load_skill_index(root: Path, *, allow_locked: bool) -> dict[str, Any]:
    root = root.resolve()
    state_root = root / STATE_RELATIVE
    ensure_managed_path(root, state_root, purpose="Skill index state directory")
    if not allow_locked and _skill_index_lock_status(root).get("state") == "held":
        raise ArchMarshalError(
            "skill_index_read_blocked",
            "Skill index publication is in progress; activation is blocked until it completes.",
        )
    head = _read_head(root)
    if head is None:
        return {"format": FORMAT, "head": None, "generation": None, "object_path": None}
    # Runtime consumers must fail closed when any reachable generation is
    # missing or semantically invalid.  Verifying only HEAD would allow the
    # resolver to activate skills after their audit chain had been truncated.
    chain = _load_history_chain(root, head)
    generation = chain[0]["generation"]
    object_relative = _relative_object_path(head)
    loaded = {
        "format": FORMAT,
        "head": head,
        "generation": generation,
        "object_path": object_relative.as_posix(),
    }
    if not allow_locked:
        lock_state = _skill_index_lock_status(root).get("state")
        current_head = _read_head(root)
        if lock_state == "held" or current_head != head:
            raise ArchMarshalError(
                "skill_index_read_race",
                "Skill index changed while it was being verified; activation was blocked.",
                details={"observed_head": head, "current_head": current_head},
            )
    return loaded


def _load_generation_object(root: Path, digest: str) -> dict[str, Any]:
    if not _is_sha256(digest):
        raise ArchMarshalError(
            "skill_index_target_invalid",
            "Skill index generation target is not a SHA-256 digest.",
            details={"target": digest},
        )
    object_relative = _relative_object_path(digest)
    object_path = root / object_relative
    ensure_managed_path(root, object_path, purpose="Skill index generation object")
    if not object_path.is_file() or object_path.is_symlink():
        raise ArchMarshalError(
            "skill_index_object_missing",
            "Skill index HEAD points to a missing or linked generation object.",
            details={"head": digest, "path": str(object_path)},
        )
    try:
        object_size = object_path.stat().st_size
    except OSError as exc:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation metadata could not be read.",
            details={"head": digest, "path": str(object_path)},
        ) from exc
    if object_size > MAX_INDEX_OBJECT_BYTES:
        raise ArchMarshalError(
            "skill_index_limit_exceeded",
            "Skill index generation exceeds the safe size limit.",
            details={"limit": MAX_INDEX_OBJECT_BYTES, "actual": object_size},
        )
    if sha256_file(object_path) != digest:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation bytes do not match HEAD.",
            details={"head": digest, "path": str(object_path)},
        )
    try:
        generation = json.loads(object_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation is not valid UTF-8 JSON.",
            details={"head": digest},
        ) from exc
    _validate_generation(generation, digest)
    return generation


def public_skill_index_plan(plan: dict[str, Any]) -> dict[str, Any]:
    generation = plan.get("generation")
    exclusions = _generation_exclusions(generation if isinstance(generation, dict) else None)
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
        "excluded_packages": exclusions,
        "excluded_package_count": len(exclusions),
        "commit_policy": "exclusive-lock-and-head-compare-and-swap",
        "source_mutation": False,
    }


def skill_index_summary(loaded: dict[str, Any]) -> dict[str, Any]:
    records = (loaded.get("generation") or {}).get("skills", [])
    exclusions = _generation_exclusions(loaded.get("generation"))
    excluded_keys = {_portable_source_key(source) for source in exclusions}
    return {
        "format": FORMAT,
        "head": loaded.get("head"),
        "object_path": loaded.get("object_path"),
        "active_skills": sum(
            record.get("state") == "active"
            and _portable_source_key(str(record.get("source") or "")) not in excluded_keys
            for record in records
        ),
        "removed_skills": sum(record.get("state") == "removed" for record in records),
        "excluded_packages": exclusions,
        "excluded_package_count": len(exclusions),
        "immutable_objects": True,
        "head_commit": "lock_and_compare_and_swap",
    }


def skill_index_status(
    root: Path | str,
    *,
    history_limit: int = 20,
    history_from: str | None = None,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    if history_limit < 1 or history_limit > 100:
        raise ArchMarshalError(
            "skill_index_history_limit_invalid",
            "Skill index history limit must be between 1 and 100.",
        )
    lock_status = _skill_index_lock_status(root_path)
    if lock_status.get("state") == "held":
        return {
            "tool": "archmarshal",
            "stage": "skill_index_status",
            "mode": "read_only",
            "root": str(root_path),
            "head": None,
            "object_path": None,
            "chain_status": "publication_in_progress",
            "history": [],
            "history_limit": history_limit,
            "history_from": history_from,
            "continuation": None,
            "lock": lock_status,
            "source_mutation": False,
        }
    loaded = load_skill_index(root_path)
    history: list[dict[str, Any]] = []
    continuation = None
    chain_status = "uninitialized"
    if loaded["head"]:
        chain = _load_history_chain(root_path, str(loaded["head"]))
        start_index = 0
        if history_from is not None:
            if not _is_sha256(history_from):
                raise ArchMarshalError(
                    "skill_index_history_cursor_invalid",
                    "Skill index history cursor must be a full SHA-256 digest.",
                )
            positions = {str(item["digest"]): index for index, item in enumerate(chain)}
            if history_from not in positions:
                raise ArchMarshalError(
                    "skill_index_history_cursor_invalid",
                    "Skill index history cursor is not reachable from current HEAD.",
                    details={"cursor": history_from},
                )
            start_index = positions[history_from]
        page = chain[start_index : start_index + history_limit]
        history = [_history_item(item) for item in page]
        next_index = start_index + history_limit
        if len(chain) > next_index:
            continuation = chain[next_index]["digest"]
        chain_status = "healthy"
    elif history_from is not None:
        raise ArchMarshalError(
            "skill_index_history_cursor_invalid",
            "Uninitialized skill index has no reachable history cursor.",
        )
    return {
        "tool": "archmarshal",
        "stage": "skill_index_status",
        "mode": "read_only",
        "root": str(root_path),
        "head": loaded["head"],
        "object_path": loaded["object_path"],
        "chain_status": chain_status,
        "history": history,
        "history_limit": history_limit,
        "history_from": history_from,
        "continuation": continuation,
        "lock": _skill_index_lock_status(root_path),
        "source_mutation": False,
    }


def plan_skill_index_rollback(
    root: Path | str,
    target: str,
    *,
    expected_head: str | None = None,
    reason: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    reason = reason.strip()
    if len(reason) > MAX_ROLLBACK_REASON_LENGTH or "\x00" in reason:
        raise ArchMarshalError(
            "skill_index_rollback_reason_invalid",
            f"Rollback reason must be at most {MAX_ROLLBACK_REASON_LENGTH} characters.",
        )
    if not _is_sha256(target):
        raise ArchMarshalError(
            "skill_index_target_invalid",
            "Rollback target must be a full SHA-256 generation digest.",
            details={"target": target},
        )
    loaded = load_skill_index(root_path)
    current_head = loaded["head"]
    if current_head is None:
        raise ArchMarshalError(
            "skill_index_uninitialized",
            "Skill index rollback requires an initialized HEAD.",
        )
    if expected_head is not None and expected_head != current_head:
        raise ArchMarshalError(
            "skill_index_stale_plan",
            "Skill index HEAD does not match the explicitly expected generation.",
            details={"expected_head": expected_head, "actual_head": current_head},
        )
    if target == current_head:
        raise ArchMarshalError(
            "skill_index_rollback_noop",
            "Rollback target is already the active generation.",
        )
    chain = _load_history_chain(root_path, str(current_head))
    by_digest = {str(item["digest"]): item for item in chain}
    target_item = by_digest.get(target)
    if target_item is None:
        raise ArchMarshalError(
            "skill_index_target_not_ancestor",
            "Rollback target is not an ancestor of the active skill index HEAD.",
            details={"target": target, "head": current_head},
        )
    current_generation = loaded["generation"]
    target_generation = target_item["generation"]
    records = _rollback_records(
        current_generation.get("skills") or [],
        target_generation.get("skills") or [],
    )
    current_exclusions = _generation_exclusions(current_generation)
    target_exclusions = _generation_exclusions(target_generation)
    changes = _diff_skill_records(
        current_generation.get("skills") or [],
        records,
        previous_exclusions=current_exclusions,
        current_exclusions=target_exclusions,
    )
    changes.append(
        {
            "kind": "rollback",
            "source": "",
            "target": target,
            "reason": reason,
        }
    )
    generation = {
        "format": FORMAT,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "parent": current_head,
        "skills": records,
        "changes": changes,
        "selection": {
            "format": SELECTION_FORMAT,
            "excluded_packages": target_exclusions,
        },
    }
    payload = _object_bytes(generation)
    _ensure_object_size(payload)
    digest = hashlib.sha256(payload).hexdigest()
    preconditions = _source_preconditions(
        records,
        include_removed=False,
        excluded_packages=target_exclusions,
    )
    _verify_source_preconditions(root_path, preconditions)
    return {
        "changed": True,
        "expected_head": current_head,
        "digest": digest,
        "generation": generation,
        "changes": changes,
        "object_path": _relative_object_path(digest),
        "source_precondition_policy": "active-match",
        "source_preconditions": preconditions,
        "rollback_target": target,
        "rollback_reason": reason,
    }


def rollback_skill_index(
    root: Path | str,
    target: str,
    *,
    expected_head: str | None = None,
    expected_plan: str | None = None,
    reason: str = "",
    apply: bool = False,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    plan = plan_skill_index_rollback(
        root_path,
        target,
        expected_head=expected_head,
        reason=reason,
    )
    plan_digest = _rollback_plan_digest(root_path, plan)
    payload = {
        "tool": "archmarshal",
        "stage": "skill_index_rollback",
        "mode": "propose_only",
        "root": str(root_path),
        "expected_head": plan["expected_head"],
        "target": target,
        "proposed_head": plan["digest"],
        "plan_digest": plan_digest,
        "apply_precondition": "--expect-head <head> --expect-plan <plan_digest>",
        "object_path": plan["object_path"].as_posix(),
        "changes": plan["changes"],
        "reason": reason.strip(),
        "source_mutation": False,
        "notes": [
            "Rollback creates a new audited metadata generation; it never moves HEAD backward.",
            "Source skill files are not restored, modified, removed, or overwritten.",
            "Active target skills must still match their recorded complete-package hashes.",
        ],
    }
    if not apply:
        return payload
    if expected_head is None:
        raise ArchMarshalError(
            "skill_index_expected_head_required",
            "Applied rollback requires --expect-head from a reviewed preview.",
        )
    if expected_plan is None:
        raise ArchMarshalError(
            "skill_index_expected_plan_required",
            "Applied rollback requires --expect-plan from the reviewed preview.",
        )
    if expected_plan != plan_digest:
        raise ArchMarshalError(
            "skill_index_stale_plan",
            "Skill rollback target, reason, or source preconditions differ from the reviewed plan.",
            details={"expected_plan": expected_plan, "actual_plan": plan_digest},
        )
    with workspace_mutation_lock(root_path, operation="skill_index_rollback") as held:
        revalidated = plan_skill_index_rollback(
            root_path,
            target,
            expected_head=expected_head,
            reason=reason,
            created_at=plan["generation"]["created_at"],
        )
        if revalidated["digest"] != plan["digest"]:
            raise ArchMarshalError(
                "skill_index_stale_plan",
                "Rollback plan changed before backup; HEAD was not updated.",
            )
        held.verify()
        backup_dir = root_path / ".agent" / "backups"
        ensure_managed_path(root_path, backup_dir, purpose="Skill rollback backup directory")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_files = [
            root_path / STATE_RELATIVE / HEAD_NAME,
            root_path / _relative_object_path(str(plan["expected_head"])),
            root_path / _relative_object_path(target),
        ]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = create_backup(
            root_path,
            backup_files,
            backup_dir / f"{timestamp}-pre-skill-index-rollback.zip",
            reason="ArchMarshal skill index rollback before publishing a new audited generation.",
        )
        held.verify()
        revalidated = plan_skill_index_rollback(
            root_path,
            target,
            expected_head=expected_head,
            reason=reason,
            created_at=plan["generation"]["created_at"],
        )
        if revalidated["digest"] != plan["digest"]:
            raise ArchMarshalError(
                "skill_index_stale_plan",
                "Rollback plan changed after backup; HEAD was not updated.",
            )
        commit = commit_skill_index(root_path, revalidated)
        held.verify()
    payload["mode"] = "rolled_back"
    payload["backup"] = backup
    payload["commit"] = commit
    return payload


def _rollback_plan_digest(root: Path, plan: dict[str, Any]) -> str:
    generation = plan.get("generation") if isinstance(plan.get("generation"), dict) else {}
    intent = {
        "format": "archmarshal-skill-index-rollback-plan-v1",
        "root": str(root.resolve()),
        "expected_head": plan.get("expected_head"),
        "target": plan.get("rollback_target"),
        "reason": plan.get("rollback_reason"),
        "skills": generation.get("skills") or [],
        "selection": generation.get("selection") or {},
        "changes": plan.get("changes") or [],
        "source_precondition_policy": plan.get("source_precondition_policy"),
        "source_preconditions": plan.get("source_preconditions") or [],
    }
    canonical = json.dumps(
        intent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_history_chain(root: Path, head: str) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = head
    total_bytes = 0
    while current is not None:
        if current in seen:
            raise ArchMarshalError(
                "skill_index_history_invalid",
                "Skill index history contains a parent cycle.",
                details={"digest": current},
            )
        if len(chain) >= MAX_HISTORY_GENERATIONS:
            raise ArchMarshalError(
                "skill_index_history_limit_exceeded",
                "Skill index history exceeds the safe generation limit.",
                details={"limit": MAX_HISTORY_GENERATIONS},
            )
        object_path = root / _relative_object_path(current)
        ensure_managed_path(root, object_path, purpose="Skill index history object")
        try:
            total_bytes += object_path.stat().st_size
        except OSError as exc:
            raise ArchMarshalError(
                "skill_index_object_missing",
                "Skill index history points to a missing generation object.",
                details={"head": current, "path": str(object_path)},
            ) from exc
        if total_bytes > MAX_HISTORY_BYTES:
            raise ArchMarshalError(
                "skill_index_history_limit_exceeded",
                "Skill index history exceeds the safe cumulative byte limit.",
                details={"limit": MAX_HISTORY_BYTES, "actual": total_bytes},
            )
        generation = _load_generation_object(root, current)
        chain.append({"digest": current, "generation": generation})
        seen.add(current)
        current = generation.get("parent")
    _validate_history_transitions(chain)
    return chain


def _validate_planned_transition(
    root: Path,
    digest: str,
    generation: dict[str, Any],
    expected_head: str | None,
) -> None:
    if generation.get("parent") != expected_head:
        raise ArchMarshalError(
            "skill_index_plan_invalid",
            "Planned generation parent does not match its expected HEAD.",
            details={"parent": generation.get("parent"), "expected_head": expected_head},
        )
    parent_chain = _load_history_chain(root, expected_head) if expected_head else []
    _validate_history_transitions([{"digest": digest, "generation": generation}, *parent_chain])


def _validate_history_transitions(chain: list[dict[str, Any]]) -> None:
    digests = {str(item["digest"]): index for index, item in enumerate(chain)}
    for index, item in enumerate(chain):
        generation = item["generation"]
        parent_item = chain[index + 1] if index + 1 < len(chain) else None
        parent_digest = generation.get("parent")
        if parent_item is None:
            if parent_digest is not None:
                raise ArchMarshalError(
                    "skill_index_history_invalid",
                    "Skill index history ended before reaching its declared parent.",
                    details={"digest": item["digest"], "parent": parent_digest},
                )
            parent_records: list[dict[str, Any]] = []
            parent_exclusions: list[str] = []
        else:
            if parent_digest != parent_item["digest"]:
                raise ArchMarshalError(
                    "skill_index_history_invalid",
                    "Skill index history parent order is inconsistent.",
                    details={"digest": item["digest"], "parent": parent_digest},
                )
            parent_records = parent_item["generation"].get("skills") or []
            parent_exclusions = _generation_exclusions(parent_item["generation"])
        child_records = generation.get("skills") or []
        child_exclusions = _generation_exclusions(generation)
        changes = generation.get("changes") or []
        rollback_changes = [change for change in changes if change.get("kind") == "rollback"]
        ordinary_changes = [change for change in changes if change.get("kind") != "rollback"]
        expected_changes = _diff_skill_records(
            parent_records,
            child_records,
            initializing=parent_item is None,
            previous_exclusions=parent_exclusions,
            current_exclusions=child_exclusions,
        )
        if ordinary_changes != expected_changes:
            raise ArchMarshalError(
                "skill_index_history_invalid",
                "Skill index generation changes do not match its parent snapshot.",
                details={"digest": item["digest"]},
            )
        if rollback_changes:
            rollback = rollback_changes[0]
            target = str(rollback.get("target") or "")
            target_index = digests.get(target)
            if target_index is None or target_index <= index:
                raise ArchMarshalError(
                    "skill_index_history_invalid",
                    "Skill index rollback target is not an ancestor of the rollback generation.",
                    details={"digest": item["digest"], "target": target},
                )
            expected_records = _rollback_records(
                parent_records,
                chain[target_index]["generation"].get("skills") or [],
            )
            if child_records != expected_records:
                raise ArchMarshalError(
                    "skill_index_history_invalid",
                    "Skill index rollback snapshot does not match its declared target.",
                    details={"digest": item["digest"], "target": target},
                )
            if child_exclusions != _generation_exclusions(chain[target_index]["generation"]):
                raise ArchMarshalError(
                    "skill_index_history_invalid",
                    "Skill index rollback selection does not match its declared target.",
                    details={"digest": item["digest"], "target": target},
                )


def _history_item(item: dict[str, Any]) -> dict[str, Any]:
    generation = item["generation"]
    records = generation.get("skills") or []
    exclusions = _generation_exclusions(generation)
    excluded_keys = {_portable_source_key(source) for source in exclusions}
    return {
        "digest": item["digest"],
        "created_at": generation.get("created_at"),
        "parent": generation.get("parent"),
        "changes": generation.get("changes") or [],
        "active_skills": sum(
            record.get("state") == "active"
            and _portable_source_key(str(record.get("source") or "")) not in excluded_keys
            for record in records
        ),
        "removed_skills": sum(record.get("state") == "removed" for record in records),
        "excluded_package_count": len(exclusions),
        "excluded_packages": exclusions,
    }


def _rollback_records(
    current_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current = {str(record["source"]): record for record in current_records}
    target = {str(record["source"]): record for record in target_records}
    records: list[dict[str, Any]] = []
    for source in sorted(set(current) | set(target), key=str.casefold):
        if source in target:
            records.append(_plain_manifest(target[source]))
        else:
            records.append({**_plain_manifest(current[source]), "state": "removed"})
    return records


def _diff_skill_records(
    parent_records: list[dict[str, Any]],
    child_records: list[dict[str, Any]],
    *,
    initializing: bool = False,
    previous_exclusions: list[str] | None = None,
    current_exclusions: list[str] | None = None,
) -> list[dict[str, str]]:
    parent = {str(record["source"]): record for record in parent_records}
    child = {str(record["source"]): record for record in child_records}
    changes: list[dict[str, str]] = []
    if initializing:
        changes.append({"kind": "initialized", "source": ""})
    for source in sorted(set(parent) | set(child), key=str.casefold):
        previous = parent.get(source)
        current = child.get(source)
        if current is None:
            raise ArchMarshalError(
                "skill_index_history_invalid",
                "Skill index generations must retain removed source tombstones.",
                details={"source": source},
            )
        if previous is None:
            if current.get("state") != "active":
                raise ArchMarshalError(
                    "skill_index_history_invalid",
                    "A new skill index source cannot begin as a removed tombstone.",
                    details={"source": source},
                )
            changes.append({"kind": "added", "source": source})
        elif previous.get("state") == "active" and current.get("state") == "removed":
            changes.append({"kind": "removed", "source": source})
        elif previous.get("state") == "removed" and current.get("state") == "active":
            changes.append({"kind": "restored", "source": source})
        elif previous.get("manifest") != current.get("manifest"):
            changes.append({"kind": "modified", "source": source})
    previous_selection = set(previous_exclusions or [])
    current_selection = set(current_exclusions or [])
    for source in sorted(current_selection - previous_selection, key=str.casefold):
        changes.append({"kind": "excluded", "source": source})
    for source in sorted(previous_selection - current_selection, key=str.casefold):
        changes.append({"kind": "included", "source": source})
    return changes


def _skill_index_lock_status(root: Path) -> dict[str, Any]:
    lock_path = root / STATE_RELATIVE / LOCK_NAME
    ensure_managed_path(root, lock_path, purpose="Skill index lock status")
    if not lock_path.exists():
        return {"state": "absent", "automatic_recovery": False}
    if not lock_path.is_file() or is_link_or_reparse(lock_path):
        return {
            "state": "invalid",
            "automatic_recovery": False,
            "reason": "lock_not_regular_file",
        }
    try:
        handle = lock_path.open("r+b", buffering=0)
    except OSError:
        return {
            "state": "invalid",
            "automatic_recovery": False,
            "reason": "lock_unreadable",
        }
    acquired = False
    try:
        acquired = _try_os_lock(handle)
        if not acquired:
            return {
                "state": "held",
                "automatic_recovery": False,
                "path": lock_path.relative_to(root).as_posix(),
                "warning": "An OS-level lock is active; recovery and commit are blocked.",
            }
        try:
            raw = _read_lock_bytes(handle)
        except ArchMarshalError as exc:
            return {
                "state": "invalid",
                "automatic_recovery": False,
                "reason": exc.code,
            }
        if not raw:
            return {
                "state": "idle",
                "automatic_recovery": True,
                "path": lock_path.relative_to(root).as_posix(),
            }
        try:
            metadata = _parse_lock_metadata(raw)
        except ArchMarshalError as exc:
            return {
                "state": "legacy_manual_review"
                if exc.code == "skill_index_legacy_lock"
                else "invalid",
                "automatic_recovery": False,
                "reason": exc.code,
                "warning": "Do not delete legacy or malformed lock metadata automatically.",
            }
        try:
            classification = _classify_recoverable_lock(root, metadata)
        except ArchMarshalError as exc:
            return {
                "state": "recovery_blocked",
                "automatic_recovery": False,
                "reason": exc.code,
                "metadata": metadata,
            }
        return {
            "state": "recoverable",
            "automatic_recovery": True,
            "classification": classification,
            "path": lock_path.relative_to(root).as_posix(),
            "metadata": metadata,
        }
    finally:
        if acquired:
            _unlock_os_lock(handle)
        handle.close()


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
    if not isinstance(changes, list) or len(changes) > (MAX_INDEX_SKILLS * 2) + 1:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation contains an invalid change list.",
        )
    rollback_count = 0
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("source"), str):
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index generation contains an invalid change record.",
            )
        kind = change.get("kind")
        if kind == "rollback":
            rollback_count += 1
            reason = change.get("reason")
            if (
                change.get("source") != ""
                or not isinstance(change.get("target"), str)
                or not _is_sha256(str(change.get("target")))
                or not isinstance(reason, str)
                or len(reason) > MAX_ROLLBACK_REASON_LENGTH
                or "\x00" in reason
            ):
                raise ArchMarshalError(
                    "skill_index_integrity_failed",
                    "Skill index generation contains an invalid rollback record.",
                )
        elif (
            kind
            not in {
                "initialized",
                "added",
                "modified",
                "removed",
                "restored",
                "excluded",
                "included",
            }
            or (kind != "initialized" and not _is_safe_source(str(change.get("source"))))
            or (kind == "initialized" and change.get("source") != "")
        ):
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index generation contains an invalid change record.",
            )
    if rollback_count > 1:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation contains more than one rollback operation.",
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
        source_key = _portable_source_key(source)
        if source_key in seen:
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index generation contains duplicate portable source paths.",
                details={"source": source},
            )
        source_metadata = record["manifest"].get("source")
        skill_dir = source_metadata.get("skill_dir") if isinstance(source_metadata, dict) else None
        skill_md = source_metadata.get("skill_md") if isinstance(source_metadata, dict) else None
        package_hash = (
            source_metadata.get("package_sha256") if isinstance(source_metadata, dict) else None
        )
        if (
            skill_dir != source
            or not isinstance(skill_md, str)
            or not _is_safe_source(skill_md)
            or PurePosixPath(skill_md).parent.as_posix() != source
            or not isinstance(package_hash, str)
            or not _is_sha256(package_hash)
        ):
            raise ArchMarshalError(
                "skill_index_integrity_failed",
                "Skill index manifest source metadata does not match its record path.",
                details={"source": source},
            )
        seen.add(source_key)
    try:
        _generation_exclusions(generation)
    except ArchMarshalError as exc:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "Skill index generation contains an invalid selection policy.",
            details={"cause": exc.code},
        ) from exc


def _source_is_nested(candidate: str, parent: str) -> bool:
    candidate_parts = () if candidate == "." else PurePosixPath(candidate).parts
    parent_parts = () if parent == "." else PurePosixPath(parent).parts
    return (
        candidate != parent
        and len(candidate_parts) > len(parent_parts)
        and candidate_parts[: len(parent_parts)] == parent_parts
    )


def _source_preconditions(
    records: list[dict[str, Any]],
    *,
    include_removed: bool,
    excluded_packages: list[str] | None = None,
) -> list[dict[str, Any]]:
    preconditions: list[dict[str, Any]] = []
    excluded_keys = {_portable_source_key(item) for item in (excluded_packages or [])}
    package_boundaries = {
        str(record["source"]) for record in records if record.get("state") == "active"
    }
    package_boundaries.update(excluded_packages or [])
    for record in records:
        source = str(record["source"])
        if _portable_source_key(source) in excluded_keys:
            continue
        if record.get("state") == "active":
            source_metadata = record["manifest"].get("source")
            package_hash = (
                source_metadata.get("package_sha256") if isinstance(source_metadata, dict) else None
            )
            item = {
                "source": source,
                "state": "active",
                "package_sha256": str(package_hash or ""),
            }
            if (
                isinstance(source_metadata, dict)
                and source_metadata.get("package_boundary") == "portable-source-v1"
            ):
                item["package_boundary"] = "portable-source-v1"
            nested = sorted(
                (
                    candidate
                    for candidate in package_boundaries
                    if _source_is_nested(candidate, source)
                ),
                key=str.casefold,
            )
            if nested:
                item["nested_skill_boundaries"] = nested
            preconditions.append(item)
        elif include_removed:
            preconditions.append({"source": source, "state": "absent"})
    return preconditions


def _validate_source_preconditions(
    plan: dict[str, Any],
    generation: dict[str, Any],
) -> list[dict[str, Any]]:
    policy = plan.get("source_precondition_policy")
    if policy not in {"active-match", "active-match-and-removed-absent"}:
        raise ArchMarshalError(
            "skill_index_plan_invalid",
            "Skill index commit requires an explicit source precondition policy.",
        )
    preconditions = plan.get("source_preconditions")
    if not isinstance(preconditions, list):
        raise ArchMarshalError(
            "skill_index_plan_invalid",
            "Skill index commit requires source preconditions.",
        )
    records = generation.get("skills") or []
    expected = _source_preconditions(
        records,
        include_removed=policy == "active-match-and-removed-absent",
        excluded_packages=_generation_exclusions(generation),
    )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in preconditions:
        if not isinstance(item, dict):
            raise ArchMarshalError(
                "skill_index_plan_invalid",
                "Skill index source preconditions contain a non-object entry.",
            )
        source = item.get("source")
        state = item.get("state")
        package_hash = item.get("package_sha256")
        package_boundary = item.get("package_boundary")
        nested_boundaries = item.get("nested_skill_boundaries")
        source_key = _portable_source_key(source) if isinstance(source, str) else ""
        if (
            not isinstance(source, str)
            or not _is_safe_source(source)
            or source_key in seen
            or state not in {"active", "absent"}
            or (
                state == "active"
                and (not isinstance(package_hash, str) or not _is_sha256(package_hash))
            )
            or (state == "absent" and package_hash is not None)
            or package_boundary not in {None, "portable-source-v1"}
            or (state == "absent" and package_boundary is not None)
            or (
                nested_boundaries is not None
                and (
                    state != "active"
                    or not isinstance(nested_boundaries, list)
                    or not all(
                        isinstance(value, str)
                        and _is_safe_source(value)
                        and _source_is_nested(value, source)
                        for value in nested_boundaries
                    )
                    or nested_boundaries != sorted(set(nested_boundaries), key=str.casefold)
                )
            )
        ):
            raise ArchMarshalError(
                "skill_index_plan_invalid",
                "Skill index source preconditions are invalid or duplicated.",
                details={"source": source},
            )
        normalized_item = {"source": source, "state": str(state)}
        if state == "active":
            normalized_item["package_sha256"] = str(package_hash)
            if package_boundary is not None:
                normalized_item["package_boundary"] = str(package_boundary)
            if nested_boundaries is not None:
                normalized_item["nested_skill_boundaries"] = list(nested_boundaries)
        normalized.append(normalized_item)
        seen.add(source_key)
    if normalized != expected:
        raise ArchMarshalError(
            "skill_index_plan_invalid",
            "Skill index source preconditions do not match the planned generation.",
        )
    return normalized


def _verify_source_preconditions(root: Path, preconditions: list[dict[str, Any]]) -> None:
    failures: list[dict[str, str]] = []
    for precondition in preconditions:
        source = precondition["source"]
        source_dir = root if source == "." else root / source
        try:
            ensure_managed_path(root, source_dir, purpose="Skill source precondition")
            if precondition["state"] == "active":
                package = fingerprint_directory(
                    source_dir,
                    purpose="Skill source precondition",
                    entrypoint_only=source == ".",
                    include_modes=True,
                    excluded_parts=(
                        EXCLUDED_BACKUP_PARTS
                        if precondition.get("package_boundary") == "portable-source-v1"
                        else ()
                    ),
                    excluded_directories=[
                        root / PurePosixPath(value)
                        for value in precondition.get("nested_skill_boundaries", [])
                    ],
                )
                if not fingerprint_directory_matches(
                    package,
                    precondition["package_sha256"],
                ):
                    failures.append({"source": source, "reason": "package_changed"})
            else:
                skill_md = source_dir / "SKILL.md"
                try:
                    skill_md.lstat()
                except FileNotFoundError:
                    pass
                else:
                    failures.append({"source": source, "reason": "source_restored"})
        except (ArchMarshalError, OSError) as exc:
            reason = exc.code if isinstance(exc, ArchMarshalError) else "source_unreadable"
            failures.append({"source": source, "reason": reason})
    if failures:
        raise ArchMarshalError(
            "skill_index_source_changed",
            "Skill sources changed or became unsafe after planning; HEAD was not updated.",
            details={"failures": failures[:100], "failure_count": len(failures)},
        )


def _acquire_lock(
    root: Path,
    lock_path: Path,
    token: str,
    expected_head: str | None,
    proposed_head: str,
) -> _HeldLock:
    ensure_managed_path(root, lock_path, purpose="Skill index process lock")
    handle = lock_path.open("a+b", buffering=0)
    try:
        if not _try_os_lock(handle):
            raise ArchMarshalError(
                "skill_index_locked",
                "Another process holds the skill index OS lock; no state was changed.",
                details={"path": str(lock_path)},
            )
        prior_raw = _read_lock_bytes(handle)
        if prior_raw:
            prior = _parse_lock_metadata(prior_raw)
            recovery = _classify_recoverable_lock(root, prior)
            _record_lock_recovery(root, lock_path.parent, prior, recovery)
        metadata = {
            "format": LOCK_FORMAT,
            "token": token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expected_head": expected_head,
            "proposed_head": proposed_head,
        }
        _write_lock_metadata(handle, metadata)
        handle_metadata = os.fstat(handle.fileno())
        held = _HeldLock(
            lock_path,
            handle,
            token,
            (handle_metadata.st_dev, handle_metadata.st_ino),
        )
        _verify_held_lock(held)
        return held
    except BaseException:
        try:
            _unlock_os_lock(handle)
        except OSError:
            pass
        handle.close()
        raise


def _release_lock(held: _HeldLock) -> None:
    handle = held.handle
    try:
        try:
            metadata = _parse_lock_metadata(_read_lock_bytes(handle))
        except ArchMarshalError:
            metadata = {}
        if metadata.get("token") == held.token:
            handle.seek(0)
            handle.truncate(0)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        try:
            _unlock_os_lock(handle)
        finally:
            handle.close()


def _verify_held_lock(held: _HeldLock) -> None:
    try:
        metadata = held.path.lstat()
    except OSError as exc:
        raise ArchMarshalError(
            "skill_index_lock_replaced",
            "Skill index lock path disappeared while held; publication stopped.",
        ) from exc
    if (
        is_link_or_reparse(held.path)
        or metadata.st_nlink < 1
        or (metadata.st_dev, metadata.st_ino) != held.identity
    ):
        raise ArchMarshalError(
            "skill_index_lock_replaced",
            "Skill index lock path was replaced while held; publication stopped.",
        )


def _try_os_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_os_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_lock_bytes(handle: BinaryIO) -> bytes:
    handle.seek(0)
    raw = handle.read(MAX_LOCK_BYTES + 1)
    if len(raw) > MAX_LOCK_BYTES:
        raise ArchMarshalError(
            "skill_index_lock_invalid",
            "Skill index lock metadata exceeds the safe size limit.",
            details={"limit": MAX_LOCK_BYTES},
        )
    return raw


def _parse_lock_metadata(raw: bytes) -> dict[str, Any]:
    try:
        metadata = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "skill_index_lock_invalid",
            "Skill index lock metadata is not valid UTF-8 JSON.",
        ) from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("format") != LOCK_FORMAT
        or not isinstance(metadata.get("token"), str)
        or not isinstance(metadata.get("pid"), int)
        or not isinstance(metadata.get("hostname"), str)
        or not isinstance(metadata.get("created_at"), str)
        or (
            metadata.get("expected_head") is not None
            and (
                not isinstance(metadata.get("expected_head"), str)
                or not _is_sha256(str(metadata.get("expected_head")))
            )
        )
        or not isinstance(metadata.get("proposed_head"), str)
        or not _is_sha256(str(metadata.get("proposed_head")))
    ):
        raise ArchMarshalError(
            "skill_index_legacy_lock",
            "Skill index lock is legacy or malformed and requires explicit manual review.",
        )
    return metadata


def _write_lock_metadata(handle: BinaryIO, metadata: dict[str, Any]) -> None:
    content = (
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(content) > MAX_LOCK_BYTES:
        raise ArchMarshalError(
            "skill_index_lock_invalid",
            "Skill index lock metadata exceeds the safe size limit.",
        )
    handle.seek(0)
    handle.write(content)
    handle.truncate()
    handle.flush()
    os.fsync(handle.fileno())


def _classify_recoverable_lock(root: Path, metadata: dict[str, Any]) -> str:
    current_head = _read_head(root)
    expected_head = metadata.get("expected_head")
    proposed_head = str(metadata["proposed_head"])
    if current_head == expected_head:
        return "abandoned_before_publish"
    if current_head == proposed_head:
        _load_generation_object(root, proposed_head)
        return "published_before_exit"
    raise ArchMarshalError(
        "skill_index_recovery_required",
        "Released lock metadata does not match current HEAD; automatic recovery was refused.",
        details={
            "current_head": current_head,
            "expected_head": expected_head,
            "proposed_head": proposed_head,
        },
    )


def _record_lock_recovery(
    root: Path,
    state_root: Path,
    metadata: dict[str, Any],
    classification: str,
) -> None:
    recovery_root = state_root / "recovery"
    ensure_managed_path(root, recovery_root, purpose="Skill index recovery audit directory")
    recovery_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}.json"
    record = {
        "format": "archmarshal-skill-index-recovery-v1",
        "recovered_at": timestamp.isoformat(),
        "classification": classification,
        "current_head": _read_head(root),
        "prior_lock": metadata,
        "source_mutation": False,
    }
    path = recovery_root / filename
    _write_exclusive_bytes(
        path,
        (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"),
    )
    _fsync_directory_chain(recovery_root, state_root)


def _restore_head(
    root: Path,
    state_root: Path,
    expected_head: str | None,
    proposed_head: str,
    token: str,
) -> None:
    head_path = state_root / HEAD_NAME
    rollback_head = state_root / f".{HEAD_NAME}.{token}.rollback.tmp"
    try:
        actual_head = _read_head(root)
        if actual_head != proposed_head:
            raise ArchMarshalError(
                "skill_index_rollback_conflict",
                "Skill index HEAD changed after publication; automatic rollback was refused.",
                details={"expected_proposed_head": proposed_head, "actual_head": actual_head},
            )
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
    create_bytes_exclusive(path, content)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory_chain(path: Path, stop: Path) -> None:
    current = path
    while True:
        _fsync_directory(current)
        if current == stop:
            return
        if stop not in current.parents:
            raise ArchMarshalError(
                "skill_index_path_invalid",
                "Skill index object directory is outside its state root.",
            )
        current = current.parent


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


def _generation_exclusions(generation: object) -> list[str]:
    if generation is None:
        return []
    if not isinstance(generation, dict):
        raise ArchMarshalError(
            "skill_index_selection_invalid",
            "Skill index selection requires a generation mapping.",
        )
    selection = generation.get("selection")
    if selection is None:
        return []
    if (
        not isinstance(selection, dict)
        or set(selection) != {"format", "excluded_packages"}
        or selection.get("format") != SELECTION_FORMAT
        or not isinstance(selection.get("excluded_packages"), list)
    ):
        raise ArchMarshalError(
            "skill_index_selection_invalid",
            "Skill index selection has an unsupported structure.",
        )
    return _validated_exclusions(selection["excluded_packages"])


def _validated_exclusions(values: list[object]) -> list[str]:
    if len(values) > MAX_INDEX_SKILLS:
        raise ArchMarshalError(
            "skill_index_selection_invalid",
            "Skill index exclusion count exceeds the safe Skill limit.",
            details={"limit": MAX_INDEX_SKILLS, "actual": len(values)},
        )
    normalized: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str) or not _is_safe_source(value):
            raise ArchMarshalError(
                "skill_index_selection_invalid",
                "Skill index exclusion contains an unsafe package path.",
                details={"source": value},
            )
        key = _portable_source_key(value)
        previous = normalized.get(key)
        if previous is not None and previous != value:
            raise ArchMarshalError(
                "skill_index_selection_invalid",
                "Skill index exclusions collide under portable path rules.",
                details={"first": previous, "second": value},
            )
        normalized[key] = value
    return sorted(normalized.values(), key=lambda item: (_portable_source_key(item), item))


def _is_safe_source(value: str) -> bool:
    if not value or len(value) > MAX_SOURCE_LENGTH or "\\" in value or "\x00" in value:
        return False
    if value == ".":
        return True
    path = PurePosixPath(value)
    invalid_component = any(
        not part
        or part.endswith((" ", "."))
        or any(character in part for character in '<>:"|?*')
        or any(ord(character) < 32 for character in part)
        or part.split(".", 1)[0].upper() in WINDOWS_RESERVED_SOURCE_NAMES
        for part in path.parts
    )
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and "." not in path.parts
        and not invalid_component
        and path.as_posix() == value
    )


def _portable_source_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


__all__ = [
    "commit_skill_index",
    "disabled_skill_index_plan",
    "load_skill_index",
    "plan_skill_index",
    "plan_skill_index_rollback",
    "public_skill_index_plan",
    "rollback_skill_index",
    "skill_index_status",
    "skill_index_exclusions",
    "skill_index_summary",
    "skill_review_subject_digest",
]
