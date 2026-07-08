from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .inventory import collect_inventory
from .io import read_text


GLOBAL_SKILL_MAX_LINES = 120
AGENTS_MD_MAX_BYTES = 6000
HISTORICAL_PREFIXES = (
    ".agent/reports/",
    ".agent/history/",
    ".agent/archive/",
    ".agent/cache/",
)


def lint_workspace(root: Path | str) -> list[Diagnostic]:
    inventory = collect_inventory(root)
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_lint_project_files(inventory.root, inventory.to_dict()))
    diagnostics.extend(_lint_registry(inventory.to_dict()))
    diagnostics.extend(_lint_context_modules(inventory.to_dict()))
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


def _lint_registry(data: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    artifact_ids: set[str] = set()
    root = Path(data["root"])
    for artifact in data["artifacts"]:
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
        name = str(skill.get("name", ""))
        if name:
            names[name].append(manifest_path)
        for trigger in skill.get("triggers") or []:
            triggers[str(trigger).strip().lower()].append(manifest_path)
        diagnostics.extend(_lint_skill_required_fields(skill, manifest_path))
        diagnostics.extend(_lint_skill_reproducibility(skill, manifest_path))
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
    if skill.get("kind") != "common_project_skill":
        return []
    reproducibility = skill.get("reproducibility") or {}
    expected_true = ["required", "scripts_local", "templates_local", "references_local"]
    if all(reproducibility.get(key) is True for key in expected_true):
        return []
    return [
        Diagnostic(
            "skill.common_project_missing_reproducibility",
            "error",
            "Common project skill does not prove local reproducibility.",
            manifest_path,
            "Set reproducibility.required/scripts_local/templates_local/references_local to true.",
        )
    ]


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
