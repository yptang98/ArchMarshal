from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ArchMarshalError
from .io import read_bytes_safe
from .ownership import require_owned_workspace
from .safety import create_backup, ensure_managed_path
from .skill_index import (
    HEAD_NAME,
    STATE_RELATIVE,
    _object_bytes,
    _relative_object_path,
    _source_preconditions,
    commit_skill_index,
    load_skill_index,
    skill_review_subject_digest,
)
from .skill_validation import validate_skill_package
from .workspace_lock import workspace_mutation_lock

MAX_REVIEW_TEXT = 500
MAX_REVIEWED_PLAN_BYTES = 72 * 1024 * 1024
REVIEW_PLAN_FORMAT = "archmarshal-skill-review-plan-v2"


def review_workspace_skill(
    root: Path | str,
    source: str,
    *,
    decision: str,
    reviewer: str = "human",
    reason: str = "",
    allow_global_policy: bool = False,
    expected_head: str | None = None,
    expected_plan: str | None = None,
    reviewed_plan: dict[str, Any] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    root_path = require_owned_workspace(root, operation="Skill review")
    exact_apply = bool(
        apply
        and expected_head is not None
        and expected_plan is not None
        and reviewed_plan is not None
    )
    saved_plan = (
        _validate_saved_review_plan(
            root_path,
            reviewed_plan,
            expected_head=expected_head,
            expected_plan=expected_plan,
        )
        if exact_apply
        else None
    )
    plan = _plan_skill_review(
        root_path,
        source,
        decision=decision,
        reviewer=reviewer,
        reason=reason,
        allow_global_policy=allow_global_policy,
        expected_head=expected_head,
        reviewed_at=saved_plan["reviewed_at"] if saved_plan is not None else None,
    )
    review_plan = _make_review_plan(root_path, plan)
    if saved_plan is not None and review_plan != saved_plan:
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Skill package, routing metadata, review inputs, or HEAD changed after preview.",
        )
    plan_digest = review_plan["plan_digest"]
    payload = {
        "tool": "archmarshal",
        "stage": "skill_review",
        "mode": "propose_only",
        "root": str(root_path),
        "source": plan["source"],
        "skill_id": plan["skill_id"],
        "decision": decision,
        "reviewer": reviewer.strip(),
        "reason": reason.strip(),
        "global_policy_review": plan["global_policy_review"],
        "validation": plan["validation"],
        "subject_digest": plan["subject_digest"],
        "expected_head": plan["expected_head"],
        "proposed_head": plan["digest"],
        "reviewed_at": review_plan["reviewed_at"],
        "generation_object": review_plan["generation_object"],
        "review_plan": review_plan,
        "plan_digest": plan_digest,
        "apply_precondition": (
            "--plan-file <saved-preview.json> --expect-head <head> "
            "--expect-plan <plan_digest> --apply"
        ),
        "source_mutation": False,
        "notes": [
            "The decision is bound to the exact package and routing digest.",
            "The saved plan binds reviewed_at and the complete immutable generation object.",
            "A package or routing change invalidates approval and returns the Skill to review.",
            "Review publishes only a new immutable metadata generation; source files remain unchanged.",
        ],
    }
    if not apply:
        return payload
    if not exact_apply:
        payload["mode"] = "review_required"
        payload["notes"].append(
            "Apply requires the complete saved preview, exact HEAD, and exact plan digest."
        )
        return payload

    with workspace_mutation_lock(root_path, operation="skill_review") as held:
        revalidated = _plan_skill_review(
            root_path,
            source,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            allow_global_policy=allow_global_policy,
            expected_head=expected_head,
            reviewed_at=saved_plan["reviewed_at"],
        )
        if _make_review_plan(root_path, revalidated) != saved_plan:
            raise ArchMarshalError(
                "skill_review_stale_plan",
                "Skill package, routing metadata, or HEAD changed before backup.",
            )
        held.verify()
        backup_files = [
            root_path / STATE_RELATIVE / HEAD_NAME,
            root_path / _relative_object_path(str(plan["expected_head"])),
        ]
        backup_dir = root_path / ".agent" / "backups"
        ensure_managed_path(root_path, backup_dir, purpose="Skill review backup directory")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = create_backup(
            root_path,
            backup_files,
            backup_dir / f"{timestamp}-pre-skill-review.zip",
            reason="ArchMarshal Skill review before publishing an immutable decision.",
        )
        held.verify()
        revalidated = _plan_skill_review(
            root_path,
            source,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            allow_global_policy=allow_global_policy,
            expected_head=expected_head,
            reviewed_at=saved_plan["reviewed_at"],
        )
        if _make_review_plan(root_path, revalidated) != saved_plan:
            raise ArchMarshalError(
                "skill_review_stale_plan",
                "Skill package, routing metadata, or HEAD changed after backup.",
            )
        commit = commit_skill_index(root_path, revalidated)
        held.verify()
    payload["mode"] = "review_recorded"
    payload["backup"] = backup
    payload["commit"] = commit
    return payload


