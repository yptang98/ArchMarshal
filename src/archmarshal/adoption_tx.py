from __future__ import annotations

import hashlib
import json
import os
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
    create_bytes_exclusive,
    ensure_managed_path,
    fsync_directory,
    is_link_or_reparse,
    sha256_file,
    verify_backup,
)
from .skill_index import commit_skill_index, load_skill_index

FORMAT = "archmarshal-adoption-transaction-v1"
RECEIPT_FORMAT = "archmarshal-adoption-receipt-v1"
STATE_RELATIVE = Path(".agent/transactions/adoption")
ACTIVE_NAME = "ACTIVE"
LOCK_NAME = "LOCK"
JOURNAL_NAME = "journal.json"
RECEIPT_NAME = "COMMITTED.json"
MAX_TRANSACTION_FILES = 10_000
MAX_TRANSACTION_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_ACTIVE_BYTES = 512
WINDOWS_RESERVED_NAMES = {
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
    identity: tuple[int, int]


def adoption_transaction_status(root: Path | str) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    envelope = {
        "tool": "archmarshal",
        "stage": "adoption_status",
        "mode": "read_only",
        "root": str(root_path),
    }
    state_root = root_path / STATE_RELATIVE
    ensure_managed_path(root_path, state_root, purpose="Adoption transaction state")
    active_path = state_root / ACTIVE_NAME
    if not active_path.exists():
        return {
            **envelope,
            "state": "none",
            "active": False,
            "source_mutation": False,
            "lock": _lock_status(root_path),
        }
    try:
        journal = _load_active_journal(root_path)
        inspection = _inspect_targets(root_path, journal)
    except ArchMarshalError as exc:
        return {
            **envelope,
            "state": "invalid",
            "active": True,
            "source_mutation": False,
            "error": exc.to_dict(),
            "lock": _lock_status(root_path),
        }
    receipt = root_path / STATE_RELATIVE / journal["transaction_id"] / RECEIPT_NAME
    return {
        **envelope,
        "state": "committed_pending_finalize" if receipt.exists() else "recovery_required",
        "active": True,
        "transaction_id": journal["transaction_id"],
        "plan_digest": journal["plan_digest"],
        "created_at": journal["created_at"],
        "backup": journal["backup"],
        "targets": inspection,
        "expected_skill_head": journal["skill_index_plan"].get("expected_head"),
        "proposed_skill_head": journal["skill_index_plan"].get("digest"),
        "source_mutation": False,
        "lock": _lock_status(root_path),
    }


def apply_adoption_transaction(
    root: Path | str,
    *,
    plan_digest: str,
    writes: dict[str, bytes],
    skill_index_plan: dict[str, Any],
    backup: dict[str, Any],
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    state_root = root_path / STATE_RELATIVE
    ensure_managed_path(root_path, state_root, purpose="Adoption transaction state")
    state_root.mkdir(parents=True, exist_ok=True)
    held = _acquire_lock(root_path)
    try:
        _verify_lock_identity(held)
        if (state_root / ACTIVE_NAME).exists():
            raise ArchMarshalError(
                "adoption_recovery_required",
                "An earlier adoption transaction must be reviewed or recovered first.",
                details={"status": adoption_transaction_status(root_path)},
            )
        journal = _prepare_transaction(
            root_path,
            plan_digest=plan_digest,
            writes=writes,
            skill_index_plan=skill_index_plan,
            backup=backup,
        )
        return _complete_transaction(root_path, journal, held)
    finally:
        _release_lock(held)


def recover_adoption_transaction(
    root: Path | str,
    *,
    apply: bool = False,
    expected_transaction: str | None = None,
    expected_plan: str | None = None,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    status = adoption_transaction_status(root_path)
    payload = {
        "tool": "archmarshal",
        "stage": "adoption_recovery",
        "mode": "propose_only",
        "root": str(root_path),
        "transaction": status,
        "expected_transaction": status.get("transaction_id"),
        "expected_plan": status.get("plan_digest"),
        "apply_precondition": (
            "--expect-transaction <transaction_id> --expect-plan <plan_digest>"
        ),
        "source_mutation": False,
        "notes": [
            "Recovery only completes missing create-only control files.",
            "A changed or replaced target blocks recovery and is never overwritten or deleted.",
            "Project and Skill source files remain read-only.",
        ],
    }
    if not apply:
        return payload
    if status.get("state") == "none":
        payload["mode"] = "nothing_to_recover"
        return payload
    if status.get("state") == "invalid":
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "The active adoption transaction is invalid and requires manual review.",
            details={"status": status},
        )
    if expected_transaction is None or expected_plan is None:
        payload["mode"] = "review_required"
        payload["notes"].append(
            "Recovery apply requires the exact transaction id and plan digest from preview."
        )
        return payload
    held = _acquire_lock(root_path)
    try:
        journal = _load_active_journal(root_path)
        if (
            journal["transaction_id"] != expected_transaction
            or journal["plan_digest"] != expected_plan
        ):
            raise ArchMarshalError(
                "adoption_recovery_stale_plan",
                "The active adoption transaction no longer matches the reviewed recovery preview.",
                details={
                    "expected_transaction": expected_transaction,
                    "actual_transaction": journal["transaction_id"],
                    "expected_plan": expected_plan,
                    "actual_plan": journal["plan_digest"],
                },
            )
        result = _complete_transaction(root_path, journal, held)
    finally:
        _release_lock(held)
    payload["mode"] = "recovered"
    payload["result"] = result
    payload["transaction"] = adoption_transaction_status(root_path)
    return payload


def has_active_adoption_transaction(root: Path | str) -> bool:
    root_path = Path(root).resolve()
    active = root_path / STATE_RELATIVE / ACTIVE_NAME
    ensure_managed_path(root_path, active, purpose="Adoption transaction marker")
    if not active.exists():
        return False
    if not active.is_file() or is_link_or_reparse(active):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption ACTIVE marker is not a regular file.",
            details={"path": str(active)},
        )
    return True


def _prepare_transaction(
    root: Path,
    *,
    plan_digest: str,
    writes: dict[str, bytes],
    skill_index_plan: dict[str, Any],
    backup: dict[str, Any],
) -> dict[str, Any]:
    if not _is_sha256(plan_digest):
        raise ArchMarshalError(
            "adoption_plan_invalid",
            "Adoption transaction requires a full plan digest.",
        )
    if len(writes) > MAX_TRANSACTION_FILES:
        raise ArchMarshalError(
            "adoption_transaction_limit_exceeded",
            "Adoption transaction contains too many files.",
            details={"limit": MAX_TRANSACTION_FILES, "actual": len(writes)},
        )
    state_root = root / STATE_RELATIVE
    transaction_id = uuid.uuid4().hex
    transaction_root = state_root / transaction_id
    payload_root = transaction_root / "payloads"
    ensure_managed_path(root, payload_root, purpose="Adoption staged payload directory")
    payload_root.mkdir(parents=True, exist_ok=False)

    total_bytes = 0
    records: list[dict[str, Any]] = []
    for index, (relative, content) in enumerate(sorted(writes.items())):
        safe_relative = _safe_relative(relative)
        if not isinstance(content, bytes):
            raise ArchMarshalError(
                "adoption_plan_invalid",
                "Adoption transaction payload must contain bytes.",
                details={"path": relative},
            )
        total_bytes += len(content)
        if total_bytes > MAX_TRANSACTION_BYTES:
            raise ArchMarshalError(
                "adoption_transaction_limit_exceeded",
                "Adoption transaction payload exceeds the safe byte limit.",
                details={"limit": MAX_TRANSACTION_BYTES, "actual": total_bytes},
            )
        staged = payload_root / f"{index:05d}.bin"
        create_bytes_exclusive(staged, content, mode=0o600)
        records.append(
            {
                "path": safe_relative,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "staged": staged.relative_to(transaction_root).as_posix(),
                "precondition": "absent",
            }
        )

    journal = {
        "format": FORMAT,
        "transaction_id": transaction_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_digest": plan_digest,
        "phase": "prepared",
        "files": records,
        "file_count": len(records),
        "content_bytes": total_bytes,
        "backup": _backup_record(backup),
        "skill_index_plan": _serializable_skill_plan(skill_index_plan),
        "source_mutation": False,
    }
    _validate_journal(root, journal)
    journal_bytes = _json_bytes(journal)
    if len(journal_bytes) > MAX_JOURNAL_BYTES:
        raise ArchMarshalError(
            "adoption_transaction_limit_exceeded",
            "Adoption transaction journal exceeds the safe size limit.",
        )
    journal_path = transaction_root / JOURNAL_NAME
    create_bytes_exclusive(journal_path, journal_bytes, mode=0o600)
    fsync_directory(transaction_root)
    active_bytes = _json_bytes(
        {
            "transaction_id": transaction_id,
            "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
        }
    )
    create_bytes_exclusive(
        state_root / ACTIVE_NAME,
        active_bytes,
        mode=0o600,
    )
    fsync_directory(state_root)
    active_metadata = (state_root / ACTIVE_NAME).lstat()
    journal["_active_identity"] = (active_metadata.st_dev, active_metadata.st_ino)
    journal["_active_bytes"] = active_bytes
    return journal


def _complete_transaction(root: Path, journal: dict[str, Any], held: _HeldLock) -> dict[str, Any]:
    _verify_lock_identity(held)
    _validate_backup(root, journal["backup"])
    inspection = _inspect_targets(root, journal)
    conflicts = [item for item in inspection if item["state"] == "conflict"]
    if conflicts:
        raise ArchMarshalError(
            "adoption_recovery_conflict",
            "An adoption target changed or was replaced; recovery stopped without overwriting it.",
            details={"conflicts": conflicts},
        )

    transaction_root = root / STATE_RELATIVE / journal["transaction_id"]
    created: list[str] = []
    for record in journal["files"]:
        target = root.joinpath(*PurePosixPath(record["path"]).parts)
        if target.exists():
            _verify_target(target, record)
            continue
        staged = transaction_root / record["staged"]
        _verify_staged(staged, record)
        ensure_managed_path(root, target, purpose="Adoption transaction target")
        target.parent.mkdir(parents=True, exist_ok=True)
        ensure_managed_path(root, target, purpose="Adoption transaction target")
        try:
            os.link(staged, target)
        except FileExistsError:
            _verify_target(target, record)
        fsync_directory(target.parent)
        _verify_target(target, record)
        created.append(record["path"])
        _verify_lock_identity(held)

    skill_plan = journal["skill_index_plan"]
    index_commit: dict[str, Any] | None = None
    verified_skill_head: str | None = None
    if not skill_plan.get("disabled"):
        current_head = load_skill_index(root)["head"]
        if skill_plan.get("changed"):
            if current_head == skill_plan.get("digest"):
                index_commit = {
                    "mode": "already_committed",
                    "head": current_head,
                    "changes": skill_plan.get("changes") or [],
                }
            elif current_head == skill_plan.get("expected_head"):
                index_commit = commit_skill_index(root, skill_plan)
            else:
                raise ArchMarshalError(
                    "adoption_skill_head_conflict",
                    "Skill index HEAD no longer matches this adoption transaction.",
                    details={
                        "expected_head": skill_plan.get("expected_head"),
                        "proposed_head": skill_plan.get("digest"),
                        "actual_head": current_head,
                    },
                )
        else:
            if current_head != skill_plan.get("expected_head") or current_head is None:
                raise ArchMarshalError(
                    "adoption_skill_head_conflict",
                    "Unchanged Skill index HEAD no longer matches this adoption transaction.",
                    details={
                        "expected_head": skill_plan.get("expected_head"),
                        "actual_head": current_head,
                    },
                )
            index_commit = {
                "mode": "unchanged_verified",
                "head": current_head,
                "changes": [],
            }
        verified_skill_head = str(index_commit["head"])

    _verify_lock_identity(held)
    receipt = {
        "format": RECEIPT_FORMAT,
        "transaction_id": journal["transaction_id"],
        "plan_digest": journal["plan_digest"],
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "backup": journal["backup"],
        "files": [
            {"path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]}
            for item in journal["files"]
        ],
        "skill_index_head": verified_skill_head,
        "source_mutation": False,
    }
    receipt_path = transaction_root / RECEIPT_NAME
    if receipt_path.exists():
        _validate_existing_receipt(receipt_path, receipt)
    else:
        create_bytes_exclusive(receipt_path, _json_bytes(receipt), mode=0o600)
        fsync_directory(transaction_root)
    _clear_active(
        root,
        journal["transaction_id"],
        journal.get("_active_identity"),
        journal.get("_active_bytes"),
    )
    return {
        "mode": "committed",
        "transaction_id": journal["transaction_id"],
        "plan_digest": journal["plan_digest"],
        "created": created,
        "verified_targets": len(journal["files"]),
        "backup": journal["backup"],
        "skill_index_commit": index_commit,
        "receipt": receipt_path.relative_to(root).as_posix(),
        "source_mutation": False,
    }


def _load_active_journal(root: Path) -> dict[str, Any]:
    active = root / STATE_RELATIVE / ACTIVE_NAME
    ensure_managed_path(root, active, purpose="Adoption transaction marker")
    if not active.is_file() or is_link_or_reparse(active):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption ACTIVE marker is missing, linked, or not a regular file.",
        )
    with active.open("rb") as handle:
        active_metadata = os.fstat(handle.fileno())
        raw = handle.read(MAX_ACTIVE_BYTES + 1)
    if len(raw) > MAX_ACTIVE_BYTES:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption ACTIVE marker exceeds the safe size limit.",
        )
    try:
        marker = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption ACTIVE marker is not valid UTF-8 JSON.",
        ) from exc
    transaction_id = marker.get("transaction_id") if isinstance(marker, dict) else None
    journal_hash = marker.get("journal_sha256") if isinstance(marker, dict) else None
    if not _is_transaction_id(transaction_id):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption ACTIVE marker contains an invalid transaction id.",
        )
    journal_path = root / STATE_RELATIVE / transaction_id / JOURNAL_NAME
    ensure_managed_path(root, journal_path, purpose="Adoption transaction journal")
    if not journal_path.is_file() or is_link_or_reparse(journal_path):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Active adoption journal is missing, linked, or not a regular file.",
            details={"path": str(journal_path)},
        )
    if not _is_sha256(journal_hash) or sha256_file(journal_path) != journal_hash:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Active adoption journal does not match its content hash.",
            details={"path": str(journal_path)},
        )
    if journal_path.stat().st_size > MAX_JOURNAL_BYTES:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Active adoption journal exceeds the safe size limit.",
        )
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Active adoption journal is not valid UTF-8 JSON.",
        ) from exc
    _validate_journal(root, journal)
    if journal["transaction_id"] != transaction_id:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption ACTIVE marker and journal id do not match.",
        )
    journal["_active_identity"] = (active_metadata.st_dev, active_metadata.st_ino)
    journal["_active_bytes"] = raw
    return journal


