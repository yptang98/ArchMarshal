from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .inventory import collect_inventory
from .io import list_files, load_yaml_safe, read_text
from .schema_validation import validate_schema


GLOBAL_SKILL_MAX_LINES = 120
AGENTS_MD_MAX_BYTES = 6000
HISTORICAL_PREFIXES = (
    ".agent/reports/",
    ".agent/history/",
    ".agent/archive/",
    ".agent/cache/",
)
REQUIRED_PROJECT_FILE_SAVE_PATHS = ("checkpoints", "reports", "plans", "history", "knowledge")
REQUIRED_PROJECT_FILE_NAMING_FIELDS = ("strategy", "timezone", "timestamp_format", "max_slug_words")


def lint_workspace(root: Path | str) -> list[Diagnostic]:
    inventory = collect_inventory(root)
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_lint_project_files(inventory.root, inventory.to_dict()))
    diagnostics.extend(_lint_workspace_manifest(inventory.root, inventory.to_dict()))
    diagnostics.extend(_lint_registry(inventory.to_dict()))
    diagnostics.extend(_lint_context_modules(inventory.to_dict()))
    diagnostics.extend(_lint_memory_stores(inventory.root, inventory.to_dict()))
    diagnostics.extend(_lint_memory_records(inventory.root, inventory.to_dict()))
    diagnostics.extend(_lint_skills(inventory.root, inventory.to_dict()))
    return diagnostics


