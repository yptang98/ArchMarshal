from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .errors import ArchMarshalError
from .safety import is_link_or_reparse

MAX_SKILL_MD_BYTES = 512 * 1024
MAX_SKILL_MD_LINES = 500
MAX_DESCRIPTION_LENGTH = 2048
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER_FIELDS = {"name", "description"}


def validate_skill_package(skill_dir: Path, *, enforce_folder_name: bool = True) -> dict[str, Any]:
    """Validate the human-facing Codex Skill contract without executing package code."""
    directory = Path(skill_dir)
    skill_md = directory / "SKILL.md"
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    frontmatter: dict[str, Any] = {}

    if is_link_or_reparse(directory) or not directory.is_dir():
        errors.append(_issue("skill_directory_invalid", "Skill directory is missing or linked."))
    elif is_link_or_reparse(skill_md) or not skill_md.is_file():
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
            _issue("skill_description_missing", "Skill frontmatter requires a non-empty description.")
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
    agents_present = agents_file.exists() or agents_dir_linked or is_link_or_reparse(agents_file)
    agents_metadata = {"present": agents_present, "valid": None}
    if agents_dir_linked or (agents_dir.exists() and not agents_dir.is_dir()):
        errors.append(
            _issue("skill_agents_metadata_invalid", "agents/ must be a real local directory.")
        )
        agents_metadata["valid"] = False
    elif agents_file.exists() or is_link_or_reparse(agents_file):
        if is_link_or_reparse(agents_file) or not agents_file.is_file():
            errors.append(
                _issue("skill_agents_metadata_invalid", "agents/openai.yaml must be a regular file.")
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
        return {}, [_issue("skill_frontmatter_missing", "SKILL.md must start with YAML frontmatter.")]
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
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
                "Only name and description are allowed in SKILL.md frontmatter: " + ", ".join(unknown),
            )
        )
    return data, errors


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


__all__ = ["validate_skill_package"]