def _validate_journal(root: Path, journal: object) -> None:
    if (
        not isinstance(journal, dict)
        or journal.get("format") != FORMAT
        or not _is_transaction_id(journal.get("transaction_id"))
        or not _is_sha256(journal.get("plan_digest"))
        or journal.get("phase") != "prepared"
        or not isinstance(journal.get("created_at"), str)
        or journal.get("source_mutation") is not False
        or not isinstance(journal.get("files"), list)
        or not isinstance(journal.get("skill_index_plan"), dict)
        or not isinstance(journal.get("backup"), dict)
    ):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption transaction journal structure is invalid.",
        )
    files = journal["files"]
    if len(files) > MAX_TRANSACTION_FILES or journal.get("file_count") != len(files):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption transaction file count is invalid.",
        )
    seen: set[str] = set()
    total = 0
    transaction_root = root / STATE_RELATIVE / journal["transaction_id"]
    for index, record in enumerate(files):
        expected_staged = f"payloads/{index:05d}.bin"
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("path"), str)
            or _safe_relative(record["path"]) != record["path"]
            or record["path"] in seen
            or record.get("precondition") != "absent"
            or not isinstance(record.get("bytes"), int)
            or record["bytes"] < 0
            or not _is_sha256(record.get("sha256"))
            or record.get("staged") != expected_staged
        ):
            raise ArchMarshalError(
                "adoption_transaction_invalid",
                "Adoption transaction contains an invalid file record.",
                details={"index": index},
            )
        ensure_managed_path(
            root,
            transaction_root / expected_staged,
            purpose="Adoption staged payload",
        )
        seen.add(record["path"])
        total += record["bytes"]
    if total > MAX_TRANSACTION_BYTES or journal.get("content_bytes") != total:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption transaction content byte count is invalid.",
        )
    _validate_skill_plan(journal["skill_index_plan"])
    _validate_backup_record(journal["backup"])


