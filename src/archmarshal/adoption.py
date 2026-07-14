from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .io import load_yaml_safe
from .safety import create_backup, create_text_exclusive, files_for_full_backup


SKILL_ROOT_CANDIDATES = (
    ".agents",
    ".codex/skills",
    ".claude/skills",
    "skills",
)
MANAGED_DIRECTORIES = (
    ".agent/archive",
    ".agent/backups",
    ".agent/cache",
    ".agent/context-modules",
    ".agent/history",
    ".agent/inbox",
    ".agent/knowledge",
    ".agent/plans",
    ".agent/reports",
    ".agent/skill-overlays/global",
    ".agent/skill-overlays/functional",
    ".agent/skill-overlays/common-project",
    ".agent/skill-overlays/project",
    ".agent/skill-overlays/generated",
)
RESERVED_FILES = (
    "AGENTS.md",
    ".agent/workspace.yaml",
    ".agent/INDEX.md",
    ".agent/registry.yaml",
    ".agent/memory-stores.yaml",
    ".agent/memory-records.yaml",
)


def plan_adoption(
    root: Path | str,
    *,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    built = _build_adoption(root_path, tags or [], backup_scope)
    return _public_plan(built, applied=False)


def adopt_workspace(
    root: Path | str,
    *,
    apply: bool = False,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    built = _build_adoption(root_path, tags or [], backup_scope)
    if not apply:
        return _public_plan(built, applied=False)
    if built["blocked"]:
        payload = _public_plan(built, applied=False)
        payload["mode"] = "blocked"
        return payload
    if not built["writes"]:
        payload = _public_plan(built, applied=True)
        payload["mode"] = "already_managed"
        payload["backup"] = None
        return payload

    for target in built["writes"]:
        if target.exists():
            payload = _public_plan(built, applied=False)
            payload["mode"] = "blocked"
            payload["conflicts"] = sorted(
                set(payload["conflicts"] + [target.relative_to(root_path).as_posix()])
            )
            payload["notes"].append("A target appeared after planning; no managed files were written.")
            return payload

    backup_dir = root_path / ".agent" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = create_backup(
        root_path,
        built["backup_files"],
        backup_dir / f"{built['timestamp']}-pre-adoption.zip",
        reason="ArchMarshal adoption before adding a non-destructive management overlay.",
    )

    created: list[Path] = []
    try:
        for relative in MANAGED_DIRECTORIES:
            (root_path / relative).mkdir(parents=True, exist_ok=True)
        for target, content in built["writes"].items():
            create_text_exclusive(target, content)
            created.append(target)
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise

    payload = _public_plan(built, applied=True)
    payload["mode"] = "overlay_applied"
    payload["backup"] = backup
    payload["created"] = [path.relative_to(root_path).as_posix() for path in created]
    payload["safety_guarantees"] = [
        "No existing file was overwritten.",
        "No existing skill or project file was moved, renamed, or deleted.",
        "Skill metadata was written only under .agent/skill-overlays.",
        "The pre-adoption snapshot was verified before managed files were created.",
    ]
    return payload


def _build_adoption(root: Path, tags: list[str], backup_scope: str) -> dict[str, Any]:
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace root is not a directory: {root}")
    if backup_scope not in {"managed", "full"}:
        raise ValueError("backup_scope must be 'managed' or 'full'")

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    workspace_file = root / ".agent" / "workspace.yaml"
    configured = _is_archmarshal_workspace(workspace_file)
    reserved_conflicts = [
        relative
        for relative in RESERVED_FILES[1:]
        if (root / relative).exists() and not configured
    ]
    skills = _discover_skills(root)
    normalized_tags = _normalize_tags(tags) or ["archmarshal"]
    writes: dict[Path, str] = {}

    if not workspace_file.exists():
        writes[workspace_file] = _workspace_yaml(root, normalized_tags, now)
        index = root / ".agent" / "INDEX.md"
        registry = root / ".agent" / "registry.yaml"
        memory_stores = root / ".agent" / "memory-stores.yaml"
        memory_records = root / ".agent" / "memory-records.yaml"
        backup_ignore = root / ".agent" / "backups" / ".gitignore"
        if not index.exists():
            writes[index] = _index_markdown(root, skills, normalized_tags, now)
        if not registry.exists():
            writes[registry] = _registry_yaml(skills)
        if not memory_stores.exists():
            writes[memory_stores] = yaml.safe_dump(
                {"memory_stores": []}, sort_keys=False, allow_unicode=True
            )
        if not memory_records.exists():
            writes[memory_records] = yaml.safe_dump(
                {"memory_records": []}, sort_keys=False, allow_unicode=True
            )
        if not backup_ignore.exists():
            writes[backup_ignore] = "*\n!.gitignore\n"
        agents = root / "AGENTS.md"
        if not agents.exists():
            writes[agents] = _agents_markdown()
        for skill in skills:
            overlay = root / skill["overlay_manifest"]
            if not overlay.exists():
                writes[overlay] = yaml.safe_dump(
                    skill["manifest"], sort_keys=False, allow_unicode=True
                )

    blocked = bool(reserved_conflicts)
    if configured:
        writes = {}
    elif blocked:
        writes = {}

    if backup_scope == "full":
        backup_files = files_for_full_backup(root)
    else:
        backup_files = _managed_backup_files(root, skills)

    return {
        "root": root,
        "timestamp": timestamp,
        "configured": configured,
        "backup_scope": backup_scope,
        "tags": normalized_tags,
        "skills": skills,
        "writes": writes,
        "backup_files": backup_files,
        "conflicts": reserved_conflicts,
        "blocked": blocked,
    }


def _public_plan(built: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    root: Path = built["root"]
    operations = [
        {
            "action": "create",
            "path": path.relative_to(root).as_posix(),
            "overwrite": False,
        }
        for path in built["writes"]
    ]
    return {
        "tool": "archmarshal",
        "stage": "adopt",
        "root": str(root),
        "mode": "applied" if applied else "propose_only",
        "configured": built["configured"],
        "blocked": built["blocked"],
        "backup_scope": built["backup_scope"],
        "project_tags": built["tags"],
        "discovered_skills": [
            {
                "source": skill["source"],
                "source_manifest": skill["source_manifest"],
                "kind": skill["manifest"]["kind"],
                "overlay_manifest": skill["overlay_manifest"],
                "source_will_change": False,
            }
            for skill in built["skills"]
        ],
        "operations": operations,
        "conflicts": built["conflicts"],
        "notes": [
            "Preview is the default; pass --apply to create the listed files.",
            "Existing files are never overwritten, even with --apply.",
            "Existing skills stay in place; overlays provide routing metadata without changing SKILL.md.",
            "A verified backup is created before the first managed file is added.",
        ],
    }


def _discover_skills(root: Path) -> list[dict[str, Any]]:
    skill_docs: set[Path] = set()
    root_skill = root / "SKILL.md"
    if root_skill.is_file():
        skill_docs.add(root_skill)
    for relative in SKILL_ROOT_CANDIDATES:
        candidate = root / relative
        if not candidate.exists():
            continue
        for path in candidate.rglob("SKILL.md"):
            if ".agent" not in path.relative_to(root).parts:
                skill_docs.add(path)

    skills: list[dict[str, Any]] = []
    for skill_md in sorted(skill_docs):
        source_dir = skill_md.parent
        source = source_dir.relative_to(root).as_posix()
        frontmatter = _skill_frontmatter(skill_md)
        name = _slug(str(frontmatter.get("name") or source_dir.name))
        description = str(frontmatter.get("description") or _first_summary(skill_md) or name)
        kind, scope, overlay_group = _classify_skill(source)
        identity_suffix = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
        skill_id = f"skill.{scope}.{name}-{identity_suffix}"
        overlay_manifest = f".agent/skill-overlays/{overlay_group}/{name}-{identity_suffix}/manifest.yaml"
        source_manifest = source_dir / "manifest.yaml"
        tags = sorted(set([scope.replace("_", "-"), *[item for item in name.split("-") if item]]))
        manifest: dict[str, Any] = {
            "id": skill_id,
            "name": name,
            "kind": kind,
            "version": "0.1.0",
            "status": "active",
            "priority": "highest" if kind == "global_skill" else "normal",
            "scope": scope,
            "summary": description[:500],
            "tags": tags or ["project"],
            "triggers": [name.replace("-", " ")],
            "negative_triggers": [f"tasks unrelated to {name.replace('-', ' ')}"],
            "source": {
                "skill_dir": source,
                "skill_md": skill_md.relative_to(root).as_posix(),
                "original_manifest": (
                    source_manifest.relative_to(root).as_posix() if source_manifest.exists() else None
                ),
                "managed": False,
                "mutation_policy": "never",
            },
        }
        if kind == "common_project_skill":
            local = {
                key: (source_dir / key).is_dir() and any((source_dir / key).iterdir())
                for key in ("scripts", "templates", "references")
            }
            manifest["reproducibility"] = {
                "required": True,
                "scripts_local": local["scripts"],
                "templates_local": local["templates"],
                "references_local": local["references"],
            }
            manifest["paths"] = {
                key: key for key, present in local.items() if present
            }
        skills.append(
            {
                "source": source,
                "source_manifest": (
                    source_manifest.relative_to(root).as_posix() if source_manifest.exists() else None
                ),
                "overlay_manifest": overlay_manifest,
                "manifest": manifest,
            }
        )
    return skills


def _classify_skill(source: str) -> tuple[str, str, str]:
    normalized = source.lower().replace("_", "-")
    if "/global/" in f"/{normalized}/":
        return "global_skill", "global", "global"
    if "common-project" in normalized or "common/project" in normalized:
        return "common_project_skill", "common_project", "common-project"
    if "/functional/" in f"/{normalized}/":
        return "functional_skill", "functional", "functional"
    if "/generated/" in f"/{normalized}/":
        return "generated_project_skill", "generated", "generated"
    return "project_skill", "project", "project"


def _skill_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")[:65536]
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _first_summary(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    in_frontmatter = bool(lines and lines[0].strip() == "---")
    for line in lines[1:] if in_frontmatter else lines:
        stripped = line.strip()
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _workspace_yaml(
    root: Path,
    tags: list[str],
    now: datetime,
) -> str:
    created_on = _git_creation_date(root) or now.date().isoformat()
    code_roots = [name for name in ("src", "app", "packages", "lib") if (root / name).exists()]
    data = {
        "workspace": {
            "name": _slug(root.name),
            "version": "0.1.0",
            "description": "Project adopted through a non-destructive ArchMarshal overlay.",
            "created_on": created_on,
            "adopted_on": now.date().isoformat(),
            "tags": tags,
            "management_mode": "overlay",
        },
        "save_paths": {
            "skills": {
                "generated": ".agents/skills/generated",
                "project": ".agents/skills/project",
            },
            "project_files": {
                "checkpoints": ".agent/inbox/checkpoints",
                "reports": ".agent/reports",
                "plans": ".agent/plans",
                "history": ".agent/history",
                "knowledge": ".agent/knowledge",
                "artifacts": ".agent/inbox",
            },
        },
        "naming": {
            "project_files": {
                "strategy": "time_topic_kind",
                "timezone": "UTC",
                "timestamp_format": "%Y%m%d-%H%M%S",
                "max_slug_words": 6,
            }
        },
        "paths": {
            "project_root": ".",
            "code_roots": code_roots,
            "agent_root": ".agent",
            "global_skills": [".agent/skill-overlays/global"],
            "functional_skills": [".agent/skill-overlays/functional"],
            "common_project_skills": [".agent/skill-overlays/common-project"],
            "project_skills": [".agent/skill-overlays/project"],
            "generated_skills": [".agent/skill-overlays/generated"],
            "knowledge": [".agent/knowledge"],
            "context_modules": [".agent/context-modules"],
            "reports": [".agent/reports"],
            "plans": [".agent/plans"],
            "history": [".agent/history"],
            "archive": [".agent/archive"],
            "cache": [".agent/cache"],
            "inbox": [".agent/inbox"],
        },
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _registry_yaml(skills: list[dict[str, Any]]) -> str:
    artifacts: list[dict[str, Any]] = [
        _artifact("project.index", "project_doc", ".agent/INDEX.md", "default", ["index"]),
        _artifact("managed.history", "history", ".agent/history", "explicit_only", ["history"]),
        _artifact("managed.reports", "report", ".agent/reports", "explicit_only", ["reports"]),
        _artifact("managed.plans", "plan", ".agent/plans", "explicit_only", ["plans"]),
        _artifact("managed.inbox", "artifact", ".agent/inbox", "explicit_only", ["inbox"]),
        _artifact("managed.backups", "artifact", ".agent/backups", "never_default", ["backup"]),
        _artifact("managed.archive", "artifact", ".agent/archive", "never_default", ["archive"]),
        _artifact("managed.cache", "cache", ".agent/cache", "never_default", ["cache"]),
        _artifact("managed.knowledge", "knowledge", ".agent/knowledge", "task_based", ["knowledge"]),
        _artifact(
            "managed.skill-overlays",
            "config",
            ".agent/skill-overlays",
            "task_based",
            ["skills", "metadata"],
        ),
    ]
    for skill in skills:
        manifest = skill["manifest"]
        artifacts.append(
            {
                **_artifact(
                    manifest["id"].replace("skill.", "skill-source.", 1),
                    "generated_skill" if manifest["kind"] == "generated_project_skill" else "skill",
                    skill["source"],
                    "when_task_matches",
                    manifest["tags"],
                ),
                "overlay_manifest": skill["overlay_manifest"],
                "mutation_policy": "never",
            }
        )
    return yaml.safe_dump({"artifacts": artifacts}, sort_keys=False, allow_unicode=True)


def _artifact(
    artifact_id: str,
    kind: str,
    path: str,
    read_policy: str,
    tags: list[str],
) -> dict[str, Any]:
    return {
        "id": artifact_id,
        "kind": kind,
        "path": path,
        "status": "active",
        "read_policy": read_policy,
        "update_policy": "agent_propose_diff",
        "source_of_truth": path != ".agent/skill-overlays",
        "owner": "human",
        "tags": tags,
    }


def _index_markdown(
    root: Path,
    skills: list[dict[str, Any]],
    tags: list[str],
    now: datetime,
) -> str:
    skill_lines = [
        f"- `{skill['manifest']['name']}` ({skill['manifest']['kind']}): "
        f"source `{skill['source']}`, overlay `{skill['overlay_manifest']}`"
        for skill in skills
    ] or ["- No existing skills were discovered during adoption."]
    return "\n".join(
        [
            f"# {root.name} · ArchMarshal Index",
            "",
            f"- Adopted: {now.date().isoformat()}",
            f"- Tags: {', '.join(tags)}",
            "- Management mode: non-destructive overlay",
            "",
            "## Active project map",
            "",
            "- Knowledge: `.agent/knowledge/`",
            "- Plans: `.agent/plans/`",
            "- Reports: `.agent/reports/`",
            "- Date-organized history: `.agent/history/YYYY/MM/DD/`",
            "- Review inbox: `.agent/inbox/`",
            "- Verified backups: `.agent/backups/` (never loaded by default)",
            "",
            "## Existing skills (sources remain untouched)",
            "",
            *skill_lines,
            "",
            "## Safety boundary",
            "",
            "ArchMarshal metadata may describe existing files, but it must not move, rename,",
            "delete, or overwrite them. Skill overlays are routing metadata, not replacements.",
            "",
        ]
    )


def _agents_markdown() -> str:
    return (
        "# Agent Instructions\n\n"
        "Read `.agent/INDEX.md` as the project map. Existing project files and skills are "
        "human-owned: do not move, rename, delete, or overwrite them during ArchMarshal "
        "organization. Read reports, history, archive, cache, and backups only when explicitly "
        "needed. Skill overlays under `.agent/skill-overlays/` provide metadata and never "
        "replace the original `SKILL.md`.\n"
    )


def _managed_backup_files(root: Path, skills: list[dict[str, Any]]) -> list[Path]:
    files: set[Path] = set()
    for relative in RESERVED_FILES:
        candidate = root / relative
        if candidate.is_file():
            files.add(candidate)
    agent_root = root / ".agent"
    if agent_root.exists():
        files.update(path for path in agent_root.rglob("*") if path.is_file())
    for skill in skills:
        source_dir = root / skill["source"]
        for name in ("SKILL.md", "manifest.yaml"):
            candidate = source_dir / name
            if candidate.is_file():
                files.add(candidate)
    return sorted(files)


def _is_archmarshal_workspace(path: Path) -> bool:
    if not path.exists():
        return False
    result = load_yaml_safe(path)
    if result.error or not isinstance(result.data, dict):
        return False
    workspace = result.data.get("workspace")
    paths = result.data.get("paths")
    return isinstance(workspace, dict) and isinstance(paths, dict) and paths.get("agent_root") == ".agent"


def _git_creation_date(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "--reverse", "--format=%aI", "-1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value[:10] if result.returncode == 0 and len(value) >= 10 else None


def _normalize_tags(tags: list[str]) -> list[str]:
    return sorted({item for tag in tags if (item := _slug(tag))})


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "project"


__all__ = ["adopt_workspace", "plan_adoption"]
