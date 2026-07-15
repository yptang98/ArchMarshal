from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ArchMarshalError
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
    apply: bool = False,
) -> dict[str, Any]:
    root_path = require_owned_workspace(root, operation="Skill review")
    plan = _plan_skill_review(
        root_path,
        source,
        decision=decision,
        reviewer=reviewer,
        reason=reason,
        allow_global_policy=allow_global_policy,
        expected_head=expected_head,
    )
    plan_digest = _review_plan_digest(root_path, plan)
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
        "plan_digest": plan_digest,
        "apply_precondition": "--expect-head <head> --expect-plan <plan_digest>",
        "source_mutation": False,
        "notes": [
            "The decision is bound to the exact package and routing digest.",
            "A package or routing change invalidates approval and returns the Skill to review.",
            "Review publishes only a new immutable metadata generation; source files remain unchanged.",
        ],
    }
    if not apply:
        return payload
    if expected_head is None or expected_plan is None:
        payload["mode"] = "review_required"
        payload["notes"].append("Apply requires exact HEAD and plan digest from preview.")
        return payload
    if expected_plan != plan_digest:
        raise ArchMarshalError(
            "skill_review_stale_plan",
            "Skill review inputs no longer match the reviewed plan.",
            details={"expected_plan": expected_plan, "actual_plan": plan_digest},
        )

    with workspace_mutation_lock(root_path, operation="skill_review") as held:
        revalidated = _plan_skill_review(
            root_path,
            source,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            allow_global_policy=allow_global_policy,
            expected_head=expected_head,
            reviewed_at=plan["generation"]["created_at"],
        )
        if _review_plan_digest(root_path, revalidated) != plan_digest:
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
            reviewed_at=plan["generation"]["created_at"],
        )
        if _review_plan_digest(root_path, revalidated) != plan_digest:
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
        "source_preconditions": _source_preconditions(records, include_removed=False),
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


def _review_plan_digest(root: Path, plan: dict[str, Any]) -> str:
    intent = {
        "format": "archmarshal-skill-review-plan-v1",
        "root": str(root.resolve()),
        "expected_head": plan["expected_head"],
        "source": plan["source"],
        "skill_id": plan["skill_id"],
        "decision": plan["decision"],
        "reviewer": plan["reviewer"],
        "reason": plan["reason"],
        "allow_global_policy": plan["allow_global_policy"],
        "subject_digest": plan["subject_digest"],
        "source_preconditions": plan["source_preconditions"],
    }
    return hashlib.sha256(
        json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


__all__ = ["review_workspace_skill"]
