from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NamedTuple

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .errors import ArchMarshalError
from .io import load_yaml_safe
from .safety import (
    create_bytes_exclusive,
    ensure_managed_path,
    fingerprint_directory,
    fsync_directory,
    is_link_or_reparse,
    sha256_file,
)
from .schema_validation import validate_schema
from .skill_validation import validate_skill_package

OWNERSHIP_FORMAT = "archmarshal-user-store-ownership-v1"
GENERATION_FORMAT = "archmarshal-user-store-generation-v1"
PACKAGE_COMMIT_FORMAT = "archmarshal-user-skill-package-v1"
PLAN_FORMAT = "archmarshal-user-store-plan-v1"
LOCK_FORMAT = "archmarshal-user-store-lock-v1"
STATUS_API_VERSION = "archmarshal-user-store-status-v2"

OWNERSHIP_NAME = "ownership.json"
STATE_NAME = ".archmarshal"
HEAD_NAME = "HEAD"
LOCK_NAME = "HEAD.lock"
PACKAGE_COMMIT_NAME = "COMMITTED.json"

MAX_OWNERSHIP_BYTES = 64 * 1024
MAX_HEAD_BYTES = 128
MAX_LOCK_BYTES = 16 * 1024
MAX_GENERATION_BYTES = 16 * 1024 * 1024
MAX_HISTORY_GENERATIONS = 10_000
MAX_HISTORY_BYTES = 256 * 1024 * 1024
MAX_COMMON_SKILLS = 500
MAX_CANDIDATE_DECISIONS = 10_000
MAX_SKILL_PACKAGE_FILES = 1_000
MAX_SKILL_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_PREFERENCES = 100
MAX_PREFERENCE_ENTRY_BYTES = 4 * 1024
MAX_PREFERENCES_BYTES = 64 * 1024
MAX_REASON_LENGTH = 1_000
MAX_LABEL_LENGTH = 512

_SKILL_ID = re.compile(r"^skill\.[a-z0-9_.-]+$")
_PREFERENCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|\\\\|/(?!/))")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|token|secret|api[-_]?key|private[-_]?key)\s*(?:=|:)\s*\S+"
)
_SECRET_TOKENS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


class _HeldLock(NamedTuple):
    path: Path
    handle: BinaryIO
    token: str
    identity: tuple[int, int]


