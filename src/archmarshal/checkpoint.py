from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .diagnostics import severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace
from .workspace_layout import load_workspace_layout


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
    layout = load_workspace_layout(inventory.root)
    diagnostics = lint_workspace(root)
    workspace_name = str(inventory.workspace.get("name") or inventory.root.name)
    created_at = datetime.now(layout.effective_timezone).replace(microsecond=0)
    filename_stem = layout.project_file_stem(
        "checkpoint",
        task or summary,
        created_at,
    )
    checkpoint_id = f"checkpoint.{filename_stem}" if filename_stem else "checkpoint.pending-name"
    checkpoint_path = layout.save_dir(
        "checkpoints",
        created_at,
        override=save_path,
        default_partition="none",
    )
    checkpoint_dir = layout.relative(checkpoint_path)
    save_path_source = "cli" if save_path is not None else layout.source_for("checkpoints")
    suggested_path = f"{checkpoint_dir}/{filename_stem}.md" if filename_stem else None
    retrieval_keys = _retrieval_keys(task, summary, decisions)
    requires_name = filename_stem is None

    return {
        "tool": "archmarshal",
        "root": str(inventory.root),
        "mode": "requires_user_input" if requires_name else "propose_only",
        "stage": "context_checkpoint",
        "layout_profile": layout.to_dict(),
        "recording_policy": {
            "mode": "auto",
            "level": "light",
            "record": ["important_changes", "decisions", "risks", "next_steps"],
            "reason": "Checkpoint depth is auto-selected; routine checkpoints stay light unless the project shows new reusable knowledge or workflow changes.",
        },
        "save_path": {
            "kind": "project_file",
            "path": checkpoint_dir,
            "source": save_path_source,
            "user_configured": save_path_source in {"cli", "workspace", "provided"},
            "requires_user_configuration": save_path_source == "default" or requires_name,
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
            "filename": f"{filename_stem}.md" if filename_stem else None,
            "naming_strategy": layout.naming_strategy,
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
            *(
                [
                    "The confirmed preserve naming policy requires an explicit filename before ArchMarshal can suggest a checkpoint path."
                ]
                if requires_name
                else []
            ),
        ],
    }


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