def _inspect_targets(root: Path, journal: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in journal["files"]:
        target = root.joinpath(*PurePosixPath(record["path"]).parts)
        ensure_managed_path(root, target, purpose="Adoption transaction target")
        if not target.exists():
            state = "missing"
        elif _target_matches(target, record):
            state = "verified"
        else:
            state = "conflict"
        result.append({"path": record["path"], "state": state})
    return result


def _target_matches(path: Path, record: dict[str, Any]) -> bool:
    try:
        return (
            path.is_file()
            and not is_link_or_reparse(path)
            and path.stat().st_size == record["bytes"]
            and sha256_file(path) == record["sha256"]
        )
    except (ArchMarshalError, OSError):
        return False


def _verify_target(path: Path, record: dict[str, Any]) -> None:
    if not _target_matches(path, record):
        raise ArchMarshalError(
            "adoption_recovery_conflict",
            "Adoption target does not match the prepared transaction.",
            details={"path": record["path"]},
        )


def _verify_staged(path: Path, record: dict[str, Any]) -> None:
    if not _target_matches(path, record):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption staged payload is missing or corrupt.",
            details={"path": record["staged"]},
        )


def _backup_record(backup: dict[str, Any]) -> dict[str, Any]:
    record = {
        "path": str(backup.get("path") or ""),
        "sha256": str(backup.get("sha256") or ""),
        "bytes": backup.get("bytes"),
        "verified": backup.get("verified"),
    }
    _validate_backup_record(record)
    return record


