from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from .adoption_tx import adoption_transaction_status, apply_adoption_transaction
from .errors import ArchMarshalError, require_workspace_root
from .io import load_yaml_safe
from .layout_policy import build_layout_plan, workspace_layout_metadata
from .ownership import ownership_skill_index_mode, valid_ownership_marker, workspace_id
from .safety import (
    EXCLUDED_BACKUP_PARTS,
    MAX_BACKUP_CONTENT_BYTES,
    MAX_BACKUP_FILES,
    MAX_DIRECTORY_SCAN_FILES,
    backup_relative_is_excluded,
    create_backup,
    ensure_managed_path,
    ensure_path_within,
    files_below_no_links,
    files_for_full_backup,
    fingerprint_directory,
    fingerprint_regular_file,
    is_link_or_reparse,
    sha256_file,
    verify_backup,
)
from .skill_index import (
    disabled_skill_index_plan,
    plan_skill_index,
    public_skill_index_plan,
    skill_index_exclusions,
)
from .skill_validation import validate_skill_package
from .workspace_lock import WorkspaceMutationLock, workspace_mutation_lock

SKILL_ROOT_CANDIDATES = (
    ".agents",
    ".codex/skills",
    ".claude/skills",
    "plugins",
    "skills",
)
SKILL_PATH_FIELDS = (
    "global_skills",
    "functional_skills",
    "common_project_skills",
    "project_skills",
    "generated_skills",
)
WINDOWS_RESERVED_SKILL_ROOT_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{index}" for index in range(1, 10)},
    *{f"LPT{index}" for index in range(1, 10)},
}
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
MANAGED_BACKUP_EXCLUDED_AGENT_ROOTS = frozenset(
    {
        "backups",
        "cache",
        "history",
        "inbox",
        "transactions",
    }
)


