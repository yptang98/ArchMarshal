from __future__ import annotations

from pathlib import Path
from typing import Any

from .adoption import plan_adoption
from .adoption_tx import adoption_transaction_status
from .inventory import collect_inventory
from .skill_index import skill_review_subject_digest
from .user_store import read_user_store_active

HISTORICAL_KEYS = ["reports", "history", "archive", "cache"]


def resolve_workspace(
    root: Path | str,
    task: str,
    *,
    user_store: Path | str | None = None,
    adoption_preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    transaction_before = adoption_transaction_status(root)
    inventory = collect_inventory(root)
    transaction_after = adoption_transaction_status(root)
    task_text = task.lower()
    transaction_incomplete = (
        transaction_before.get("state") != "none"
        or transaction_after.get("state") != "none"
    )
    transaction = (
        transaction_after
        if transaction_after.get("state") != "none"
        else transaction_before
    )
    user_state = read_user_store_active(user_store) if user_store is not None else None
    sync_preview = adoption_preview or plan_adoption(root)
    workspace_matches = [] if transaction_incomplete else _match_skills(inventory.skills, task_text)
    user_matches = _match_user_store_skills(user_state, task_text) if user_state else []
    suggested_skills, skill_conflicts = _select_skill_matches(
        workspace_matches, user_matches
    )
    blocked_skills = _blocked_skills(
        inventory.skills,
        task_text=task_text,
        override_reason="adoption_transaction_incomplete"
        if transaction_incomplete
        else None,
    )
    blocked_skills = _merge_sync_blocked_skills(
        blocked_skills,
        sync_preview,
        task_text=task_text,
        transaction_incomplete=transaction_incomplete,
    )
    return {
        "tool": "archmarshal",
        "root": str(inventory.root),
        "task": task,
        "required_policy_skills": (
            [] if transaction_incomplete else _required_policy_skills(inventory.skills)
        ),
        "suggested_skills": suggested_skills,
        "skill_conflicts": skill_conflicts,
        "blocked_skills": blocked_skills,
        "skill_sync": _skill_sync_status(sync_preview),
        "adoption_transaction": transaction,
        "suggested_context_modules": _match_context_modules(inventory.context_modules, task_text),
        "suggested_memory_records": _match_memory_records(inventory.memory_records, task_text),
        "user_store": (
            {
                "root": user_state["root"],
                "head": user_state["head"],
                "preference_count": len(user_state["preferences"]),
            }
            if user_state
            else None
        ),
        "user_preferences": user_state["preferences"] if user_state else [],
        "memory_budget": {
            "max_records": 5,
            "max_tokens": 6000,
            "prefer_reviewed": True,
        },
        "explicit_only_paths": _historical_paths(inventory.paths),
        "notes": [
            "Resolution is advisory and read-only.",
            "Active highest-priority global skills are returned separately as required policy.",
            "Missing, unsafe, or drifted skill sources are blocked until reviewed and synchronized.",
            "A workspace Skill deterministically shadows a user-store Skill with the same id; conflicts remain explicit for review.",
            "Historical artifact paths remain explicit-only unless a selected context module references them.",
        ],
    }


def _normalize(value: str) -> str:
    return value.lower().replace("-", " ").replace("_", " ")


def _contains(text: str, needle: str) -> bool:
    return _normalize(needle) in _normalize(text)


def _match_skills(skills: list[dict[str, Any]], task_text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for skill in skills:
        if (
            skill.get("status") not in {"active", "experimental"}
            or skill_activation_block_reason(skill) is not None
        ):
            continue
        negative_matches = [
            item for item in skill.get("negative_triggers") or [] if _contains(task_text, str(item))
        ]
        if negative_matches:
            continue
        trigger_matches = [
            item for item in skill.get("triggers") or [] if _contains(task_text, str(item))
        ]
        tag_matches = [item for item in skill.get("tags") or [] if _contains(task_text, str(item))]
        priority_bonus = {"highest": 3, "high": 2, "normal": 0, "low": -1}.get(
            str(skill.get("priority") or "normal"), 0
        )
        score = len(trigger_matches) * 3 + len(tag_matches) + priority_bonus
        if score <= 0:
            continue
        matches.append(
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "kind": skill.get("kind"),
                "scope": skill.get("scope"),
                "priority": skill.get("priority") or "normal",
                "score": score,
                "path": skill.get("_skill_dir"),
                "metadata_path": skill.get("_overlay_manifest_path") or skill.get("_manifest_path"),
                "source_managed": _source_managed(skill),
                "origin": "workspace",
                "trigger_matches": trigger_matches,
                "tag_matches": tag_matches,
                "implementation_sha256": skill.get("_current_package_sha256"),
            }
        )
    return sorted(matches, key=lambda item: (-item["score"], str(item["id"])))


def _match_user_store_skills(
    user_state: dict[str, Any],
    task_text: str,
) -> list[dict[str, Any]]:
    virtual: list[dict[str, Any]] = []
    for record in user_state.get("common_skills") or []:
        if not isinstance(record, dict) or not isinstance(record.get("manifest"), dict):
            continue
        skill = dict(record["manifest"])
        skill["_skill_dir"] = record.get("package_dir")
        skill["_manifest_path"] = f"{record.get('package_dir')}/manifest.yaml"
        skill["_has_skill_md"] = True
        skill["_source_drift"] = "unchanged"
        skill["_user_package_sha256"] = record.get("package_sha256")
        virtual.append(skill)
    matches = _match_skills(virtual, task_text)
    for item in matches:
        item["origin"] = "user_store"
        item["user_store_head"] = user_state.get("head")
        item["implementation_sha256"] = next(
            (
                record.get("package_sha256")
                for record in user_state.get("common_skills") or []
                if isinstance(record, dict) and record.get("id") == item.get("id")
            ),
            None,
        )
    return matches


def _select_skill_matches(
    workspace_matches: list[dict[str, Any]],
    user_matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in [*workspace_matches, *user_matches]:
        grouped.setdefault(str(item.get("id")), []).append(item)
    selected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for skill_id, matches in grouped.items():
        ranked = sorted(
            matches,
            key=lambda item: (
                0 if item.get("origin") == "workspace" else 1,
                -int(item.get("score") or 0),
                str(item.get("path") or ""),
            ),
        )
        winner = ranked[0]
        selected.append(winner)
        if len(ranked) == 1:
            continue
        identities = {
            item.get("implementation_sha256")
            for item in ranked
            if isinstance(item.get("implementation_sha256"), str)
        }
        conflicts.append(
            {
                "id": skill_id,
                "resolution": "workspace_precedence",
                "selected": _conflict_subject(winner),
                "suppressed": [_conflict_subject(item) for item in ranked[1:]],
                "requires_review": len(identities) != 1 or any(
                    not item.get("implementation_sha256") for item in ranked
                ),
            }
        )
    return (
        sorted(selected, key=lambda item: (-item["score"], str(item["id"]))),
        sorted(conflicts, key=lambda item: item["id"]),
    )


def _conflict_subject(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "origin": item.get("origin"),
        "path": item.get("path"),
        "implementation_sha256": item.get("implementation_sha256"),
    }


def _merge_sync_blocked_skills(
    blocked: list[dict[str, Any]],
    preview: dict[str, Any],
    *,
    task_text: str,
    transaction_incomplete: bool,
) -> list[dict[str, Any]]:
    existing_paths = {str(item.get("path")) for item in blocked}
    merged = [*blocked]
    should_surface = not preview.get("configured") or preview.get("overlay_enabled")
    if not should_surface:
        return merged
    for skill in preview.get("discovered_skills") or []:
        if not isinstance(skill, dict):
            continue
        state = str(skill.get("tracking_state") or skill.get("source_drift") or "")
        source = str(skill.get("source") or "")
        if not source or source in existing_paths or state in {"indexed", "unchanged"}:
            continue
        reason = (
            "adoption_transaction_incomplete"
            if transaction_incomplete
            else "workspace_unmanaged"
            if not preview.get("configured")
            else "index_untracked"
            if state == "new"
            else f"source_{state}"
        )
        merged.append(
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "path": source,
                "metadata_path": skill.get("overlay_manifest"),
                "reason": reason,
                "source_drift": skill.get("source_drift"),
                "tracking_state": state,
                "task_relevant": _skill_matches_task_signals(skill, task_text),
            }
        )
        existing_paths.add(source)
    return sorted(merged, key=lambda item: (str(item.get("id")), str(item.get("path"))))


def _skill_sync_status(preview: dict[str, Any]) -> dict[str, Any]:
    changes = [
        {
            "id": skill.get("id"),
            "source": skill.get("source"),
            "tracking_state": skill.get("tracking_state"),
        }
        for skill in preview.get("discovered_skills") or []
        if isinstance(skill, dict)
        and str(skill.get("tracking_state") or "") not in {"indexed", "unchanged"}
    ]
    required = bool(
        preview.get("blocked")
        or preview.get("review_required")
        or (preview.get("overlay_enabled") and changes)
    )
    return {
        "required": required,
        "configured": bool(preview.get("configured")),
        "plan_digest": preview.get("plan_digest"),
        "changes": changes,
    }


def _required_policy_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = []
    for skill in skills:
        if (
            skill.get("kind") != "global_skill"
            or skill.get("priority") != "highest"
            or skill.get("status") != "active"
            or skill_activation_block_reason(skill) is not None
        ):
            continue
        required.append(
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "path": skill.get("_skill_dir"),
                "metadata_path": skill.get("_overlay_manifest_path")
                or skill.get("_manifest_path"),
                "priority": "highest",
                "source_managed": _source_managed(skill),
            }
        )
    return sorted(required, key=lambda item: str(item["id"]))