def _lint_project_files(root: Path, data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    files = data["files"]
    if not files["workspace_yaml"]["exists"]:
        diagnostics.append(
            Diagnostic(
                "project.missing_workspace_yaml",
                "error",
                "Project is missing .agent/workspace.yaml path mapping.",
                ".agent/workspace.yaml",
                "Create workspace.yaml so tools can honor project-specific paths.",
            )
        )
    if not files["index_md"]["exists"]:
        diagnostics.append(
            Diagnostic(
                "project.missing_agent_index",
                "error",
                "Project is missing .agent/INDEX.md human map.",
                ".agent/INDEX.md",
                "Create INDEX.md to route humans and agents through active context.",
            )
        )
    agents = files["agents_md"]
    if agents["exists"] and agents["bytes"] > AGENTS_MD_MAX_BYTES:
        diagnostics.append(
            Diagnostic(
                "project.agents_md_too_large",
                "warning",
                "AGENTS.md is large enough to become a context dumping ground.",
                "AGENTS.md",
                "Keep AGENTS.md as an entry router and move long content into knowledge or reports.",
            )
        )
    agents_path = root / "AGENTS.md"
    if agents_path.exists():
        text = read_text(agents_path).lower()
        if text.count("history") + text.count("report") + text.count("archive") > 8:
            diagnostics.append(
                Diagnostic(
                    "project.agents_md_contains_history",
                    "warning",
                    "AGENTS.md appears to contain or route heavily through historical material.",
                    "AGENTS.md",
                    "Keep history explicit-only and summarize durable facts in knowledge files.",
                )
            )
    for path in data["unregistered_agent_files"]:
        diagnostics.append(
            Diagnostic(
                "project.unregistered_agent_file",
                "warning",
                "Agent workspace file is not registered in .agent/registry.yaml.",
                path,
                "Register durable files or move temporary artifacts to inbox/history/archive.",
            )
        )
    return diagnostics


def _lint_workspace_manifest(root: Path, data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    workspace_file = root / ".agent" / "workspace.yaml"
    if not workspace_file.exists():
        return diagnostics
    result = load_yaml_safe(workspace_file)
    if result.error:
        return [
            Diagnostic(
                "project.workspace_yaml_invalid",
                "error",
                f"workspace.yaml could not be parsed: {result.error}",
                ".agent/workspace.yaml",
                "Fix workspace.yaml syntax.",
            )
        ]
    raw = result.data
    diagnostics.extend(
        _schema_diagnostics(
            "project.workspace_schema_invalid",
            ".agent/workspace.yaml",
            "workspace",
            raw,
        )
    )
    if not isinstance(raw, dict):
        return diagnostics
    workspace = raw.get("workspace") if isinstance(raw, dict) else None
    paths = raw.get("paths") if isinstance(raw, dict) else None
    if not isinstance(workspace, dict):
        diagnostics.append(
            Diagnostic(
                "project.workspace_missing_metadata",
                "error",
                "workspace.yaml is missing the workspace metadata object.",
                ".agent/workspace.yaml",
                "Add workspace.name and workspace.version.",
            )
        )
    else:
        for field in ["name", "version"]:
            if not workspace.get(field):
                diagnostics.append(
                    Diagnostic(
                        "project.workspace_missing_metadata",
                        "error",
                        f"workspace.yaml is missing workspace.{field}.",
                        ".agent/workspace.yaml",
                        "Add stable workspace metadata for inventory and reports.",
                    )
                )
    if not isinstance(paths, dict):
        diagnostics.append(
            Diagnostic(
                "project.workspace_missing_paths",
                "error",
                "workspace.yaml is missing the paths object.",
                ".agent/workspace.yaml",
                "Add paths so ArchMarshal can honor project-specific layout.",
            )
        )
        return diagnostics
    for field in ["project_root", "agent_root"]:
        if not paths.get(field):
            diagnostics.append(
                Diagnostic(
                    "project.workspace_missing_paths",
                    "error",
                    f"workspace.yaml is missing paths.{field}.",
                    ".agent/workspace.yaml",
                    "Declare the required root path mappings.",
                )
            )
    for key, value in paths.items():
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if not isinstance(entry, str) or not entry:
                diagnostics.append(
                    Diagnostic(
                        "project.workspace_invalid_path_entry",
                        "error",
                        f"paths.{key} contains an invalid path entry.",
                        ".agent/workspace.yaml",
                        "Use non-empty relative path strings.",
                    )
                )
                continue
            if _path_escapes_root(root, entry):
                diagnostics.append(
                    Diagnostic(
                        "project.workspace_path_outside_root",
                        "warning",
                        f"paths.{key} points outside the project root.",
                        entry,
                        "Keep mappings inside the project unless this is an intentional external workspace.",
                    )
                )
    diagnostics.extend(_lint_save_paths(root, raw.get("save_paths")))
    diagnostics.extend(_lint_naming(raw.get("naming")))
    return diagnostics


def _lint_save_paths(root: Path, save_paths: Any) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not isinstance(save_paths, dict):
        return [
            Diagnostic(
                "project.save_paths_missing",
                "warning",
                "workspace.yaml does not record project save paths.",
                ".agent/workspace.yaml",
                "Add save_paths.project_files so project artifacts have user-approved destinations.",
            )
        ]
    project_files = save_paths.get("project_files")
    if not isinstance(project_files, dict) or not project_files:
        diagnostics.append(
            Diagnostic(
                "project.project_file_save_paths_missing",
                "warning",
                "workspace.yaml does not declare project file save paths.",
                ".agent/workspace.yaml#$.save_paths.project_files",
                "Declare checkpoints, reports, plans, history, and knowledge save paths.",
            )
        )
        return diagnostics
    for key in REQUIRED_PROJECT_FILE_SAVE_PATHS:
        if not project_files.get(key):
            diagnostics.append(
                Diagnostic(
                    "project.project_file_save_paths_missing",
                    "warning",
                    f"Project file save path '{key}' is not declared.",
                    f".agent/workspace.yaml#$.save_paths.project_files.{key}",
                    "Record user-approved save paths for project files instead of relying on implicit defaults.",
                )
            )
    for key, value in project_files.items():
        if not isinstance(value, str) or not value:
            diagnostics.append(
                Diagnostic(
                    "project.project_file_save_path_invalid",
                    "error",
                    f"Project file save path '{key}' is invalid.",
                    f".agent/workspace.yaml#$.save_paths.project_files.{key}",
                    "Use a non-empty relative path string.",
                )
            )
            continue
        if _path_escapes_root(root, value):
            diagnostics.append(
                Diagnostic(
                    "project.project_file_save_path_outside_root",
                    "warning",
                    f"Project file save path '{key}' points outside the project root.",
                    value,
                    "Keep project artifact save paths inside the workspace unless explicitly reviewed.",
                )
            )
    return diagnostics


def _lint_naming(naming: Any) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not isinstance(naming, dict):
        return [
            Diagnostic(
                "project.project_file_naming_missing",
                "warning",
                "workspace.yaml does not record a project file naming policy.",
                ".agent/workspace.yaml#$.naming.project_files",
                "Use time_topic_kind naming so files sort by time and remain recognizable by content.",
            )
        ]
    project_files = naming.get("project_files")
    if not isinstance(project_files, dict):
        return [
            Diagnostic(
                "project.project_file_naming_missing",
                "warning",
                "workspace.yaml does not declare naming.project_files.",
                ".agent/workspace.yaml#$.naming.project_files",
                "Declare strategy, timezone, timestamp_format, and max_slug_words for project files.",
            )
        ]
    for field in REQUIRED_PROJECT_FILE_NAMING_FIELDS:
        if not project_files.get(field):
            diagnostics.append(
                Diagnostic(
                    "project.project_file_naming_missing",
                    "warning",
                    f"Project file naming field '{field}' is not declared.",
                    f".agent/workspace.yaml#$.naming.project_files.{field}",
                    "Record a time-first naming policy before creating project artifacts.",
                )
            )
    if project_files.get("strategy") and project_files.get("strategy") != "time_topic_kind":
        diagnostics.append(
            Diagnostic(
                "project.project_file_naming_invalid",
                "error",
                "Project file naming strategy is not supported.",
                ".agent/workspace.yaml#$.naming.project_files.strategy",
                "Use strategy: time_topic_kind.",
            )
        )
    if project_files.get("timezone") and project_files.get("timezone") != "UTC":
        diagnostics.append(
            Diagnostic(
                "project.project_file_naming_invalid",
                "error",
                "Project file naming timezone is not supported.",
                ".agent/workspace.yaml#$.naming.project_files.timezone",
                "Use timezone: UTC for stable cross-machine ordering.",
            )
        )
    max_slug_words = project_files.get("max_slug_words")
    if max_slug_words and not isinstance(max_slug_words, int):
        diagnostics.append(
            Diagnostic(
                "project.project_file_naming_invalid",
                "error",
                "Project file naming max_slug_words must be an integer.",
                ".agent/workspace.yaml#$.naming.project_files.max_slug_words",
                "Use an integer between 1 and 12.",
            )
        )
    return diagnostics


def _schema_diagnostics(
    rule: str,
    source_path: str,
    schema_name: str,
    payload: object,
) -> list[Diagnostic]:
    return [
        Diagnostic(
            rule,
            "error",
            f"Schema violation at {issue.location}: {issue.message}",
            f"{source_path}#{issue.location}",
            issue.suggestion,
        )
        for issue in validate_schema(payload, schema_name)
    ]


def _path_escapes_root(root: Path, entry: str) -> bool:
    path = Path(entry)
    if path.is_absolute():
        target = path.resolve()
    else:
        target = (root / path).resolve()
    try:
        target.relative_to(root.resolve())
        return False
    except ValueError:
        return True


def _lint_registry(data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    artifact_ids: set[str] = set()
    root = Path(data["root"])
    registry_path = root / ".agent" / "registry.yaml"
    if registry_path.exists():
        result = load_yaml_safe(registry_path)
        if result.error:
            diagnostics.append(
                Diagnostic(
                    "project.registry_yaml_invalid",
                    "error",
                    f"registry.yaml could not be parsed: {result.error}",
                    ".agent/registry.yaml",
                    "Fix registry.yaml syntax.",
                )
            )
            return diagnostics
        diagnostics.extend(
            _schema_diagnostics(
                "project.registry_schema_invalid",
                ".agent/registry.yaml",
                "artifact-registry",
                result.data,
            )
        )
    for artifact in data["artifacts"]:
        if artifact.get("_load_error"):
            continue
        artifact_id = str(artifact.get("id", ""))
        path = str(artifact.get("path", "")).replace("\\", "/")
        kind = artifact.get("kind")
        read_policy = artifact.get("read_policy")
        if artifact_id in artifact_ids:
            diagnostics.append(
                Diagnostic(
                    "project.duplicate_artifact_id",
                    "error",
                    f"Artifact id '{artifact_id}' is declared more than once.",
                    path or ".agent/registry.yaml",
                    "Make registry ids stable and unique.",
                )
            )
        artifact_ids.add(artifact_id)
        if path and not (root / path).exists():
            diagnostics.append(
                Diagnostic(
                    "project.artifact_path_missing",
                    "error",
                    "Registered artifact path does not exist.",
                    path,
                    "Fix the registry path or create the missing artifact.",
                )
            )
        if (kind == "report" or path.startswith(".agent/reports/")) and read_policy not in {
            "explicit_only",
            "never_default",
        }:
            diagnostics.append(
                Diagnostic(
                    "project.report_read_policy_not_explicit",
                    "error",
                    "Reports must not be loaded by default.",
                    path,
                    "Use read_policy: explicit_only for raw reports.",
                )
            )
        if path.startswith(".agent/archive/") and read_policy not in {
            "explicit_only",
            "never_default",
        }:
            diagnostics.append(
                Diagnostic(
                    "project.archive_read_policy_not_never_default",
                    "error",
                    "Archived files must not be loaded by default.",
                    path,
                    "Use read_policy: never_default or explicit_only for archived artifacts.",
                )
            )
        if kind == "knowledge" and not read_policy:
            diagnostics.append(
                Diagnostic(
                    "project.knowledge_without_read_policy",
                    "warning",
                    "Knowledge artifact has no read policy.",
                    path,
                    "Choose task_based or a narrower task-specific read policy.",
                )
            )
    return diagnostics


def _lint_context_modules(data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    registered_context_paths = {
        str(item.get("path", "")).replace("\\", "/")
        for item in data["artifacts"]
        if item.get("kind") == "context_module"
    }
    for module in data["context_modules"]:
        path = str(module.get("_module_path", ""))
        if module.get("_load_error"):
            diagnostics.append(
                Diagnostic(
                    "project.context_module_invalid_yaml",
                    "error",
                    f"Context module could not be parsed: {module['_load_error']}",
                    path,
                    "Fix module.yaml syntax.",
                )
            )
            continue
        if not module.get("source_files"):
            diagnostics.append(
                Diagnostic(
                    "project.context_module_missing_source_files",
                    "error",
                    "Context module does not declare source_files.",
                    path,
                    "List the knowledge files or reports this module was distilled from.",
                )
            )
        if path and path not in registered_context_paths:
            diagnostics.append(
                Diagnostic(
                    "project.context_module_not_registered",
                    "error",
                    "Context module exists but is not registered as a context_module artifact.",
                    path,
                    "Add a context_module entry to .agent/registry.yaml.",
                )
            )
    return diagnostics


def _lint_memory_stores(root: Path, data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    registered_paths = {
        str(store.get("path", "")).replace("\\", "/")
        for store in data["memory_stores"]
        if store.get("path")
    }
    for detected in data["detected_memory_locations"]:
        detected_path = str(detected.get("path", "")).replace("\\", "/")
        if detected_path and detected_path not in registered_paths:
            diagnostics.append(
                Diagnostic(
                    "memory.store_unregistered",
                    "warning",
                    "Detected a memory or rule location that is not declared in .agent/memory-stores.yaml.",
                    detected_path,
                    "Register the store so ownership, privacy, read policy, and promotion rules are explicit.",
                )
            )
    for store in data["memory_stores"]:
        path = str(store.get("_memory_file", ".agent/memory-stores.yaml"))
        if store.get("_load_error"):
            diagnostics.append(
                Diagnostic(
                    "memory.store_yaml_invalid",
                    "error",
                    f"memory-stores.yaml could not be parsed: {store['_load_error']}",
                    path,
                    "Fix memory-stores.yaml syntax.",
                )
            )
            continue
        for field in ["id", "name", "scope", "store_type", "path", "read_policy", "write_policy", "owner", "privacy"]:
            if not store.get(field):
                diagnostics.append(
                    Diagnostic(
                        "memory.store_missing_required_field",
                        "error",
                        f"Memory store is missing required field '{field}'.",
                        path,
                        "Declare memory store identity, ownership, privacy, and read/write policy.",
                    )
                )
        store_path = str(store.get("path", ""))
        if store.get("store_type") == "filesystem" and store_path and not _is_external_path(store_path):
            target = (root / store_path).resolve()
            if not target.exists():
                diagnostics.append(
                    Diagnostic(
                        "memory.store_path_missing",
                        "warning",
                        "Filesystem memory store path does not exist.",
                        store_path,
                        "Create the store path or remove the stale memory store declaration.",
                    )
                )
        if "forget_policy" not in store and "supersession_policy" not in store:
            diagnostics.append(
                Diagnostic(
                    "memory.no_forget_policy",
                    "warning",
                    "Memory store has no forget or supersession policy.",
                    path,
                    "Declare how memory records are deleted, archived, superseded, or exported.",
                )
            )
        read_policy = store.get("read_policy")
        if read_policy == "default":
            target = root / store_path
            budget = int(store.get("default_token_budget") or 4000)
            for file_path in list_files(target) if target.exists() else []:
                estimated_tokens = max(1, file_path.stat().st_size // 4)
                if estimated_tokens > budget:
                    diagnostics.append(
                        Diagnostic(
                            "memory.default_blob_too_large",
                            "warning",
                            "Default-loaded memory file exceeds the store token budget.",
                            _relative_or_absolute(file_path, root),
                            "Use task_based retrieval or split the memory into smaller records.",
                        )
                    )
    return diagnostics


def _lint_memory_records(root: Path, data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    store_ids = {str(store.get("id")) for store in data["memory_stores"] if store.get("id")}
    active_keys: dict[str, list[str]] = defaultdict(list)
    for record in data["memory_records"]:
        path = str(record.get("_memory_file", ".agent/memory-records.yaml"))
        if record.get("_load_error"):
            diagnostics.append(
                Diagnostic(
                    "memory.record_yaml_invalid",
                    "error",
                    f"memory-records.yaml could not be parsed: {record['_load_error']}",
                    path,
                    "Fix memory-records.yaml syntax.",
                )
            )
            continue
        for field in ["id", "store_id", "kind", "scope", "namespace", "status", "content_path", "confidence", "review_status", "retrieval_keys", "read_policy"]:
            if not record.get(field):
                diagnostics.append(
                    Diagnostic(
                        "memory.record_missing_required_field",
                        "error",
                        f"Memory record is missing required field '{field}'.",
                        path,
                        "Declare memory record identity, provenance, review state, and retrieval metadata.",
                    )
                )
        store_id = str(record.get("store_id", ""))
        if store_id and store_id not in store_ids:
            diagnostics.append(
                Diagnostic(
                    "memory.record_unknown_store",
                    "error",
                    "Memory record references an unknown memory store.",
                    path,
                    "Declare the store in .agent/memory-stores.yaml or fix store_id.",
                )
            )
        content_path = str(record.get("content_path", ""))
        if content_path and not (root / content_path).exists():
            diagnostics.append(
                Diagnostic(
                    "memory.record_content_missing",
                    "error",
                    "Memory record content_path does not exist.",
                    content_path,
                    "Create the content file or update the memory record path.",
                )
            )
        if record.get("status") in {"active", "promoted"} and not record.get("evidence_refs"):
            diagnostics.append(
                Diagnostic(
                    "memory.no_source_evidence",
                    "error",
                    "Active or promoted memory record has no source evidence.",
                    path,
                    "Add evidence_refs pointing to reports, decisions, or reviewed artifacts.",
                )
            )
        if record.get("status") in {"active", "promoted"} and record.get("confidence") == "generated" and record.get("review_status") != "reviewed":
            diagnostics.append(
                Diagnostic(
                    "memory.generated_unreviewed",
                    "error",
                    "Generated memory is active without review.",
                    path,
                    "Keep generated memory as candidate until reviewed or mark review_status: reviewed.",
                )
            )
        if record.get("status") in {"active", "promoted"}:
            for key in record.get("retrieval_keys") or []:
                active_keys[str(key).lower()].append(str(record.get("id", path)))
    for key, record_ids in active_keys.items():
        if len(record_ids) > 1:
            diagnostics.append(
                Diagnostic(
                    "memory.conflicting_records",
                    "warning",
                    f"Multiple active memory records share retrieval key '{key}'.",
                    ", ".join(record_ids),
                    "Check whether one record supersedes another or narrow their retrieval keys.",
                )
            )
    return diagnostics


def _lint_skills(root: Path, data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    names: dict[str, list[str]] = defaultdict(list)
    triggers: dict[str, list[str]] = defaultdict(list)
    generated_registry_paths = {
        str(item.get("path", "")).replace("\\", "/")
        for item in data["artifacts"]
        if item.get("kind") == "generated_skill"
    }

    for skill in data["skills"]:
        skill_dir = str(skill.get("_skill_dir", ""))
        manifest_path = str(skill.get("_manifest_path", ""))
        if skill.get("_missing_manifest"):
            diagnostics.append(
                Diagnostic(
                    "skill.missing_manifest",
                    "error",
                    "Skill directory has SKILL.md but no manifest.yaml.",
                    skill_dir,
                    "Add manifest.yaml with kind, tags, triggers, dependencies, and outputs.",
                )
            )
            continue
        if skill.get("_load_error"):
            diagnostics.append(
                Diagnostic(
                    "skill.invalid_manifest_yaml",
                    "error",
                    f"Skill manifest could not be parsed: {skill['_load_error']}",
                    manifest_path,
                    "Fix manifest.yaml syntax.",
                )
            )
            continue
        schema_payload = skill.get("_schema_data")
        if schema_payload is None:
            schema_payload = {key: value for key, value in skill.items() if not key.startswith("_")}
        diagnostics.extend(
            _schema_diagnostics(
                "skill.manifest_schema_invalid",
                manifest_path,
                "skill-manifest",
                schema_payload,
            )
        )
        name = str(skill.get("name", ""))
        if name:
            names[name].append(manifest_path)
        for trigger in skill.get("triggers") or []:
            triggers[str(trigger).strip().lower()].append(manifest_path)
        diagnostics.extend(_lint_skill_required_fields(skill, manifest_path))
        diagnostics.extend(_lint_skill_reproducibility(skill, manifest_path))
        diagnostics.extend(_lint_skill_local_paths(root, skill, manifest_path))
        diagnostics.extend(_lint_skill_memory_effects(skill, manifest_path))
        diagnostics.extend(_lint_skill_boundaries(root, skill, manifest_path))
        if "/generated/" in skill_dir.replace("\\", "/") and skill_dir not in generated_registry_paths:
            diagnostics.append(
                Diagnostic(
                    "project.generated_skill_not_registered",
                    "error",
                    "Generated skill is not registered in .agent/registry.yaml.",
                    skill_dir,
                    "Add a generated_skill artifact entry so generated behavior is traceable.",
                )
            )

    for name, paths in names.items():
        if len(paths) > 1:
            diagnostics.append(
                Diagnostic(
                    "skill.duplicate_name",
                    "error",
                    f"Skill name '{name}' is used by multiple manifests.",
                    ", ".join(paths),
                    "Rename or archive duplicate skills to prevent ambiguous routing.",
                )
            )
    for trigger, paths in triggers.items():
        if trigger and len(paths) > 1:
            diagnostics.append(
                Diagnostic(
                    "skill.overlapping_trigger",
                    "warning",
                    f"Trigger '{trigger}' appears in multiple active skill manifests.",
                    ", ".join(paths),
                    "Tighten triggers or add negative_triggers to avoid skill conflicts.",
                )
            )
    return diagnostics


def _lint_skill_required_fields(skill: dict[str, Any], manifest_path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for field in ["id", "name", "kind", "version", "status", "scope", "summary"]:
        if not skill.get(field):
            diagnostics.append(
                Diagnostic(
                    "skill.missing_required_field",
                    "error",
                    f"Skill manifest is missing required field '{field}'.",
                    manifest_path,
                    f"Add {field} to the skill manifest.",
                )
            )
    required = [
        ("tags", "skill.missing_tags", "Skill manifest has no tags."),
        ("triggers", "skill.missing_triggers", "Skill manifest has no triggers."),
        (
            "negative_triggers",
            "skill.missing_negative_triggers",
            "Skill manifest has no negative_triggers.",
        ),
    ]
    for field, rule, message in required:
        if not skill.get(field):
            diagnostics.append(
                Diagnostic(
                    rule,
                    "error",
                    message,
                    manifest_path,
                    f"Declare {field} to make skill selection explicit.",
                )
            )
    expected_scope = {
        "global_skill": "global",
        "functional_skill": "functional",
        "common_project_skill": "common_project",
        "project_skill": "project",
        "generated_project_skill": "generated",
        "governance_skill": "global",
    }.get(skill.get("kind"))
    if expected_scope and skill.get("scope") != expected_scope:
        diagnostics.append(
            Diagnostic(
                "skill.kind_scope_mismatch",
                "error",
                f"Skill kind '{skill.get('kind')}' should use scope '{expected_scope}'.",
                manifest_path,
                "Align kind and scope so routing layers stay unambiguous.",
            )
        )
    return diagnostics


def _lint_skill_reproducibility(skill: dict[str, Any], manifest_path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if skill.get("kind") != "common_project_skill":
        return diagnostics
    reproducibility = skill.get("reproducibility") or {}
    expected_true = ["required", "scripts_local", "templates_local", "references_local"]
    if not all(reproducibility.get(key) is True for key in expected_true):
        diagnostics.append(
            Diagnostic(
                "skill.common_project_missing_reproducibility",
                "error",
                "Common project skill does not prove local reproducibility.",
                manifest_path,
                "Set reproducibility.required/scripts_local/templates_local/references_local to true.",
            )
        )
    return diagnostics


def _lint_skill_local_paths(root: Path, skill: dict[str, Any], manifest_path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    skill_root = (root / str(skill.get("_skill_dir", ""))).resolve()
    manifest_paths = skill.get("paths") or {}
    for field in ["scripts", "templates", "references", "tests"]:
        declared = manifest_paths.get(field)
        if not declared:
            continue
        target = (skill_root / str(declared)).resolve()
        if not _is_relative_to(target, skill_root):
            diagnostics.append(
                Diagnostic(
                    "skill.path_outside_skill_root",
                    "error",
                    f"Skill path '{field}' points outside the skill directory.",
                    manifest_path,
                    "Keep scripts, templates, references, and tests inside the skill directory.",
                )
            )
            continue
        if field != "tests" and skill.get("kind") == "common_project_skill":
            reproducibility = skill.get("reproducibility") or {}
            flag_name = f"{field}_local"
            if reproducibility.get(flag_name) is True and not target.exists():
                diagnostics.append(
                    Diagnostic(
                        "skill.local_path_missing",
                        "error",
                        f"Common project skill declares local {field}, but the directory is missing.",
                        _relative_or_absolute(target, root),
                        "Create the declared directory under the skill path.",
                    )
                )
            elif reproducibility.get(flag_name) is True and not list_files(target):
                diagnostics.append(
                    Diagnostic(
                        "skill.local_path_empty",
                        "warning",
                        f"Common project skill declares local {field}, but the directory has no files.",
                        _relative_or_absolute(target, root),
                        "Add the reproducibility material or set the reproducibility flag honestly.",
                    )
                )
    dependencies = skill.get("dependencies") or {}
    for file_path in dependencies.get("files") or []:
        target = (skill_root / str(file_path)).resolve()
        if not _is_relative_to(target, skill_root):
            diagnostics.append(
                Diagnostic(
                    "skill.dependency_file_outside_skill_root",
                    "error",
                    "Skill dependency file points outside the skill directory.",
                    manifest_path,
                    "Copy required dependency files into the skill directory or declare a command dependency instead.",
                )
            )
        elif not target.exists():
            diagnostics.append(
                Diagnostic(
                    "skill.declared_dependency_file_missing",
                    "error",
                    "Skill dependency file is declared but missing.",
                    _relative_or_absolute(target, root),
                    "Add the dependency file under the skill directory.",
                )
            )
    for command in dependencies.get("commands") or []:
        if not shutil.which(str(command)):
            diagnostics.append(
                Diagnostic(
                    "skill.command_dependency_missing",
                    "warning",
                    f"Declared command dependency '{command}' is not available on PATH.",
                    manifest_path,
                    "Install the command or run this skill in an environment that provides it.",
                )
            )
    return diagnostics


def _lint_skill_memory_effects(skill: dict[str, Any], manifest_path: str) -> list[Diagnostic]:
    permissions = skill.get("permissions") or {}
    writes = [str(item) for item in permissions.get("writes") or []]
    proposes = [str(item) for item in permissions.get("proposes") or []]
    memoryish_permissions = [item for item in writes + proposes if item.startswith("memory.") or item.startswith("mem.")]
    if memoryish_permissions and not skill.get("memory_effects"):
        return [
            Diagnostic(
                "skill.memory_side_effect_undeclared",
                "error",
                "Skill declares memory writes/proposals but has no memory_effects section.",
                manifest_path,
                "Declare memory_effects reads/writes/consolidates/forbidden and memory budgets.",
            )
        ]
    return []


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_external_path(path: str) -> bool:
    expanded = Path(path).expanduser()
    return path.startswith("~") or expanded.is_absolute()


def _lint_skill_boundaries(root: Path, skill: dict[str, Any], manifest_path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    skill_md = root / str(skill.get("_skill_dir", "")) / "SKILL.md"
    text = read_text(skill_md) if skill_md.exists() else ""
    kind = skill.get("kind")
    if kind == "global_skill" and len(text.splitlines()) > GLOBAL_SKILL_MAX_LINES:
        diagnostics.append(
            Diagnostic(
                "skill.global_too_large",
                "warning",
                "Global skill is too large for a highest-priority policy layer.",
                manifest_path,
                "Keep global skills as lightweight governance policy.",
            )
        )
    project_markers = [".agent/", ".agents/", "project-specific", "deployment", "database"]
    lowered = text.lower()
    if kind == "global_skill" and any(marker in lowered for marker in project_markers):
        diagnostics.append(
            Diagnostic(
                "skill.global_contains_project_fact",
                "warning",
                "Global skill appears to contain project-specific facts or paths.",
                manifest_path,
                "Move project facts into project knowledge, context modules, or project skills.",
            )
        )
    if kind == "functional_skill" and any(marker in lowered for marker in [".agent/knowledge", "project-specific deployment"]):
        diagnostics.append(
            Diagnostic(
                "skill.functional_contains_project_fact",
                "warning",
                "Functional skill appears to contain project-private knowledge.",
                manifest_path,
                "Keep functional skills general and let project skills provide local facts.",
            )
        )
    return diagnostics