def plan_user_store_initialization(
    user_store: Path | str,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    root = _store_root(user_store)
    root_state = _initializable_root_state(root)
    ownership = {
        "format": OWNERSHIP_FORMAT,
        "store_id": _store_id(root),
        "managed_root": ".",
        "created_at": created_at or _now(),
        "source_mutation": False,
    }
    ownership_bytes = _json_bytes(ownership)
    operation = {
        "action": "create",
        "path": OWNERSHIP_NAME,
        "bytes": len(ownership_bytes),
        "sha256": hashlib.sha256(ownership_bytes).hexdigest(),
        "overwrite": False,
    }
    plan = {
        "format": PLAN_FORMAT,
        "kind": "initialize",
        "mode": "propose_only",
        "root": str(root),
        "expected_root_state": root_state,
        "expected_head": None,
        "proposed_head": None,
        "ownership": ownership,
        "operations": [operation],
        "source_mutation": False,
    }
    plan["plan_digest"] = _plan_digest(plan)
    return plan


def apply_user_store_initialization(
    user_store: Path | str,
    plan: dict[str, Any],
    *,
    expected_plan: str,
) -> dict[str, Any]:
    root = _store_root(user_store)
    actual_plan = _validate_initialization_plan(root, plan)
    if expected_plan != actual_plan:
        raise ArchMarshalError(
            "user_store_stale_plan",
            "User store initialization does not match the exact reviewed plan.",
            details={"expected_plan": expected_plan, "actual_plan": actual_plan},
        )
    actual_state = _initializable_root_state(root)
    if actual_state != plan["expected_root_state"]:
        raise ArchMarshalError(
            "user_store_stale_plan",
            "User store root state changed after initialization planning.",
            details={"expected_state": plan["expected_root_state"], "actual_state": actual_state},
        )
    if actual_state == "absent":
        try:
            root.mkdir()
        except FileExistsError as exc:
            raise ArchMarshalError(
                "user_store_stale_plan",
                "User store root appeared concurrently; initialization was refused.",
            ) from exc
    root_metadata = root.stat()
    root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    if is_link_or_reparse(root) or list(root.iterdir()):
        raise ArchMarshalError(
            "user_store_stale_plan",
            "User store root was replaced or populated before ownership publication.",
        )
    ownership_path = root / OWNERSHIP_NAME
    ensure_managed_path(root, ownership_path, purpose="User store ownership marker")
    ownership_bytes = _json_bytes(plan["ownership"])
    try:
        create_bytes_exclusive(ownership_path, ownership_bytes, mode=0o600)
    except FileExistsError as exc:
        raise ArchMarshalError(
            "user_store_stale_plan",
            "User store ownership marker appeared concurrently; initialization was refused.",
        ) from exc
    marker_metadata = ownership_path.lstat()
    root_after = root.stat()
    entries = list(root.iterdir())
    if (
        (root_after.st_dev, root_after.st_ino) != root_identity
        or is_link_or_reparse(root)
        or entries != [ownership_path]
    ):
        try:
            current_marker = ownership_path.lstat()
            marker_unchanged = (
                (current_marker.st_dev, current_marker.st_ino)
                == (marker_metadata.st_dev, marker_metadata.st_ino)
                and ownership_path.read_bytes() == ownership_bytes
            )
        except OSError:
            marker_unchanged = False
        if marker_unchanged:
            ownership_path.unlink()
            fsync_directory(root)
        raise ArchMarshalError(
            "user_store_stale_plan",
            "User store root changed during ownership publication; external content was preserved.",
        )
    ownership = _read_ownership(root)
    return {
        "mode": "initialized",
        "root": str(root),
        "store_id": ownership["store_id"],
        "head": None,
        "plan_digest": actual_plan,
        "source_mutation": False,
    }


def user_store_status(user_store: Path | str) -> dict[str, Any]:
    root = _store_root(user_store)
    if not root.exists():
        return {
            "api_version": STATUS_API_VERSION,
            "mode": "read_only",
            "state": "absent",
            "initialized": False,
            "root": str(root),
            "head": None,
            "source_mutation": False,
        }
    try:
        ownership = _read_ownership(root)
    except ArchMarshalError as exc:
        return {
            "api_version": STATUS_API_VERSION,
            "mode": "read_only",
            "state": "invalid",
            "initialized": False,
            "root": str(root),
            "head": None,
            "error": exc.to_dict(),
            "source_mutation": False,
        }
    lock = _lock_status(root)
    if lock["state"] == "held":
        return {
            "api_version": STATUS_API_VERSION,
            "mode": "read_only",
            "state": "publication_in_progress",
            "initialized": True,
            "root": str(root),
            "store_id": ownership["store_id"],
            "head": None,
            "lock": lock,
            "source_mutation": False,
        }
    loaded = _load_store(root, allow_locked=False)
    generation = loaded.get("generation") or {}
    chain = loaded.get("chain") or []
    history = [
        {
            "digest": item["digest"],
            "parent": item["generation"].get("parent"),
            "created_at": item["generation"].get("created_at"),
            "operation": _plain_json(item["generation"].get("operation") or {}),
            "active": index == 0,
        }
        for index, item in enumerate(chain)
    ]
    current_decisions_by_id: dict[str, dict[str, Any]] = {}
    for decision in generation.get("candidate_decisions") or []:
        candidate_id = decision.get("candidate_id")
        if isinstance(candidate_id, str):
            current_decisions_by_id[candidate_id] = {
                "candidate_id": candidate_id,
                "candidate_digest": decision.get("candidate_digest"),
                "decision": decision.get("decision"),
                "reason": decision.get("reason"),
                "decided_at": decision.get("decided_at"),
                "decision_digest": decision.get("digest"),
            }
    return {
        "api_version": STATUS_API_VERSION,
        "mode": "read_only",
        "state": "healthy" if loaded["head"] else "initialized_empty",
        "initialized": True,
        "root": str(root),
        "store_id": ownership["store_id"],
        "head": loaded["head"],
        "object_path": loaded["object_path"],
        "generation_count": len(chain),
        "history": history,
        "active_common_skills": len(generation.get("common_skills") or []),
        "active_preferences": len(generation.get("preferences") or []),
        "candidate_decisions": len(generation.get("candidate_decisions") or []),
        "current_candidate_decisions": [
            current_decisions_by_id[key] for key in sorted(current_decisions_by_id)
        ],
        "lock": lock,
        "immutable_objects": True,
        "source_mutation": False,
    }


def read_user_store_active(user_store: Path | str) -> dict[str, Any]:
    root = _require_owned_store(user_store)
    loaded = _load_store(root, allow_locked=False)
    generation = loaded.get("generation") or {}
    skills: list[dict[str, Any]] = []
    for record in generation.get("common_skills") or []:
        package_dir = root / PurePosixPath(record["package_path"])
        item = _plain_json(record)
        item["package_dir"] = str(package_dir)
        skills.append(item)
    preferences = [_plain_json(item) for item in generation.get("preferences") or []]
    return {
        "api_version": "archmarshal-user-store-active-v1",
        "mode": "read_only",
        "root": str(root),
        "head": loaded["head"],
        "common_skills": skills,
        "preferences": preferences,
        "preference_values": {item["key"]: item["value"] for item in preferences},
        "source_mutation": False,
    }


def _plan_user_store_decision(
    user_store: Path | str,
    *,
    candidate_id: str,
    candidate_digest: str,
    decision: str,
    provenance: list[dict[str, Any]],
    reason: str = "",
    expected_head: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    root = _require_owned_store(user_store)
    loaded = _load_store(root, allow_locked=False)
    _check_planning_head(loaded["head"], expected_head)
    timestamp = created_at or _now()
    decision_record = _candidate_decision(
        candidate_id,
        candidate_digest,
        decision,
        provenance,
        reason=reason,
        decided_at=timestamp,
    )
    parent = loaded.get("generation") or _empty_snapshot()
    decisions = [_plain_json(item) for item in parent["candidate_decisions"]]
    if any(item["digest"] == decision_record["digest"] for item in decisions):
        raise ArchMarshalError(
            "user_store_decision_duplicate",
            "The exact candidate decision is already present in the active generation.",
        )
    decisions.append(decision_record)
    _validate_decision_budget(decisions)
    generation = _generation(
        created_at=timestamp,
        parent=loaded["head"],
        common_skills=parent["common_skills"],
        preferences=parent["preferences"],
        candidate_decisions=decisions,
        operation={"kind": "decision", "decision_digest": decision_record["digest"]},
    )
    return _make_plan(root, "decision", generation, package=None)


def _apply_user_store_decision(
    user_store: Path | str,
    plan: dict[str, Any],
    *,
    expected_head: str | None,
    expected_plan: str,
) -> dict[str, Any]:
    return _apply_plan(
        user_store,
        plan,
        expected_kind="decision",
        expected_head=expected_head,
        expected_plan=expected_plan,
    )


def _plan_user_store_promotion(
    user_store: Path | str,
    *,
    candidate_id: str,
    candidate_digest: str,
    provenance: list[dict[str, Any]],
    skill_draft: Path | str | None = None,
    preference: dict[str, Any] | None = None,
    reason: str = "",
    allow_skill_replace: bool = False,
    allow_preference_replace: bool = False,
    expected_head: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if (skill_draft is None) == (preference is None):
        raise ArchMarshalError(
            "user_store_promotion_invalid",
            "Promotion requires exactly one Skill draft or preference entry.",
        )
    root = _require_owned_store(user_store)
    loaded = _load_store(root, allow_locked=False)
    _check_planning_head(loaded["head"], expected_head)
    timestamp = created_at or _now()
    normalized_provenance = _normalize_provenance(provenance)
    parent = loaded.get("generation") or _empty_snapshot()
    decisions = [_plain_json(item) for item in parent["candidate_decisions"]]
    package: dict[str, Any] | None = None
    common_skills = [_plain_json(item) for item in parent["common_skills"]]
    preferences = [_plain_json(item) for item in parent["preferences"]]
    if skill_draft is not None:
        package = _plan_package(Path(skill_draft))
        _validate_package_topology(root, package)
        manifest = package["manifest"]
        skill_record = _skill_record(manifest, package, normalized_provenance)
        existing_skill = next(
            (item for item in common_skills if item.get("id") == skill_record["id"]),
            None,
        )
        replacing_skill = bool(
            existing_skill is not None
            and existing_skill.get("digest") != skill_record["digest"]
        )
        if replacing_skill and not allow_skill_replace:
            raise ArchMarshalError(
                "user_store_skill_replace_requires_confirmation",
                "Promotion would replace the active record for this Skill id; explicit replacement confirmation is required.",
                details={
                    "skill_id": skill_record["id"],
                    "active_digest": existing_skill.get("digest"),
                    "proposed_digest": skill_record["digest"],
                },
            )
        common_skills = _replace_by_key(common_skills, skill_record, "id")
        if len(common_skills) > MAX_COMMON_SKILLS:
            raise ArchMarshalError(
                "user_store_skill_budget_exceeded",
                "User store common Skill count exceeds its lightweight budget.",
                details={"limit": MAX_COMMON_SKILLS},
            )
        operation_kind = "promotion_skill"
        record_digest = skill_record["digest"]
    else:
        preference_record = _preference_record(preference or {}, normalized_provenance)
        existing_preference = next(
            (
                item
                for item in preferences
                if str(item.get("key") or "").casefold()
                == preference_record["key"].casefold()
            ),
            None,
        )
        replacing_preference = bool(
            existing_preference is not None
            and existing_preference.get("digest") != preference_record["digest"]
        )
        if replacing_preference and not allow_preference_replace:
            raise ArchMarshalError(
                "user_store_preference_replace_requires_confirmation",
                "Promotion would replace the active value for this preference key; explicit replacement confirmation is required.",
                details={
                    "preference_key": preference_record["key"],
                    "active_digest": existing_preference.get("digest"),
                    "proposed_digest": preference_record["digest"],
                },
            )
        preferences = _replace_by_key(preferences, preference_record, "key")
        _validate_preference_budget(preferences)
        operation_kind = "promotion_preference"
        record_digest = preference_record["digest"]
    matching_decisions = [
        item
        for item in decisions
        if item.get("candidate_id") == candidate_id
        and item.get("candidate_digest") == candidate_digest
        and item.get("provenance") == normalized_provenance
    ]
    approval = matching_decisions[-1] if matching_decisions else None
    if approval is None or approval.get("decision") != "accepted":
        raise ArchMarshalError(
            "user_store_candidate_not_accepted",
            "Promotion requires the latest review decision for this exact candidate and provenance to be accepted.",
            details={
                "candidate_id": candidate_id,
                "candidate_digest": candidate_digest,
                "latest_decision": approval.get("decision") if approval else None,
            },
        )
    operation = {
        "kind": operation_kind,
        "record_digest": record_digest,
        "decision_digest": approval["digest"],
        "reason": _normalize_reason(reason),
    }
    if operation_kind == "promotion_skill":
        operation["replace_existing"] = replacing_skill
    elif operation_kind == "promotion_preference":
        operation["replace_existing"] = replacing_preference
    generation = _generation(
        created_at=timestamp,
        parent=loaded["head"],
        common_skills=common_skills,
        preferences=preferences,
        candidate_decisions=decisions,
        operation=operation,
    )
    return _make_plan(root, "promotion", generation, package=package)


def _apply_user_store_promotion(
    user_store: Path | str,
    plan: dict[str, Any],
    *,
    expected_head: str | None,
    expected_plan: str,
) -> dict[str, Any]:
    return _apply_plan(
        user_store,
        plan,
        expected_kind="promotion",
        expected_head=expected_head,
        expected_plan=expected_plan,
    )


def plan_user_store_forward_rollback(
    user_store: Path | str,
    target: str,
    *,
    reason: str = "",
    expected_head: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    root = _require_owned_store(user_store)
    if not _is_sha256(target):
        raise ArchMarshalError(
            "user_store_rollback_target_invalid",
            "Forward rollback target must be a complete SHA-256 generation digest.",
        )
    loaded = _load_store(root, allow_locked=False)
    current_head = loaded["head"]
    _check_planning_head(current_head, expected_head)
    if current_head is None:
        raise ArchMarshalError(
            "user_store_uninitialized",
            "Forward rollback requires an initialized user store HEAD.",
        )
    if target == current_head:
        raise ArchMarshalError(
            "user_store_rollback_noop",
            "Forward rollback target is already active.",
        )
    by_digest = {item["digest"]: item for item in loaded["chain"]}
    target_item = by_digest.get(target)
    if target_item is None:
        raise ArchMarshalError(
            "user_store_rollback_target_not_ancestor",
            "Forward rollback target is not an ancestor of the active HEAD.",
            details={"target": target, "head": current_head},
        )
    target_generation = target_item["generation"]
    _verify_generation_packages(root, target_generation)
    reason = _normalize_reason(reason)
    generation = _generation(
        created_at=created_at or _now(),
        parent=current_head,
        common_skills=target_generation["common_skills"],
        preferences=target_generation["preferences"],
        candidate_decisions=target_generation["candidate_decisions"],
        operation={"kind": "rollback", "target": target, "reason": reason},
    )
    return _make_plan(root, "rollback", generation, package=None)


def apply_user_store_forward_rollback(
    user_store: Path | str,
    plan: dict[str, Any],
    *,
    expected_head: str | None,
    expected_plan: str,
) -> dict[str, Any]:
    return _apply_plan(
        user_store,
        plan,
        expected_kind="rollback",
        expected_head=expected_head,
        expected_plan=expected_plan,
    )


def _apply_plan(
    user_store: Path | str,
    plan: dict[str, Any],
    *,
    expected_kind: str,
    expected_head: str | None,
    expected_plan: str,
) -> dict[str, Any]:
    root = _require_owned_store(user_store)
    actual_plan = _validate_plan(root, plan, expected_kind=expected_kind)
    if expected_plan != actual_plan:
        raise ArchMarshalError(
            "user_store_stale_plan",
            "User store apply does not match the exact reviewed plan.",
            details={"expected_plan": expected_plan, "actual_plan": actual_plan},
        )
    if expected_head != plan["expected_head"]:
        raise ArchMarshalError(
            "user_store_expected_head_mismatch",
            "Apply expected HEAD does not match the reviewed plan.",
            details={"expected_head": expected_head, "plan_head": plan["expected_head"]},
        )
    package = plan.get("package")
    if package is not None:
        _verify_draft(package)

    state_root = root / STATE_NAME
    ensure_managed_path(root, state_root, purpose="User store state directory")
    state_root.mkdir(parents=True, exist_ok=True)
    proposed_head = str(plan["proposed_head"])
    token = uuid.uuid4().hex
    held = _acquire_lock(root, expected_head, proposed_head, token)
    temporary_head = state_root / f".{HEAD_NAME}.{token}.tmp"
    published = False
    try:
        _verify_held_lock(held)
        actual_head = _read_head(root)
        if actual_head != expected_head:
            raise ArchMarshalError(
                "user_store_stale_head",
                "User store HEAD changed after this plan was reviewed.",
                details={"expected_head": expected_head, "actual_head": actual_head},
            )
        parent_chain = _load_history_chain(root, actual_head) if actual_head else []
        _validate_history_transitions(
            [{"digest": proposed_head, "generation": plan["generation"]}, *parent_chain]
        )
        if package is not None:
            _verify_draft(package)
            _publish_package(root, package)
            _verify_draft(package)
        if plan["generation"]["operation"]["kind"] == "rollback":
            _verify_generation_packages(root, plan["generation"])
        _publish_generation_object(root, proposed_head, plan["generation"])

        head_bytes = f"{proposed_head}\n".encode("ascii")
        create_bytes_exclusive(temporary_head, head_bytes, mode=0o600)
        _verify_held_lock(held)
        actual_head = _read_head(root)
        if actual_head != expected_head:
            raise ArchMarshalError(
                "user_store_stale_head",
                "User store HEAD changed before the atomic commit.",
                details={"expected_head": expected_head, "actual_head": actual_head},
            )
        os.replace(temporary_head, state_root / HEAD_NAME)
        published = True
        _verify_held_lock(held)
        fsync_directory(state_root)
        verified = _load_store(root, allow_locked=True)
        if verified["head"] != proposed_head:
            raise ArchMarshalError(
                "user_store_commit_failed",
                "User store HEAD did not verify after publication.",
            )
        _verify_held_lock(held)
    except BaseException:
        if published:
            try:
                _restore_head(root, expected_head, proposed_head, token)
            except BaseException as rollback_error:
                raise ArchMarshalError(
                    "user_store_head_restore_failed",
                    "User store verification failed and the previous HEAD could not be restored.",
                    details={"expected_head": expected_head, "proposed_head": proposed_head},
                ) from rollback_error
        raise
    finally:
        temporary_head.unlink(missing_ok=True)
        _release_lock(held)
    return {
        "mode": "applied",
        "kind": expected_kind,
        "root": str(root),
        "expected_head": expected_head,
        "head": proposed_head,
        "object_path": _generation_relative(proposed_head).as_posix(),
        "package_path": package.get("package_path") if package else None,
        "plan_digest": actual_plan,
        "source_mutation": False,
    }


def _make_plan(
    root: Path,
    kind: str,
    generation: dict[str, Any],
    *,
    package: dict[str, Any] | None,
) -> dict[str, Any]:
    generation_bytes = _generation_bytes(generation)
    if len(generation_bytes) > MAX_GENERATION_BYTES:
        raise ArchMarshalError(
            "user_store_generation_budget_exceeded",
            "User store generation exceeds its byte budget.",
            details={"limit": MAX_GENERATION_BYTES, "actual": len(generation_bytes)},
        )
    proposed_head = hashlib.sha256(generation_bytes).hexdigest()
    _validate_generation(generation, proposed_head)
    operations = _plan_operations(proposed_head, generation_bytes, package)
    operations[-1]["expected"] = generation["parent"]
    plan = {
        "format": PLAN_FORMAT,
        "kind": kind,
        "mode": "propose_only",
        "root": str(root),
        "expected_head": generation["parent"],
        "proposed_head": proposed_head,
        "generation": generation,
        "generation_object_path": _generation_relative(proposed_head).as_posix(),
        "package": package,
        "operations": operations,
        "source_mutation": False,
    }
    plan["plan_digest"] = _plan_digest(plan)
    return plan


def _plan_operations(
    proposed_head: str,
    generation_bytes: bytes,
    package: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if package is not None:
        package_path = PurePosixPath(package["package_path"])
        for record in package["fingerprint"]["files"]:
            operations.append(
                {
                    "action": "copy_create_only",
                    "source": record["path"],
                    "path": (package_path / record["path"]).as_posix(),
                    "bytes": record["bytes"],
                    "sha256": record["sha256"],
                    "overwrite": False,
                }
            )
        commit_bytes = _json_bytes(package["commit"])
        operations.append(
            {
                "action": "commit_package_last",
                "path": (package_path / PACKAGE_COMMIT_NAME).as_posix(),
                "bytes": len(commit_bytes),
                "sha256": hashlib.sha256(commit_bytes).hexdigest(),
                "overwrite": False,
            }
        )
    operations.extend(
        [
            {
                "action": "create_generation_object",
                "path": _generation_relative(proposed_head).as_posix(),
                "bytes": len(generation_bytes),
                "sha256": proposed_head,
                "overwrite": False,
            },
            {
                "action": "compare_and_swap_head",
                "path": f"{STATE_NAME}/{HEAD_NAME}",
                "expected": None,
                "value": proposed_head,
                "bytes": len(f"{proposed_head}\n".encode("ascii")),
            },
        ]
    )
    return operations


def _validate_plan(root: Path, plan: dict[str, Any], *, expected_kind: str) -> str:
    if (
        not isinstance(plan, dict)
        or plan.get("format") != PLAN_FORMAT
        or plan.get("kind") != expected_kind
        or plan.get("mode") != "propose_only"
        or plan.get("root") != str(root)
        or plan.get("source_mutation") is not False
        or not isinstance(plan.get("generation"), dict)
        or not _is_sha256(str(plan.get("proposed_head") or ""))
    ):
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "User store plan has an invalid structure or belongs to another root.",
        )
    generation = plan["generation"]
    proposed_head = str(plan["proposed_head"])
    generation_bytes = _generation_bytes(generation)
    if hashlib.sha256(generation_bytes).hexdigest() != proposed_head:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Planned generation bytes do not match the proposed HEAD.",
        )
    if generation.get("parent") != plan.get("expected_head"):
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Planned generation parent does not match the expected HEAD.",
        )
    if plan.get("generation_object_path") != _generation_relative(proposed_head).as_posix():
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Planned generation object path is not content-addressed.",
        )
    _validate_generation(generation, proposed_head)
    package = plan.get("package")
    operation_kind = generation["operation"]["kind"]
    allowed_operations = {
        "decision": {"decision"},
        "promotion": {"promotion_skill", "promotion_preference"},
        "rollback": {"rollback"},
    }
    if operation_kind not in allowed_operations[expected_kind]:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "User store plan kind does not match its generation operation.",
        )
    if (operation_kind == "promotion_skill") != (package is not None):
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Skill promotion package presence does not match the generation operation.",
        )
    if operation_kind in {"promotion_skill", "promotion_preference"} and not isinstance(
        generation["operation"].get("replace_existing"), bool
    ):
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "New promotion plans must record whether an active record is being replaced.",
        )
    if package is not None:
        _validate_package_plan(package)
        _validate_package_topology(root, package)
    expected_operations = _plan_operations(proposed_head, generation_bytes, package)
    expected_operations[-1]["expected"] = plan["expected_head"]
    if plan.get("operations") != expected_operations:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "User store operations do not match the exact planned bytes.",
        )
    actual_plan = _plan_digest(plan)
    if plan.get("plan_digest") != actual_plan:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "User store plan digest does not match its canonical content.",
        )
    return actual_plan


