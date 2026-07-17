from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import yaml

from .errors import ArchMarshalError
from .safety import is_link_or_reparse

MAX_SKILL_MD_BYTES = 512 * 1024
MAX_SKILL_MD_LINES = 500
MAX_DESCRIPTION_LENGTH = 2048
MAX_LICENSE_LENGTH = 1024
MAX_ALLOWED_TOOLS_LENGTH = 4096
MAX_ALLOWED_TOOL_COUNT = 64
MAX_METADATA_BYTES = 16 * 1024
MAX_METADATA_DEPTH = 4
MAX_METADATA_ITEMS = 128
MAX_METADATA_KEY_LENGTH = 128
MAX_METADATA_STRING_LENGTH = 2048
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMPATIBILITY_FRONTMATTER_FIELDS = {"license", "allowed-tools", "metadata"}
ALLOWED_FRONTMATTER_FIELDS = {"name", "description", *COMPATIBILITY_FRONTMATTER_FIELDS}


def validate_skill_package(skill_dir: Path, *, enforce_folder_name: bool = True) -> dict[str, Any]:
    """Validate the human-facing Codex Skill contract without executing package code."""
    directory = Path(skill_dir)
    skill_md = directory / "SKILL.md"
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    frontmatter: dict[str, Any] = {}

    if is_link_or_reparse(directory) or not directory.is_dir():
        errors.append(_issue("skill_directory_invalid", "Skill directory is missing or linked."))
        return {
            "format": "archmarshal-skill-validation-v1",
            "valid": False,
            "frontmatter": {"name": None, "description": None, "extensions": []},
            "resources": {
                resource_name: {"present": False, "referenced": False}
                for resource_name in ("scripts", "references", "assets")
            },
            "agents_metadata": {"present": False, "valid": None},
            "scripts_executed": False,
            "errors": errors,
            "warnings": warnings,
        }
    if is_link_or_reparse(skill_md) or not skill_md.is_file():
        errors.append(_issue("skill_entrypoint_invalid", "SKILL.md is missing or linked."))
    else:
        try:
            size = skill_md.stat().st_size
            if size > MAX_SKILL_MD_BYTES:
                errors.append(
                    _issue(
                        "skill_entrypoint_too_large",
                        f"SKILL.md exceeds the {MAX_SKILL_MD_BYTES}-byte validation limit.",
                    )
                )
                text = ""
            else:
                text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ArchMarshalError(
                "skill_entrypoint_unreadable",
                "SKILL.md could not be read as UTF-8.",
                details={"path": str(skill_md)},
            ) from exc
        if text:
            if len(text.splitlines()) > MAX_SKILL_MD_LINES:
                errors.append(
                    _issue(
                        "skill_entrypoint_too_long",
                        f"SKILL.md exceeds the {MAX_SKILL_MD_LINES}-line progressive-disclosure limit.",
                    )
                )
            frontmatter, frontmatter_errors = _parse_frontmatter(text)
            errors.extend(frontmatter_errors)

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
        errors.append(
            _issue(
                "skill_name_invalid",
                "Skill frontmatter name must use lowercase letters, digits, and single hyphens.",
            )
        )
    elif len(name) > 64:
        errors.append(_issue("skill_name_too_long", "Skill name must be at most 64 characters."))
    elif enforce_folder_name and directory.name != name:
        errors.append(
            _issue(
                "skill_folder_name_mismatch",
                "Skill directory name must exactly match frontmatter name.",
            )
        )
    if not isinstance(description, str) or not description.strip():
        errors.append(
            _issue(
                "skill_description_missing", "Skill frontmatter requires a non-empty description."
            )
        )
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(
            _issue(
                "skill_description_too_long",
                f"Skill description exceeds {MAX_DESCRIPTION_LENGTH} characters.",
            )
        )

    resources: dict[str, dict[str, Any]] = {}
    text_for_references = ""
    try:
        if skill_md.is_file() and skill_md.stat().st_size <= MAX_SKILL_MD_BYTES:
            text_for_references = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    for resource_name in ("scripts", "references", "assets"):
        resource = directory / resource_name
        linked = is_link_or_reparse(resource)
        present = resource.exists() or linked
        if present and (linked or not resource.is_dir()):
            errors.append(
                _issue(
                    f"skill_{resource_name}_invalid",
                    f"{resource_name}/ must be a real local directory when present.",
                )
            )
        referenced = resource_name in text_for_references
        if present and not referenced:
            warnings.append(
                _issue(
                    f"skill_{resource_name}_not_referenced",
                    f"SKILL.md does not mention the bundled {resource_name}/ directory.",
                )
            )
        resources[resource_name] = {"present": present, "referenced": referenced}

    agents_dir = directory / "agents"
    agents_file = agents_dir / "openai.yaml"
    agents_dir_linked = is_link_or_reparse(agents_dir)
    agents_dir_exists = agents_dir.exists() or agents_dir_linked
    agents_dir_valid = agents_dir_exists and not agents_dir_linked and agents_dir.is_dir()
    agents_file_linked = is_link_or_reparse(agents_file) if agents_dir_valid else False
    agents_file_exists = agents_file.exists() if agents_dir_valid else False
    agents_present = agents_dir_exists or agents_file_exists or agents_file_linked
    agents_metadata = {"present": agents_present, "valid": None}
    if agents_dir_linked or (agents_dir_exists and not agents_dir_valid):
        errors.append(
            _issue("skill_agents_metadata_invalid", "agents/ must be a real local directory.")
        )
        agents_metadata["valid"] = False
    elif agents_file_exists or agents_file_linked:
        if agents_file_linked or not agents_file.is_file():
            errors.append(
                _issue(
                    "skill_agents_metadata_invalid", "agents/openai.yaml must be a regular file."
                )
            )
            agents_metadata["valid"] = False
        else:
            try:
                data = yaml.safe_load(agents_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                data = None
            valid_agents = isinstance(data, dict) and isinstance(data.get("interface"), dict)
            agents_metadata["valid"] = valid_agents
            if not valid_agents:
                errors.append(
                    _issue(
                        "skill_agents_metadata_invalid",
                        "agents/openai.yaml is not valid interface metadata.",
                    )
                )

    return {
        "format": "archmarshal-skill-validation-v1",
        "valid": not errors,
        "frontmatter": {
            "name": name if isinstance(name, str) else None,
            "description": description if isinstance(description, str) else None,
            "extensions": sorted(COMPATIBILITY_FRONTMATTER_FIELDS & set(frontmatter)),
        },
        "resources": resources,
        "agents_metadata": agents_metadata,
        "scripts_executed": False,
        "errors": errors,
        "warnings": warnings,
    }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, [
            _issue("skill_frontmatter_missing", "SKILL.md must start with YAML frontmatter.")
        ]
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None
    )
    if closing is None:
        return {}, [_issue("skill_frontmatter_unclosed", "SKILL.md frontmatter is not closed.")]
    try:
        data = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError:
        return {}, [_issue("skill_frontmatter_invalid", "SKILL.md frontmatter is invalid YAML.")]
    if not isinstance(data, dict):
        return {}, [_issue("skill_frontmatter_invalid", "SKILL.md frontmatter must be a mapping.")]
    unknown = sorted(str(key) for key in set(data) - ALLOWED_FRONTMATTER_FIELDS)
    if unknown:
        errors.append(
            _issue(
                "skill_frontmatter_extra_fields",
                "Unsupported SKILL.md frontmatter fields: "
                + ", ".join(unknown)
                + ". Supported fields are: "
                + ", ".join(sorted(ALLOWED_FRONTMATTER_FIELDS)),
            )
        )
    errors.extend(_validate_compatibility_frontmatter(data))
    return data, errors