def _blocked_skills(
    skills: list[dict[str, Any]],
    *,
    task_text: str = "",
    override_reason: str | None = None,
) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for skill in skills:
        reason = override_reason or skill_activation_block_reason(skill)
        if reason is None:
            continue
        blocked.append(
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "path": skill.get("_skill_dir"),
                "metadata_path": skill.get("_overlay_manifest_path")
                or skill.get("_manifest_path"),
                "reason": reason,
                "source_drift": skill.get("_source_drift"),
                "task_relevant": _skill_matches_task_signals(skill, task_text),
            }
        )
    return sorted(blocked, key=lambda item: str(item["id"]))


def _skill_matches_task_signals(skill: dict[str, Any], task_text: str) -> bool:
    if not task_text.strip():
        return False
    if any(
        _contains(task_text, str(item)) for item in skill.get("negative_triggers") or []
    ):
        return False
    signals = [
        *(skill.get("triggers") or []),
        *(skill.get("tags") or []),
        skill.get("name"),
        skill.get("id"),
    ]
    return any(
        isinstance(signal, str) and signal.strip() and _contains(task_text, signal)
        for signal in signals
    )


def skill_activation_block_reason(skill: dict[str, Any]) -> str | None:
    if skill.get("_index_state") == "untracked":
        return "index_untracked"
    status = skill.get("status")
    if status not in {"active", "experimental"}:
        return f"status_{status or 'unknown'}"
    if skill.get("_source_error"):
        return "source_unsafe"
    if skill.get("_has_skill_md") is False:
        return "source_missing"
    drift = skill.get("_source_drift")
    if drift and drift != "unchanged":
        return f"source_{drift}"
    source = skill.get("source")
    source_managed = bool(source.get("managed", True)) if isinstance(source, dict) else True
    if not source_managed:
        review_state = skill.get("review_state")
        review = skill.get("review")
        if review_state == "rejected":
            return "metadata_rejected"
        if review_state != "approved" or not isinstance(review, dict):
            return "metadata_needs_review"
        if review.get("decision") != "approved":
            return "metadata_review_invalid"
        try:
            current_subject = skill_review_subject_digest(skill)
        except (TypeError, ValueError):
            return "metadata_review_invalid"
        if review.get("subject_digest") != current_subject:
            return "metadata_review_stale"
        elevated = (
            skill.get("kind") == "global_skill"
            or skill.get("scope") == "global"
            or skill.get("priority") == "highest"
        )
        if elevated and review.get("allow_global_policy") is not True:
            return "global_policy_not_approved"
    return None