def _validate_initialization_plan(root: Path, plan: dict[str, Any]) -> str:
    if (
        not isinstance(plan, dict)
        or plan.get("format") != PLAN_FORMAT
        or plan.get("kind") != "initialize"
        or plan.get("root") != str(root)
        or plan.get("expected_head") is not None
        or plan.get("proposed_head") is not None
        or plan.get("source_mutation") is not False
        or plan.get("expected_root_state") not in {"absent", "empty"}
        or not isinstance(plan.get("ownership"), dict)
    ):
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "User store initialization plan is invalid or belongs to another root.",
        )
    ownership = plan["ownership"]
    _validate_ownership(root, ownership)
    content = _json_bytes(ownership)
    expected_operations = [
        {
            "action": "create",
            "path": OWNERSHIP_NAME,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "overwrite": False,
        }
    ]
    if plan.get("operations") != expected_operations:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "User store ownership operation does not match the exact planned bytes.",
        )
    actual = _plan_digest(plan)
    if plan.get("plan_digest") != actual:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "User store initialization plan digest is invalid.",
        )
    return actual


def _generation(
    *,
    created_at: str,
    parent: str | None,
    common_skills: list[dict[str, Any]],
    preferences: list[dict[str, Any]],
    candidate_decisions: list[dict[str, Any]],
    operation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format": GENERATION_FORMAT,
        "created_at": created_at,
        "parent": parent,
        "common_skills": sorted(
            (_plain_json(item) for item in common_skills), key=lambda item: item["id"].casefold()
        ),
        "preferences": sorted(
            (_plain_json(item) for item in preferences), key=lambda item: item["key"].casefold()
        ),
        "candidate_decisions": [_plain_json(item) for item in candidate_decisions],
        "operation": _plain_json(operation),
    }


def _empty_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {"common_skills": [], "preferences": [], "candidate_decisions": []}


