from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace


def catalog_projects(
    roots: list[Path | str],
    *,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    requested_tags = {item.strip().lower() for item in tags or [] if item.strip()}
    projects: list[dict[str, Any]] = []
    for value in roots:
        root = Path(value).resolve()
        inventory = collect_inventory(root)
        workspace = inventory.workspace
        project_tags = [str(item) for item in workspace.get("tags") or []]
        if requested_tags and not requested_tags.issubset({item.lower() for item in project_tags}):
            continue
        projects.append(
            {
                "name": workspace.get("name") or root.name,
                "root": str(root),
                "created_on": workspace.get("created_on"),
                "adopted_on": workspace.get("adopted_on"),
                "tags": project_tags,
                "management_mode": workspace.get("management_mode"),
                "skill_count": len(inventory.skills),
                "artifact_count": len(inventory.artifacts),
                "diagnostics": severity_counts(lint_workspace(root)),
            }
        )
    projects.sort(
        key=lambda item: (str(item.get("created_on") or ""), str(item.get("name") or "")),
        reverse=True,
    )
    return {
        "tool": "archmarshal",
        "stage": "catalog",
        "mode": "read_only",
        "filter_tags": sorted(requested_tags),
        "project_count": len(projects),
        "projects": projects,
        "notes": [
            "Projects are sorted by recorded creation date, then name.",
            "Tags are matched as an AND filter.",
            "Catalog reads project control planes and does not load raw history.",
        ],
    }


__all__ = ["catalog_projects"]
