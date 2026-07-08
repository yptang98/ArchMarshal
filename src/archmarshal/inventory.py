from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .io import list_files, load_yaml, rel


DEFAULT_PATHS = {
    "project_root": ".",
    "code_roots": ["src"],
    "agent_root": ".agent",
    "global_skills": [".agents/global"],
    "functional_skills": [".agents/skills/functional"],
    "common_project_skills": [".agents/skills/common-project"],
    "project_skills": [".agents/skills"],
    "generated_skills": [".agents/skills/generated"],
    "knowledge": [".agent/knowledge"],
    "context_modules": [".agent/context-modules"],
    "reports": [".agent/reports"],
    "plans": [".agent/plans"],
    "history": [".agent/history"],
    "archive": [".agent/archive"],
    "cache": [".agent/cache"],
    "inbox": [".agent/inbox"],
}

HISTORICAL_PATH_KEYS = {"reports", "history", "archive", "cache"}
RESERVED_AGENT_FILES = {
    ".agent/workspace.yaml",
    ".agent/INDEX.md",
    ".agent/registry.yaml",
}


@dataclass(frozen=True)
class WorkspaceInventory:
    root: Path
    workspace: dict[str, Any]
    paths: dict[str, Any]
    files: dict[str, Any]
    directories: dict[str, Any]
    artifacts: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    context_modules: list[dict[str, Any]]
    unregistered_agent_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "archmarshal",
            "version": __version__,
            "root": str(self.root),
            "workspace": self.workspace,
            "paths": self.paths,
            "files": self.files,
            "directories": self.directories,
            "artifacts": self.artifacts,
            "skills": self.skills,
            "context_modules": self.context_modules,
            "unregistered_agent_files": self.unregistered_agent_files,
            "notes": [
                "Read-only scan; no files were modified.",
                "Historical artifact directories should not be loaded by default.",
                "Use this output as input to lint, audit, and plan commands.",
            ],
        }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _load_workspace(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace_file = root / ".agent" / "workspace.yaml"
    if not workspace_file.exists():
        return {}, DEFAULT_PATHS.copy()
    data = load_yaml(workspace_file)
    paths = DEFAULT_PATHS.copy()
    paths.update(data.get("paths", {}) or {})
    return data.get("workspace", {}) or {}, paths


def _path_entries(root: Path, paths: dict[str, Any]) -> dict[str, list[Path]]:
    entries: dict[str, list[Path]] = {}
    for key, value in paths.items():
        if key == "project_root":
            entries[key] = [root / str(value)]
        else:
            entries[key] = [root / item for item in _as_list(value)]
    return entries


def _file_status(root: Path) -> dict[str, dict[str, Any]]:
    expected = {
        "agents_md": root / "AGENTS.md",
        "workspace_yaml": root / ".agent" / "workspace.yaml",
        "index_md": root / ".agent" / "INDEX.md",
        "registry_yaml": root / ".agent" / "registry.yaml",
    }
    return {
        key: {
            "path": rel(path, root),
            "exists": path.exists(),
            "bytes": path.stat().st_size if path.exists() else 0,
        }
        for key, path in expected.items()
    }


def _directory_status(root: Path, entries: dict[str, list[Path]]) -> dict[str, Any]:
    directories: dict[str, Any] = {}
    for key, roots in entries.items():
        if key == "project_root":
            continue
        directories[key] = [
            {
                "path": _display_path(path, root),
                "exists": path.exists(),
                "file_count": len(list_files(path)),
            }
            for path in roots
        ]
    return directories


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_registry(root: Path) -> list[dict[str, Any]]:
    registry_file = root / ".agent" / "registry.yaml"
    if not registry_file.exists():
        return []
    data = load_yaml(registry_file)
    artifacts = data.get("artifacts", []) if isinstance(data, dict) else []
    return [item for item in artifacts if isinstance(item, dict)]


def _load_skill_manifest(manifest_path: Path, root: Path) -> dict[str, Any]:
    try:
        manifest = load_yaml(manifest_path)
    except Exception as exc:  # pragma: no cover - surfaced as data for lint
        manifest = {"_load_error": str(exc)}
    skill_dir = manifest_path.parent
    manifest["_manifest_path"] = rel(manifest_path, root)
    manifest["_skill_dir"] = rel(skill_dir, root)
    manifest["_has_skill_md"] = (skill_dir / "SKILL.md").exists()
    return manifest


def _find_skills(root: Path, entries: dict[str, list[Path]]) -> list[dict[str, Any]]:
    skill_roots = (
        entries.get("global_skills", [])
        + entries.get("functional_skills", [])
        + entries.get("common_project_skills", [])
        + entries.get("project_skills", [])
        + entries.get("generated_skills", [])
    )
    manifests: dict[Path, dict[str, Any]] = {}
    for skill_root in skill_roots:
        if not skill_root.exists():
            continue
        for manifest_path in skill_root.rglob("manifest.yaml"):
            manifests[manifest_path.resolve()] = _load_skill_manifest(manifest_path, root)
        for skill_md in skill_root.rglob("SKILL.md"):
            manifest_path = skill_md.parent / "manifest.yaml"
            if not manifest_path.exists():
                manifests[manifest_path.resolve()] = {
                    "_manifest_path": rel(manifest_path, root),
                    "_skill_dir": rel(skill_md.parent, root),
                    "_has_skill_md": True,
                    "_missing_manifest": True,
                }
    return sorted(manifests.values(), key=lambda item: item.get("_skill_dir", ""))


def _load_context_module(module_path: Path, root: Path) -> dict[str, Any]:
    try:
        module = load_yaml(module_path)
    except Exception as exc:  # pragma: no cover - surfaced as data for lint
        module = {"_load_error": str(exc)}
    module["_module_path"] = rel(module_path, root)
    return module


def _find_context_modules(root: Path, entries: dict[str, list[Path]]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for context_root in entries.get("context_modules", []):
        if not context_root.exists():
            continue
        for module_path in context_root.rglob("module.yaml"):
            modules.append(_load_context_module(module_path, root))
    return sorted(modules, key=lambda item: item.get("_module_path", ""))


def _unregistered_agent_files(root: Path, artifacts: list[dict[str, Any]]) -> list[str]:
    agent_root = root / ".agent"
    if not agent_root.exists():
        return []
    registered_paths = {
        str(item.get("path", "")).replace("\\", "/")
        for item in artifacts
        if item.get("path")
    }
    unregistered: list[str] = []
    for path in list_files(agent_root):
        relative = rel(path, root)
        if relative in RESERVED_AGENT_FILES:
            continue
        if relative not in registered_paths:
            unregistered.append(relative)
    return sorted(unregistered)


def collect_inventory(root: Path | str) -> WorkspaceInventory:
    resolved_root = Path(root).resolve()
    workspace, paths = _load_workspace(resolved_root)
    entries = _path_entries(resolved_root, paths)
    artifacts = _load_registry(resolved_root)
    return WorkspaceInventory(
        root=resolved_root,
        workspace=workspace,
        paths=paths,
        files=_file_status(resolved_root),
        directories=_directory_status(resolved_root, entries),
        artifacts=artifacts,
        skills=_find_skills(resolved_root, entries),
        context_modules=_find_context_modules(resolved_root, entries),
        unregistered_agent_files=_unregistered_agent_files(resolved_root, artifacts),
    )
