from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ArchMarshalError
from .io import load_yaml_safe
from .safety import EXCLUDED_BACKUP_PARTS, is_link_or_reparse
from .user_store import read_user_store_active

PROJECT_FILE_KINDS = (
    "checkpoints",
    "reports",
    "plans",
    "history",
    "knowledge",
    "artifacts",
)
SKILL_SAVE_KINDS = ("project", "generated")
SAVE_PATH_KEYS = {
    **{key: ("project_files", key) for key in PROJECT_FILE_KINDS},
    **{f"project_files.{key}": ("project_files", key) for key in PROJECT_FILE_KINDS},
    **{f"skills.{key}": ("skills", key) for key in SKILL_SAVE_KINDS},
}
DEFAULT_SAVE_PATHS = {
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
}
DEFAULT_NAMING = {
    "project_files": {
        "strategy": "time_topic_kind",
        "timezone": "UTC",
        "date_partition": "YYYY/MM/DD",
        "timestamp_format": "%Y%m%d-%H%M%S",
        "max_slug_words": 6,
    }
}
DETECTED_PATH_CANDIDATES = {
    "checkpoints": ("checkpoints", "docs/checkpoints", ".agent/inbox/checkpoints"),
    "reports": ("reports", "docs/reports", ".agent/reports"),
    "plans": ("plans", "docs/plans", ".agent/plans"),
    "history": ("history", "records", "docs/history", ".agent/history"),
    "knowledge": ("knowledge", "docs/knowledge", ".agent/knowledge"),
    "artifacts": ("artifacts", "outputs", "results", ".agent/inbox"),
}
DETECTED_SKILL_CANDIDATES = {
    "project": (
        ".agents/skills/project",
        ".codex/skills/project",
        ".claude/skills/project",
        "skills/project",
    ),
    "generated": (
        ".agents/skills/generated",
        ".codex/skills/generated",
        ".claude/skills/generated",
        "skills/generated",
    ),
}
FORBIDDEN_SAVE_PARTS = {
    *EXCLUDED_BACKUP_PARTS,
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
SUPPORTED_STRATEGIES = {
    "time_topic_kind",
    "date_topic_kind",
    "topic_kind",
    "preserve",
}
SUPPORTED_PARTITIONS = {"none", "YYYY", "YYYY/MM", "YYYY/MM/DD"}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{index}" for index in range(1, 10)},
    *{f"LPT{index}" for index in range(1, 10)},
}


def parse_save_path_assignments(values: Iterable[str] | None) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {"skills": {}, "project_files": {}}
    for raw in values or []:
        if not isinstance(raw, str) or "=" not in raw:
            raise ArchMarshalError(
                "layout_assignment_invalid",
                "Save-path overrides must use kind=project/relative/path.",
                details={"value": raw},
            )
        key, value = (item.strip() for item in raw.split("=", 1))
        target = SAVE_PATH_KEYS.get(key)
        if target is None or not value:
            raise ArchMarshalError(
                "layout_assignment_invalid",
                "Save-path override has an unknown kind or an empty path.",
                details={"value": raw, "supported": sorted(SAVE_PATH_KEYS)},
            )
        section, role = target
        if role in result[section]:
            raise ArchMarshalError(
                "layout_assignment_duplicate",
                "A save-path role may be set only once per preview.",
                details={"role": f"{section}.{role}"},
            )
        result[section][role] = value
    return result


