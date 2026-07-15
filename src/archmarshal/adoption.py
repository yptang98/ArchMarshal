from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .adoption_tx import adoption_transaction_status, apply_adoption_transaction
from .errors import ArchMarshalError, require_workspace_root
from .io import load_yaml_safe
from .ownership import ownership_skill_index_mode, valid_ownership_marker, workspace_id
from .safety import (
    create_backup,
    ensure_managed_path,
    ensure_path_within,
    files_below_no_links,
    files_for_full_backup,
    fingerprint_directory,
    is_link_or_reparse,
    sha256_file,
)
from .skill_index import (
    disabled_skill_index_plan,
    plan_skill_index,
    public_skill_index_plan,
)
from .skill_validation import validate_skill_package
from .workspace_lock import WorkspaceMutationLock, workspace_mutation_lock

SKILL_ROOT_CANDIDATES = (
    ".agents",
    ".codex/skills",
    ".claude/skills",
    "skills",
)
RESERVED_FILES = (
    "AGENTS.md",
    ".agent/ownership.json",
    ".agent/workspace.yaml",
    ".agent/INDEX.md",
    ".agent/registry.yaml",
    ".agent/memory-stores.yaml",
    ".agent/memory-records.yaml",
)
MANAGED_PLACEHOLDERS = (
    ".agent/archive/.gitkeep",
    ".agent/cache/.gitkeep",
    ".agent/context-modules/.gitkeep",
    ".agent/history/.gitkeep",
    ".agent/inbox/.gitkeep",
    ".agent/knowledge/.gitkeep",
    ".agent/plans/.gitkeep",
    ".agent/reports/.gitkeep",
)