def _candidate_decision(
    candidate_id: str,
    candidate_digest: str,
    decision: str,
    provenance: list[dict[str, Any]],
    *,
    reason: str,
    decided_at: str,
) -> dict[str, Any]:
    candidate_id = _normalize_label(candidate_id, "candidate_id")
    _reject_sensitive_or_absolute(candidate_id, field="candidate_id")
    if not _is_sha256(candidate_digest):
        raise ArchMarshalError(
            "user_store_candidate_invalid",
            "Candidate digest must be a complete SHA-256 digest.",
        )
    if decision not in {"accepted", "rejected", "deferred", "superseded"}:
        raise ArchMarshalError(
            "user_store_decision_invalid",
            "Candidate decision must be accepted, rejected, deferred, or superseded.",
        )
    record = {
        "candidate_id": candidate_id,
        "candidate_digest": candidate_digest,
        "decision": decision,
        "reason": _normalize_reason(reason),
        "decided_at": _normalize_label(decided_at, "decided_at"),
        "provenance": _normalize_provenance(provenance),
    }
    record["digest"] = _record_digest(record)
    return record


def _skill_record(
    manifest: dict[str, Any],
    package: dict[str, Any],
    provenance: list[dict[str, Any]],
) -> dict[str, Any]:
    record = {
        "id": manifest["id"],
        "name": manifest["name"],
        "kind": "common_project_skill",
        "status": "active",
        "package_sha256": package["fingerprint"]["sha256"],
        "package_path": package["package_path"],
        "manifest": _plain_json(manifest),
        "manifest_digest": package["manifest_digest"],
        "provenance": _plain_json(provenance),
    }
    record["digest"] = _record_digest(record)
    return record


def _preference_record(
    preference: dict[str, Any],
    provenance: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(preference, dict) or set(preference) != {"key", "value"}:
        raise ArchMarshalError(
            "user_store_preference_invalid",
            "Preference promotion requires exactly key and value fields.",
        )
    key = preference.get("key")
    if not isinstance(key, str) or not _PREFERENCE_KEY.fullmatch(key):
        raise ArchMarshalError(
            "user_store_preference_invalid",
            "Preference key must be a compact dotted identifier.",
        )
    value = _plain_json(preference.get("value"))
    _reject_sensitive_or_absolute(value, field="preference.value")
    record = {
        "key": key,
        "value": value,
        "status": "active",
        "provenance": _plain_json(provenance),
    }
    record["digest"] = _record_digest(record)
    entry_bytes = len(_canonical_bytes(record))
    if entry_bytes > MAX_PREFERENCE_ENTRY_BYTES:
        raise ArchMarshalError(
            "user_store_preference_budget_exceeded",
            "Preference entry exceeds its byte budget.",
            details={"limit": MAX_PREFERENCE_ENTRY_BYTES, "actual": entry_bytes},
        )
    return record


def _plan_package(draft: Path) -> dict[str, Any]:
    draft_input = draft.expanduser().absolute()
    if is_link_or_reparse(draft_input):
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Skill draft root must not be a symbolic link or junction.",
        )
    draft_root = draft_input.resolve()
    if not draft_root.is_dir():
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Skill draft must be an existing directory.",
            details={"path": str(draft_root)},
        )
    manifest = _load_draft_manifest(draft_root)
    fingerprint = fingerprint_directory(draft_root, purpose="User Skill draft package")
    if not any(item["path"] == "SKILL.md" for item in fingerprint["files"]):
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Skill draft package must contain SKILL.md.",
        )
    if any(item["path"] == PACKAGE_COMMIT_NAME for item in fingerprint["files"]):
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            f"Skill draft reserves the root {PACKAGE_COMMIT_NAME} name.",
        )
    if (
        fingerprint["file_count"] > MAX_SKILL_PACKAGE_FILES
        or fingerprint["content_bytes"] > MAX_SKILL_PACKAGE_BYTES
    ):
        raise ArchMarshalError(
            "user_store_skill_budget_exceeded",
            "Skill draft package exceeds the lightweight file or byte budget.",
            details={
                "max_files": MAX_SKILL_PACKAGE_FILES,
                "max_bytes": MAX_SKILL_PACKAGE_BYTES,
                "actual_files": fingerprint["file_count"],
                "actual_bytes": fingerprint["content_bytes"],
            },
        )
    manifest_digest = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    commit = {
        "format": PACKAGE_COMMIT_FORMAT,
        "package_sha256": fingerprint["sha256"],
        "file_count": fingerprint["file_count"],
        "content_bytes": fingerprint["content_bytes"],
        "files": fingerprint["files"],
        "manifest_digest": manifest_digest,
        "source_mutation": False,
    }
    return {
        "draft_root": str(draft_root),
        "package_path": _package_relative(fingerprint["sha256"]).as_posix(),
        "fingerprint": fingerprint,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "commit": commit,
    }


def _load_draft_manifest(
    draft_root: Path,
    *,
    enforce_folder_name: bool = True,
) -> dict[str, Any]:
    manifest_path = draft_root / "manifest.yaml"
    skill_path = draft_root / "SKILL.md"
    for path in (manifest_path, skill_path):
        if not path.is_file() or is_link_or_reparse(path):
            raise ArchMarshalError(
                "user_store_skill_draft_invalid",
                "Skill draft requires regular manifest.yaml and SKILL.md files.",
                details={"path": str(path)},
            )
    loaded_manifest = load_yaml_safe(manifest_path)
    if loaded_manifest.error:
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Skill draft manifest is not valid UTF-8 YAML.",
            details={"path": str(manifest_path), "error": loaded_manifest.error},
        )
    manifest = loaded_manifest.data
    validation = validate_skill_package(
        draft_root,
        enforce_folder_name=enforce_folder_name,
    )
    if not validation["valid"]:
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Skill draft does not satisfy the Codex Skill package contract.",
            details={"validation": validation},
        )
    required = {
        "id",
        "name",
        "kind",
        "version",
        "status",
        "scope",
        "summary",
        "tags",
        "triggers",
        "negative_triggers",
    }
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Skill draft manifest is incomplete.",
            details={"required": sorted(required)},
        )
    schema_issues = validate_schema(manifest, "skill-manifest")
    if schema_issues:
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Skill draft manifest does not satisfy the Skill manifest schema.",
            details={
                "issues": [
                    {"location": item.location, "message": item.message}
                    for item in schema_issues[:20]
                ]
            },
        )
    if (
        not isinstance(manifest.get("id"), str)
        or not _SKILL_ID.fullmatch(manifest["id"])
        or not isinstance(manifest.get("name"), str)
        or not manifest["name"].strip()
        or manifest["name"] != validation["frontmatter"]["name"]
        or manifest.get("kind") != "common_project_skill"
        or manifest.get("scope") != "common_project"
        or manifest.get("status") != "active"
        or not isinstance(manifest.get("version"), str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["version"])
        or not isinstance(manifest.get("summary"), str)
        or not manifest["summary"].strip()
        or "source" in manifest
    ):
        raise ArchMarshalError(
            "user_store_skill_draft_invalid",
            "Promoted Skill manifest must be a self-contained active common-project Skill.",
        )
    for field in ("tags", "triggers", "negative_triggers"):
        values = manifest.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item.strip() for item in values)
        ):
            raise ArchMarshalError(
                "user_store_skill_draft_invalid",
                f"Skill draft manifest field {field} must be a non-empty string list.",
            )
    normalized = _plain_json(manifest)
    _reject_absolute_paths(normalized, field="skill_manifest")
    return normalized


def _validate_package_plan(package: Any) -> None:
    if not isinstance(package, dict):
        raise ArchMarshalError("user_store_plan_invalid", "Skill package plan is invalid.")
    fingerprint = package.get("fingerprint")
    digest = fingerprint.get("sha256") if isinstance(fingerprint, dict) else None
    files = fingerprint.get("files") if isinstance(fingerprint, dict) else None
    valid_file_sizes = isinstance(files, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("bytes"), int)
        and item["bytes"] >= 0
        for item in files
    )
    summed_bytes = sum(item["bytes"] for item in files) if valid_file_sizes else -1
    if (
        not isinstance(package.get("draft_root"), str)
        or not isinstance(fingerprint, dict)
        or not _is_sha256(str(digest or ""))
        or package.get("package_path") != _package_relative(str(digest)).as_posix()
        or not isinstance(package.get("manifest"), dict)
        or not _is_sha256(str(package.get("manifest_digest") or ""))
        or package.get("commit", {}).get("package_sha256") != digest
        or package.get("commit", {}).get("files") != fingerprint.get("files")
    ):
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Skill package plan is not content-addressed or internally consistent.",
        )
    content_bytes = fingerprint.get("content_bytes")
    if (
        not valid_file_sizes
        or fingerprint.get("file_count") != len(files)
        or not isinstance(content_bytes, int)
        or content_bytes < 0
        or content_bytes != summed_bytes
        or len(files) > MAX_SKILL_PACKAGE_FILES
        or content_bytes > MAX_SKILL_PACKAGE_BYTES
    ):
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Skill package plan has invalid counts or exceeds its lightweight budget.",
        )
    if hashlib.sha256(_canonical_bytes(package["manifest"])).hexdigest() != package["manifest_digest"]:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Skill package manifest digest is inconsistent.",
        )
    if _records_digest(fingerprint.get("files")) != digest:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Skill package file records do not match the package digest.",
        )
    commit = package["commit"]
    expected_commit = {
        "format": PACKAGE_COMMIT_FORMAT,
        "package_sha256": digest,
        "file_count": fingerprint.get("file_count"),
        "content_bytes": fingerprint.get("content_bytes"),
        "files": fingerprint.get("files"),
        "manifest_digest": package["manifest_digest"],
        "source_mutation": False,
    }
    if commit != expected_commit:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Skill package commit marker does not match the reviewed package.",
        )


