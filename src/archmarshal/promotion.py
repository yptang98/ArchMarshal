from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import ArchMarshalError
from .io import read_bytes_safe
from .learning import verify_learning_pack
from .ownership import require_owned_workspace
from .safety import ensure_managed_path, is_link_or_reparse
from .user_store import (
    _apply_user_store_decision,
    _apply_user_store_promotion,
    _plan_user_store_decision,
    _plan_user_store_promotion,
)

MAX_REVIEWED_PLAN_BYTES = 32 * 1024 * 1024


def review_learning_candidate(
    root: Path | str,
    pack: Path | str,
    candidate_id: str,
    user_store: Path | str,
    *,
    decision: str,
    reason: str = "",
    expected_head_token: str | None = None,
    expected_plan: str | None = None,
    reviewed_plan: dict[str, Any] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    candidate, candidate_digest, provenance, pack_info = _candidate_context(
        root, pack, candidate_id
    )
    normalized_decision = {
        "accept": "accepted",
        "reject": "rejected",
        "defer": "deferred",
    }.get(decision)
    if normalized_decision is None:
        raise ArchMarshalError(
            "learning_decision_invalid",
            "Candidate decision must be accept, reject, or defer.",
        )
    if not apply:
        plan = _plan_user_store_decision(
            user_store,
            candidate_id=candidate_id,
            candidate_digest=candidate_digest,
            decision=normalized_decision,
            provenance=provenance,
            reason=reason,
        )
        return _candidate_plan_envelope(candidate, candidate_digest, pack_info, plan, "decision")
    plan, expected_head = _reviewed_plan(
        reviewed_plan,
        expected_plan=expected_plan,
        expected_head_token=expected_head_token,
        kind="decision",
    )
    _verify_plan_candidate(
        plan,
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
        provenance=provenance,
        decision=normalized_decision,
        reason=reason,
    )
    result = _apply_user_store_decision(
        user_store,
        plan,
        expected_head=expected_head,
        expected_plan=str(expected_plan),
    )
    return {
        "tool": "archmarshal",
        "stage": "candidate_decision",
        "mode": "decision_recorded",
        "candidate_id": candidate_id,
        "candidate_digest": candidate_digest,
        "decision": normalized_decision,
        "user_store": result,
        "source_mutation": False,
    }


def promote_learning_candidate(
    root: Path | str,
    pack: Path | str,
    candidate_id: str,
    user_store: Path | str,
    *,
    draft: Path | str | None = None,
    reason: str = "",
    expected_head_token: str | None = None,
    expected_plan: str | None = None,
    reviewed_plan: dict[str, Any] | None = None,
    replace_existing_skill: bool = False,
    replace_existing_preference: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    candidate, candidate_digest, provenance, pack_info = _candidate_context(
        root, pack, candidate_id
    )
    candidate_type = candidate.get("candidate_type")
    if candidate_type == "common_skill":
        if draft is None:
            raise ArchMarshalError(
                "learning_promotion_draft_required",
                "Common Skill promotion requires an explicitly reviewed draft package.",
            )
        skill_draft: Path | str | None = draft
        preference = None
        if replace_existing_preference:
            raise ArchMarshalError(
                "learning_promotion_replace_invalid",
                "Common Skill promotion does not accept preference replacement confirmation.",
            )
    elif candidate_type == "preference":
        if draft is not None:
            raise ArchMarshalError(
                "learning_promotion_draft_invalid",
                "Preference promotion does not accept a Skill draft.",
            )
        skill_draft = None
        preference = {"key": candidate.get("key"), "value": candidate.get("value")}
        if replace_existing_skill:
            raise ArchMarshalError(
                "learning_promotion_replace_invalid",
                "Preference promotion does not accept Skill replacement confirmation.",
            )
    else:
        raise ArchMarshalError(
            "learning_candidate_type_invalid",
            "Only common Skill and preference candidates can be promoted.",
        )
    if not apply:
        plan = _plan_user_store_promotion(
            user_store,
            candidate_id=candidate_id,
            candidate_digest=candidate_digest,
            provenance=provenance,
            skill_draft=skill_draft,
            preference=preference,
            reason=reason,
            allow_skill_replace=replace_existing_skill,
            allow_preference_replace=replace_existing_preference,
        )
        _verify_promotion_payload(
            plan,
            candidate=candidate,
            candidate_digest=candidate_digest,
            draft=draft,
            replace_existing_skill=replace_existing_skill,
            replace_existing_preference=replace_existing_preference,
        )
        return _candidate_plan_envelope(candidate, candidate_digest, pack_info, plan, "promotion")
    plan, expected_head = _reviewed_plan(
        reviewed_plan,
        expected_plan=expected_plan,
        expected_head_token=expected_head_token,
        kind="promotion",
    )
    _verify_plan_candidate(
        plan,
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
        provenance=provenance,
        decision="accepted",
        reason=reason,
        prior_acceptance=True,
    )
    _verify_promotion_payload(
        plan,
        candidate=candidate,
        candidate_digest=candidate_digest,
        draft=draft,
        replace_existing_skill=replace_existing_skill,
        replace_existing_preference=replace_existing_preference,
    )
    result = _apply_user_store_promotion(
        user_store,
        plan,
        expected_head=expected_head,
        expected_plan=str(expected_plan),
    )
    return {
        "tool": "archmarshal",
        "stage": "candidate_promotion",
        "mode": "promoted",
        "candidate_id": candidate_id,
        "candidate_digest": candidate_digest,
        "candidate_type": candidate_type,
        "user_store": result,
        "source_mutation": False,
    }


def load_reviewed_plan(path: Path | str) -> dict[str, Any]:
    plan_path = Path(path)
    try:
        loaded = read_bytes_safe(
            plan_path,
            max_bytes=MAX_REVIEWED_PLAN_BYTES,
            label="Reviewed plan",
        )
        if loaded.error:
            raise ValueError(loaded.error)
        raw = loaded.data
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "reviewed_plan_invalid",
            "Reviewed plan file is not readable UTF-8 or BOM-marked UTF-16 JSON.",
            details={"path": str(plan_path)},
        ) from exc
    except ValueError as exc:
        raise ArchMarshalError(
            "reviewed_plan_invalid",
            "Reviewed plan file is linked or exceeds the safe size limit.",
            details={"path": str(plan_path)},
        ) from exc
    if isinstance(payload, dict) and isinstance(payload.get("user_store_plan"), dict):
        payload = payload["user_store_plan"]
    if not isinstance(payload, dict):
        raise ArchMarshalError("reviewed_plan_invalid", "Reviewed plan file is not an object.")
    return payload


def _candidate_context(
    root: Path | str,
    pack: Path | str,
    candidate_id: str,
) -> tuple[dict[str, Any], str, list[dict[str, str]], dict[str, Any]]:
    root_path = require_owned_workspace(root, operation="Learning candidate review")
    pack_path = Path(pack)
    pack_path = pack_path if pack_path.is_absolute() else root_path / pack_path
    pack_path = ensure_managed_path(root_path, pack_path, purpose="Learning candidate pack")
    try:
        relative = pack_path.relative_to(root_path).as_posix()
    except ValueError as exc:
        raise ArchMarshalError(
            "learning_pack_outside_workspace",
            "Learning candidate pack must stay inside its owned workspace.",
        ) from exc
    if not relative.startswith(".agent/inbox/learning/"):
        raise ArchMarshalError(
            "learning_pack_location_invalid",
            "Candidate review accepts only committed packs under .agent/inbox/learning/.",
        )
    verified = verify_learning_pack(pack_path)
    profile = verified["profile"]
    candidates = (profile.get("common_skill_candidates") or []) + (
        profile.get("preference_candidates") or []
    )
    matches = [
        item
        for item in candidates
        if isinstance(item, dict) and item.get("candidate_id") == candidate_id
    ]
    if len(matches) != 1:
        raise ArchMarshalError(
            "learning_candidate_missing",
            "Candidate id is missing or duplicated in the committed pack.",
            details={"candidate_id": candidate_id},
        )
    candidate = matches[0]
    candidate_digest = _canonical_digest(candidate)
    provenance = [
        {
            "kind": "learning_pack",
            "ref": f"learning-pack/{pack_path.name}",
            "digest": str(verified["sha256"]),
        }
    ]
    for evidence in candidate.get("evidence_refs") or []:
        if not isinstance(evidence, dict):
            continue
        workspace = evidence.get("workspace_id")
        session = evidence.get("session")
        digest = evidence.get("commit_sha256")
        if (
            isinstance(workspace, str)
            and isinstance(session, str)
            and isinstance(digest, str)
            and len(digest) == 64
        ):
            provenance.append(
                {
                    "kind": "committed_session",
                    "ref": f"{workspace}:{session}",
                    "digest": digest,
                }
            )
    normalized = [
        {"kind": kind, "ref": ref, "digest": digest}
        for kind, ref, digest in sorted(
            {(item["kind"], item["ref"], item["digest"]) for item in provenance}
        )
    ]
    return candidate, candidate_digest, normalized, verified


def _candidate_plan_envelope(
    candidate: dict[str, Any],
    candidate_digest: str,
    pack: dict[str, Any],
    plan: dict[str, Any],
    stage: str,
) -> dict[str, Any]:
    return {
        "tool": "archmarshal",
        "stage": f"candidate_{stage}",
        "mode": "propose_only",
        "candidate_id": candidate.get("candidate_id"),
        "candidate_type": candidate.get("candidate_type"),
        "candidate_digest": candidate_digest,
        "candidate_pack_sha256": pack["sha256"],
        "expected_head": plan.get("expected_head"),
        "expected_head_token": plan.get("expected_head") or "none",
        "plan_digest": plan["plan_digest"],
        "apply_precondition": (
            "--plan-file <saved-preview.json> --expect-head <head|none> "
            "--expect-plan <plan_digest> --apply"
        ),
        "user_store_plan": plan,
        "source_mutation": False,
        "notes": [
            "Save this complete JSON preview; apply re-verifies the committed candidate pack and draft.",
            "The project, candidate pack, and draft are never modified.",
            "Only the user store's immutable package/index paths and internal HEAD can change.",
        ],
    }


def _reviewed_plan(
    plan: dict[str, Any] | None,
    *,
    expected_plan: str | None,
    expected_head_token: str | None,
    kind: str,
) -> tuple[dict[str, Any], str | None]:
    if plan is None or expected_plan is None or expected_head_token is None:
        raise ArchMarshalError(
            "reviewed_plan_required",
            "Apply requires the saved complete preview, exact HEAD token, and exact plan digest.",
        )
    if plan.get("kind") != kind or plan.get("plan_digest") != expected_plan:
        raise ArchMarshalError(
            "reviewed_plan_invalid",
            "Saved plan kind or digest does not match the apply request.",
        )
    expected_head = None if expected_head_token == "none" else expected_head_token
    if expected_head != plan.get("expected_head"):
        raise ArchMarshalError(
            "reviewed_plan_invalid",
            "Expected HEAD token does not match the saved reviewed plan.",
        )
    return plan, expected_head


def _verify_plan_candidate(
    plan: dict[str, Any],
    *,
    candidate_id: str,
    candidate_digest: str,
    provenance: list[dict[str, str]],
    decision: str,
    reason: str,
    prior_acceptance: bool = False,
) -> None:
    generation = plan.get("generation")
    decisions = generation.get("candidate_decisions") if isinstance(generation, dict) else None
    matches = [
        item
        for item in decisions or []
        if isinstance(item, dict)
        and item.get("candidate_id") == candidate_id
        and item.get("candidate_digest") == candidate_digest
        and item.get("decision") == decision
        and item.get("provenance") == provenance
        and (prior_acceptance or item.get("reason") == reason.strip())
    ]
    operation = generation.get("operation") if isinstance(generation, dict) else None
    if not matches or not isinstance(operation, dict):
        raise ArchMarshalError(
            "reviewed_plan_candidate_mismatch",
            "Saved plan does not bind this exact candidate, decision, and provenance.",
        )
    referenced = [
        item for item in matches if operation.get("decision_digest") == item.get("digest")
    ]
    if len(referenced) != 1 or (
        prior_acceptance and operation.get("reason") != reason.strip()
    ):
        raise ArchMarshalError(
            "reviewed_plan_candidate_mismatch",
            "Saved plan operation does not reference the exact candidate decision.",
        )


def _verify_promotion_payload(
    plan: dict[str, Any],
    *,
    candidate: dict[str, Any],
    candidate_digest: str,
    draft: Path | str | None,
    replace_existing_skill: bool = False,
    replace_existing_preference: bool = False,
) -> None:
    generation = plan.get("generation")
    operation = generation.get("operation") if isinstance(generation, dict) else None
    if not isinstance(operation, dict):
        raise ArchMarshalError(
            "reviewed_plan_candidate_mismatch",
            "Saved promotion plan has no valid operation.",
        )
    candidate_type = candidate.get("candidate_type")
    if candidate_type == "common_skill":
        package = plan.get("package")
        draft_root = package.get("draft_root") if isinstance(package, dict) else None
        if (
            operation.get("kind") != "promotion_skill"
            or not isinstance(draft_root, str)
            or draft is None
        ):
            raise ArchMarshalError(
                "reviewed_plan_candidate_mismatch",
                "Saved plan is not a common-Skill promotion for this candidate.",
            )
        manifest = package.get("manifest") if isinstance(package, dict) else None
        lineage = manifest.get("promotion") if isinstance(manifest, dict) else None
        expected_lineage = {
            "candidate_id": candidate.get("candidate_id"),
            "candidate_digest": candidate_digest,
            "source_skill_id": candidate.get("skill_id"),
            "source_implementation_sha256": candidate.get("implementation_sha256"),
        }
        if lineage != expected_lineage:
            raise ArchMarshalError(
                "learning_promotion_lineage_mismatch",
                "Reviewed Skill draft must declare exact promotion lineage for this candidate.",
                details={"expected_promotion": expected_lineage},
            )
        if bool(operation.get("replace_existing")) is not replace_existing_skill:
            raise ArchMarshalError(
                "reviewed_plan_candidate_mismatch",
                "Saved plan does not match the explicit Skill replacement confirmation.",
            )
        supplied = Path(draft).expanduser().absolute()
        if is_link_or_reparse(supplied) or str(supplied.resolve(strict=False)) != draft_root:
            raise ArchMarshalError(
                "reviewed_plan_draft_mismatch",
                "Apply --draft must be the exact real directory recorded in the reviewed plan.",
            )
        return
    if candidate_type != "preference" or operation.get("kind") != "promotion_preference":
        raise ArchMarshalError(
            "reviewed_plan_candidate_mismatch",
            "Saved plan promotion kind does not match the committed candidate.",
        )
    if bool(operation.get("replace_existing")) is not replace_existing_preference:
        raise ArchMarshalError(
            "reviewed_plan_candidate_mismatch",
            "Saved plan does not match the explicit preference replacement confirmation.",
        )
    record_digest = operation.get("record_digest")
    preferences = generation.get("preferences") if isinstance(generation, dict) else None
    matches = [
        item
        for item in preferences or []
        if isinstance(item, dict) and item.get("digest") == record_digest
    ]
    if (
        len(matches) != 1
        or matches[0].get("key") != candidate.get("key")
        or matches[0].get("value") != candidate.get("value")
        or plan.get("package") is not None
    ):
        raise ArchMarshalError(
            "reviewed_plan_candidate_mismatch",
            "Saved plan preference does not match the exact committed candidate value.",
        )


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


__all__ = [
    "load_reviewed_plan",
    "promote_learning_candidate",
    "review_learning_candidate",
]