def _validate_backup_record(record: dict[str, Any]) -> None:
    if (
        _safe_relative(str(record.get("path") or "")) != record.get("path")
        or not _is_sha256(record.get("sha256"))
        or not isinstance(record.get("bytes"), int)
        or record["bytes"] < 0
        or record.get("verified") is not True
    ):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption transaction backup record is invalid.",
        )


def _validate_backup(root: Path, record: dict[str, Any]) -> None:
    archive = root.joinpath(*PurePosixPath(record["path"]).parts)
    ensure_managed_path(root, archive, purpose="Adoption transaction backup")
    if (
        not archive.is_file()
        or is_link_or_reparse(archive)
        or archive.stat().st_size != record["bytes"]
        or sha256_file(archive) != record["sha256"]
    ):
        raise ArchMarshalError(
            "adoption_backup_changed",
            "The verified pre-adoption backup is missing or changed; recovery stopped.",
            details={"path": record["path"]},
        )
    verify_backup(archive)


def _serializable_skill_plan(plan: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "changed",
        "expected_head",
        "digest",
        "generation",
        "changes",
        "source_precondition_policy",
        "source_preconditions",
        "disabled",
    )
    payload = {field: plan.get(field) for field in fields if field in plan}
    return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _validate_skill_plan(plan: dict[str, Any]) -> None:
    changed = plan.get("changed")
    if not isinstance(changed, bool):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption skill-index plan has an invalid changed flag.",
        )
    if changed and (
        not _is_sha256(plan.get("digest"))
        or not isinstance(plan.get("generation"), dict)
        or not isinstance(plan.get("changes"), list)
        or not isinstance(plan.get("source_preconditions"), list)
    ):
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption skill-index plan is incomplete.",
        )