def _verify_draft(package: dict[str, Any]) -> None:
    _validate_package_plan(package)
    draft_root = Path(package["draft_root"])
    if not draft_root.is_dir() or is_link_or_reparse(draft_root):
        raise ArchMarshalError(
            "user_store_source_changed",
            "Skill draft disappeared or became linked after planning.",
        )
    manifest = _load_draft_manifest(draft_root)
    current = fingerprint_directory(draft_root, purpose="User Skill draft precondition")
    if current != package["fingerprint"] or manifest != package["manifest"]:
        raise ArchMarshalError(
            "user_store_source_changed",
            "Skill draft bytes changed after promotion planning.",
            details={"path": str(draft_root)},
        )


def _validate_package_topology(root: Path, package: dict[str, Any]) -> None:
    draft_root = Path(str(package.get("draft_root") or "")).resolve(strict=False)
    root = root.resolve()
    store_overlap = False
    try:
        draft_root.relative_to(root)
    except ValueError:
        try:
            root.relative_to(draft_root)
        except ValueError:
            pass
        else:
            store_overlap = True
    else:
        store_overlap = True
    if store_overlap:
        raise ArchMarshalError(
            "user_store_source_destination_overlap",
            "Skill draft and the complete user-store ownership domain must be disjoint.",
            details={"draft": str(draft_root), "user_store": str(root)},
        )
    destination = root / PurePosixPath(str(package.get("package_path") or ""))
    destination = ensure_managed_path(
        root,
        destination,
        purpose="User Skill immutable package topology",
    )
    overlaps = draft_root == destination
    if not overlaps:
        try:
            draft_root.relative_to(destination)
        except ValueError:
            try:
                destination.relative_to(draft_root)
            except ValueError:
                pass
            else:
                overlaps = True
        else:
            overlaps = True
    if overlaps:
        raise ArchMarshalError(
            "user_store_source_destination_overlap",
            "Skill draft and immutable package destination must be disjoint.",
            details={"draft": str(draft_root), "destination": str(destination)},
        )


def _publish_package(root: Path, package: dict[str, Any]) -> None:
    _validate_package_topology(root, package)
    _verify_draft(package)
    destination = root / PurePosixPath(package["package_path"])
    ensure_managed_path(root, destination, purpose="User Skill immutable package")
    destination.mkdir(parents=True, exist_ok=True)
    if is_link_or_reparse(destination) or not destination.is_dir():
        raise ArchMarshalError(
            "user_store_package_collision",
            "User Skill package path is not a real directory.",
        )
    commit_path = destination / PACKAGE_COMMIT_NAME
    staging = root / STATE_NAME / "staging"
    ensure_managed_path(root, staging, purpose="User Skill package staging")
    if commit_path.exists():
        _verify_package(root, package["fingerprint"]["sha256"], package["manifest_digest"])
        return

    expected = {item["path"]: item for item in package["fingerprint"]["files"]}
    existing = fingerprint_directory(destination, purpose="Partial User Skill package")
    extras = sorted(item["path"] for item in existing["files"] if item["path"] not in expected)
    if extras:
        raise ArchMarshalError(
            "user_store_package_collision",
            "Partial immutable package contains undeclared files.",
            details={"paths": extras[:100]},
        )
    draft_root = Path(package["draft_root"])
    for relative, record in expected.items():
        source = draft_root.joinpath(*PurePosixPath(relative).parts)
        content = _read_exact_source(source, record)
        target = destination.joinpath(*PurePosixPath(relative).parts)
        ensure_managed_path(root, target, purpose="User Skill package file")
        if target.exists():
            _verify_file(target, record, collision_code="user_store_package_collision")
            continue
        try:
            create_bytes_exclusive(target, content, temporary_directory=staging)
        except FileExistsError:
            _verify_file(target, record, collision_code="user_store_package_collision")
        _verify_file(target, record, collision_code="user_store_package_collision")

    _verify_draft(package)
    commit_bytes = _json_bytes(package["commit"])
    try:
        create_bytes_exclusive(
            commit_path,
            commit_bytes,
            mode=0o600,
            temporary_directory=staging,
        )
    except FileExistsError:
        if commit_path.read_bytes() != commit_bytes:
            raise ArchMarshalError(
                "user_store_package_collision",
                "User Skill package commit marker appeared with different bytes.",
            )
    fsync_directory(destination)
    _verify_package(root, package["fingerprint"]["sha256"], package["manifest_digest"])


def _verify_package(root: Path, package_digest: str, manifest_digest: str) -> dict[str, Any]:
    destination = root / _package_relative(package_digest)
    ensure_managed_path(root, destination, purpose="User Skill package verification")
    commit_path = destination / PACKAGE_COMMIT_NAME
    if (
        not destination.is_dir()
        or is_link_or_reparse(destination)
        or not commit_path.is_file()
        or is_link_or_reparse(commit_path)
    ):
        raise ArchMarshalError(
            "user_store_package_uncommitted",
            "User Skill package is missing its final regular commit marker.",
            details={"package": package_digest},
        )
    try:
        commit = json.loads(commit_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "user_store_package_integrity_failed",
            "User Skill package commit marker is invalid.",
        ) from exc
    records = commit.get("files") if isinstance(commit, dict) else None
    if (
        not isinstance(commit, dict)
        or commit.get("format") != PACKAGE_COMMIT_FORMAT
        or commit.get("package_sha256") != package_digest
        or commit.get("manifest_digest") != manifest_digest
        or commit.get("source_mutation") is not False
        or not isinstance(records, list)
        or commit.get("file_count") != len(records)
        or _records_digest(records) != package_digest
        or len(records) > MAX_SKILL_PACKAGE_FILES
        or not isinstance(commit.get("content_bytes"), int)
        or commit["content_bytes"] > MAX_SKILL_PACKAGE_BYTES
    ):
        raise ArchMarshalError(
            "user_store_package_integrity_failed",
            "User Skill package commit marker does not match its content address.",
        )
    total = 0
    expected: set[str] = set()
    for record in records:
        relative = _safe_relative(record.get("path"))
        if relative in expected:
            raise ArchMarshalError(
                "user_store_package_integrity_failed",
                "User Skill package contains duplicate file records.",
            )
        target = destination.joinpath(*PurePosixPath(relative).parts)
        _verify_file(target, record, collision_code="user_store_package_integrity_failed")
        total += record["bytes"]
        expected.add(relative)
    actual_fingerprint = fingerprint_directory(destination, purpose="Committed User Skill package")
    actual = {
        item["path"]
        for item in actual_fingerprint["files"]
        if item["path"] != PACKAGE_COMMIT_NAME
    }
    if actual != expected or commit.get("content_bytes") != total:
        raise ArchMarshalError(
            "user_store_package_integrity_failed",
            "User Skill package contains undeclared, missing, or miscounted files.",
        )
    manifest = _load_draft_manifest(destination, enforce_folder_name=False)
    if hashlib.sha256(_canonical_bytes(manifest)).hexdigest() != manifest_digest:
        raise ArchMarshalError(
            "user_store_package_integrity_failed",
            "User Skill package manifest no longer matches its generation record.",
        )
    return manifest


def _publish_generation_object(root: Path, digest: str, generation: dict[str, Any]) -> None:
    payload = _generation_bytes(generation)
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ArchMarshalError(
            "user_store_plan_invalid",
            "Generation bytes changed before immutable publication.",
        )
    target = root / _generation_relative(digest)
    ensure_managed_path(root, target, purpose="User store generation object")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if (
            not target.is_file()
            or is_link_or_reparse(target)
            or target.stat().st_size != len(payload)
            or sha256_file(target) != digest
        ):
            raise ArchMarshalError(
                "user_store_object_collision",
                "Existing generation object does not match its content address.",
            )
        return
    try:
        create_bytes_exclusive(target, payload, mode=0o600)
    except FileExistsError:
        if not target.is_file() or sha256_file(target) != digest:
            raise ArchMarshalError(
                "user_store_object_collision",
                "Generation object appeared concurrently with different bytes.",
            )
    fsync_directory(target.parent)


def _load_store(root: Path, *, allow_locked: bool) -> dict[str, Any]:
    _read_ownership(root)
    if not allow_locked:
        lock = _lock_status(root)
        if lock["state"] == "held":
            raise ArchMarshalError(
                "user_store_read_blocked",
                "User store publication is in progress; activation is blocked.",
            )
        if lock["state"] == "invalid":
            raise ArchMarshalError(
                "user_store_lock_invalid",
                "User store lock path is unsafe or unreadable.",
            )
    head = _read_head(root)
    if head is None:
        return {"head": None, "generation": None, "object_path": None, "chain": []}
    chain = _load_history_chain(root, head)
    generation = chain[0]["generation"]
    _verify_generation_packages(root, generation)
    if not allow_locked:
        lock = _lock_status(root)
        current = _read_head(root)
        if lock["state"] == "held" or current != head:
            raise ArchMarshalError(
                "user_store_read_race",
                "User store changed while it was being verified; activation was blocked.",
            )
    return {
        "head": head,
        "generation": generation,
        "object_path": _generation_relative(head).as_posix(),
        "chain": chain,
    }


def _load_history_chain(root: Path, head: str | None) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = head
    total_bytes = 0
    while current is not None:
        if current in seen:
            raise ArchMarshalError(
                "user_store_history_invalid",
                "User store generation history contains a parent cycle.",
            )
        if len(chain) >= MAX_HISTORY_GENERATIONS:
            raise ArchMarshalError(
                "user_store_history_budget_exceeded",
                "User store generation history exceeds its count budget.",
            )
        path = root / _generation_relative(current)
        try:
            total_bytes += path.stat().st_size
        except OSError as exc:
            raise ArchMarshalError(
                "user_store_object_missing",
                "User store history points to a missing generation object.",
                details={"digest": current},
            ) from exc
        if total_bytes > MAX_HISTORY_BYTES:
            raise ArchMarshalError(
                "user_store_history_budget_exceeded",
                "User store history exceeds its cumulative byte budget.",
            )
        generation = _load_generation_object(root, current)
        chain.append({"digest": current, "generation": generation})
        seen.add(current)
        current = generation["parent"]
    _validate_history_transitions(chain)
    return chain