def plan_adoption(
    root: Path | str,
    *,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
    project_initialization: bool = False,
    skill_roots: list[str] | None = None,
    exclude_skills: list[str] | None = None,
    manage_skills: list[str] | None = None,
    save_paths: list[str] | None = None,
    naming_strategy: str | None = None,
    naming_timezone: str | None = None,
    date_partition: str | None = None,
    timestamp_format: str | None = None,
    user_store: Path | str | None = None,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    built = _build_adoption(
        root_path,
        tags or [],
        backup_scope,
        project_initialization=project_initialization,
        skill_roots=skill_roots,
        exclude_skills=exclude_skills,
        manage_skills=manage_skills,
        save_paths=save_paths,
        naming_strategy=naming_strategy,
        naming_timezone=naming_timezone,
        date_partition=date_partition,
        timestamp_format=timestamp_format,
        user_store=user_store,
    )
    return _public_plan(built, applied=False)


def adopt_workspace(
    root: Path | str,
    *,
    apply: bool = False,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
    expected_plan: str | None = None,
    project_initialization: bool = False,
    skill_roots: list[str] | None = None,
    exclude_skills: list[str] | None = None,
    manage_skills: list[str] | None = None,
    save_paths: list[str] | None = None,
    naming_strategy: str | None = None,
    naming_timezone: str | None = None,
    date_partition: str | None = None,
    timestamp_format: str | None = None,
    user_store: Path | str | None = None,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    if apply:
        operation = "project_initialization" if project_initialization else "adoption"
        with workspace_mutation_lock(root_path, operation=operation) as held:
            return _adopt_workspace_locked(
                root_path,
                apply=True,
                tags=tags,
                backup_scope=backup_scope,
                expected_plan=expected_plan,
                project_initialization=project_initialization,
                skill_roots=skill_roots,
                exclude_skills=exclude_skills,
                manage_skills=manage_skills,
                save_paths=save_paths,
                naming_strategy=naming_strategy,
                naming_timezone=naming_timezone,
                date_partition=date_partition,
                timestamp_format=timestamp_format,
                user_store=user_store,
                held=held,
            )
    return _adopt_workspace_locked(
        root_path,
        apply=False,
        tags=tags,
        backup_scope=backup_scope,
        expected_plan=expected_plan,
        project_initialization=project_initialization,
        skill_roots=skill_roots,
        exclude_skills=exclude_skills,
        manage_skills=manage_skills,
        save_paths=save_paths,
        naming_strategy=naming_strategy,
        naming_timezone=naming_timezone,
        date_partition=date_partition,
        timestamp_format=timestamp_format,
        user_store=user_store,
        held=None,
    )


def _adopt_workspace_locked(
    root_path: Path,
    *,
    apply: bool,
    tags: list[str] | None,
    backup_scope: str,
    expected_plan: str | None,
    project_initialization: bool,
    skill_roots: list[str] | None,
    exclude_skills: list[str] | None,
    manage_skills: list[str] | None,
    save_paths: list[str] | None,
    naming_strategy: str | None,
    naming_timezone: str | None,
    date_partition: str | None,
    timestamp_format: str | None,
    user_store: Path | str | None,
    held: WorkspaceMutationLock | None,
) -> dict[str, Any]:
    built = _build_adoption(
        root_path,
        tags or [],
        backup_scope,
        project_initialization=project_initialization,
        skill_roots=skill_roots,
        exclude_skills=exclude_skills,
        manage_skills=manage_skills,
        save_paths=save_paths,
        naming_strategy=naming_strategy,
        naming_timezone=naming_timezone,
        date_partition=date_partition,
        timestamp_format=timestamp_format,
        user_store=user_store,
    )
    if not apply:
        return _public_plan(built, applied=False)
    if built["blocked"]:
        payload = _public_plan(built, applied=False)
        payload["mode"] = "blocked"
        return payload
    if not built["writes"] and not built["skill_index_plan"]["changed"]:
        payload = _public_plan(built, applied=True)
        payload["mode"] = (
            "review_required"
            if built["review_required"]
            else "already_initialized"
            if project_initialization
            else "already_managed"
        )
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
            payload["notes"].append(
                "A target appeared after planning; no managed files were written."
            )
            return payload

    if held is not None:
        held.verify()

    backup_dir = root_path / ".agent" / "backups"
    backup_label = "initialization" if project_initialization else "adoption"
    backup = create_backup(
        root_path,
        built["backup_files"],
        backup_dir / f"{built['timestamp']}-pre-{backup_label}.zip",
        reason=(
            "ArchMarshal initialization before adding a create-only project Skill scaffold."
            if project_initialization
            else "ArchMarshal adoption before adding a non-destructive management overlay."
        ),
        scope=built["backup_archive_scope"],
    )
    backup_verification = verify_backup(root_path / backup["path"])
    actual_backup_records = sorted(
        backup_verification["manifest"]["files"],
        key=lambda item: str(item["path"]).casefold(),
    )
    if actual_backup_records != built["backup_records"]:
        raise ArchMarshalError(
            "backup_plan_mismatch",
            "The verified backup does not match the exact reviewed adoption sources.",
            details={
                "backup": backup["path"],
                "expected_file_count": len(built["backup_records"]),
                "actual_file_count": len(actual_backup_records),
            },
        )
    if held is not None:
        held.verify()

    revalidated = (
        disabled_skill_index_plan()
        if built["skill_index_plan"].get("disabled")
        else plan_skill_index(
            root_path,
            _discover_skills(
                root_path,
                built["additional_skill_roots"],
                excluded_packages=built["skill_selection"]["excluded_packages"],
            )[0],
            created_at=(built["skill_index_plan"].get("generation") or {}).get("created_at"),
            excluded_packages=built["skill_selection"]["excluded_packages"],
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
    payload["mode"] = (
        "project_initialization_applied"
        if project_initialization
        else "overlay_synced"
        if built["configured"]
        else "overlay_applied"
    )
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
    if project_initialization:
        payload["safety_guarantees"].append(
            "Project Skill scaffold files were created only where paths were absent."
        )
    return payload


def _build_adoption(
    root: Path,
    tags: list[str],
    backup_scope: str,
    *,
    project_initialization: bool = False,
    skill_roots: list[str] | None = None,
    exclude_skills: list[str] | None = None,
    manage_skills: list[str] | None = None,
    save_paths: list[str] | None = None,
    naming_strategy: str | None = None,
    naming_timezone: str | None = None,
    date_partition: str | None = None,
    timestamp_format: str | None = None,
    user_store: Path | str | None = None,
) -> dict[str, Any]:
    if backup_scope not in {"managed", "full"}:
        raise ValueError("backup_scope must be 'managed' or 'full'")

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    workspace_file = root / ".agent" / "workspace.yaml"
    ensure_managed_path(root, root / ".agent", purpose="ArchMarshal control directory")
    default_skill_parent = root / ".agents"
    if (
        project_initialization
        and default_skill_parent.exists()
        and not default_skill_parent.is_dir()
    ):
        raise ArchMarshalError(
            "project_initialization_path_conflict",
            "Project Skill scaffold has a file/directory path conflict.",
            details={"path": str(default_skill_parent), "target": ".agents/skills"},
        )
    configured = _is_archmarshal_workspace(workspace_file)
    overlay_enabled = _workspace_uses_overlays(workspace_file) if configured else True
    transaction_status = adoption_transaction_status(root)
    reserved_conflicts = [
        relative for relative in RESERVED_FILES[1:] if (root / relative).exists() and not configured
    ]
    skill_selection = _resolve_skill_selection(
        root,
        exclude_skills or [],
        manage_skills or [],
    )
    skills, effective_skill_roots, additional_skill_roots = _discover_skills(
        root,
        skill_roots,
        excluded_packages=skill_selection["excluded_packages"],
    )
    naming_overrides = {
        "strategy": naming_strategy,
        "timezone": naming_timezone,
        "date_partition": date_partition,
        "timestamp_format": timestamp_format,
    }
    layout = build_layout_plan(
        root,
        configured=configured,
        save_path_overrides=save_paths,
        naming_overrides=naming_overrides,
        user_store=user_store,
        effective_skill_roots=effective_skill_roots,
    )
    scaffold_writes: dict[Path, str] = {}
    scaffold_existing: list[str] = []
    scaffold_paths: list[str] = []
    if project_initialization and not layout["issues"]:
        scaffold_writes, scaffold_existing, scaffold_paths = _project_skill_scaffold_writes(
            root,
            layout,
        )
    manage_index = not configured or overlay_enabled
    skill_selection["persistence"] = (
        "immutable_skill_index" if manage_index else "current_preview_only"
    )
    skill_index_plan = (
        plan_skill_index(
            root,
            skills,
            created_at=_skill_evidence_timestamp(
                root,
                skills,
                excluded_packages=skill_selection["excluded_packages"],
            ),
            excluded_packages=skill_selection["excluded_packages"],
        )
        if manage_index
        else disabled_skill_index_plan()
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
    review_required = review_required or layout["requires_confirmation"]
    normalized_tags = _normalize_tags(tags)
    if not normalized_tags and configured:
        existing_workspace = load_yaml_safe(workspace_file)
        workspace_data = (
            existing_workspace.data if isinstance(existing_workspace.data, dict) else {}
        )
        workspace_tags = workspace_data.get("workspace", {}).get("tags", [])
        if isinstance(workspace_tags, list):
            normalized_tags = _normalize_tags(
                [item for item in workspace_tags if isinstance(item, str)]
            )
    normalized_tags = normalized_tags or ["archmarshal"]
    writes: dict[Path, str] = {}

    if not workspace_file.exists():
        writes[workspace_file] = _workspace_yaml(
            root,
            normalized_tags,
            now,
            source_skill_roots=additional_skill_roots,
            layout=layout,
        )
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
        writes[index] = _index_markdown(
            root,
            skills,
            normalized_tags,
            now,
            layout=layout,
            excluded_packages=skill_selection["excluded_packages"],
        )
    if not registry.exists():
        writes[registry] = _registry_yaml(skills, layout=layout)
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
    internal_placeholder_roots = {
        ".agent/archive",
        ".agent/cache",
        ".agent/context-modules",
        ".agent/inbox",
    }
    mapped_project_roots = set(layout["effective_profile"]["save_paths"]["project_files"].values())
    for relative in MANAGED_PLACEHOLDERS:
        parent = PurePosixPath(relative).parent.as_posix()
        if parent not in internal_placeholder_roots and parent not in mapped_project_roots:
            continue
        placeholder = root / relative
        if not placeholder.exists():
            writes[placeholder] = ""
    if project_initialization:
        writes.update(scaffold_writes)
    if overlay_enabled:
        for skill in skills:
            overlay = root / skill["overlay_manifest"]
            if not overlay.exists():
                writes[overlay] = yaml.safe_dump(
                    skill["manifest"], sort_keys=False, allow_unicode=True
                )

    ownership_mode_conflict = (
        configured
        and ownership_index_mode is not None
        and (
            (overlay_enabled and ownership_index_mode != "required")
            or (not overlay_enabled and ownership_index_mode != "disabled")
        )
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

    reserved_conflicts.extend(
        f"layout:{issue['code']}:{issue.get('field', '')}" for issue in layout["issues"]
    )

    blocked = bool(reserved_conflicts) or transaction_active
    if blocked:
        writes = {}

    if backup_scope == "full":
        backup_files = files_for_full_backup(
            root,
            excluded_directories=[
                root / PurePosixPath(item) for item in skill_selection["excluded_packages"]
            ],
        )
    else:
        backup_files = _managed_backup_files(
            root,
            skills,
            excluded_packages=skill_selection["excluded_packages"],
        )
    backup_records = _backup_source_records(root, backup_files)
    backup_coverage = _skill_backup_coverage(root, skills, backup_records)
    backup_coverage["excluded_package_count"] = len(skill_selection["excluded_packages"])
    backup_coverage["excluded_packages"] = list(skill_selection["excluded_packages"])
    if not backup_coverage["complete"]:
        raise ArchMarshalError(
            "skill_backup_coverage_incomplete",
            "The adoption backup plan does not completely cover every discovered Skill package.",
            details={"packages": backup_coverage["packages"]},
        )

    for target in writes:
        ensure_managed_path(root, target, purpose="Adoption target")

    return {
        "root": root,
        "timestamp": timestamp,
        "configured": configured,
        "overlay_enabled": overlay_enabled,
        "project_initialization": project_initialization,
        "scaffold_existing": scaffold_existing,
        "scaffold_paths": scaffold_paths,
        "backup_scope": backup_scope,
        "backup_archive_scope": (
            "managed_workspace"
            if backup_scope == "full" and skill_selection["excluded_packages"]
            else f"{backup_scope}_workspace"
        ),
        "tags": normalized_tags,
        "skills": skills,
        "effective_skill_roots": effective_skill_roots,
        "additional_skill_roots": additional_skill_roots,
        "skill_selection": skill_selection,
        "backup_coverage": backup_coverage,
        "review_required": review_required,
        "skill_index_plan": skill_index_plan,
        "writes": writes,
        "backup_files": backup_files,
        "backup_records": backup_records,
        "conflicts": reserved_conflicts,
        "blocked": blocked,
        "transaction_status": transaction_status,
        "layout": layout,
        "layout_arguments": {
            "save_paths": list(save_paths or []),
            "naming_strategy": naming_strategy,
            "naming_timezone": naming_timezone,
            "date_partition": date_partition,
            "timestamp_format": timestamp_format,
            "user_store": str(Path(user_store).resolve()) if user_store is not None else None,
        },
    }


def _public_plan(built: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    root: Path = built["root"]
    scaffold_paths = set(built["scaffold_paths"])
    operations = [
        {
            "action": "create",
            "path": path.relative_to(root).as_posix(),
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "overwrite": False,
            "category": (
                "project_skill_scaffold"
                if path.relative_to(root).as_posix() in scaffold_paths
                else "control_plane"
            ),
        }
        for path, content in built["writes"].items()
    ]
    tracked_changes = {
        str(change.get("source")): (
            "new" if change.get("kind") == "added" else str(change.get("kind"))
        )
        for change in built["skill_index_plan"].get("changes", [])
        if change.get("kind") in {"added", "modified", "restored"}
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
    backup_records = built["backup_records"]
    plan_digest = _adoption_plan_digest(built)
    public_skills = [
        _public_discovered_skill(built, skill, tracked_changes, index_plan)
        for skill in built["skills"]
    ]
    preserved_artifacts = [
        {
            "skill": skill["source"],
            "path": (artifact if skill["source"] == "." else f"{skill['source']}/{artifact}"),
            "policy": "preserve_unmanaged",
            "contents_inspected": False,
            "source_mutation": False,
        }
        for skill in built["skills"]
        for artifact in skill.get("preserved_artifacts", [])
    ]
    skill_reviews_required = [
        {
            "id": skill["id"],
            "source": skill["source"],
            "review_state": skill["review_state"],
            "activation_state": skill["activation_state"],
            "expected_head": index_plan["proposed_head"],
            "available_after": (
                "now"
                if applied or not index_plan["changed"]
                else "project_initialization_apply"
                if built["project_initialization"]
                else "adoption_apply"
            ),
        }
        for skill in public_skills
        if skill["review_required"]
    ]
    next_actions = _public_next_actions(
        built,
        applied=applied,
        plan_digest=plan_digest,
        operations=operations,
        index_plan=index_plan,
        skills=public_skills,
    )
    return {
        "tool": "archmarshal",
        "stage": (
            "init"
            if built["project_initialization"]
            else "sync"
            if built["configured"]
            else "adopt"
        ),
        "root": str(root),
        "mode": "applied" if applied else "propose_only",
        "configured": built["configured"],
        "overlay_enabled": built["overlay_enabled"],
        "project_initialization": built["project_initialization"],
        "mutation_scope": (
            "archmarshal_control_plane_and_project_skill_scaffold"
            if built["project_initialization"]
            else "archmarshal_control_plane_only"
        ),
        "source_files_modified": False,
        "blocked": built["blocked"],
        "review_required": built["review_required"],
        "plan_digest": plan_digest,
        "apply_precondition": "--expect-plan <plan_digest>",
        "transaction": built["transaction_status"],
        "layout": built["layout"],
        "human_review": {
            "entrypoint": ".agent/INDEX.md",
            "mapped_paths": [
                {
                    "role": f"{section}.{role}",
                    "path": path,
                    "source": built["layout"]["field_provenance"].get(
                        f"save_paths.{section}.{role}"
                    ),
                }
                for section, values in built["layout"]["effective_profile"]["save_paths"].items()
                for role, path in values.items()
            ],
            "skill_packages": [skill["source"] for skill in public_skills],
            "excluded_skill_packages": built["skill_selection"]["records"],
        },
        "skill_index": index_plan,
        "backup_scope": built["backup_scope"],
        "backup_archive_scope": built["backup_archive_scope"],
        "skill_discovery": {
            "effective_roots": built["effective_skill_roots"],
            "additional_roots": built["additional_skill_roots"],
            "root_count": len(built["effective_skill_roots"]),
            "discovered_package_count": len(public_skills),
            "prepared_management_packages": [skill["source"] for skill in public_skills],
            "excluded_packages": built["skill_selection"]["records"],
            "excluded_package_count": len(built["skill_selection"]["excluded_packages"]),
            "selection_added": built["skill_selection"]["added"],
            "selection_removed": built["skill_selection"]["removed"],
            "selection_persistence": built["skill_selection"]["persistence"],
            "preserved_artifacts": preserved_artifacts,
            "preserved_artifact_count": len(preserved_artifacts),
            "boundary_confirmation_required": bool(preserved_artifacts),
        },
        "backup_file_count": len(backup_records),
        "backup_file_preview": backup_records[:100],
        "backup_file_preview_truncated": len(backup_records) > 100,
        "skill_backup_coverage": built["backup_coverage"],
        "project_tags": built["tags"],
        "discovered_skills": public_skills,
        "skill_reviews_required": skill_reviews_required,
        "next_actions": next_actions,
        "project_skill_scaffold": {
            "paths": sorted(scaffold_paths),
            "planned_create": sorted(
                operation["path"]
                for operation in operations
                if operation.get("category") == "project_skill_scaffold"
            ),
            "preserved_existing": built["scaffold_existing"],
        },
        "operations": operations,
        "conflicts": built["conflicts"],
        "notes": [
            "Preview is the default; apply requires --expect-plan with this exact plan digest.",
            "Existing user-owned files are never overwritten, even with --apply.",
            "Only ArchMarshal's internal HEAD pointer may be atomically replaced after backup and CAS validation.",
            "Existing skills stay in place; overlays provide routing metadata without changing SKILL.md.",
            "Every managed Skill source file is included in the exact verified backup before the first managed file is added.",
            "Excluded Skill packages and preserved cache/repository artifacts remain outside ArchMarshal management; their contents are not read, backed up, indexed, learned from, or modified.",
            "Project save paths and naming follow project config, explicit CLI choices, a confirmed user profile, detected layout, then defaults in that priority order.",
            "Detected paths require exact-plan confirmation; detection alone is never promoted to a user habit.",
            (
                "Configured overlay projects are incrementally scanned for newly added source skills."
                if built["configured"] and built["overlay_enabled"]
                else "Native workspaces keep their declared skill layout and are not converted implicitly."
            ),
        ],
    }


def _project_skill_scaffold_writes(
    root: Path,
    layout: dict[str, Any],
) -> tuple[dict[Path, str], list[str], list[str]]:
    skill_paths = layout["effective_profile"]["save_paths"]["skills"]
    project = PurePosixPath(skill_paths["project"])
    generated = PurePosixPath(skill_paths["generated"])
    guide = (
        project.parent / "README.md"
        if project.parent == generated.parent
        else PurePosixPath(".agent/SKILL_PATHS.md")
    )
    scaffold = (
        (
            guide.as_posix(),
            "# Project Skills\n\n"
            f"- Human-reviewed project Skills: `{project.as_posix()}/`.\n"
            f"- Generated Skill drafts: `{generated.as_posix()}/`.\n\n"
            "ArchMarshal creates only missing scaffold files. It never moves or rewrites "
            "an existing Skill package.\n",
        ),
        ((project / ".gitkeep").as_posix(), ""),
        ((generated / ".gitkeep").as_posix(), ""),
    )
    writes: dict[Path, str] = {}
    existing: list[str] = []
    for relative, content in scaffold:
        target = root / relative
        current = root
        parts = Path(relative).parts
        for index, part in enumerate(parts):
            current = current / part
            if is_link_or_reparse(current):
                raise ArchMarshalError(
                    "unsafe_managed_link",
                    "Project Skill scaffold crosses a symbolic link or junction.",
                    details={"path": str(current)},
                )
            if not current.exists():
                continue
            is_target = index == len(parts) - 1
            if (is_target and not current.is_file()) or (not is_target and not current.is_dir()):
                raise ArchMarshalError(
                    "project_initialization_path_conflict",
                    "Project Skill scaffold has a file/directory path conflict.",
                    details={"path": str(current), "target": relative},
                )
        ensure_managed_path(root, target, purpose="Project Skill scaffold target")
        if target.exists():
            existing.append(relative)
        else:
            writes[target] = content
    return writes, sorted(existing), sorted(relative for relative, _content in scaffold)


def _planned_skill_manifest(built: dict[str, Any], source: str) -> dict[str, Any] | None:
    generation = built["skill_index_plan"].get("generation")
    records = generation.get("skills") if isinstance(generation, dict) else None
    if not isinstance(records, list):
        return None
    matches = [
        record.get("manifest")
        for record in records
        if isinstance(record, dict)
        and record.get("state") == "active"
        and record.get("source") == source
        and isinstance(record.get("manifest"), dict)
    ]
    return matches[0] if len(matches) == 1 else None


def _public_discovered_skill(
    built: dict[str, Any],
    skill: dict[str, Any],
    tracked_changes: dict[str, str],
    index_plan: dict[str, Any],
) -> dict[str, Any]:
    manifest = _planned_skill_manifest(built, skill["source"]) or skill["manifest"]
    validation = manifest.get("validation")
    validation_valid = isinstance(validation, dict) and validation.get("valid") is True
    provenance = manifest.get("metadata_provenance")
    import_errors = provenance.get("errors") if isinstance(provenance, dict) else []
    invalid = not validation_valid or bool(import_errors)
    effective_status = str(manifest.get("status") or "disabled")
    index_enabled = bool(index_plan.get("enabled"))
    review_state = str(manifest.get("review_state") or "needs_review")
    if invalid:
        activation_state = "disabled_invalid"
    elif effective_status not in {"active", "experimental"}:
        activation_state = "disabled_source"
    elif not index_enabled:
        activation_state = "active_native"
        review_state = "not_applicable"
    elif review_state == "approved":
        activation_state = "active_approved"
    elif review_state == "rejected":
        activation_state = "quarantined_rejected"
    else:
        activation_state = "quarantined_needs_review"
        review_state = "needs_review"
    review_required = activation_state == "quarantined_needs_review"
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "source": skill["source"],
        "source_manifest": skill["source_manifest"],
        "kind": manifest["kind"],
        "source_declared_status": skill["source_declared_status"],
        "normalized_source_status": effective_status,
        "review_state": review_state,
        "activation_state": activation_state,
        "review_required": review_required,
        "tags": manifest["tags"],
        "triggers": manifest["triggers"],
        "negative_triggers": manifest["negative_triggers"],
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


def _public_next_actions(
    built: dict[str, Any],
    *,
    applied: bool,
    plan_digest: str,
    operations: list[dict[str, Any]],
    index_plan: dict[str, Any],
    skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not applied and not built["blocked"] and (operations or index_plan.get("changed")):
        command_name = "init" if built["project_initialization"] else "adopt"
        command_args = ["archmarshal", command_name, str(built["root"])]
        for tag in built["tags"]:
            command_args.extend(["--tag", tag])
        for skill_root in built["additional_skill_roots"]:
            command_args.extend(["--skill-root", skill_root])
        for source in built["skill_selection"]["requested_exclusions"]:
            command_args.extend(["--exclude-skill", source])
        for source in built["skill_selection"]["requested_management"]:
            command_args.extend(["--manage-skill", source])
        layout_arguments = built["layout_arguments"]
        for assignment in layout_arguments["save_paths"]:
            command_args.extend(["--save-path", assignment])
        if layout_arguments["naming_strategy"] is not None:
            command_args.extend(["--naming-strategy", layout_arguments["naming_strategy"]])
        if layout_arguments["naming_timezone"] is not None:
            command_args.extend(["--timezone", layout_arguments["naming_timezone"]])
        if layout_arguments["date_partition"] is not None:
            command_args.extend(["--date-partition", layout_arguments["date_partition"]])
        if layout_arguments["timestamp_format"] is not None:
            command_args.extend(["--timestamp-format", layout_arguments["timestamp_format"]])
        if layout_arguments["user_store"] is not None:
            command_args.extend(["--user-store", layout_arguments["user_store"]])
        if built["backup_scope"] != "managed":
            command_args.extend(["--backup-scope", built["backup_scope"]])
        command_args.extend(["--expect-plan", plan_digest, "--apply", "--pretty"])
        actions.append(
            {
                "id": "apply_project_initialization"
                if built["project_initialization"]
                else "apply_adoption",
                "kind": "apply_project_initialization"
                if built["project_initialization"]
                else "apply_adoption",
                "available": True,
                "command_args": command_args,
                "command": subprocess.list2cmdline(command_args),
            }
        )

    review_head = index_plan.get("proposed_head")
    for skill in skills:
        if not skill["review_required"] or not isinstance(review_head, str):
            continue
        elevated = skill["kind"] == "global_skill"
        preview_args = [
            "archmarshal",
            "skill-review",
            str(built["root"]),
            "--source",
            skill["source"],
            "--decision",
            "approve",
            "--expect-head",
            review_head,
        ]
        if elevated:
            preview_args.append("--allow-global-policy")
        preview_args.append("--pretty")
        apply_args = [*preview_args[:-1]]
        apply_args.extend(
            [
                "--plan-file",
                "skill-review-plan.json",
                "--expect-plan",
                "<plan_digest>",
                "--apply",
                "--pretty",
            ]
        )
        available = applied or not index_plan.get("changed")
        actions.append(
            {
                "id": f"review_skill:{skill['id']}",
                "kind": "review_skill",
                "skill_id": skill["id"],
                "source": skill["source"],
                "expected_head": review_head,
                "available": available,
                "available_after": None
                if available
                else "apply_project_initialization"
                if built["project_initialization"]
                else "apply_adoption",
                "preview_command_args": preview_args,
                "preview_command": subprocess.list2cmdline(preview_args),
                "apply_command_args": apply_args,
                "apply_command": subprocess.list2cmdline(apply_args),
            }
        )
    return actions


def _adoption_plan_digest(built: dict[str, Any]) -> str:
    root: Path = built["root"]
    intent = {
        "format": "archmarshal-adoption-plan-v4",
        "backup_scope": built["backup_scope"],
        "backup_archive_scope": built["backup_archive_scope"],
        "effective_skill_roots": built["effective_skill_roots"],
        "additional_skill_roots": built["additional_skill_roots"],
        "skill_selection": built["skill_selection"],
        "preserved_artifacts": [
            {"source": skill["source"], "paths": skill.get("preserved_artifacts", [])}
            for skill in built["skills"]
        ],
        "configured": built["configured"],
        "overlay_enabled": built["overlay_enabled"],
        "project_initialization": built["project_initialization"],
        "tags": built["tags"],
        "layout": built["layout"],
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
        "backup_sources": built["backup_records"],
        "skill_backup_coverage": built["backup_coverage"],
        "skill_index": _logical_skill_plan(built["skill_index_plan"]),
    }
    canonical = json.dumps(
        intent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _backup_source_records(root: Path, files: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total_bytes = 0
    for path in files:
        if not path.exists() and not is_link_or_reparse(path):
            continue
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise ArchMarshalError(
                "backup_source_escape",
                "An adoption backup source resolves outside the project root.",
                details={"path": str(path)},
            ) from exc
        if backup_relative_is_excluded(relative):
            continue
        if len(records) >= MAX_BACKUP_FILES:
            raise ArchMarshalError(
                "backup_limit_exceeded",
                f"Adoption backup planning exceeds the {MAX_BACKUP_FILES}-file limit.",
            )
        record = fingerprint_regular_file(
            root,
            path,
            purpose="Adoption backup source",
            max_bytes=MAX_BACKUP_CONTENT_BYTES - total_bytes,
        )
        total_bytes += int(record["bytes"])
        records.append(record)
    return sorted(records, key=lambda item: item["path"].casefold())


def _logical_skill_plan(plan: dict[str, Any]) -> dict[str, Any]:
    generation = plan.get("generation")
    return {
        "changed": bool(plan.get("changed")),
        "disabled": bool(plan.get("disabled")),
        "expected_head": plan.get("expected_head"),
        "proposed_head": plan.get("digest"),
        "object_path": (
            plan.get("object_path").as_posix()
            if isinstance(plan.get("object_path"), Path)
            else plan.get("object_path")
        ),
        "generation": generation if isinstance(generation, dict) else None,
        "skills": generation.get("skills") if isinstance(generation, dict) else [],
        "changes": plan.get("changes") or [],
        "source_precondition_policy": plan.get("source_precondition_policy"),
        "source_preconditions": plan.get("source_preconditions") or [],
    }


def _skill_evidence_timestamp(
    root: Path,
    skills: list[dict[str, Any]],
    *,
    excluded_packages: list[str] | None = None,
) -> str:
    candidates = [root]
    head = root / ".agent" / "skill-overlays" / ".archmarshal" / "HEAD"
    if head.is_file() and not is_link_or_reparse(head):
        candidates.append(head)
    boundaries = {
        (root / PurePosixPath(str(skill.get("source") or "."))).resolve() for skill in skills
    }
    boundaries.update(
        (root / PurePosixPath(source)).resolve() for source in (excluded_packages or [])
    )
    for skill in skills:
        source = root / str(skill.get("source") or "")
        if source.resolve(strict=False) == root.resolve():
            source_files = [source / "SKILL.md", source / "manifest.yaml"]
        elif source.is_dir() and not is_link_or_reparse(source):
            source_files = files_below_no_links(
                source,
                purpose="Skill timestamp evidence",
                excluded_parts=EXCLUDED_BACKUP_PARTS,
                excluded_directories=[
                    boundary
                    for boundary in boundaries
                    if boundary != source.resolve() and _path_is_within(boundary, source.resolve())
                ],
            )
        else:
            source_files = []
        candidates.extend(path for path in source_files if path.is_file())
    newest = max(path.lstat().st_mtime_ns for path in candidates)
    return datetime.fromtimestamp(newest / 1_000_000_000, timezone.utc).isoformat(
        timespec="microseconds"
    )


def _resolve_skill_selection(
    root: Path,
    requested_exclusions: list[str],
    requested_management: list[str],
) -> dict[str, Any]:
    persisted = skill_index_exclusions(root)
    persisted_by_key = {_portable_skill_root_key(item): item for item in persisted}
    exclusions = [
        _normalize_skill_package_selection(root, item, allow_missing=False)
        for item in requested_exclusions
    ]
    management = [
        _normalize_skill_package_selection(
            root,
            item,
            allow_missing=(
                isinstance(item, str)
                and _portable_skill_root_key(item.strip().replace("\\", "/")) in persisted_by_key
            ),
        )
        for item in requested_management
    ]
    excluded_by_key = _portable_skill_selection_map(exclusions)
    managed_by_key = _portable_skill_selection_map(management)
    for key, value in {**excluded_by_key, **managed_by_key}.items():
        previous = persisted_by_key.get(key)
        if previous is not None and previous != value:
            raise ArchMarshalError(
                "skill_selection_portable_collision",
                "Skill selection spelling collides with persisted portable path identity.",
                details={"persisted": previous, "requested": value},
            )
    overlap = sorted(set(excluded_by_key) & set(managed_by_key))
    if overlap:
        raise ArchMarshalError(
            "skill_selection_conflict",
            "The same Skill package cannot be excluded and managed in one plan.",
            details={"packages": [excluded_by_key[key] for key in overlap]},
        )
    desired = dict(persisted_by_key)
    desired.update(excluded_by_key)
    for key in managed_by_key:
        desired.pop(key, None)
    desired_values = sorted(
        desired.values(), key=lambda item: (_portable_skill_root_key(item), item)
    )
    added = sorted(
        [value for key, value in desired.items() if key not in persisted_by_key],
        key=str.casefold,
    )
    removed = sorted(
        [value for key, value in persisted_by_key.items() if key not in desired],
        key=str.casefold,
    )
    records: list[dict[str, Any]] = []
    for source in desired_values:
        candidate = root / PurePosixPath(source)
        linked = is_link_or_reparse(candidate)
        directory_present = not linked and candidate.exists() and candidate.is_dir()
        entrypoint = candidate / "SKILL.md"
        entrypoint_present = (
            directory_present and entrypoint.is_file() and not is_link_or_reparse(entrypoint)
        )
        records.append(
            {
                "source": source,
                "state": (
                    "excluded_present"
                    if entrypoint_present
                    else "excluded_unsafe_boundary"
                    if linked
                    else "excluded_dormant"
                ),
                "entrypoint_present": entrypoint_present,
                "contents_inspected": False,
                "backup_included": False,
                "indexed": False,
                "learning_included": False,
                "source_mutation": False,
            }
        )
    return {
        "format": "archmarshal-skill-selection-v1",
        "excluded_packages": desired_values,
        "requested_exclusions": sorted(set(exclusions), key=str.casefold),
        "requested_management": sorted(set(management), key=str.casefold),
        "added": added,
        "removed": removed,
        "records": records,
    }


def _portable_skill_selection_map(values: list[str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for value in values:
        key = _portable_skill_root_key(value)
        previous = selected.get(key)
        if previous is not None and previous != value:
            raise ArchMarshalError(
                "skill_selection_portable_collision",
                "Skill selections collide under portable case/Unicode path rules.",
                details={"first": previous, "second": value},
            )
        selected[key] = value
    return selected


def _normalize_skill_package_selection(
    root: Path,
    value: str,
    *,
    allow_missing: bool,
) -> str:
    if not isinstance(value, str):
        raise ArchMarshalError(
            "skill_selection_invalid",
            "Skill package selections must be project-relative strings.",
        )
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:/", normalized)
        or ".." in path.parts
    ):
        raise ArchMarshalError(
            "skill_selection_outside_project",
            "Skill package selections must stay inside the project root.",
            details={"source": value},
        )
    parts = tuple(part for part in path.parts if part not in {"", "."})
    relative = PurePosixPath(*parts).as_posix() if parts else "."
    if (
        relative == "."
        or unicodedata.normalize("NFC", relative) != relative
        or any(_unsafe_skill_root_component(part) for part in parts)
    ):
        raise ArchMarshalError(
            "skill_selection_not_portable",
            "Skill package selections require a portable, non-root package directory.",
            details={"source": value},
        )
    if parts[0].casefold() == ".agent":
        raise ArchMarshalError(
            "skill_selection_managed_state",
            "Skill package selections must not point into ArchMarshal's .agent state.",
            details={"source": relative},
        )
    candidate = root / PurePosixPath(relative)
    if not candidate.exists():
        if allow_missing:
            return relative
        raise ArchMarshalError(
            "skill_selection_missing",
            "An explicitly selected Skill package does not exist.",
            details={"source": relative},
        )
    ensure_managed_path(root, candidate, purpose="Skill package selection")
    entrypoint = candidate / "SKILL.md"
    if (
        not candidate.is_dir()
        or is_link_or_reparse(candidate)
        or not entrypoint.is_file()
        or is_link_or_reparse(entrypoint)
    ):
        raise ArchMarshalError(
            "skill_selection_invalid",
            "A Skill package selection must be a real directory with an unlinked SKILL.md.",
            details={"source": relative},
        )
    return relative


def _discover_skills(
    root: Path,
    additional_roots: list[str] | None = None,
    *,
    excluded_packages: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    effective_roots, normalized_additional = _effective_skill_roots(root, additional_roots or [])
    skill_docs: set[Path] = set()
    excluded_packages = excluded_packages or []
    excluded_keys = {_portable_skill_root_key(item) for item in excluded_packages}
    excluded_directories = [root / PurePosixPath(item) for item in excluded_packages]
    root_skill = root / "SKILL.md"
    if root_skill.is_file() and _portable_skill_root_key(".") not in excluded_keys:
        skill_docs.add(root_skill)
    for relative in effective_roots:
        candidate = root if relative == "." else root / PurePosixPath(relative)
        if _portable_skill_root_key(relative) in excluded_keys:
            continue
        for path in files_below_no_links(
            candidate,
            purpose="Skill discovery",
            excluded_parts=EXCLUDED_BACKUP_PARTS,
            excluded_directories=excluded_directories,
        ):
            if path.name != "SKILL.md":
                continue
            if ".agent" not in path.relative_to(root).parts:
                skill_docs.add(path)

    skills: list[dict[str, Any]] = []
    package_boundaries = {path.parent.resolve() for path in skill_docs}
    package_boundaries.update(path.resolve() for path in excluded_directories)
    for skill_md in sorted(skill_docs):
        ensure_path_within(root, skill_md, purpose="Discovered skill source")
        source_dir = skill_md.parent
        source = source_dir.relative_to(root).as_posix()
        if _portable_skill_root_key(source) in excluded_keys:
            continue
        frontmatter = _skill_frontmatter(skill_md)
        source_manifest = source_dir / "manifest.yaml"
        declared, import_errors, source_declared_status = _import_source_manifest(source_manifest)
        display_name = (
            str(declared.get("name") or frontmatter.get("name") or source_dir.name).strip()
            or source_dir.name
        )
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
        skill_id = str(declared.get("id") or f"skill.{scope}.{name_slug}-{identity_suffix}")
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
        nested_boundaries = [
            boundary
            for boundary in package_boundaries
            if boundary != source_dir.resolve() and _path_is_within(boundary, source_dir.resolve())
        ]
        package = fingerprint_directory(
            source_dir,
            purpose="Skill package",
            entrypoint_only=source_dir.resolve() == root,
            include_modes=True,
            excluded_parts=EXCLUDED_BACKUP_PARTS,
            excluded_directories=nested_boundaries,
        )
        preserved_nested = {
            boundary.relative_to(source_dir.resolve()).as_posix() for boundary in nested_boundaries
        }
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
                "package_boundary": "portable-source-v1",
                "original_manifest": (
                    source_manifest.relative_to(root).as_posix()
                    if source_manifest.exists()
                    else None
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
            manifest["paths"] = {key: key for key, present in local.items() if present}
        skills.append(
            {
                "source": source,
                "package_files": package["files"],
                "preserved_artifacts": [
                    item for item in package["preserved_paths"] if item not in preserved_nested
                ],
                "source_declared_status": source_declared_status,
                "source_manifest": (
                    source_manifest.relative_to(root).as_posix()
                    if source_manifest.exists()
                    else None
                ),
                "overlay_manifest": overlay_manifest,
                "manifest": manifest,
                "source_drift": source_drift,
            }
        )
    return skills, effective_roots, normalized_additional


def _effective_skill_roots(root: Path, additional_roots: list[str]) -> tuple[list[str], list[str]]:
    candidates: list[tuple[str, bool, str]] = [
        (value, False, "default") for value in SKILL_ROOT_CANDIDATES
    ]
    declared_typed, declared_source = _declared_skill_roots(root)
    candidates.extend((value, False, "workspace") for value in declared_typed)
    candidates.extend((value, True, "workspace_source") for value in declared_source)
    candidates.extend((value, True, "explicit") for value in additional_roots)

    effective: dict[str, str] = {}
    additional: dict[str, str] = {}
    for value, required, source in candidates:
        relative = _normalize_skill_root(root, value, required=required, source=source)
        if relative is None:
            continue
        key = _portable_skill_root_key(relative)
        previous = effective.get(key)
        if previous is not None and previous != relative:
            raise ArchMarshalError(
                "skill_root_portable_collision",
                "Skill roots collide under portable case/Unicode path rules.",
                details={"first": previous, "second": relative},
            )
        effective[key] = relative
        if source == "explicit" and previous is None:
            additional[key] = relative
    effective_values = _collapse_skill_roots(effective.values())
    effective_keys = {_portable_skill_root_key(value) for value in effective_values}
    additional_values = [
        value
        for value in _collapse_skill_roots(additional.values())
        if _portable_skill_root_key(value) in effective_keys
    ]
    return effective_values, additional_values


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _collapse_skill_roots(values: Iterable[str]) -> list[str]:
    ordered = sorted(
        set(values),
        key=lambda item: (
            len(PurePosixPath(item).parts),
            _portable_skill_root_key(item),
            item,
        ),
    )
    kept: list[str] = []
    for value in ordered:
        parts = PurePosixPath(value).parts if value != "." else ()
        portable_parts = tuple(_portable_skill_root_key(part) for part in parts)
        redundant = False
        for parent in kept:
            parent_parts = PurePosixPath(parent).parts if parent != "." else ()
            portable_parent = tuple(_portable_skill_root_key(part) for part in parent_parts)
            if portable_parts[: len(portable_parent)] != portable_parent:
                continue
            if parts[: len(parent_parts)] != parent_parts:
                raise ArchMarshalError(
                    "skill_root_portable_collision",
                    "Skill roots overlap only under portable case/Unicode path rules.",
                    details={"first": parent, "second": value},
                )
            redundant = True
            break
        if not redundant:
            kept.append(value)
    return sorted(kept, key=lambda item: (_portable_skill_root_key(item), item))


def _declared_skill_roots(root: Path) -> tuple[list[str], list[str]]:
    workspace_file = root / ".agent" / "workspace.yaml"
    if not workspace_file.exists():
        return [], []
    owned = valid_ownership_marker(root / ".agent" / "ownership.json")
    loaded = load_yaml_safe(workspace_file)
    if loaded.error or not isinstance(loaded.data, dict):
        if owned:
            raise ArchMarshalError(
                "skill_root_config_invalid",
                "The owned workspace configuration cannot be read safely for Skill discovery.",
                details={"path": ".agent/workspace.yaml", "error": loaded.error},
            )
        return [], []
    paths = loaded.data.get("paths")
    if not isinstance(paths, dict):
        if owned:
            raise ArchMarshalError(
                "skill_root_config_invalid",
                "The owned workspace configuration has no valid paths mapping.",
                details={"path": ".agent/workspace.yaml"},
            )
        return [], []

    def values(field: str, *, strict: bool = False) -> list[str]:
        value = paths.get(field)
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            if all(isinstance(item, str) for item in value):
                return value
        if strict:
            raise ArchMarshalError(
                "skill_root_config_invalid",
                "Owned source_skill_roots must contain only project-relative strings.",
                details={"path": ".agent/workspace.yaml", "field": field},
            )
        return []

    typed = [item for field in SKILL_PATH_FIELDS for item in values(field)]
    return typed, values("source_skill_roots", strict=owned)


def _normalize_skill_root(
    root: Path,
    value: str,
    *,
    required: bool,
    source: str,
) -> str | None:
    if not isinstance(value, str):
        raise ArchMarshalError(
            "skill_root_invalid", "Skill roots must be project-relative strings."
        )
    normalized = value.strip().replace("\\", "/")
    if not normalized or "\x00" in normalized:
        raise ArchMarshalError("skill_root_invalid", "Skill roots must not be empty.")
    path = PurePosixPath(normalized)
    if path.is_absolute() or re.match(r"^[A-Za-z]:/", normalized) or ".." in path.parts:
        raise ArchMarshalError(
            "skill_root_outside_project",
            "Skill roots must stay inside the project root.",
            details={"root": value, "source": source},
        )
    parts = tuple(part for part in path.parts if part not in {"", "."})
    relative = PurePosixPath(*parts).as_posix() if parts else "."
    if unicodedata.normalize("NFC", relative) != relative or any(
        _unsafe_skill_root_component(part) for part in parts
    ):
        raise ArchMarshalError(
            "skill_root_not_portable",
            "Skill roots must use portable Unicode-normalized path components.",
            details={"root": value, "source": source},
        )
    if parts and parts[0].casefold() == ".agent":
        if source == "workspace":
            return None
        raise ArchMarshalError(
            "skill_root_managed_state",
            "Source Skill roots must not point into ArchMarshal's .agent state.",
            details={"root": relative, "source": source},
        )
    candidate = root if relative == "." else root / path
    if not candidate.exists():
        if required:
            raise ArchMarshalError(
                "skill_root_missing",
                "An explicit or source Skill root does not exist.",
                details={"root": relative, "source": source},
            )
        return None
    ensure_managed_path(root, candidate, purpose="Skill discovery root")
    if not candidate.is_dir() or is_link_or_reparse(candidate):
        raise ArchMarshalError(
            "skill_root_invalid",
            "Skill roots must be real, unlinked directories.",
            details={"root": relative, "source": source},
        )
    return relative


def _unsafe_skill_root_component(part: str) -> bool:
    return (
        part in {"", ".", ".."}
        or part.endswith((" ", "."))
        or ":" in part
        or part.split(".", 1)[0].rstrip(" .").upper() in WINDOWS_RESERVED_SKILL_ROOT_NAMES
    )


def _portable_skill_root_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


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


def _import_source_manifest(
    path: Path,
) -> tuple[dict[str, Any], list[str], str | None]:
    if not path.exists():
        return {}, [], None
    result = load_yaml_safe(path)
    if result.error or not isinstance(result.data, dict):
        return {}, ["source_manifest_invalid"], None
    data = result.data
    source_declared_status = data.get("status")
    if not isinstance(source_declared_status, str) or len(source_declared_status) > 64:
        source_declared_status = None
    imported: dict[str, Any] = {}
    errors: list[str] = []
    string_fields = {
        "id": lambda value: bool(re.fullmatch(r"skill\.[a-z0-9_.-]+", value)),
        "name": lambda value: bool(value.strip()),
        "summary": lambda value: bool(value.strip()),
        "version": lambda value: bool(re.fullmatch(r"\d+\.\d+\.\d+", value)),
        "kind": lambda value: (
            value
            in {
                "global_skill",
                "functional_skill",
                "common_project_skill",
                "project_skill",
                "generated_project_skill",
                "governance_skill",
            }
        ),
        "scope": lambda value: (
            value in {"global", "functional", "common_project", "project", "module", "generated"}
        ),
        "status": lambda value: (
            value in {"active", "disabled", "experimental", "deprecated", "archived"}
        ),
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
    return imported, errors, source_declared_status


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
    *,
    source_skill_roots: list[str],
    layout: dict[str, Any],
) -> str:
    created_on = _git_creation_date(root) or now.date().isoformat()
    code_roots = [name for name in ("src", "app", "packages", "lib") if (root / name).exists()]
    data = {
        "layout": workspace_layout_metadata(layout),
        "workspace": {
            "name": root.name,
            "version": "0.1.0",
            "description": "Project adopted through a non-destructive ArchMarshal overlay.",
            "created_on": created_on,
            "adopted_on": now.date().isoformat(),
            "tags": tags,
            "management_mode": "overlay",
        },
        "save_paths": layout["effective_profile"]["save_paths"],
        "naming": layout["effective_profile"]["naming"],
        "paths": {
            "project_root": ".",
            "code_roots": code_roots,
            "agent_root": ".agent",
            "source_skill_roots": source_skill_roots,
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
    return (
        json.dumps(
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
        )
        + "\n"
    )


def _registry_yaml(
    skills: list[dict[str, Any]],
    *,
    layout: dict[str, Any],
) -> str:
    project_paths = layout["effective_profile"]["save_paths"]["project_files"]
    artifacts: list[dict[str, Any]] = [
        _artifact("project.index", "project_doc", ".agent/INDEX.md", "default", ["index"]),
        _artifact(
            "managed.history", "history", project_paths["history"], "explicit_only", ["history"]
        ),
        _artifact(
            "managed.reports", "report", project_paths["reports"], "explicit_only", ["reports"]
        ),
        _artifact("managed.plans", "plan", project_paths["plans"], "explicit_only", ["plans"]),
        _artifact(
            "managed.checkpoints",
            "artifact",
            project_paths["checkpoints"],
            "task_based",
            ["checkpoint", "context"],
        ),
        _artifact(
            "managed.artifacts",
            "artifact",
            project_paths["artifacts"],
            "explicit_only",
            ["artifacts"],
        ),
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
        _artifact(
            "managed.knowledge",
            "knowledge",
            project_paths["knowledge"],
            "task_based",
            ["knowledge"],
        ),
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
    *,
    layout: dict[str, Any],
    excluded_packages: list[str],
) -> str:
    skill_lines = [
        f"- `{skill['manifest']['name']}` ({skill['manifest']['kind']}): "
        f"source `{skill['source']}`, overlay `{skill['overlay_manifest']}`"
        for skill in skills
    ] or ["- No existing skills were discovered during adoption."]
    profile = layout["effective_profile"]
    project_paths = profile["save_paths"]["project_files"]
    skill_paths = profile["save_paths"]["skills"]
    naming = profile["naming"]["project_files"]
    mapped_lines = [
        f"- {role.replace('_', ' ').title()}: `{path}/`" for role, path in project_paths.items()
    ]
    root_lines = [f"- `{path}`" for path in profile["skill_roots"]] or [
        "- No source Skill root was present during adoption."
    ]
    exclusion_lines = [f"- `{path}`" for path in excluded_packages] or [
        "- No Skill package is excluded."
    ]
    return "\n".join(
        [
            f"# {root.name} - ArchMarshal Index",
            "",
            f"- Adopted: {now.date().isoformat()}",
            f"- Tags: {', '.join(tags)}",
            "- Management mode: non-destructive overlay",
            f"- Layout foundation: {layout['foundation']}",
            f"- Layout source: {layout['source']}",
            "",
            "## Active project map",
            "",
            *mapped_lines,
            "- Verified backups: `.agent/backups/` (never loaded by default)",
            f"- Naming: `{naming['strategy']}` in `{naming['timezone']}`",
            f"- Date partition: `{naming.get('date_partition', 'YYYY/MM/DD')}`",
            "",
            "## Project Skill paths",
            "",
            f"- Human-reviewed project Skills: `{skill_paths['project']}/`",
            f"- Generated Skill drafts: `{skill_paths['generated']}/`",
            "",
            "## Source Skill roots",
            "",
            *root_lines,
            "",
            "## Excluded Skill packages",
            "",
            *exclusion_lines,
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


def _managed_backup_files(
    root: Path,
    skills: list[dict[str, Any]],
    *,
    excluded_packages: list[str] | None = None,
) -> list[Path]:
    files: set[Path] = set()
    for relative in RESERVED_FILES:
        candidate = root / relative
        if candidate.is_file():
            files.add(candidate)
    agent_root = root / ".agent"
    if agent_root.exists():
        if is_link_or_reparse(agent_root) or not agent_root.is_dir():
            # Preserve the previous fail-closed behavior for the managed root.
            files_below_no_links(agent_root, purpose="Managed metadata backup")
        agent_files: list[Path] = []
        for child in sorted(agent_root.iterdir(), key=lambda path: path.name.casefold()):
            if child.name.casefold() in MANAGED_BACKUP_EXCLUDED_AGENT_ROOTS:
                continue
            if is_link_or_reparse(child):
                # The bounded scanner never followed linked children. Keep that
                # behavior while avoiding traversal of excluded runtime stores.
                continue
            if child.is_file():
                if len(agent_files) >= MAX_DIRECTORY_SCAN_FILES:
                    raise ArchMarshalError(
                        "directory_scan_limit_exceeded",
                        "Managed metadata backup exceeds the bounded file scan limit.",
                        details={"path": str(agent_root)},
                    )
                agent_files.append(child)
            elif child.is_dir():
                remaining = MAX_DIRECTORY_SCAN_FILES - len(agent_files)
                agent_files.extend(
                    files_below_no_links(
                        child,
                        purpose="Managed metadata backup",
                        max_files=remaining,
                    )
                )
        files.update(path for path in agent_files if path.is_file())
    boundaries = {(root / PurePosixPath(str(skill["source"]))).resolve() for skill in skills}
    boundaries.update(
        (root / PurePosixPath(source)).resolve() for source in (excluded_packages or [])
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
                    excluded_parts=EXCLUDED_BACKUP_PARTS,
                    excluded_directories=[
                        boundary
                        for boundary in boundaries
                        if boundary != source_dir.resolve()
                        and _path_is_within(boundary, source_dir.resolve())
                    ],
                )
                if path.is_file()
            )
    return sorted(files)


def _skill_backup_coverage(
    root: Path,
    skills: list[dict[str, Any]],
    backup_records: list[dict[str, Any]],
) -> dict[str, Any]:
    by_path = {
        str(record["path"]): record
        for record in backup_records
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    packages: list[dict[str, Any]] = []
    all_complete = True
    for skill in skills:
        source = str(skill["source"])
        package_files = skill.get("package_files")
        if not isinstance(package_files, list):
            package_files = []
        expected_paths = []
        for record in package_files:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                continue
            expected_paths.append(record["path"] if source == "." else f"{source}/{record['path']}")
        covered = [by_path[path] for path in expected_paths if path in by_path]
        missing = sorted(set(expected_paths) - set(by_path), key=str.casefold)
        manifest_source = skill.get("manifest", {}).get("source", {})
        expected_count = (
            manifest_source.get("package_file_count") if isinstance(manifest_source, dict) else None
        )
        complete = (
            isinstance(expected_count, int)
            and expected_count == len(expected_paths)
            and not missing
            and len(covered) == len(expected_paths)
        )
        all_complete = all_complete and complete
        coverage_bytes = json.dumps(
            covered,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        packages.append(
            {
                "source": source,
                "package_sha256": manifest_source.get("package_sha256")
                if isinstance(manifest_source, dict)
                else None,
                "expected_file_count": expected_count,
                "planned_backup_file_count": len(covered),
                "backup_records_sha256": hashlib.sha256(coverage_bytes).hexdigest(),
                "missing": missing,
                "complete": complete,
                "boundary": "workspace-root-entrypoint-only"
                if source == "."
                else "skill-directory",
            }
        )
    return {
        "format": "archmarshal-skill-backup-coverage-v1",
        "complete": all_complete,
        "discovered_package_count": len(skills),
        "covered_package_count": sum(item["complete"] for item in packages),
        "packages": sorted(packages, key=lambda item: str(item["source"]).casefold()),
        "source_mutation": False,
    }


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
        str(value).replace("\\", "/").startswith(".agent/skill-overlays/") for value in skill_paths
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
    root_commit = (
        roots.stdout.splitlines()[0].strip() if roots.returncode == 0 and roots.stdout else ""
    )
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
    cleaned = "".join(char for char in cleaned if char.isalnum() or char in {"-", "_", "."})
    return cleaned[:80]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "project"


__all__ = [
    "adopt_workspace",
    "ownership_skill_index_mode",
    "plan_adoption",
    "valid_ownership_marker",
]