def load_reviewed_skill_plan(path: Path | str) -> dict[str, Any]:
    plan_path = Path(path)
    loaded = read_bytes_safe(
        plan_path,
        max_bytes=MAX_REVIEWED_PLAN_BYTES,
        label="Reviewed Skill plan",
    )
    if loaded.error:
        raise ArchMarshalError(
            "reviewed_plan_invalid",
            "Reviewed Skill plan is linked, unreadable, unstable, or exceeds the size limit.",
            details={"path": str(plan_path)},
        )
    try:
        if loaded.data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = loaded.data.decode("utf-16")
        else:
            text = loaded.data.decode("utf-8-sig")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "reviewed_plan_invalid",
            "Reviewed Skill plan is not UTF-8 or BOM-marked UTF-16 JSON.",
            details={"path": str(plan_path)},
        ) from exc
    if isinstance(payload, dict) and isinstance(payload.get("review_plan"), dict):
        payload = payload["review_plan"]
    if not isinstance(payload, dict):
        raise ArchMarshalError("reviewed_plan_invalid", "Reviewed Skill plan is not an object.")
    return payload


def _plan_skill_review(
    root: Path,
    source: str,
    *,
    decision: str,
    reviewer: str,
    reason: str,
    allow_global_policy: bool,
    expected_head: str | None,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    decision = decision.strip().lower()
    reviewer = reviewer.strip()
    reason = reason.strip()
    if decision not in {"approve", "reject"}:
        raise ArchMarshalError("skill_review_decision_invalid", "Decision must be approve or reject.")
    if not reviewer or len(reviewer) > MAX_REVIEW_TEXT or "\x00" in reviewer:
        raise ArchMarshalError("skill_review_reviewer_invalid", "Reviewer is missing or too long.")
    if len(reason) > MAX_REVIEW_TEXT or "\x00" in reason:
        raise ArchMarshalError("skill_review_reason_invalid", "Review reason is too long or invalid.")
    loaded = load_skill_index(root)
    head = loaded.get("head")
    if head is None:
        raise ArchMarshalError("skill_index_uninitialized", "Skill review requires an initialized index.")
    if expected_head is not None and expected_head != head:
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Skill index HEAD differs from the reviewed generation.",
            details={"expected_head": expected_head, "actual_head": head},
        )
    generation = loaded.get("generation") or {}
    selection = generation.get("selection")
    excluded_packages = (
        list(selection.get("excluded_packages") or [])
        if isinstance(selection, dict)
        else []
    )
    if source in excluded_packages:
        raise ArchMarshalError(
            "skill_review_source_excluded",
            "Skill review cannot inspect a package outside ArchMarshal's management boundary.",
            details={"source": source},
        )
    records = json.loads(json.dumps(generation.get("skills") or [], ensure_ascii=False))
    matches = [record for record in records if record.get("source") == source]
    if len(matches) != 1 or matches[0].get("state") != "active":
        raise ArchMarshalError(
            "skill_review_source_missing",
            "Skill review source is not an active indexed package.",
            details={"source": source},
        )
    record = matches[0]
    manifest = record.get("manifest")
    if not isinstance(manifest, dict):
        raise ArchMarshalError("skill_index_integrity_failed", "Indexed Skill manifest is invalid.")
    source_dir = root if source == "." else root.joinpath(*Path(source).parts)
    validation = validate_skill_package(source_dir, enforce_folder_name=source != ".")
    manifest["validation"] = validation
    manifest.pop("review", None)
    manifest["review_state"] = "needs_review"
    subject_digest = skill_review_subject_digest(manifest)
    elevated = (
        manifest.get("kind") == "global_skill"
        or manifest.get("scope") == "global"
        or manifest.get("priority") == "highest"
    )
    if decision == "approve" and not validation["valid"]:
        raise ArchMarshalError(
            "skill_review_validation_failed",
            "Invalid Skill packages cannot be approved; source files were not changed.",
            details={"validation": validation},
        )
    if decision == "approve" and elevated and not allow_global_policy:
        raise ArchMarshalError(
            "skill_review_global_confirmation_required",
            "Global or highest-priority policy requires --allow-global-policy.",
        )
    normalized_decision = "approved" if decision == "approve" else "rejected"
    manifest["review_state"] = normalized_decision
    manifest["review"] = {
        "format": "archmarshal-skill-review-v1",
        "decision": normalized_decision,
        "subject_digest": subject_digest,
        "reviewer": reviewer,
        "reason": reason,
        "reviewed_at": reviewed_at or datetime.now(timezone.utc).isoformat(),
        "allow_global_policy": bool(allow_global_policy and elevated),
        "source_mutation": False,
    }
    new_generation = {
        "format": generation["format"],
        "created_at": manifest["review"]["reviewed_at"],
        "parent": head,
        "skills": records,
        "changes": [{"kind": "modified", "source": source}],
    }
    if isinstance(selection, dict):
        new_generation["selection"] = json.loads(json.dumps(selection, ensure_ascii=False))
    generation_bytes = _object_bytes(new_generation)
    digest = hashlib.sha256(generation_bytes).hexdigest()
    return {
        "changed": True,
        "expected_head": head,
        "digest": digest,
        "generation": new_generation,
        "changes": new_generation["changes"],
        "object_path": _relative_object_path(digest),
        "source_precondition_policy": "active-match",
        "source_preconditions": _source_preconditions(
            records,
            include_removed=False,
            excluded_packages=excluded_packages,
        ),
        "source": source,
        "skill_id": manifest.get("id"),
        "decision": normalized_decision,
        "reviewer": reviewer,
        "reason": reason,
        "allow_global_policy": bool(allow_global_policy),
        "global_policy_review": elevated,
        "validation": validation,
        "subject_digest": subject_digest,
    }