def _load_generation_object(root: Path, digest: str) -> dict[str, Any]:
    if not _is_sha256(digest):
        raise ArchMarshalError(
            "user_store_head_invalid",
            "User store generation digest is invalid.",
        )
    path = root / _generation_relative(digest)
    ensure_managed_path(root, path, purpose="User store generation object")
    if not path.is_file() or is_link_or_reparse(path):
        raise ArchMarshalError(
            "user_store_object_missing",
            "User store generation object is missing or linked.",
            details={"digest": digest},
        )
    if path.stat().st_size > MAX_GENERATION_BYTES or sha256_file(path) != digest:
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation object does not match its content address.",
        )
    try:
        generation = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation object is not valid UTF-8 JSON.",
        ) from exc
    _validate_generation(generation, digest)
    return generation


def _validate_generation(generation: Any, digest: str) -> None:
    if (
        not isinstance(generation, dict)
        or set(generation)
        != {
            "format",
            "created_at",
            "parent",
            "common_skills",
            "preferences",
            "candidate_decisions",
            "operation",
        }
        or generation.get("format") != GENERATION_FORMAT
        or not isinstance(generation.get("created_at"), str)
        or not generation["created_at"]
        or (
            generation.get("parent") is not None
            and not _is_sha256(str(generation.get("parent")))
        )
        or hashlib.sha256(_generation_bytes(generation)).hexdigest() != digest
    ):
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation has an invalid structure or canonical digest.",
        )
    skills = generation.get("common_skills")
    preferences = generation.get("preferences")
    decisions = generation.get("candidate_decisions")
    if (
        not isinstance(skills, list)
        or len(skills) > MAX_COMMON_SKILLS
        or not isinstance(preferences, list)
        or len(preferences) > MAX_PREFERENCES
        or not isinstance(decisions, list)
        or len(decisions) > MAX_CANDIDATE_DECISIONS
    ):
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation exceeds a record budget or contains a non-list snapshot.",
        )
    _validate_record_list(skills, "id", _validate_skill_record)
    _validate_record_list(preferences, "key", _validate_preference_record)
    decision_digests: set[str] = set()
    for record in decisions:
        _validate_decision_record(record)
        if record["digest"] in decision_digests:
            raise ArchMarshalError(
                "user_store_generation_integrity_failed",
                "User store generation contains duplicate candidate decisions.",
            )
        decision_digests.add(record["digest"])
    _validate_preference_budget(preferences)
    _validate_decision_budget(decisions)
    _validate_operation(generation.get("operation"))


def _validate_record_list(records: list[Any], key: str, validator: Any) -> None:
    seen: set[str] = set()
    order: list[str] = []
    for record in records:
        validator(record)
        portable = str(record[key]).casefold()
        if portable in seen:
            raise ArchMarshalError(
                "user_store_generation_integrity_failed",
                f"User store generation contains duplicate {key} records.",
            )
        seen.add(portable)
        order.append(portable)
    if order != sorted(order):
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            f"User store generation {key} records are not canonicalized.",
        )


def _validate_skill_record(record: Any) -> None:
    required = {
        "id",
        "name",
        "kind",
        "status",
        "package_sha256",
        "package_path",
        "manifest",
        "manifest_digest",
        "provenance",
        "digest",
    }
    if (
        not isinstance(record, dict)
        or set(record) != required
        or not isinstance(record.get("id"), str)
        or not _SKILL_ID.fullmatch(record["id"])
        or not isinstance(record.get("name"), str)
        or not record["name"]
        or record.get("kind") != "common_project_skill"
        or record.get("status") != "active"
        or not _is_sha256(str(record.get("package_sha256") or ""))
        or record.get("package_path")
        != _package_relative(str(record.get("package_sha256"))).as_posix()
        or not isinstance(record.get("manifest"), dict)
        or not _is_sha256(str(record.get("manifest_digest") or ""))
        or hashlib.sha256(_canonical_bytes(record["manifest"])).hexdigest()
        != record["manifest_digest"]
        or record["manifest"].get("id") != record["id"]
        or record["manifest"].get("name") != record["name"]
        or record["manifest"].get("kind") != "common_project_skill"
        or _normalize_provenance(record.get("provenance")) != record.get("provenance")
        or not _is_sha256(str(record.get("digest") or ""))
        or _record_digest(record) != record["digest"]
    ):
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation contains an invalid common Skill record.",
        )


def _validate_preference_record(record: Any) -> None:
    if (
        not isinstance(record, dict)
        or set(record) != {"key", "value", "status", "provenance", "digest"}
        or not isinstance(record.get("key"), str)
        or not _PREFERENCE_KEY.fullmatch(record["key"])
        or record.get("status") != "active"
        or _normalize_provenance(record.get("provenance")) != record.get("provenance")
        or not _is_sha256(str(record.get("digest") or ""))
        or _record_digest(record) != record["digest"]
    ):
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation contains an invalid preference record.",
        )
    _plain_json(record["value"])
    _reject_sensitive_or_absolute(record["value"], field="preference.value")
    if len(_canonical_bytes(record)) > MAX_PREFERENCE_ENTRY_BYTES:
        raise ArchMarshalError(
            "user_store_preference_budget_exceeded",
            "Preference entry exceeds its byte budget.",
        )


def _validate_decision_record(record: Any) -> None:
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "candidate_id",
            "candidate_digest",
            "decision",
            "reason",
            "decided_at",
            "provenance",
            "digest",
        }
        or not isinstance(record.get("candidate_id"), str)
        or not record["candidate_id"]
        or not _is_sha256(str(record.get("candidate_digest") or ""))
        or record.get("decision") not in {"accepted", "rejected", "deferred", "superseded"}
        or not isinstance(record.get("reason"), str)
        or len(record["reason"]) > MAX_REASON_LENGTH
        or not isinstance(record.get("decided_at"), str)
        or _normalize_provenance(record.get("provenance")) != record.get("provenance")
        or not _is_sha256(str(record.get("digest") or ""))
        or _record_digest(record) != record["digest"]
    ):
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation contains an invalid candidate decision.",
        )


def _validate_operation(operation: Any) -> None:
    if not isinstance(operation, dict):
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation operation is invalid.",
        )
    kind = operation.get("kind")
    valid = False
    if kind == "decision":
        valid = set(operation) == {"kind", "decision_digest"} and _is_sha256(
            str(operation.get("decision_digest") or "")
        )
    elif kind in {"promotion_skill", "promotion_preference"}:
        base_fields = {"kind", "record_digest", "decision_digest"}
        allowed_fields = {frozenset(base_fields), frozenset({*base_fields, "reason"})}
        allowed_fields.add(frozenset({*base_fields, "reason", "replace_existing"}))
        valid = (
            frozenset(operation) in allowed_fields
            and _is_sha256(str(operation.get("record_digest") or ""))
            and _is_sha256(str(operation.get("decision_digest") or ""))
            and (
                "reason" not in operation
                or (
                    isinstance(operation.get("reason"), str)
                    and len(operation["reason"]) <= MAX_REASON_LENGTH
                )
            )
            and (
                "replace_existing" not in operation
                or isinstance(operation.get("replace_existing"), bool)
            )
        )
    elif kind == "rollback":
        valid = (
            set(operation) == {"kind", "target", "reason"}
            and _is_sha256(str(operation.get("target") or ""))
            and isinstance(operation.get("reason"), str)
            and len(operation["reason"]) <= MAX_REASON_LENGTH
        )
    if not valid:
        raise ArchMarshalError(
            "user_store_generation_integrity_failed",
            "User store generation operation is malformed.",
        )