def plan_adoption(
    root: Path | str,
    *,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    built = _build_adoption(root_path, tags or [], backup_scope)
    return _public_plan(built, applied=False)


def adopt_workspace(
    root: Path | str,
    *,
    apply: bool = False,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
    expected_plan: str | None = None,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    if apply:
        with workspace_mutation_lock(root_path, operation="adoption") as held:
            return _adopt_workspace_locked(
                root_path,
                apply=True,
                tags=tags,
                backup_scope=backup_scope,
                expected_plan=expected_plan,
                held=held,
            )
    return _adopt_workspace_locked(
        root_path,
        apply=False,
        tags=tags,
        backup_scope=backup_scope,
        expected_plan=expected_plan,
        held=None,
    )


def _adopt_workspace_locked(
    root_path: Path,
    *,
    apply: bool,
    tags: list[str] | None,
    backup_scope: str,
    expected_plan: str | None,
    held: WorkspaceMutationLock | None,
) -> dict[str, Any]:
    built = _build_adoption(root_path, tags or [], backup_scope)
    if not apply:
        return _public_plan(built, applied=False)
    if built["blocked"]:
        payload = _public_plan(built, applied=False)
        payload["mode"] = "blocked"
        return payload
    if not built["writes"] and not built["skill_index_plan"]["changed"]:
        payload = _public_plan(built, applied=True)
        payload["mode"] = "review_required" if built["review_required"] else "already_managed"
        payload["backup"] = None
        return payload

    plan_digest = _adoption_plan_digest(built)
    if expected_plan is None:
        payload = _public_plan(built, applied=False)
        payload["mode"] = "review_required"
        payload["notes"].append(
            "Apply requires expected_plan from this exact preview; no managed files were written."
        )
        return payload
    if expected_plan != plan_digest:
        payload = _public_plan(built, applied=False)
        payload["mode"] = "blocked"
        payload["conflicts"] = sorted(set(payload["conflicts"] + ["plan_digest_changed"]))
        payload["expected_plan"] = expected_plan
        payload["actual_plan"] = plan_digest
        payload["notes"].append("The reviewed adoption plan no longer matches current state.")
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

    if held is not None:
        held.verify()

    backup_dir = root_path / ".agent" / "backups"
    backup = create_backup(
        root_path,
        built["backup_files"],
        backup_dir / f"{built['timestamp']}-pre-adoption.zip",
        reason="ArchMarshal adoption before adding a non-destructive management overlay.",
    )
    if held is not None:
        held.verify()

    revalidated = (
        disabled_skill_index_plan()
        if built["skill_index_plan"].get("disabled")
        else plan_skill_index(
            root_path,
            _discover_skills(root_path),
            created_at=(built["skill_index_plan"].get("generation") or {}).get(
                "created_at"
            ),
        )
    )
    if _logical_skill_plan(revalidated) != _logical_skill_plan(built["skill_index_plan"]):
        raise ArchMarshalError(
            "skill_index_stale_plan",
            "Skill sources or HEAD changed after backup; no adoption targets were published.",
        )
    if held is not None:
        held.verify()
    transaction = apply_adoption_transaction(
        root_path,
        plan_digest=plan_digest,
        writes={
            target.relative_to(root_path).as_posix(): content.encode("utf-8")
            for target, content in built["writes"].items()
        },
        skill_index_plan=revalidated,
        backup=backup,
    )
    if held is not None:
        held.verify()

    payload = _public_plan(built, applied=True)
    payload["mode"] = "overlay_synced" if built["configured"] else "overlay_applied"
    payload["backup"] = backup
    payload["transaction"] = transaction
    payload["skill_index_commit"] = transaction["skill_index_commit"]
    payload["created"] = transaction["created"]
    payload["safety_guarantees"] = [
        "No existing user-owned file was overwritten.",
        "No existing skill or project file was moved, renamed, or deleted.",
        "Skill metadata was written only under .agent/skill-overlays.",
        "The pre-adoption snapshot was verified before managed files were created.",
        "Skill generations are immutable; only the internal HEAD pointer is atomically updated.",
        "Multi-file adoption is recoverable from a durable create-only transaction journal.",
        "Published transaction files are never automatically deleted during recovery.",
    ]
    return payload


def _build_adoption(root: Path, tags: list[str], backup_scope: str) -> dict[str, Any]:
    if backup_scope not in {"managed", "full"}:
        raise ValueError("backup_scope must be 'managed' or 'full'")

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    workspace_file = root / ".agent" / "workspace.yaml"
    ensure_managed_path(root, root / ".agent", purpose="ArchMarshal control directory")
    configured = _is_archmarshal_workspace(workspace_file)
    overlay_enabled = _workspace_uses_overlays(workspace_file) if configured else True
    transaction_status = adoption_transaction_status(root)
    reserved_conflicts = [
        relative
        for relative in RESERVED_FILES[1:]
        if (root / relative).exists() and not configured
    ]
    skills = _discover_skills(root)
    manage_index = not configured or overlay_enabled
    skill_index_plan = (
        plan_skill_index(root, skills) if manage_index else disabled_skill_index_plan()
    )
    indexed_review_required = any(
        change.get("kind") in {"modified", "removed", "restored"}
        for change in skill_index_plan["changes"]
    )
    legacy_review_required = skill_index_plan.get("expected_head") is None and any(
        skill["source_drift"] not in {"new", "unchanged"} for skill in skills
    )
    overlay_conflicts = [
        skill["overlay_manifest"]
        for skill in skills
        if not configured
        and (root / skill["overlay_manifest"]).exists()
        and skill["source_drift"] != "unchanged"
    ]
    reserved_conflicts.extend(overlay_conflicts)
    transaction_active = transaction_status.get("state") != "none"
    review_required = indexed_review_required or legacy_review_required or transaction_active
    normalized_tags = _normalize_tags(tags) or ["archmarshal"]
    writes: dict[Path, str] = {}

    if not workspace_file.exists():
        writes[workspace_file] = _workspace_yaml(root, normalized_tags, now)
    ownership = root / ".agent" / "ownership.json"
    ownership_index_mode = ownership_skill_index_mode(ownership)
    index = root / ".agent" / "INDEX.md"
    registry = root / ".agent" / "registry.yaml"
    memory_stores = root / ".agent" / "memory-stores.yaml"
    memory_records = root / ".agent" / "memory-records.yaml"
    backup_ignore = root / ".agent" / "backups" / ".gitignore"
    agents = root / "AGENTS.md"
    if not ownership.exists():
        writes[ownership] = _ownership_json(root, index_required=overlay_enabled)
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
    if not agents.exists():
        writes[agents] = _agents_markdown()
    for relative in MANAGED_PLACEHOLDERS:
        placeholder = root / relative
        if not placeholder.exists():
            writes[placeholder] = ""
    if overlay_enabled:
        for skill in skills:
            overlay = root / skill["overlay_manifest"]
            if not overlay.exists():
                writes[overlay] = yaml.safe_dump(
                    skill["manifest"], sort_keys=False, allow_unicode=True
                )

    ownership_mode_conflict = configured and ownership_index_mode is not None and (
        (overlay_enabled and ownership_index_mode != "required")
        or (not overlay_enabled and ownership_index_mode != "disabled")
    )
    if ownership_mode_conflict:
        reserved_conflicts.append(".agent/ownership.json#skill_index")
    required_index_missing = (
        configured
        and ownership_index_mode == "required"
        and skill_index_plan.get("expected_head") is None
    )
    if required_index_missing:
        reserved_conflicts.append(".agent/skill-overlays/.archmarshal/HEAD")

    blocked = bool(reserved_conflicts) or transaction_active
    if blocked:
        writes = {}

    if backup_scope == "full":
        backup_files = files_for_full_backup(root)
    else:
        backup_files = _managed_backup_files(root, skills)

    for target in writes:
        ensure_managed_path(root, target, purpose="Adoption target")

    return {
        "root": root,
        "timestamp": timestamp,
        "configured": configured,
        "overlay_enabled": overlay_enabled,
        "backup_scope": backup_scope,
        "tags": normalized_tags,
        "skills": skills,
        "review_required": review_required,
        "skill_index_plan": skill_index_plan,
        "writes": writes,
        "backup_files": backup_files,
        "conflicts": reserved_conflicts,
        "blocked": blocked,
        "transaction_status": transaction_status,
    }


def _public_plan(built: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    root: Path = built["root"]
    operations = [
        {
            "action": "create",
            "path": path.relative_to(root).as_posix(),
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "overwrite": False,
        }
        for path, content in built["writes"].items()
    ]
    tracked_changes = {
        str(change.get("source")): str(change.get("kind"))
        for change in built["skill_index_plan"].get("changes", [])
        if change.get("kind") in {"modified", "restored"}
    }
    legacy_review = built["skill_index_plan"].get("expected_head") is None
    operations.extend(
        {
            "action": "review_source_change",
            "path": skill["overlay_manifest"],
            "source": skill["source"],
            "overwrite": False,
        }
        for skill in built["skills"]
        if skill["source"] in tracked_changes
        or (legacy_review and skill["source_drift"] not in {"new", "unchanged"})
    )
    index_plan = public_skill_index_plan(built["skill_index_plan"])
    if index_plan["changed"]:
        operations.extend(
            [
                {
                    "action": "create_immutable_generation",
                    "path": index_plan["object_path"],
                    "overwrite": False,
                },
                {
                    "action": "compare_and_swap_internal_head",
                    "path": ".agent/skill-overlays/.archmarshal/HEAD",
                    "expected": index_plan["expected_head"],
                    "value": index_plan["proposed_head"],
                    "user_owned": False,
                },
            ]
        )
    return {
        "tool": "archmarshal",
        "stage": "sync" if built["configured"] else "adopt",
        "root": str(root),
        "mode": "applied" if applied else "propose_only",
        "configured": built["configured"],
        "overlay_enabled": built["overlay_enabled"],
        "blocked": built["blocked"],
        "review_required": built["review_required"],
        "plan_digest": _adoption_plan_digest(built),
        "apply_precondition": "--expect-plan <plan_digest>",
        "transaction": built["transaction_status"],
        "skill_index": index_plan,
        "backup_scope": built["backup_scope"],
        "project_tags": built["tags"],
        "discovered_skills": [
            {
                "source": skill["source"],
                "source_manifest": skill["source_manifest"],
                "kind": skill["manifest"]["kind"],
                "overlay_manifest": skill["overlay_manifest"],
                "source_will_change": False,
                "source_drift": skill["source_drift"],
                "tracking_state": tracked_changes.get(
                    skill["source"],
                    "indexed"
                    if index_plan["enabled"] and index_plan["expected_head"]
                    else skill["source_drift"],
                ),
            }
            for skill in built["skills"]
        ],
        "operations": operations,
        "conflicts": built["conflicts"],
        "notes": [
            "Preview is the default; apply requires --expect-plan with this exact plan digest.",
            "Existing user-owned files are never overwritten, even with --apply.",
            "Only ArchMarshal's internal HEAD pointer may be atomically replaced after backup and CAS validation.",
            "Existing skills stay in place; overlays provide routing metadata without changing SKILL.md.",
            "A verified backup is created before the first managed file is added.",
            (
                "Configured overlay projects are incrementally scanned for newly added source skills."
                if built["configured"] and built["overlay_enabled"]
                else "Native workspaces keep their declared skill layout and are not converted implicitly."
            ),
        ],
    }


def _adoption_plan_digest(built: dict[str, Any]) -> str:
    root: Path = built["root"]
    intent = {
        "format": "archmarshal-adoption-plan-v1",
        "backup_scope": built["backup_scope"],
        "configured": built["configured"],
        "overlay_enabled": built["overlay_enabled"],
        "tags": built["tags"],
        "writes": [
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(content.encode("utf-8")),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            for path, content in sorted(
                built["writes"].items(), key=lambda item: item[0].as_posix()
            )
        ],
        "skill_index": _logical_skill_plan(built["skill_index_plan"]),
    }
    canonical = json.dumps(
        intent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _logical_skill_plan(plan: dict[str, Any]) -> dict[str, Any]:
    generation = plan.get("generation")
    return {
        "changed": bool(plan.get("changed")),
        "disabled": bool(plan.get("disabled")),
        "expected_head": plan.get("expected_head"),
        "skills": generation.get("skills") if isinstance(generation, dict) else [],
        "changes": plan.get("changes") or [],
        "source_precondition_policy": plan.get("source_precondition_policy"),
        "source_preconditions": plan.get("source_preconditions") or [],
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
        for path in files_below_no_links(candidate, purpose="Skill discovery"):
            if path.name != "SKILL.md":
                continue
            if ".agent" not in path.relative_to(root).parts:
                skill_docs.add(path)

    skills: list[dict[str, Any]] = []
    for skill_md in sorted(skill_docs):
        ensure_path_within(root, skill_md, purpose="Discovered skill source")
        source_dir = skill_md.parent
        source = source_dir.relative_to(root).as_posix()
        frontmatter = _skill_frontmatter(skill_md)
        source_manifest = source_dir / "manifest.yaml"
        declared, import_errors = _import_source_manifest(source_manifest)
        display_name = str(
            declared.get("name") or frontmatter.get("name") or source_dir.name
        ).strip() or source_dir.name
        name_slug = _slug(display_name)
        description = str(
            declared.get("summary")
            or frontmatter.get("description")
            or _first_summary(skill_md)
            or display_name
        )
        inferred_kind, inferred_scope, _ = _classify_skill(source)
        kind = str(declared.get("kind") or inferred_kind)
        scope = str(declared.get("scope") or inferred_scope)
        overlay_group = _overlay_group(kind, scope)
        identity_suffix = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
        skill_id = str(
            declared.get("id") or f"skill.{scope}.{name_slug}-{identity_suffix}"
        )
        overlay_manifest = (
            f".agent/skill-overlays/{overlay_group}/{name_slug}-{identity_suffix}/manifest.yaml"
        )
        inferred_tags = sorted(
            {
                scope.replace("_", "-"),
                *[item for item in name_slug.split("-") if item],
            }
        )
        tags = declared.get("tags") or inferred_tags
        source_hash = sha256_file(skill_md)
        package = fingerprint_directory(
            source_dir,
            purpose="Skill package",
            entrypoint_only=source_dir.resolve() == root,
        )
        validation = validate_skill_package(
            source_dir,
            enforce_folder_name=source_dir.resolve() != root,
        )
        source_drift = _overlay_drift(
            root / overlay_manifest,
            entrypoint_hash=source_hash,
            package_hash=package["sha256"],
        )
        manifest: dict[str, Any] = {
            "id": skill_id,
            "name": display_name,
            "kind": kind,
            "version": declared.get("version") or "0.1.0",
            "status": (
                "disabled"
                if import_errors or not validation["valid"]
                else declared.get("status") or "active"
            ),
            "priority": declared.get("priority")
            or ("highest" if kind == "global_skill" else "normal"),
            "scope": scope,
            "summary": description[:500],
            "tags": tags or ["project"],
            "triggers": declared.get("triggers") or [display_name.replace("-", " ")],
            "negative_triggers": declared.get("negative_triggers")
            or [f"tasks unrelated to {display_name.replace('-', ' ')}"],
            "review_state": "needs_review",
            "validation": validation,
            "metadata_provenance": {
                "source_manifest": source_manifest.relative_to(root).as_posix()
                if source_manifest.exists()
                else None,
                "imported_fields": sorted(declared),
                "inferred_fields": sorted(
                    {
                        "id",
                        "kind",
                        "scope",
                        "tags",
                        "triggers",
                        "negative_triggers",
                        "priority",
                        "version",
                    }
                    - set(declared)
                ),
                "errors": import_errors,
                "review_required": True,
                "source_trust": "untrusted_existing_project",
            },
            "source": {
                "skill_dir": source,
                "skill_md": skill_md.relative_to(root).as_posix(),
                "skill_sha256": source_hash,
                "package_sha256": package["sha256"],
                "package_file_count": package["file_count"],
                "package_content_bytes": package["content_bytes"],
                "original_manifest": (
                    source_manifest.relative_to(root).as_posix() if source_manifest.exists() else None
                ),
                "managed": False,
                "mutation_policy": "never",
            },
        }
        for field in ("dependencies", "outputs", "permissions", "reproducibility", "paths"):
            if field in declared:
                manifest[field] = declared[field]
        if kind == "common_project_skill" and "reproducibility" not in manifest:
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
                "source_drift": source_drift,
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


def _overlay_group(kind: str, scope: str) -> str:
    if kind == "global_skill" or scope == "global":
        return "global"
    if kind == "functional_skill" or scope == "functional":
        return "functional"
    if kind == "common_project_skill" or scope == "common_project":
        return "common-project"
    if kind == "generated_project_skill" or scope == "generated":
        return "generated"
    return "project"


def _import_source_manifest(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, []
    result = load_yaml_safe(path)
    if result.error or not isinstance(result.data, dict):
        return {}, ["source_manifest_invalid"]
    data = result.data
    imported: dict[str, Any] = {}
    errors: list[str] = []
    string_fields = {
        "id": lambda value: bool(re.fullmatch(r"skill\.[a-z0-9_.-]+", value)),
        "name": lambda value: bool(value.strip()),
        "summary": lambda value: bool(value.strip()),
        "version": lambda value: bool(re.fullmatch(r"\d+\.\d+\.\d+", value)),
        "kind": lambda value: value
        in {
            "global_skill",
            "functional_skill",
            "common_project_skill",
            "project_skill",
            "generated_project_skill",
            "governance_skill",
        },
        "scope": lambda value: value
        in {"global", "functional", "common_project", "project", "module", "generated"},
        "status": lambda value: value
        in {"active", "disabled", "experimental", "deprecated", "archived"},
        "priority": lambda value: value in {"highest", "high", "normal", "low"},
    }
    for field, validator in string_fields.items():
        if field not in data:
            continue
        value = data[field]
        if isinstance(value, str) and validator(value):
            imported[field] = value
        else:
            errors.append(f"invalid_{field}")
    for field in ("tags", "triggers", "negative_triggers"):
        if field not in data:
            continue
        value = data[field]
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, str) and item.strip() for item in value)
        ):
            imported[field] = list(dict.fromkeys(value))
        else:
            errors.append(f"invalid_{field}")
    for field in ("dependencies", "outputs", "permissions", "reproducibility", "paths"):
        if field not in data:
            continue
        if isinstance(data[field], dict):
            imported[field] = json.loads(json.dumps(data[field], ensure_ascii=False))
        else:
            errors.append(f"invalid_{field}")
    return imported, errors


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
            "name": root.name,
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


def _workspace_id(root: Path) -> str:
    return workspace_id(root)


def _ownership_json(root: Path, *, index_required: bool) -> str:
    return json.dumps(
        {
            "format": "archmarshal-workspace-ownership-v1",
            "workspace_id": _workspace_id(root),
            "managed_root": ".",
            "skill_index": "required" if index_required else "disabled",
            "source_mutation": False,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _registry_yaml(skills: list[dict[str, Any]]) -> str:
    artifacts: list[dict[str, Any]] = [
        _artifact("project.index", "project_doc", ".agent/INDEX.md", "default", ["index"]),
        _artifact("managed.history", "history", ".agent/history", "explicit_only", ["history"]),
        _artifact("managed.reports", "report", ".agent/reports", "explicit_only", ["reports"]),
        _artifact("managed.plans", "plan", ".agent/plans", "explicit_only", ["plans"]),
        _artifact("managed.inbox", "artifact", ".agent/inbox", "explicit_only", ["inbox"]),
        _artifact("managed.backups", "artifact", ".agent/backups", "never_default", ["backup"]),
        _artifact(
            "managed.transactions",
            "artifact",
            ".agent/transactions",
            "never_default",
            ["transaction", "audit"],
        ),
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
        files.update(
            path
            for path in files_below_no_links(agent_root, purpose="Managed metadata backup")
            if path.is_file()
        )
    for skill in skills:
        source_dir = root / skill["source"]
        if source_dir.resolve() == root:
            for name in ("SKILL.md", "manifest.yaml"):
                candidate = source_dir / name
                if candidate.is_file():
                    files.add(candidate)
        else:
            files.update(
                path
                for path in files_below_no_links(
                    source_dir,
                    purpose="Source skill package backup",
                    max_files=10_000,
                )
                if path.is_file()
            )
    return sorted(files)


def _is_archmarshal_workspace(path: Path) -> bool:
    if not path.exists():
        return False
    root = path.parent.parent
    ownership = path.parent / "ownership.json"
    if ownership.exists():
        return valid_ownership_marker(ownership)
    result = load_yaml_safe(path)
    if result.error or not isinstance(result.data, dict):
        return False
    workspace = result.data.get("workspace")
    paths = result.data.get("paths")
    if (
        not isinstance(workspace, dict)
        or not isinstance(paths, dict)
        or paths.get("agent_root") != ".agent"
    ):
        return False

    # v0.7 and the shipped native examples predate ownership.json.  Migrate
    # only a complete, recognizable ArchMarshal control plane; a look-alike
    # workspace.yaml by itself must not grant ownership of reserved paths.
    required = (
        path.parent / "INDEX.md",
        path.parent / "registry.yaml",
        path.parent / "memory-stores.yaml",
        path.parent / "memory-records.yaml",
        root / "AGENTS.md",
    )
    if any(not item.is_file() or is_link_or_reparse(item) for item in required):
        return False
    registry = load_yaml_safe(path.parent / "registry.yaml")
    artifacts = registry.data.get("artifacts") if isinstance(registry.data, dict) else None
    if registry.error or not isinstance(artifacts, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("id") == "project.index"
        and item.get("path") == ".agent/INDEX.md"
        for item in artifacts
    )


def _workspace_uses_overlays(path: Path) -> bool:
    result = load_yaml_safe(path)
    if result.error or not isinstance(result.data, dict):
        return False
    workspace = result.data.get("workspace")
    if isinstance(workspace, dict) and workspace.get("management_mode") == "overlay":
        return True
    paths = result.data.get("paths")
    if not isinstance(paths, dict):
        return False
    skill_paths = [
        value
        for key in (
            "global_skills",
            "functional_skills",
            "common_project_skills",
            "project_skills",
            "generated_skills",
        )
        for value in (paths.get(key) or [])
    ]
    return any(
        str(value).replace("\\", "/").startswith(".agent/skill-overlays/")
        for value in skill_paths
    )


def _overlay_drift(path: Path, *, entrypoint_hash: str, package_hash: str) -> str:
    if not path.exists():
        return "new"
    result = load_yaml_safe(path)
    if result.error or not isinstance(result.data, dict):
        return "metadata_invalid"
    source = result.data.get("source")
    if not isinstance(source, dict):
        return "untracked"
    recorded_package = source.get("package_sha256")
    if recorded_package:
        return "unchanged" if recorded_package == package_hash else "changed"
    recorded_entrypoint = source.get("skill_sha256")
    if not recorded_entrypoint:
        return "untracked"
    return "package_untracked" if recorded_entrypoint == entrypoint_hash else "changed"


def _git_creation_date(root: Path) -> str | None:
    try:
        roots = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--max-parents=0", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    root_commit = roots.stdout.splitlines()[0].strip() if roots.returncode == 0 and roots.stdout else ""
    if not root_commit:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", "-s", "--format=%aI", root_commit],
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
    return sorted({item for tag in tags if (item := _human_label(tag))}, key=str.casefold)


def _human_label(value: str) -> str:
    cleaned = "-".join(value.strip().split())
    cleaned = "".join(
        char for char in cleaned if char.isalnum() or char in {"-", "_", "."}
    )
    return cleaned[:80]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "project"


__all__ = [
    "adopt_workspace",
    "ownership_skill_index_mode",
    "plan_adoption",
    "valid_ownership_marker",
]
