from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .io import list_files, load_yaml_safe, rel


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

DEFAULT_SAVE_PATHS = {
    "skills": {
        "generated": ".agents/skills/generated",
        "project": ".agents/skills/project",
    },
    "project_files": {},
}
DEFAULT_NAMING = {
    "project_files": {
        "strategy": "time_topic_kind",
        "timezone": "UTC",
        "timestamp_format": "%Y%m%d-%H%M%S",
        "max_slug_words": 6,
    }
}

HISTORICAL_PATH_KEYS = {"reports", "history", "archive", "cache"}
RESERVED_AGENT_FILES = {
    ".agent/workspace.yaml",
    ".agent/INDEX.md",
    ".agent/registry.yaml",
    ".agent/memory-stores.yaml",
    ".agent/memory-records.yaml",
}


@dataclass(frozen=True)
class WorkspaceInventory:
    root: Path
    workspace: dict[str, Any]
    paths: dict[str, Any]
    save_paths: dict[str, Any]
    naming: dict[str, Any]
    files: dict[str, Any]
    directories: dict[str, Any]
    artifacts: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    context_modules: list[dict[str, Any]]
    memory_stores: list[dict[str, Any]]
    memory_records: list[dict[str, Any]]
    detected_memory_locations: list[dict[str, Any]]
    unregistered_agent_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "archmarshal",
            "version": __version__,
            "root": str(self.root),
            "workspace": self.workspace,
            "paths": self.paths,
            "save_paths": self.save_paths,
            "naming": self.naming,
            "files": self.files,
            "directories": self.directories,
            "artifacts": self.artifacts,
            "skills": self.skills,
            "context_modules": self.context_modules,
            "memory_stores": self.memory_stores,
            "memory_records": self.memory_records,
            "detected_memory_locations": self.detected_memory_locations,
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