def _validate_history_transitions(chain: list[dict[str, Any]]) -> None:
    positions = {item["digest"]: index for index, item in enumerate(chain)}
    for index, item in enumerate(chain):
        child = item["generation"]
        parent_item = chain[index + 1] if index + 1 < len(chain) else None
        parent = parent_item["generation"] if parent_item else _empty_snapshot()
        expected_parent = parent_item["digest"] if parent_item else None
        if child["parent"] != expected_parent:
            raise ArchMarshalError(
                "user_store_history_invalid",
                "User store generation parent order is inconsistent.",
            )
        operation = child["operation"]
        kind = operation["kind"]
        if kind == "rollback":
            target_index = positions.get(operation["target"])
            if target_index is None or target_index <= index:
                raise ArchMarshalError(
                    "user_store_history_invalid",
                    "Forward rollback target is not an ancestor of its generation.",
                )
            target = chain[target_index]["generation"]
            if any(
                child[field] != target[field]
                for field in ("common_skills", "preferences", "candidate_decisions")
            ):
                raise ArchMarshalError(
                    "user_store_history_invalid",
                    "Forward rollback snapshot does not match its target generation.",
                )
            continue
        expected_decisions = [*parent["candidate_decisions"]]
        decision = next(
            (
                record
                for record in child["candidate_decisions"]
                if record["digest"] == operation["decision_digest"]
            ),
            None,
        )
        if decision is None:
            raise ArchMarshalError(
                "user_store_history_invalid",
                "Generation operation does not reference an included candidate decision.",
            )
        matching_decisions = [
            record
            for record in child["candidate_decisions"]
            if record.get("candidate_id") == decision.get("candidate_id")
            and record.get("candidate_digest") == decision.get("candidate_digest")
            and record.get("provenance") == decision.get("provenance")
        ]
        latest_decision_valid = bool(
            matching_decisions
            and matching_decisions[-1].get("digest") == decision.get("digest")
            and decision.get("decision") == "accepted"
        )
        parent_matching_decisions = [
            record
            for record in parent["candidate_decisions"]
            if record.get("candidate_id") == decision.get("candidate_id")
            and record.get("candidate_digest") == decision.get("candidate_digest")
            and record.get("provenance") == decision.get("provenance")
        ]
        parent_latest_acceptance = bool(
            parent_matching_decisions
            and parent_matching_decisions[-1].get("digest") == decision.get("digest")
            and decision.get("decision") == "accepted"
        )
        if kind == "decision":
            expected_decisions.append(decision)
            decisions_valid = child["candidate_decisions"] == expected_decisions
            valid = (
                decisions_valid
                and child["common_skills"] == parent["common_skills"]
                and child["preferences"] == parent["preferences"]
            )
        elif kind == "promotion_skill":
            legacy_decisions = [*expected_decisions, decision]
            if "replace_existing" in operation:
                decisions_valid = (
                    parent_latest_acceptance
                    and child["candidate_decisions"] == expected_decisions
                )
            else:
                decisions_valid = (
                    latest_decision_valid
                    and child["candidate_decisions"]
                    in (expected_decisions, legacy_decisions)
                )
            record = next(
                (item for item in child["common_skills"] if item["digest"] == operation["record_digest"]),
                None,
            )
            existing = (
                next(
                    (
                        parent_record
                        for parent_record in parent["common_skills"]
                        if parent_record.get("id") == record.get("id")
                    ),
                    None,
                )
                if record is not None
                else None
            )
            actual_replacement = bool(
                existing is not None and existing.get("digest") != record.get("digest")
            )
            replacement_valid = (
                "replace_existing" not in operation
                or operation.get("replace_existing") is actual_replacement
            )
            valid = (
                decisions_valid
                and record is not None
                and replacement_valid
                and child["common_skills"]
                == _replace_by_key(parent["common_skills"], record, "id")
                and child["preferences"] == parent["preferences"]
            )
        else:
            legacy_decisions = [*expected_decisions, decision]
            if "replace_existing" in operation:
                decisions_valid = (
                    parent_latest_acceptance
                    and child["candidate_decisions"] == expected_decisions
                )
            else:
                decisions_valid = (
                    latest_decision_valid
                    and child["candidate_decisions"]
                    in (expected_decisions, legacy_decisions)
                )
            record = next(
                (item for item in child["preferences"] if item["digest"] == operation["record_digest"]),
                None,
            )
            existing = (
                next(
                    (
                        parent_record
                        for parent_record in parent["preferences"]
                        if str(parent_record.get("key") or "").casefold()
                        == str(record.get("key") or "").casefold()
                    ),
                    None,
                )
                if record is not None
                else None
            )
            actual_replacement = bool(
                existing is not None and existing.get("digest") != record.get("digest")
            )
            replacement_valid = (
                "replace_existing" not in operation
                or operation.get("replace_existing") is actual_replacement
            )
            valid = (
                decisions_valid
                and record is not None
                and replacement_valid
                and child["preferences"]
                == _replace_by_key(parent["preferences"], record, "key")
                and child["common_skills"] == parent["common_skills"]
            )
        if not valid:
            raise ArchMarshalError(
                "user_store_history_invalid",
                "User store generation snapshot does not match its declared operation.",
            )


def _verify_generation_packages(root: Path, generation: dict[str, Any]) -> None:
    for record in generation.get("common_skills") or []:
        manifest = _verify_package(root, record["package_sha256"], record["manifest_digest"])
        if manifest != record["manifest"]:
            raise ArchMarshalError(
                "user_store_package_integrity_failed",
                "User Skill package manifest differs from its active generation.",
            )


def _read_ownership(root: Path) -> dict[str, Any]:
    if not root.is_dir() or is_link_or_reparse(root):
        raise ArchMarshalError(
            "user_store_root_invalid",
            "User store root must be a real directory.",
            details={"root": str(root)},
        )
    path = root / OWNERSHIP_NAME
    if (
        not path.is_file()
        or is_link_or_reparse(path)
        or path.stat().st_size > MAX_OWNERSHIP_BYTES
    ):
        raise ArchMarshalError(
            "user_store_ownership_missing",
            "User store has no valid create-only ownership marker.",
            details={"path": str(path)},
        )
    try:
        ownership = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "user_store_ownership_invalid",
            "User store ownership marker is not valid UTF-8 JSON.",
        ) from exc
    _validate_ownership(root, ownership)
    return ownership


def _validate_ownership(root: Path, ownership: Any) -> None:
    if (
        not isinstance(ownership, dict)
        or set(ownership)
        != {"format", "store_id", "managed_root", "created_at", "source_mutation"}
        or ownership.get("format") != OWNERSHIP_FORMAT
        or ownership.get("store_id") != _store_id(root)
        or ownership.get("managed_root") != "."
        or not isinstance(ownership.get("created_at"), str)
        or not ownership["created_at"]
        or ownership.get("source_mutation") is not False
    ):
        raise ArchMarshalError(
            "user_store_ownership_invalid",
            "User store ownership marker does not match this exact root.",
        )


def _initializable_root_state(root: Path) -> str:
    parent = root.parent
    if not parent.is_dir() or is_link_or_reparse(parent):
        raise ArchMarshalError(
            "user_store_parent_invalid",
            "Explicit user store parent must be an existing real directory.",
            details={"parent": str(parent)},
        )
    if not root.exists():
        return "absent"
    if not root.is_dir() or is_link_or_reparse(root):
        raise ArchMarshalError(
            "user_store_root_invalid",
            "User store root must be absent or an empty real directory.",
        )
    entries = list(root.iterdir())
    if entries:
        ownership = root / OWNERSHIP_NAME
        if ownership.exists():
            try:
                _read_ownership(root)
            except ArchMarshalError:
                pass
            else:
                raise ArchMarshalError(
                    "user_store_already_initialized",
                    "User store is already initialized.",
                )
        raise ArchMarshalError(
            "user_store_unowned",
            "Non-empty directory cannot be claimed as an ArchMarshal user store.",
        )
    return "empty"


def _require_owned_store(user_store: Path | str) -> Path:
    root = _store_root(user_store)
    _read_ownership(root)
    return root


def _store_root(user_store: Path | str) -> Path:
    candidate = Path(user_store).expanduser()
    candidate = candidate if candidate.is_absolute() else candidate.absolute()
    linked_component = next(
        (
            component
            for component in [*reversed(candidate.parents), candidate]
            if is_link_or_reparse(component)
        ),
        None,
    )
    if linked_component is not None:
        raise ArchMarshalError(
            "user_store_root_invalid",
            "Explicit user store path must not cross a symbolic link or junction.",
            details={"path": str(linked_component)},
        )
    return candidate.resolve(strict=False)


def _store_id(root: Path) -> str:
    return hashlib.sha256(f"archmarshal-user-store-v1\x00{root}".encode("utf-8")).hexdigest()[:32]


def _read_head(root: Path) -> str | None:
    path = root / STATE_NAME / HEAD_NAME
    ensure_managed_path(root, path, purpose="User store HEAD")
    if not path.exists():
        return None
    if not path.is_file() or is_link_or_reparse(path) or path.stat().st_size > MAX_HEAD_BYTES:
        raise ArchMarshalError(
            "user_store_head_invalid",
            "User store HEAD must be a compact regular file.",
        )
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ArchMarshalError(
            "user_store_head_invalid",
            "User store HEAD is not readable ASCII.",
        ) from exc
    if not _is_sha256(value):
        raise ArchMarshalError(
            "user_store_head_invalid",
            "User store HEAD is not a SHA-256 digest.",
        )
    return value


def _acquire_lock(root: Path, expected_head: str | None, proposed_head: str, token: str) -> _HeldLock:
    path = root / STATE_NAME / LOCK_NAME
    ensure_managed_path(root, path, purpose="User store OS lock")
    handle = path.open("a+b", buffering=0)
    try:
        if not _try_os_lock(handle):
            raise ArchMarshalError(
                "user_store_locked",
                "Another process holds the user store OS lock.",
            )
        stat = os.fstat(handle.fileno())
        held = _HeldLock(path, handle, token, (stat.st_dev, stat.st_ino))
        # Verify the directory entry before writing metadata. If an attacker
        # swapped in a link between the lexical check and open(), the opened
        # descriptor is never written through.
        _verify_held_lock(held)
        metadata = {
            "format": LOCK_FORMAT,
            "token": token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": _now(),
            "expected_head": expected_head,
            "proposed_head": proposed_head,
        }
        content = _json_bytes(metadata)
        if len(content) > MAX_LOCK_BYTES:
            raise ArchMarshalError("user_store_lock_invalid", "User store lock metadata is too large.")
        handle.seek(0)
        handle.write(content)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
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
    try:
        held.handle.seek(0)
        raw = held.handle.read(MAX_LOCK_BYTES + 1)
        try:
            metadata = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            metadata = {}
        if metadata.get("token") == held.token:
            held.handle.seek(0)
            held.handle.truncate(0)
            held.handle.flush()
            os.fsync(held.handle.fileno())
    finally:
        try:
            _unlock_os_lock(held.handle)
        finally:
            held.handle.close()