def _source_managed(skill: dict[str, Any]) -> bool:
    source = skill.get("source")
    return bool(source.get("managed", True)) if isinstance(source, dict) else True


def _match_context_modules(modules: list[dict[str, Any]], task_text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for module in modules:
        if module.get("status") not in {"active", "promoted"}:
            continue
        negative_matches = [
            item for item in module.get("negative_triggers") or [] if _contains(task_text, str(item))
        ]
        if negative_matches:
            continue
        tag_matches = [item for item in module.get("tags") or [] if _contains(task_text, str(item))]
        policy_matches = [
            item
            for item in module.get("read_policy") or []
            if _policy_matches_task(str(item), task_text)
        ]
        score = len(tag_matches) * 2 + len(policy_matches)
        if score <= 0:
            continue
        matches.append(
            {
                "id": module.get("id"),
                "name": module.get("name"),
                "score": score,
                "path": module.get("_module_path"),
                "tag_matches": tag_matches,
                "read_policy_matches": policy_matches,
                "source_files": module.get("source_files") or [],
            }
        )
    return sorted(matches, key=lambda item: (-item["score"], str(item["id"])))


def _match_memory_records(records: list[dict[str, Any]], task_text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") not in {"active", "promoted"}:
            continue
        key_matches = [item for item in record.get("retrieval_keys") or [] if _contains(task_text, str(item))]
        namespace_matches = [item for item in record.get("namespace") or [] if _contains(task_text, str(item))]
        policy_matches = []
        read_policy = record.get("read_policy")
        if read_policy and _policy_matches_task(str(read_policy), task_text):
            policy_matches.append(read_policy)
        score = len(key_matches) * 3 + len(namespace_matches) + len(policy_matches)
        if score <= 0:
            continue
        matches.append(
            {
                "id": record.get("id"),
                "store_id": record.get("store_id"),
                "score": score,
                "content_path": record.get("content_path"),
                "review_status": record.get("review_status"),
                "confidence": record.get("confidence"),
                "key_matches": key_matches,
                "namespace_matches": namespace_matches,
                "read_policy_matches": policy_matches,
                "inject": False,
                "read_first": True,
            }
        )
    return sorted(matches, key=lambda item: (-item["score"], str(item["id"])))[:5]


def _policy_matches_task(policy: str, task_text: str) -> bool:
    if policy in {"default", "task_based", "when_task_matches"}:
        return True
    normalized_policy = _normalize(policy)
    for token in ["architecture", "database", "release", "planning", "migration", "frontend", "backend"]:
        if token in normalized_policy and token in _normalize(task_text):
            return True
    return False


def _historical_paths(paths: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in HISTORICAL_KEYS:
        value = paths.get(key) or []
        if isinstance(value, str):
            result.append(value)
        else:
            result.extend(str(item) for item in value)
    return result