def _validate_compatibility_frontmatter(data: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if "license" in data:
        license_value = data["license"]
        if (
            not isinstance(license_value, str)
            or not license_value.strip()
            or len(license_value) > MAX_LICENSE_LENGTH
        ):
            errors.append(
                _issue(
                    "skill_frontmatter_license_invalid",
                    f"Skill frontmatter license must be a non-empty string of at most {MAX_LICENSE_LENGTH} characters.",
                )
            )
    if "allowed-tools" in data and not _allowed_tools_valid(data["allowed-tools"]):
        errors.append(
            _issue(
                "skill_frontmatter_allowed_tools_invalid",
                "Skill frontmatter allowed-tools must be a non-empty string or a bounded list of non-empty strings.",
            )
        )
    if "metadata" in data and not _metadata_valid(data["metadata"]):
        errors.append(
            _issue(
                "skill_frontmatter_metadata_invalid",
                "Skill frontmatter metadata must be a bounded mapping of portable scalar, list, and mapping values.",
            )
        )
    return errors


def _allowed_tools_valid(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= MAX_ALLOWED_TOOLS_LENGTH
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ALLOWED_TOOL_COUNT:
        return False
    if not all(isinstance(item, str) and item.strip() for item in value):
        return False
    return sum(len(item) for item in value) <= MAX_ALLOWED_TOOLS_LENGTH


def _metadata_valid(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    budget = [0]
    if not _portable_metadata_value(value, depth=0, budget=budget):
        return False
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(encoded.encode("utf-8")) <= MAX_METADATA_BYTES


def _portable_metadata_value(value: object, *, depth: int, budget: list[int]) -> bool:
    budget[0] += 1
    if budget[0] > MAX_METADATA_ITEMS or depth > MAX_METADATA_DEPTH:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= MAX_METADATA_STRING_LENGTH
    if isinstance(value, int):
        return -(2**63) <= value <= 2**63 - 1
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_portable_metadata_value(item, depth=depth + 1, budget=budget) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and bool(key.strip())
            and len(key) <= MAX_METADATA_KEY_LENGTH
            and _portable_metadata_value(item, depth=depth + 1, budget=budget)
            for key, item in value.items()
        )
    return False


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


__all__ = ["validate_skill_package"]