def build_layout_plan(
    root: Path,
    *,
    configured: bool,
    save_path_overrides: Iterable[str] | None = None,
    naming_overrides: dict[str, Any] | None = None,
    user_store: Path | str | None = None,
    effective_skill_roots: Iterable[str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    explicit_paths = parse_save_path_assignments(save_path_overrides)
    explicit_naming = {
        key: value for key, value in (naming_overrides or {}).items() if value is not None
    }
    project_profile = _project_profile(root) if configured else None
    user_profile, user_evidence = _confirmed_user_profile(user_store)
    detected_profile, detected_evidence = _detect_profile(root)

    save_paths = deepcopy(DEFAULT_SAVE_PATHS)
    naming = deepcopy(DEFAULT_NAMING)
    provenance = {
        f"save_paths.{section}.{key}": "archmarshal_default"
        for section, values in save_paths.items()
        for key in values
    }
    provenance.update(
        {f"naming.project_files.{key}": "archmarshal_default" for key in naming["project_files"]}
    )

    has_cli = any(explicit_paths.values()) or bool(explicit_naming)
    if project_profile is not None:
        foundation = "confirmed"
        source = "project_config"
        decision = "preserve"
        requires_confirmation = False
        _merge_profile(save_paths, naming, provenance, project_profile, "project_config")
    else:
        if user_profile is not None:
            _merge_profile(
                save_paths,
                naming,
                provenance,
                user_profile,
                "confirmed_user_profile",
            )
        if detected_profile is not None and user_profile is None and not has_cli:
            _merge_profile(save_paths, naming, provenance, detected_profile, "detected")
        if any(explicit_paths.values()) or explicit_naming:
            _merge_profile(
                save_paths,
                naming,
                provenance,
                {"save_paths": explicit_paths, "naming": {"project_files": explicit_naming}},
                "cli",
            )
        if has_cli:
            foundation = "confirmed"
            source = "cli"
            decision = "preserve"
            requires_confirmation = False
        elif user_profile is not None:
            foundation = "confirmed"
            source = "confirmed_user_profile"
            decision = "preserve"
            requires_confirmation = False
        elif detected_profile is not None:
            foundation = "detected"
            source = "detected"
            decision = "preserve"
            requires_confirmation = True
        else:
            foundation = "none"
            source = "archmarshal_default"
            decision = "initialize"
            requires_confirmation = False

    issues = _validate_profile(root, save_paths, naming)
    recommendations = _layout_recommendations(root, save_paths)
    quality = "unsafe" if issues else "needs_optimization" if recommendations else "reasonable"
    if quality == "needs_optimization":
        decision = "suggest_only"
    evidence: list[dict[str, Any]] = []
    if project_profile is not None:
        evidence.append({"kind": "project_config", "path": ".agent/workspace.yaml"})
    evidence.extend(user_evidence)
    evidence.extend(detected_evidence)
    if has_cli:
        evidence.append(
            {
                "kind": "cli",
                "save_path_roles": sorted(
                    f"{section}.{key}"
                    for section, values in explicit_paths.items()
                    for key in values
                ),
                "naming_fields": sorted(explicit_naming),
            }
        )
    profile = {
        "save_paths": save_paths,
        "naming": naming,
        "skill_roots": sorted(set(effective_skill_roots or []), key=str.casefold),
    }
    return {
        "foundation": foundation,
        "quality": quality,
        "decision": decision,
        "source": source,
        "requires_confirmation": requires_confirmation,
        "effective_profile": profile,
        "field_provenance": dict(sorted(provenance.items())),
        "evidence": evidence,
        "issues": issues,
        "recommendations": recommendations,
        "user_store": user_evidence[0] if user_evidence else None,
    }


def workspace_layout_metadata(layout: dict[str, Any]) -> dict[str, Any]:
    return {
        "foundation": layout["foundation"],
        "source": layout["source"],
        "confirmed": layout["foundation"] == "confirmed",
    }


def _project_profile(root: Path) -> dict[str, Any] | None:
    path = root / ".agent" / "workspace.yaml"
    loaded = load_yaml_safe(path)
    if loaded.error or not isinstance(loaded.data, dict):
        return None
    data = loaded.data
    return {
        "save_paths": data.get("save_paths") if isinstance(data.get("save_paths"), dict) else {},
        "naming": data.get("naming") if isinstance(data.get("naming"), dict) else {},
    }


def _confirmed_user_profile(
    user_store: Path | str | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if user_store is None:
        return None, []
    active = read_user_store_active(user_store)
    value = active.get("preference_values", {}).get("preferred.workspace_layout")
    evidence = [
        {
            "kind": "confirmed_user_profile",
            "user_store": active["root"],
            "head": active["head"],
            "preference_key": "preferred.workspace_layout",
            "available": isinstance(value, dict),
        }
    ]
    if not isinstance(value, dict):
        return None, evidence
    confirmed = value.get("confirmed") is True or value.get("status") == "confirmed"
    if not confirmed:
        evidence[0]["ignored_reason"] = "profile_not_explicitly_confirmed"
        return None, evidence
    profile = value.get("profile") if isinstance(value.get("profile"), dict) else value
    return profile, evidence


def _detect_profile(root: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    save_paths: dict[str, dict[str, str]] = {"project_files": {}, "skills": {}}
    evidence: list[dict[str, Any]] = []
    for role, candidates in DETECTED_PATH_CANDIDATES.items():
        for relative in candidates:
            candidate = root / PurePosixPath(relative)
            if candidate.is_dir() and not is_link_or_reparse(candidate):
                save_paths["project_files"][role] = relative
                evidence.append({"kind": "detected_path", "role": role, "path": relative})
                break
    for role, candidates in DETECTED_SKILL_CANDIDATES.items():
        for relative in candidates:
            candidate = root / PurePosixPath(relative)
            if candidate.is_dir() and not is_link_or_reparse(candidate):
                save_paths["skills"][role] = relative
                evidence.append(
                    {"kind": "detected_path", "role": f"skills.{role}", "path": relative}
                )
                break
    if not evidence:
        return None, []
    return {"save_paths": save_paths}, evidence


def _merge_profile(
    save_paths: dict[str, dict[str, str]],
    naming: dict[str, dict[str, Any]],
    provenance: dict[str, str],
    profile: dict[str, Any],
    source: str,
) -> None:
    proposed_paths = profile.get("save_paths")
    if isinstance(proposed_paths, dict):
        for section in ("skills", "project_files"):
            values = proposed_paths.get(section)
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                if key in save_paths[section] and isinstance(value, str) and value:
                    save_paths[section][key] = value
                    provenance[f"save_paths.{section}.{key}"] = source
    proposed_naming = profile.get("naming")
    if isinstance(proposed_naming, dict):
        values = proposed_naming.get("project_files")
        if isinstance(values, dict):
            for key, value in values.items():
                if value is not None:
                    naming["project_files"][key] = value
                    provenance[f"naming.project_files.{key}"] = source


def _validate_profile(
    root: Path,
    save_paths: dict[str, dict[str, str]],
    naming: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for section, values in save_paths.items():
        for role, value in values.items():
            issue = _validate_relative_destination(root, value)
            if issue:
                issues.append(
                    {
                        "code": issue,
                        "field": f"save_paths.{section}.{role}",
                        "path": value,
                        "severity": "error",
                    }
                )
    policy = naming.get("project_files") or {}
    strategy = policy.get("strategy")
    if strategy not in SUPPORTED_STRATEGIES:
        issues.append(
            {
                "code": "naming_strategy_invalid",
                "field": "naming.project_files.strategy",
                "value": strategy,
                "severity": "error",
            }
        )
    timezone_name = policy.get("timezone")
    if not _valid_timezone(timezone_name):
        issues.append(
            {
                "code": "naming_timezone_invalid",
                "field": "naming.project_files.timezone",
                "value": timezone_name,
                "severity": "error",
            }
        )
    partition = policy.get("date_partition", "YYYY/MM/DD")
    if partition not in SUPPORTED_PARTITIONS:
        issues.append(
            {
                "code": "date_partition_invalid",
                "field": "naming.project_files.date_partition",
                "value": partition,
                "severity": "error",
            }
        )
    timestamp_format = policy.get("timestamp_format")
    if (
        not isinstance(timestamp_format, str)
        or not timestamp_format
        or any(token in timestamp_format for token in ("/", "\\", "\x00", "\n", "\r", ":"))
    ):
        issues.append(
            {
                "code": "timestamp_format_unsafe",
                "field": "naming.project_files.timestamp_format",
                "value": timestamp_format,
                "severity": "error",
            }
        )
    max_slug_words = policy.get("max_slug_words")
    if (
        not isinstance(max_slug_words, int)
        or isinstance(max_slug_words, bool)
        or not 1 <= max_slug_words <= 12
    ):
        issues.append(
            {
                "code": "max_slug_words_invalid",
                "field": "naming.project_files.max_slug_words",
                "value": max_slug_words,
                "severity": "error",
            }
        )
    if strategy == "preserve" and not policy.get("filename_template"):
        issues.append(
            {
                "code": "preserve_filename_requires_input",
                "field": "naming.project_files.filename_template",
                "severity": "requires_user_input",
            }
        )
    return issues


def _validate_relative_destination(root: Path, raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        return "save_path_invalid"
    normalized = raw.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or re.match(r"^[A-Za-z]:/", normalized) or ".." in path.parts:
        return "save_path_outside_project"
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts:
        return "save_path_project_root"
    if unicodedata.normalize("NFC", normalized) != normalized:
        return "save_path_not_portable"
    for part in parts:
        if (
            part.endswith((" ", "."))
            or ":" in part
            or part.split(".", 1)[0].rstrip(" .").upper() in WINDOWS_RESERVED_NAMES
        ):
            return "save_path_not_portable"
        if part.casefold() in {item.casefold() for item in FORBIDDEN_SAVE_PARTS}:
            return "save_path_runtime_boundary"
    current = root
    for part in parts:
        current = current / part
        if is_link_or_reparse(current):
            return "save_path_link_boundary"
    target = root.joinpath(*parts)
    if target.exists() and not target.is_dir():
        return "save_path_file_conflict"
    return None


def _layout_recommendations(
    root: Path,
    save_paths: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    code_roots = {name for name in ("src", "app", "lib", "packages") if (root / name).is_dir()}
    for section, values in save_paths.items():
        for role, value in values.items():
            first = PurePosixPath(value).parts[0] if PurePosixPath(value).parts else ""
            if first not in code_roots:
                continue
            recommendations.append(
                {
                    "code": "generated_output_inside_code_root",
                    "field": f"save_paths.{section}.{role}",
                    "from": value,
                    "to": DEFAULT_SAVE_PATHS[section][role],
                    "reason": "Generated or append-only management output shares a source-code root.",
                    "action": "suggest_only",
                }
            )
    return recommendations


def _valid_timezone(value: Any) -> bool:
    if value in {"UTC", "local"}:
        return True
    if not isinstance(value, str) or not value or len(value) > 128:
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


__all__ = [
    "DEFAULT_NAMING",
    "DEFAULT_SAVE_PATHS",
    "PROJECT_FILE_KINDS",
    "SKILL_SAVE_KINDS",
    "build_layout_plan",
    "parse_save_path_assignments",
    "workspace_layout_metadata",
]