def _make_review_plan(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    generation = plan["generation"]
    generation_bytes = _object_bytes(generation)
    review_plan = {
        "format": REVIEW_PLAN_FORMAT,
        "mode": "propose_only",
        "root": str(root.resolve()),
        "expected_head": plan["expected_head"],
        "proposed_head": plan["digest"],
        "reviewed_at": generation["created_at"],
        "source": plan["source"],
        "skill_id": plan["skill_id"],
        "decision": plan["decision"],
        "reviewer": plan["reviewer"],
        "reason": plan["reason"],
        "allow_global_policy": plan["allow_global_policy"],
        "global_policy_review": plan["global_policy_review"],
        "subject_digest": plan["subject_digest"],
        "validation": plan["validation"],
        "generation": generation,
        "generation_object": {
            "path": plan["object_path"].as_posix(),
            "bytes": len(generation_bytes),
            "sha256": plan["digest"],
        },
        "source_precondition_policy": plan["source_precondition_policy"],
        "source_preconditions": plan["source_preconditions"],
        "source_mutation": False,
    }
    review_plan["plan_digest"] = _review_plan_digest(review_plan)
    return review_plan


def _review_plan_digest(review_plan: dict[str, Any]) -> str:
    intent = {key: value for key, value in review_plan.items() if key != "plan_digest"}
    return hashlib.sha256(
        json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_saved_review_plan(
    root: Path,
    reviewed_plan: dict[str, Any] | None,
    *,
    expected_head: str | None,
    expected_plan: str | None,
) -> dict[str, Any]:
    if isinstance(reviewed_plan, dict) and isinstance(reviewed_plan.get("review_plan"), dict):
        reviewed_plan = reviewed_plan["review_plan"]
    required = {
        "format",
        "mode",
        "root",
        "expected_head",
        "proposed_head",
        "reviewed_at",
        "source",
        "skill_id",
        "decision",
        "reviewer",
        "reason",
        "allow_global_policy",
        "global_policy_review",
        "subject_digest",
        "validation",
        "generation",
        "generation_object",
        "source_precondition_policy",
        "source_preconditions",
        "source_mutation",
        "plan_digest",
    }
    if (
        not isinstance(reviewed_plan, dict)
        or set(reviewed_plan) != required
        or reviewed_plan.get("format") != REVIEW_PLAN_FORMAT
        or reviewed_plan.get("mode") != "propose_only"
        or reviewed_plan.get("root") != str(root.resolve())
        or reviewed_plan.get("source_mutation") is not False
        or reviewed_plan.get("expected_head") != expected_head
    ):
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Saved Skill review plan is malformed, belongs to another root, or has another HEAD.",
        )
    actual_plan = _review_plan_digest(reviewed_plan)
    if reviewed_plan.get("plan_digest") != actual_plan or expected_plan != actual_plan:
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Saved Skill review plan does not match the exact digest supplied for apply.",
            details={"expected_plan": expected_plan, "actual_plan": actual_plan},
        )
    reviewed_at = reviewed_plan.get("reviewed_at")
    try:
        parsed_reviewed_at = datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchMarshalError(
            "skill_review_stale_plan", "Saved Skill review time is invalid."
        ) from exc
    generation = reviewed_plan.get("generation")
    generation_object = reviewed_plan.get("generation_object")
    if (
        not isinstance(reviewed_at, str)
        or parsed_reviewed_at.tzinfo is None
        or not isinstance(generation, dict)
        or generation.get("created_at") != reviewed_at
        or not isinstance(generation_object, dict)
        or set(generation_object) != {"path", "bytes", "sha256"}
    ):
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Saved Skill review plan has inconsistent generation metadata.",
        )
    generation_bytes = _object_bytes(generation)
    proposed_head = hashlib.sha256(generation_bytes).hexdigest()
    expected_object_path = _relative_object_path(proposed_head).as_posix()
    if (
        reviewed_plan.get("proposed_head") != proposed_head
        or generation_object.get("sha256") != proposed_head
        or generation_object.get("bytes") != len(generation_bytes)
        or generation_object.get("path") != expected_object_path
    ):
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Saved Skill review generation bytes, digest, or object path do not agree.",
        )
    review_matches = []
    for record in generation.get("skills") or []:
        if not isinstance(record, dict) or record.get("source") != reviewed_plan.get("source"):
            continue
        manifest = record.get("manifest")
        review_matches.append(manifest.get("review") if isinstance(manifest, dict) else None)
    if len(review_matches) != 1 or not isinstance(review_matches[0], dict) or (
        review_matches[0].get("reviewed_at") != reviewed_at
    ):
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Saved Skill review time does not match the exact reviewed generation.",
        )
    return json.loads(json.dumps(reviewed_plan, ensure_ascii=False))


__all__ = ["load_reviewed_skill_plan", "review_workspace_skill"]