def _verify_held_lock(held: _HeldLock) -> None:
    try:
        stat = held.path.lstat()
    except OSError as exc:
        raise ArchMarshalError(
            "user_store_lock_replaced",
            "User store lock path disappeared while held.",
        ) from exc
    if (
        is_link_or_reparse(held.path)
        or stat.st_nlink < 1
        or (stat.st_dev, stat.st_ino) != held.identity
    ):
        raise ArchMarshalError(
            "user_store_lock_replaced",
            "User store lock path was replaced while held.",
        )


def _lock_status(root: Path) -> dict[str, Any]:
    path = root / STATE_NAME / LOCK_NAME
    ensure_managed_path(root, path, purpose="User store lock status")
    if not path.exists():
        return {"state": "absent"}
    if not path.is_file() or is_link_or_reparse(path):
        return {"state": "invalid", "reason": "lock_not_regular"}
    try:
        handle = path.open("r+b", buffering=0)
    except OSError:
        return {"state": "invalid", "reason": "lock_unreadable"}
    acquired = False
    try:
        acquired = _try_os_lock(handle)
        if not acquired:
            return {"state": "held", "path": f"{STATE_NAME}/{LOCK_NAME}"}
        return {"state": "idle", "path": f"{STATE_NAME}/{LOCK_NAME}"}
    finally:
        if acquired:
            _unlock_os_lock(handle)
        handle.close()


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


def _restore_head(
    root: Path,
    expected_head: str | None,
    proposed_head: str,
    token: str,
) -> None:
    state_root = root / STATE_NAME
    head = state_root / HEAD_NAME
    temporary = state_root / f".{HEAD_NAME}.{token}.restore.tmp"
    try:
        if _read_head(root) != proposed_head:
            raise ArchMarshalError(
                "user_store_head_restore_conflict",
                "User store HEAD changed after failed publication; restore was refused.",
            )
        if expected_head is None:
            head.unlink(missing_ok=True)
        else:
            create_bytes_exclusive(temporary, f"{expected_head}\n".encode("ascii"), mode=0o600)
            os.replace(temporary, head)
        fsync_directory(state_root)
        if _read_head(root) != expected_head:
            raise ArchMarshalError(
                "user_store_head_restore_failed",
                "Previous user store HEAD did not verify after restore.",
            )
    finally:
        temporary.unlink(missing_ok=True)


def _check_planning_head(actual: str | None, explicit: str | None) -> None:
    if explicit is not None and explicit != actual:
        raise ArchMarshalError(
            "user_store_stale_head",
            "Explicit expected HEAD does not match the current user store.",
            details={"expected_head": explicit, "actual_head": actual},
        )


def _normalize_provenance(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ArchMarshalError(
            "user_store_provenance_invalid",
            "User store records require at least one provenance entry.",
        )
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "ref", "digest"}:
            raise ArchMarshalError(
                "user_store_provenance_invalid",
                "Provenance entries require exactly kind, ref, and digest.",
            )
        kind = _normalize_label(item.get("kind"), "provenance.kind")
        ref = _normalize_label(item.get("ref"), "provenance.ref")
        digest = item.get("digest")
        if not isinstance(digest, str) or not _is_sha256(digest):
            raise ArchMarshalError(
                "user_store_provenance_invalid",
                "Provenance digest must be a complete SHA-256 digest.",
            )
        _reject_sensitive_or_absolute(ref, field="provenance.ref")
        key = (kind, ref, digest)
        if key not in seen:
            result.append({"kind": kind, "ref": ref, "digest": digest})
            seen.add(key)
    return sorted(result, key=lambda item: (item["kind"], item["ref"], item["digest"]))


def _normalize_label(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ArchMarshalError(
            "user_store_value_invalid",
            f"{field} must be a string.",
        )
    result = value.strip()
    if not result or len(result) > MAX_LABEL_LENGTH or "\x00" in result:
        raise ArchMarshalError(
            "user_store_value_invalid",
            f"{field} is empty or exceeds its safe length.",
        )
    return result


def _normalize_reason(reason: str) -> str:
    if not isinstance(reason, str):
        raise ArchMarshalError("user_store_reason_invalid", "Reason must be a string.")
    result = reason.strip()
    if len(result) > MAX_REASON_LENGTH or "\x00" in result:
        raise ArchMarshalError(
            "user_store_reason_invalid",
            f"Reason must be at most {MAX_REASON_LENGTH} characters.",
        )
    _reject_sensitive_or_absolute(result, field="reason")
    return result


def _validate_preference_budget(preferences: list[dict[str, Any]]) -> None:
    if len(preferences) > MAX_PREFERENCES:
        raise ArchMarshalError(
            "user_store_preference_budget_exceeded",
            "User preference count exceeds its lightweight budget.",
            details={"limit": MAX_PREFERENCES, "actual": len(preferences)},
        )
    total = len(_canonical_bytes(preferences))
    if total > MAX_PREFERENCES_BYTES:
        raise ArchMarshalError(
            "user_store_preference_budget_exceeded",
            "User preferences exceed their aggregate byte budget.",
            details={"limit": MAX_PREFERENCES_BYTES, "actual": total},
        )


def _validate_decision_budget(decisions: list[dict[str, Any]]) -> None:
    if len(decisions) > MAX_CANDIDATE_DECISIONS:
        raise ArchMarshalError(
            "user_store_decision_budget_exceeded",
            "Candidate decision history exceeds its record budget.",
        )


def _reject_sensitive_or_absolute(value: Any, *, field: str) -> None:
    _reject_absolute_paths(value, field=field)
    for text in _strings_below(value):
        if _SECRET_ASSIGNMENT.search(text) or any(pattern.search(text) for pattern in _SECRET_TOKENS):
            raise ArchMarshalError(
                "user_store_secret_rejected",
                f"{field} appears to contain an inline secret.",
            )


def _reject_absolute_paths(value: Any, *, field: str) -> None:
    for text in _strings_below(value):
        if _ABSOLUTE_PATH.match(text.strip()):
            raise ArchMarshalError(
                "user_store_absolute_path_rejected",
                f"{field} must not contain an absolute project path.",
                details={"value": text[:200]},
            )


def _strings_below(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings_below(item)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            for text in [str(key), *_strings_below(item)]
        ]
    return []


def _replace_by_key(
    records: list[dict[str, Any]],
    replacement: dict[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    result = [
        _plain_json(item)
        for item in records
        if str(item[key]).casefold() != str(replacement[key]).casefold()
    ]
    result.append(_plain_json(replacement))
    return sorted(result, key=lambda item: str(item[key]).casefold())


def _read_exact_source(path: Path, record: dict[str, Any]) -> bytes:
    if not path.is_file() or is_link_or_reparse(path):
        raise ArchMarshalError(
            "user_store_source_changed",
            "Skill draft file disappeared or became linked during publication.",
            details={"path": record["path"]},
        )
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        content = handle.read()
        after = os.fstat(handle.fileno())
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != record["bytes"]
        or hashlib.sha256(content).hexdigest() != record["sha256"]
    ):
        raise ArchMarshalError(
            "user_store_source_changed",
            "Skill draft file changed during publication.",
            details={"path": record["path"]},
        )
    return content


def _verify_file(path: Path, record: dict[str, Any], *, collision_code: str) -> None:
    if (
        not path.is_file()
        or is_link_or_reparse(path)
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise ArchMarshalError(
            collision_code,
            "Immutable user store file does not match its reviewed record.",
            details={"path": str(path)},
        )


def _records_digest(records: Any) -> str:
    if not isinstance(records, list):
        return ""
    aggregate = hashlib.sha256()
    seen: set[str] = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "bytes", "sha256"}
            or not isinstance(record.get("bytes"), int)
            or record["bytes"] < 0
            or not _is_sha256(str(record.get("sha256") or ""))
        ):
            return ""
        try:
            relative = _safe_relative(record.get("path"))
        except ArchMarshalError:
            return ""
        if relative in seen:
            return ""
        seen.add(relative)
        aggregate.update(_canonical_bytes(record))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ArchMarshalError(
            "user_store_package_integrity_failed",
            "User Skill package contains an unsafe relative path.",
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchMarshalError(
            "user_store_package_integrity_failed",
            "User Skill package contains an unsafe relative path.",
        )
    return path.as_posix()


def _record_digest(record: dict[str, Any]) -> str:
    content = {key: value for key, value in record.items() if key != "digest"}
    return hashlib.sha256(_canonical_bytes(content)).hexdigest()


def _plan_digest(plan: dict[str, Any]) -> str:
    content = {key: value for key, value in plan.items() if key != "plan_digest"}
    return hashlib.sha256(_canonical_bytes(content)).hexdigest()


def _generation_bytes(generation: dict[str, Any]) -> bytes:
    return _json_bytes(generation)


def _json_bytes(value: Any) -> bytes:
    return _canonical_bytes(value) + b"\n"


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArchMarshalError(
            "user_store_value_invalid",
            "User store values must be finite JSON-compatible data.",
        ) from exc


def _plain_json(value: Any) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _generation_relative(digest: str) -> Path:
    return Path(STATE_NAME) / "objects" / "sha256" / f"{digest}.json"


def _package_relative(digest: str) -> Path:
    return Path(STATE_NAME) / "packages" / "sha256" / digest


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "apply_user_store_forward_rollback",
    "apply_user_store_initialization",
    "plan_user_store_forward_rollback",
    "plan_user_store_initialization",
    "read_user_store_active",
    "user_store_status",
]