def _validate_existing_receipt(path: Path, expected: dict[str, Any]) -> None:
    if not path.is_file() or is_link_or_reparse(path):
        raise ArchMarshalError(
            "adoption_receipt_invalid",
            "Existing adoption receipt is linked or is not a regular file.",
            details={"path": str(path)},
        )
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "adoption_receipt_invalid",
            "Existing adoption receipt is unreadable.",
            details={"path": str(path)},
        ) from exc
    stable_fields = ("format", "transaction_id", "plan_digest", "backup", "files", "skill_index_head")
    if any(existing.get(field) != expected.get(field) for field in stable_fields):
        raise ArchMarshalError(
            "adoption_receipt_invalid",
            "Existing adoption receipt does not match the active transaction.",
            details={"path": str(path)},
        )


def _clear_active(
    root: Path,
    transaction_id: str,
    identity: object,
    expected_bytes: object,
) -> None:
    active = root / STATE_RELATIVE / ACTIVE_NAME
    if (
        not isinstance(identity, tuple)
        or len(identity) != 2
        or not all(isinstance(value, int) for value in identity)
    ):
        raise ArchMarshalError(
            "adoption_transaction_conflict",
            "Adoption ACTIVE marker identity is unavailable; automatic finalization stopped.",
        )
    metadata = active.lstat()
    if (metadata.st_dev, metadata.st_ino) != identity:
        raise ArchMarshalError(
            "adoption_transaction_conflict",
            "Adoption ACTIVE marker was replaced before finalization.",
        )
    raw = active.read_bytes()
    if not isinstance(expected_bytes, bytes) or raw != expected_bytes:
        raise ArchMarshalError(
            "adoption_transaction_conflict",
            "Adoption ACTIVE marker changed before finalization.",
        )
    active.unlink()
    fsync_directory(active.parent)