def _load_workspace(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    workspace_file = root / ".agent" / "workspace.yaml"
    if not workspace_file.exists():
        return {}, DEFAULT_PATHS.copy(), _default_save_paths(), _default_naming()
    result = load_yaml_safe(workspace_file)
    if result.error:
        return {
            "_load_error": result.error,
            "_source_path": rel(workspace_file, root),
        }, DEFAULT_PATHS.copy(), _default_save_paths(), _default_naming()
    data = result.data if isinstance(result.data, dict) else {}
    paths = DEFAULT_PATHS.copy()
    declared_paths = data.get("paths", {})
    if isinstance(declared_paths, dict):
        paths.update(declared_paths or {})
    save_paths = _default_save_paths()
    declared_save_paths = data.get("save_paths", {})
    if isinstance(declared_save_paths, dict):
        for key, value in declared_save_paths.items():
            if isinstance(value, dict) and isinstance(save_paths.get(key), dict):
                save_paths[key].update(value)
            else:
                save_paths[key] = value
    naming = _default_naming()
    declared_naming = data.get("naming", {})
    if isinstance(declared_naming, dict):
        for key, value in declared_naming.items():
            if isinstance(value, dict) and isinstance(naming.get(key), dict):
                naming[key].update(value)
            else:
                naming[key] = value
    return data.get("workspace", {}) or {}, paths, save_paths, naming


def _default_save_paths() -> dict[str, Any]:
    return {
        "skills": dict(DEFAULT_SAVE_PATHS["skills"]),
        "project_files": dict(DEFAULT_SAVE_PATHS["project_files"]),
    }


def _default_naming() -> dict[str, Any]:
    return {
        "project_files": dict(DEFAULT_NAMING["project_files"]),
    }


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
        "memory_stores_yaml": root / ".agent" / "memory-stores.yaml",
        "memory_records_yaml": root / ".agent" / "memory-records.yaml",
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
    result = load_yaml_safe(registry_file)
    if result.error:
        return [{"_load_error": result.error, "_registry_file": rel(registry_file, root)}]
    data = result.data
    artifacts = data.get("artifacts", []) if isinstance(data, dict) else []
    result_artifacts = [item for item in artifacts if isinstance(item, dict)]
    for item in result_artifacts:
        item["_registry_file"] = rel(registry_file, root)
    return result_artifacts


def _load_memory_stores(root: Path) -> list[dict[str, Any]]:
    memory_file = root / ".agent" / "memory-stores.yaml"
    if not memory_file.exists():
        return []
    result = load_yaml_safe(memory_file)
    if result.error:
        return [{"_load_error": result.error, "_memory_file": rel(memory_file, root)}]
    data = result.data
    stores = data.get("memory_stores", []) if isinstance(data, dict) else []
    result = [item for item in stores if isinstance(item, dict)]
    for item in result:
        item["_memory_file"] = rel(memory_file, root)
    return result


def _load_memory_records(root: Path) -> list[dict[str, Any]]:
    memory_file = root / ".agent" / "memory-records.yaml"
    if not memory_file.exists():
        return []
    result = load_yaml_safe(memory_file)
    if result.error:
        return [{"_load_error": result.error, "_memory_file": rel(memory_file, root)}]
    data = result.data
    records = data.get("memory_records", []) if isinstance(data, dict) else []
    result = [item for item in records if isinstance(item, dict)]
    for item in result:
        item["_memory_file"] = rel(memory_file, root)
    return result


def _load_skill_manifest(manifest_path: Path, root: Path) -> dict[str, Any]:
    result = load_yaml_safe(manifest_path)
    if result.error:
        manifest = {"_load_error": result.error}
    elif isinstance(result.data, dict):
        manifest = result.data
    else:
        manifest = {
            "_schema_data": result.data,
            "_schema_error": "Skill manifest must be a YAML mapping.",
        }
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
    result = load_yaml_safe(module_path)
    if result.error:
        module = {"_load_error": result.error}
    elif isinstance(result.data, dict):
        module = result.data
    else:
        module = {
            "_schema_data": result.data,
            "_schema_error": "Context module must be a YAML mapping.",
        }
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


def _detect_memory_locations(root: Path) -> list[dict[str, Any]]:
    candidates = [
        ("claude_project_memory", "CLAUDE.md"),
        ("cursor_rules", ".cursor/rules"),
        ("continue_rules", ".continue/rules"),
        ("windsurf_rules", ".windsurf/rules"),
        ("codex_project_memories", ".codex/memories"),
        ("claude_local_state", ".claude"),
        ("agent_memory_bank", "memory-bank"),
    ]
    detected: list[dict[str, Any]] = []
    for kind, relative_path in candidates:
        path = root / relative_path
        if path.exists():
            detected.append(
                {
                    "kind": kind,
                    "path": relative_path,
                    "is_dir": path.is_dir(),
                    "file_count": len(list_files(path)) if path.is_dir() else 1,
                }
            )
    return detected


def collect_inventory(root: Path | str) -> WorkspaceInventory:
    resolved_root = Path(root).resolve()
    workspace, paths, save_paths, naming = _load_workspace(resolved_root)
    entries = _path_entries(resolved_root, paths)
    artifacts = _load_registry(resolved_root)
    memory_stores = _load_memory_stores(resolved_root)
    memory_records = _load_memory_records(resolved_root)
    return WorkspaceInventory(
        root=resolved_root,
        workspace=workspace,
        paths=paths,
        save_paths=save_paths,
        naming=naming,
        files=_file_status(resolved_root),
        directories=_directory_status(resolved_root, entries),
        artifacts=artifacts,
        skills=_find_skills(resolved_root, entries),
        context_modules=_find_context_modules(resolved_root, entries),
        memory_stores=memory_stores,
        memory_records=memory_records,
        detected_memory_locations=_detect_memory_locations(resolved_root),
        unregistered_agent_files=_unregistered_agent_files(resolved_root, artifacts),
    )
