from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .diagnostics import severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace
from .planner import plan_workspace


def closeout_workspace(root: Path | str, used_skills: list[str] | None = None) -> dict[str, Any]:
    used_skills = used_skills or []
    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)
    skill_index = {
        str(skill.get("id") or skill.get("name")): skill
        for skill in inventory.skills
        if skill.get("id") or skill.get("name")
    }
    matched = [skill_index[item] for item in used_skills if item in skill_index]
    missing = [item for item in used_skills if item not in skill_index]
    plan = plan_workspace(root)
    inventory_dict = inventory.to_dict()
    candidate_memory_updates = _candidate_memory_updates(inventory_dict)
    promotion_candidates = _promotion_candidates(inventory_dict, matched)
    archive_candidates = _archive_candidates(inventory_dict)
    skill_candidates = _skill_candidates(inventory_dict, used_skills)
    registry_update_suggestions = _registry_update_suggestions(inventory_dict)
    session_summary = _session_summary(inventory_dict, matched, missing)
    recording_policy = _recording_policy(
        diagnostics=diagnostics,
        missing_skills=missing,
        session_summary=session_summary,
        candidate_memory_updates=candidate_memory_updates,
        promotion_candidates=promotion_candidates,
        archive_candidates=archive_candidates,
        skill_candidates=skill_candidates,
        registry_update_suggestions=registry_update_suggestions,
    )
    return {
        "tool": "archmarshal",
        "root": str(inventory.root),
        "used_skills": [
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "kind": skill.get("kind"),
                "path": skill.get("_skill_dir"),
                "tags": skill.get("tags") or [],
            }
            for skill in matched
        ],
        "missing_used_skills": missing,
        "skill_counts_by_kind": dict(Counter(str(skill.get("kind")) for skill in inventory.skills)),
        "diagnostic_summary": severity_counts(diagnostics),
        "cleanup_actions": plan["actions"],
        "candidate_memory_updates": candidate_memory_updates,
        "recording_policy": recording_policy,
        "original_preservation_policy": {
            "preserve_originals": True,
            "delete_after_summary": False,
            "archive_before_distill": True,
            "raw_history_read_policy": "explicit_only",
            "reason": "Summaries and memory candidates must point back to raw material; they do not replace it.",
        },
        "session_summary": session_summary,
        "preservation_manifest": _preservation_manifest(inventory_dict),
        "reproduction_checklist": _reproduction_checklist(inventory_dict, matched),
        "promotion_candidates": promotion_candidates,
        "archive_candidates": archive_candidates,
        "skill_candidates": skill_candidates,
        "registry_update_suggestions": registry_update_suggestions,
        "review_questions": [
            "Did any temporary report contain durable knowledge worth promoting?",
            "Did any repeated workflow deserve a project skill or common project skill?",
            "Did any selected skill have overlapping triggers or missing negative triggers?",
            "Can any generated skill be archived, registered, or promoted?",
            "Can someone reproduce the final state from source files, registry entries, context modules, and memory records?",
            "Are all raw reports, checkpoints, plans, and history entries preserved before any distillation?",
        ],
        "notes": [
            "Closeout is read-only and does not archive, promote, or modify files.",
            "Use this after project work to preserve reproducible detail without loading raw history by default.",
            "Summaries should never delete or overwrite original project history.",
        ],
}


