"""Bind an explicitly selected host project to its actual working checkout."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .portable import fail, text
from .safety import ensure_unlinked_path


def absolute_directory(value: Path | str, *, existing: bool = True) -> Path:
    path = Path(value)
    if not path.is_absolute():
        fail("Project, work and record paths must be absolute.", "project_path_invalid")
    path = ensure_unlinked_path(path, purpose="Host project path").resolve(strict=False)
    if path == Path(path.anchor) or (existing and not path.is_dir()):
        fail("Select a specific existing project directory.", "project_path_invalid")
    return path


def canonical_path(path: Path) -> str:
    return os.path.normcase(str(path))


def _git(root: Path, *args: str) -> str | None:
    # An inherited GIT_DIR/WORK_TREE can override -C and bind an unrelated repo.
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("GIT_")}
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, encoding="utf-8", timeout=10, check=False,
                                env={**environment, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and len(result.stdout) < 8192 else None


def git_context(root: Path) -> dict[str, Any] | None:
    top = _git(root, "rev-parse", "--show-toplevel")
    common = _git(root, "rev-parse", "--git-common-dir") if top else None
    if not top or not common:
        return None
    return {"root": str(Path(top).resolve()),
            "common_dir": canonical_path((root / common).resolve()),
            "head": _git(root, "rev-parse", "HEAD"),
            "branch": _git(root, "symbolic-ref", "--short", "HEAD")}


def resolve_project_context(project_root: Path | str, work_root: Path | str | None = None,
                            *, project_id: str = "", thread_id: str = "",
                            host_id: str = "local") -> dict[str, Any]:
    """Never select a project by name/remote URL or change a host project setting."""
    project = absolute_directory(project_root)
    work = absolute_directory(work_root if work_root is not None else project)
    for key, value in {"project_id": project_id, "thread_id": thread_id, "host_id": host_id}.items():
        text(value, key, 200)
    working_git = git_context(work)
    if work == project:
        relationship = "same_directory"
    elif project in work.parents:
        relationship = "project_subdirectory"
    else:
        primary_git = git_context(project)
        if (not primary_git or not working_git
                or primary_git["common_dir"] != working_git["common_dir"]):
            fail("The working directory cannot be associated with the selected project. "
                 "Use the explicit working directory as project root until the host association is known.",
                 "project_association_unknown")
        scope = project.relative_to(Path(primary_git["root"]))
        work_scope = work.relative_to(Path(working_git["root"]))
        if scope != work_scope and scope not in work_scope.parents:
            fail("Worktree directory is outside the selected project's repository subdirectory.",
                 "project_association_unknown")
        relationship = "git_worktree"
    return {"project_root": str(project), "canonical_project_root": canonical_path(project),
            "work_root": str(work), "codex_project_id": project_id, "thread_id": thread_id,
            "host_id": host_id, "relationship": relationship, "git": working_git,
            "project_identity": [project.stat().st_dev, project.stat().st_ino],
            "work_identity": [work.stat().st_dev, work.stat().st_ino]}
