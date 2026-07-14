from __future__ import annotations

from pathlib import Path
from typing import Any

from .adoption_tx import adoption_transaction_status
from .inventory import collect_inventory

HISTORICAL_KEYS = ["reports", "history", "archive", "cache"]


def resolve_workspace(root: Path | str, task: str) -> dict[str, Any]:
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
    return {
        "tool": "archmarshal",
        "root": str(inventory.root),
        "task": task,
        "required_policy_skills": (
            [] if transaction_incomplete else _required_policy_skills(inventory.skills)
        ),
        "suggested_skills": [] if transaction_incomplete else _match_skills(inventory.skills, task_text),
        "blocked_skills": _blocked_skills(
            inventory.skills,
            override_reason="adoption_transaction_incomplete"
            if transaction_incomplete
            else None,
        ),
        "adoption_transaction": transaction,
        "suggested_context_modules": _match_context_modules(inventory.context_modules, task_text),
        "suggested_memory_records": _match_memory_records(inventory.memory_records, task_text),
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
            or _skill_activation_block_reason(skill) is not None
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
                "trigger_matches": trigger_matches,
                "tag_matches": tag_matches,
            }
        )
    return sorted(matches, key=lambda item: (-item["score"], str(item["id"])))


def _required_policy_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = []
    for skill in skills:
        if (
            skill.get("kind") != "global_skill"
            or skill.get("priority") != "highest"
            or skill.get("status") != "active"
            or _skill_activation_block_reason(skill) is not None
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
    override_reason: str | None = None,
) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for skill in skills:
        reason = override_reason or _skill_activation_block_reason(skill)
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
            }
        )
    return sorted(blocked, key=lambda item: str(item["id"]))


def _skill_activation_block_reason(skill: dict[str, Any]) -> str | None:
    if skill.get("_index_state") == "untracked":
        return "index_untracked"
    if skill.get("review_state") == "needs_review":
        return "metadata_needs_review"
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