def _recording_policy(
    diagnostics: list[Any],
    missing_skills: list[str],
    session_summary: dict[str, int],
    candidate_memory_updates: list[dict[str, Any]],
    promotion_candidates: list[dict[str, Any]],
    archive_candidates: list[dict[str, Any]],
    skill_candidates: list[dict[str, Any]],
    registry_update_suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    novelty_signals: list[str] = []
    if missing_skills:
        novelty_signals.append("used_skill_not_registered")
    if session_summary["generated_skill_count"] > 0:
        novelty_signals.append("generated_skill_present")
    if candidate_memory_updates:
        novelty_signals.append("candidate_memory_updates")
    if promotion_candidates:
        novelty_signals.append("promotion_candidates")
    if archive_candidates:
        novelty_signals.append("archive_candidates")
    if skill_candidates:
        novelty_signals.append("potential_new_skill")
    if registry_update_suggestions:
        novelty_signals.append("unregistered_project_files")
    if errors:
        novelty_signals.append("governance_errors")

    if not novelty_signals:
        return {
            "mode": "auto",
            "level": "light",
            "reason": "Project appears to mostly reuse registered skills and existing governed context.",
            "record": [
                "important_changes",
                "files_touched",
                "decisions_or_risks_only_if_any",
            ],
            "skip_by_default": [
                "new_memory_promotion",
                "new_context_module",
                "new_project_skill",
                "long_narrative_summary",
            ],
        }
    if set(novelty_signals) <= {"archive_candidates", "unregistered_project_files"}:
        return {
            "mode": "auto",
            "level": "standard",
            "reason": "Project has preservation housekeeping but no strong sign of new reusable knowledge.",
            "novelty_signals": novelty_signals,
            "record": [
                "important_changes",
                "unregistered_or_archive_candidates",
                "reproduction_notes",
            ],
            "skip_by_default": [
                "new_project_skill",
                "long_narrative_summary",
            ],
        }
    return {
        "mode": "auto",
        "level": "deep",
        "reason": "Project has signals of new reusable knowledge, workflow, or governance risk.",
        "novelty_signals": novelty_signals,
        "record": [
            "important_changes",
            "decisions",
            "risks",
            "candidate_memory_updates",
            "promotion_or_skill_candidates",
            "reproduction_notes",
        ],
        "skip_by_default": [],
    }


def _candidate_memory_updates(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    known_evidence = {
        str(ref)
        for record in inventory["memory_records"]
        for ref in (record.get("evidence_refs") or [])
    }
    for artifact in inventory["artifacts"]:
        artifact_id = str(artifact.get("id", ""))
        kind = artifact.get("kind")
        status = artifact.get("status")
        if kind not in {"report", "plan", "history"}:
            continue
        if status not in {"raw", "active", "distilled"}:
            continue
        if artifact_id in known_evidence:
            continue
        candidates.append(
            {
                "source_artifact": artifact_id,
                "source_path": artifact.get("path"),
                "candidate_status": "needs_review",
                "suggested_target": "memory_record_candidate",
                "preserve_original": True,
                "reason": "Explicit-only artifact may contain durable learning; summarize only as an index and keep the original artifact intact.",
            }
        )
    return candidates


def _session_summary(
    inventory: dict[str, Any],
    matched_skills: list[dict[str, Any]],
    missing_skills: list[str],
) -> dict[str, int]:
    generated = [
        skill
        for skill in inventory["skills"]
        if skill.get("kind") == "generated_project_skill"
    ]
    return {
        "used_skill_count": len(matched_skills),
        "missing_used_skill_count": len(missing_skills),
        "generated_skill_count": len(generated),
        "artifact_count": len(inventory["artifacts"]),
        "context_module_count": len(inventory["context_modules"]),
        "memory_record_count": len(inventory["memory_records"]),
    }


def _preservation_manifest(inventory: dict[str, Any]) -> dict[str, Any]:
    artifacts = [
        _artifact_summary(artifact)
        for artifact in inventory["artifacts"]
        if artifact.get("status") in {"active", "distilled", "promoted", "raw"}
    ]
    explicit_only = [
        _artifact_summary(artifact)
        for artifact in inventory["artifacts"]
        if artifact.get("read_policy") in {"explicit_only", "never_default"}
    ]
    original_history = [
        _artifact_summary(artifact)
        for artifact in inventory["artifacts"]
        if artifact.get("kind") in {"report", "plan", "history", "artifact"}
        or str(artifact.get("path", "")).startswith((".agent/reports/", ".agent/history/", ".agent/archive/", ".agent/inbox/"))
    ]
    context_modules = [
        {
            "id": module.get("id"),
            "path": module.get("_module_path"),
            "status": module.get("status"),
            "source_files": module.get("source_files") or [],
            "read_policy": module.get("read_policy") or [],
        }
        for module in inventory["context_modules"]
        if not module.get("_load_error")
    ]
    memory_records = [
        {
            "id": record.get("id"),
            "status": record.get("status"),
            "content_path": record.get("content_path"),
            "review_status": record.get("review_status"),
            "evidence_refs": record.get("evidence_refs") or [],
        }
        for record in inventory["memory_records"]
        if not record.get("_load_error")
    ]
    return {
        "save_paths": inventory.get("save_paths") or {},
        "naming": inventory.get("naming") or {},
        "active_artifacts": artifacts,
        "explicit_only_artifacts": explicit_only,
        "original_history_artifacts": original_history,
        "context_modules": context_modules,
        "memory_records": memory_records,
    }


def _artifact_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": artifact.get("id"),
        "kind": artifact.get("kind"),
        "path": artifact.get("path"),
        "status": artifact.get("status"),
        "read_policy": artifact.get("read_policy"),
        "source_of_truth": artifact.get("source_of_truth"),
        "tags": artifact.get("tags") or [],
    }


def _reproduction_checklist(
    inventory: dict[str, Any],
    matched_skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_truths = [
        artifact for artifact in inventory["artifacts"] if artifact.get("source_of_truth") is True
    ]
    reviewed_memories = [
        record
        for record in inventory["memory_records"]
        if record.get("status") in {"active", "promoted"} and record.get("review_status") == "reviewed"
    ]
    context_modules = [
        module
        for module in inventory["context_modules"]
        if not module.get("_load_error") and module.get("source_files")
    ]
    raw_history = [
        artifact
        for artifact in inventory["artifacts"]
        if artifact.get("kind") in {"report", "plan", "history", "artifact"}
        or str(artifact.get("path", "")).startswith((".agent/reports/", ".agent/history/", ".agent/archive/", ".agent/inbox/"))
    ]
    project_file_save_paths = (inventory.get("save_paths") or {}).get("project_files") or {}
    required_save_paths = {"checkpoints", "reports", "plans", "history", "knowledge"}
    missing_save_paths = sorted(required_save_paths - set(project_file_save_paths))
    naming_policy = (inventory.get("naming") or {}).get("project_files") or {}
    return [
        {
            "id": "project_file_save_paths_recorded",
            "status": "ok" if not missing_save_paths else "needs_review",
            "detail": (
                "Project file save paths are recorded."
                if not missing_save_paths
                else f"Missing project file save path(s): {', '.join(missing_save_paths)}."
            ),
        },
        {
            "id": "project_file_naming_recorded",
            "status": "ok" if naming_policy.get("strategy") == "time_topic_kind" else "needs_review",
            "detail": (
                "Project file naming uses time_topic_kind."
                if naming_policy.get("strategy") == "time_topic_kind"
                else "Project file naming policy is missing or unsupported."
            ),
        },
        {
            "id": "source_of_truth_registered",
            "status": "ok" if source_truths else "needs_review",
            "detail": f"{len(source_truths)} source-of-truth artifact(s) registered.",
        },
        {
            "id": "used_skills_resolved",
            "status": "ok" if matched_skills else "needs_review",
            "detail": f"{len(matched_skills)} used skill(s) resolved in the workspace.",
        },
        {
            "id": "context_has_provenance",
            "status": "ok" if context_modules else "needs_review",
            "detail": f"{len(context_modules)} context module(s) declare source_files.",
        },
        {
            "id": "memory_has_reviewed_evidence",
            "status": "ok" if reviewed_memories else "needs_review",
            "detail": f"{len(reviewed_memories)} active/promoted memory record(s) are reviewed.",
        },
        {
            "id": "original_history_preserved",
            "status": "ok" if raw_history else "needs_review",
            "detail": f"{len(raw_history)} raw history/report/plan artifact(s) are registered for explicit retrieval.",
        },
    ]


def _promotion_candidates(
    inventory: dict[str, Any],
    matched_skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    related_skill_ids = {str(skill.get("id")) for skill in matched_skills if skill.get("id")}
    candidates: list[dict[str, Any]] = []
    for artifact in inventory["artifacts"]:
        related = {str(item) for item in artifact.get("related_skills") or []}
        if artifact.get("kind") in {"report", "plan", "history"} and related & related_skill_ids:
            candidates.append(
                {
                    "artifact": artifact.get("id"),
                    "path": artifact.get("path"),
                    "suggested_target": "distilled_knowledge",
                    "preserve_original": True,
                    "reason": "Artifact is related to a skill used in this session; distill reusable knowledge while keeping the original artifact intact.",
                }
            )
    return candidates


def _archive_candidates(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for artifact in inventory["artifacts"]:
        if artifact.get("kind") == "plan" and artifact.get("status") in {"raw", "active"}:
            candidates.append(
                {
                    "artifact": artifact.get("id"),
                    "path": artifact.get("path"),
                    "preserve_original": True,
                    "reason": "Plan artifact should be marked archived or distilled after completion, but the original content should remain available.",
                }
            )
    return candidates


def _skill_candidates(inventory: dict[str, Any], used_skills: list[str]) -> list[dict[str, Any]]:
    if len(used_skills) < 2:
        return []
    tags = Counter(
        tag
        for artifact in inventory["artifacts"]
        for tag in artifact.get("tags") or []
    )
    return [
        {
            "suggested_scope": "project_skill",
            "trigger_seed": tag,
            "reason": "Repeated session activity and artifact tags may indicate a reusable local workflow.",
        }
        for tag, count in tags.items()
        if count >= 2
    ][:3]


def _registry_update_suggestions(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    registered_paths = {
        str(artifact.get("path", "")).replace("\\", "/")
        for artifact in inventory["artifacts"]
        if artifact.get("path")
    }
    for path in inventory["unregistered_agent_files"]:
        if path not in registered_paths:
            suggestions.append(
                {
                    "path": path,
                    "suggested_kind": "artifact",
                    "suggested_status": "inbox",
                    "suggested_read_policy": "explicit_only",
                    "suggested_update_policy": "append_only",
                    "preserve_original": True,
                    "reason": "Agent workspace file is not registered; preserve it explicitly before any distillation or archive move.",
                }
            )
    return suggestions