def _acquire_lock(root: Path) -> _HeldLock:
    state_root = root / STATE_RELATIVE
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / LOCK_NAME
    ensure_managed_path(root, lock_path, purpose="Adoption transaction lock")
    handle = lock_path.open("a+b", buffering=0)
    try:
        if not _try_os_lock(handle):
            raise ArchMarshalError(
                "adoption_transaction_locked",
                "Another process holds the adoption transaction lock.",
                details={"path": str(lock_path)},
            )
        metadata = os.fstat(handle.fileno())
        held = _HeldLock(lock_path, handle, (metadata.st_dev, metadata.st_ino))
        _verify_lock_identity(held)
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
        _unlock_os_lock(held.handle)
    finally:
        held.handle.close()


def _verify_lock_identity(held: _HeldLock) -> None:
    try:
        metadata = held.path.lstat()
    except OSError as exc:
        raise ArchMarshalError(
            "adoption_transaction_lock_replaced",
            "Adoption lock path disappeared while held; publication stopped.",
        ) from exc
    if (
        is_link_or_reparse(held.path)
        or metadata.st_nlink < 1
        or (metadata.st_dev, metadata.st_ino) != held.identity
    ):
        raise ArchMarshalError(
            "adoption_transaction_lock_replaced",
            "Adoption lock path was replaced while held; publication stopped.",
        )


def _lock_status(root: Path) -> dict[str, Any]:
    lock_path = root / STATE_RELATIVE / LOCK_NAME
    ensure_managed_path(root, lock_path, purpose="Adoption transaction lock status")
    if not lock_path.exists():
        return {"state": "absent"}
    if not lock_path.is_file() or is_link_or_reparse(lock_path):
        return {"state": "invalid"}
    try:
        handle = lock_path.open("r+b", buffering=0)
    except OSError:
        return {"state": "invalid"}
    acquired = False
    try:
        acquired = _try_os_lock(handle)
        return {"state": "idle" if acquired else "held"}
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


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    invalid = any(
        not part
        or part in {".", ".."}
        or part.endswith((" ", "."))
        or any(character in part for character in '<>:"|?*')
        or "\x00" in part
        or any(ord(character) < 32 for character in part)
        or part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        for part in path.parts
    )
    if not value or path.is_absolute() or invalid or "\\" in value:
        raise ArchMarshalError(
            "adoption_transaction_invalid",
            "Adoption transaction contains an unsafe relative path.",
            details={"path": value},
        )
    return path.as_posix()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _is_transaction_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(
        character in "0123456789abcdef" for character in value
    )


__all__ = [
    "adoption_transaction_status",
    "apply_adoption_transaction",
    "has_active_adoption_transaction",
    "recover_adoption_transaction",
]
