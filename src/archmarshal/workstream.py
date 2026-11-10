"""Explicit handoff lifecycle guidance; no host chat hooks or implicit saves."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .handoff import resume_context
from .portable import device, digest


def start_workstream(project: Path | str, *, stream: str | None = None, task: str = "",
                     device_path: Path | str | None = None,
                     include_roots: list[str] | None = None, max_chars: int = 24_000
                     ) -> dict[str, Any]:
    context = resume_context(project, stream=stream, task=task,
                              include_roots=include_roots, max_chars=max_chars)
    active = [item for item in context["contexts"] if item["record"]["status"] == "active"]
    selected = stream
    if selected is None and len(active) == 1 and not context["omitted"]:
        selected = active[0]["record"]["stream"]
    identity = device(device_path) if device_path is not None else None
    task_slug = re.sub(r"[^\w-]+", "-", task.lower()).strip("-")[:40] or "work"
    suffix = digest({"task": task, "device": identity["id"] if identity else "local"})[:8]
    suggested = selected or f"{task_slug}-{suffix}"
    cards = [{"stream": item["record"]["stream"], "record_id": item["record_id"],
              "status": item["record"]["status"], "last_saved_at": item["last_saved_at"],
              "save_age_seconds": item["save_age_seconds"],
              "unfinished_next_steps": item["unfinished_next_steps"],
              "evidence_needs_review": bool(item["changed_files"] or item["head_changed"]
                                            or item["branch_changed"]),
              "changed_files": item["changed_files"]} for item in context["contexts"]]
    return {**context, "workstreams": cards,
            "workflow": {"selected_stream": selected, "suggested_stream": suggested,
                         "selection_required": selected is None,
                         "multiple_windows": "Choose a distinct stream for independent work; reuse it on resume.",
                         "start": "context-start ROOT [--stream NAME] [--task TEXT]",
                         "save": "context-save ROOT --stream NAME --summary TEXT --next-step TEXT",
                         "finish": "context-finish ROOT --stream NAME --summary TEXT",
                         "apply": "Preview first; apply with --apply --expect-plan DIGEST.",
                         "save_reminders": ["Before switching windows or compressing context",
                                            "After a decision, verified result, or next-step change",
                                            "Before ending the current phase"],
                         "automatic_chat_capture": False, "automatic_saves": False}}
