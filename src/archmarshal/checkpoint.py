from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .diagnostics import severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace


def checkpoint_workspace(
    root: Path | str,
    summary: str,
    task: str | None = None,
    save_path: str | None = None,
    decisions: list[str] | None = None,
    files: list[str] | None = None,
    next_steps: list[str] | None = None,
    used_skills: list[str] | None = None,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    decisions = decisions or []
    files = files or []
    next_steps = next_steps or []
    used_skills = used_skills or []
    risks = risks or []

    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)
    workspace_name = str(inventory.workspace.get("name") or inventory.root.name)
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    filename_stem = _project_file_stem(created_at, task, summary, inventory.naming)
    checkpoint_id = f"checkpoint.{filename_stem}"
    checkpoint_dir, save_path_source = _checkpoint_save_dir(inventory.save_paths, save_path)
    suggested_path = f"{checkpoint_dir}/{filename_stem}.md"
    retrieval_keys = _retrieval_keys(task, summary, decisions)

    return {
        "tool": "archmarshal",
        "root": str(inventory.root),
        "mode": "propose_only",
        "stage": "context_checkpoint",
        "save_path": {
            "kind": "project_file",
            "path": checkpoint_dir,
            "source": save_path_source,
            "user_configured": save_path_source in {"cli", "workspace"},
            "requires_user_configuration": save_path_source == "fallback",
        },
        "original_preservation_policy": {
            "preserve_originals": True,
            "delete_after_summary": False,
            "suggested_raw_policy": "explicit_only",
            "suggested_update_policy": "append_only",
            "reason": "A checkpoint is an index and summary, not a replacement for raw project history.",
        },
        "checkpoint": {
            "id": checkpoint_id,
            "workspace": workspace_name,
            "created_at": created_at.isoformat(),
            "filename": f"{filename_stem}.md",
            "naming_strategy": "time_topic_kind",
            "task": task or "",
            "summary": summary,
            "decisions": decisions,
            "key_files": files,
            "next_steps": next_steps,
            "risks": risks,
            "used_skills": used_skills,
            "suggested_path": suggested_path,
        },
        "suggested_memory_record": {
            "id": f"mem.{_slug(workspace_name)}.{checkpoint_id.replace('checkpoint.', '')}",
            "store_id": "memory.project.context",
            "kind": "evidence",
            "scope": "project",
            "namespace": [workspace_name, "checkpoint"],
            "status": "candidate",
            "content_path": suggested_path,
            "evidence_refs": [],
            "confidence": "generated",
            "review_status": "pending_human",
            "retrieval_keys": retrieval_keys,
            "read_policy": "explicit_only",
            "ttl_days": 30,
        },
        "registry_update_suggestions": [
            {
                "id": checkpoint_id,
                "kind": "history",
                "path": suggested_path,
                "status": "raw",
                "read_policy": "explicit_only",
                "update_policy": "append_only",
                "source_of_truth": False,
                "tags": ["checkpoint", "context-compression"],
                "preserve_original": True,
            }
        ],
        "diagnostic_summary": severity_counts(diagnostics),
        "notes": [
            "Checkpoint is read-only; no project files were modified.",
            "Use after context compression to preserve decisions, key files, and next steps.",
            "Do not delete raw history after summarization; keep original material explicit-only.",
            "Review the suggested memory record before promotion.",
        ],
    }


def _project_file_stem(
    created_at: datetime,
    task: str | None,
    summary: str,
    naming: dict[str, Any],
) -> str:
    policy = naming.get("project_files") if isinstance(naming, dict) else {}
    timestamp_format = "%Y%m%d-%H%M%S"
    max_slug_words = 6
    if isinstance(policy, dict):
        timestamp_format = str(policy.get("timestamp_format") or timestamp_format)
        configured_max = policy.get("max_slug_words")
        if isinstance(configured_max, int) and configured_max > 0:
            max_slug_words = min(configured_max, 12)
    timestamp = created_at.strftime(timestamp_format)
    topic = _topic_slug(task or summary, max_slug_words)
    return f"{timestamp}-{topic}-checkpoint"


def _checkpoint_save_dir(save_paths: dict[str, Any], override: str | None) -> tuple[str, str]:
    if override:
        return _normalize_save_path(override), "cli"
    project_files = save_paths.get("project_files") if isinstance(save_paths, dict) else {}
    if isinstance(project_files, dict) and project_files.get("checkpoints"):
        return _normalize_save_path(str(project_files["checkpoints"])), "workspace"
    return ".agent/inbox/checkpoints", "fallback"


def _normalize_save_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/") or "."


def _topic_slug(text: str, max_words: int) -> str:
    normalized = "".join(
        char.lower() if char.isascii() and char.isalnum() else " "
        for char in text
    )
    words = [word for word in normalized.split() if len(word) >= 3]
    return "-".join(words[:max_words]) or "checkpoint"


def _retrieval_keys(task: str | None, summary: str, decisions: list[str]) -> list[str]:
    tokens: list[str] = []
    for text in [task or "", summary, *decisions]:
        for token in _tokenize(text):
            if token not in tokens:
                tokens.append(token)
            if len(tokens) >= 8:
                return tokens
    return tokens or ["checkpoint"]


def _tokenize(text: str) -> list[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    stop = {
        "after",
        "with",
        "from",
        "that",
        "this",
        "the",
        "and",
        "for",
        "into",
        "project",
    }
    return [token for token in normalized.split() if len(token) >= 4 and token not in stop]


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "." for char in value)
    parts = [part for part in slug.split(".") if part]
    return ".".join(parts) or "workspace"


__all__ = ["checkpoint_workspace"]
