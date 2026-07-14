from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SCHEMA_FILES = {
    "workspace": "workspace.schema.yaml",
    "artifact-registry": "artifact-registry.schema.yaml",
    "skill-manifest": "skill-manifest.schema.yaml",
    "memory-stores": "memory-stores.schema.yaml",
    "memory-records": "memory-records.schema.yaml",
}


@dataclass(frozen=True)
class SchemaIssue:
    location: str
    message: str
    suggestion: str


def validate_schema(data: object, schema_name: str) -> list[SchemaIssue]:
    schema = _load_schema(schema_name)
    validator = Draft202012Validator(schema)
    return [
        SchemaIssue(
            location=_json_location(error.absolute_path),
            message=error.message,
            suggestion=_suggestion_for_error(schema_name, error.validator),
        )
        for error in sorted(
            validator.iter_errors(_json_compatible(data)),
            key=lambda item: list(item.absolute_path),
        )
    ]


def _json_compatible(value: object) -> object:
    """Normalize YAML-native scalar types before JSON Schema validation.

    PyYAML materializes ISO dates as ``datetime.date`` values even though the
    corresponding JSON representation is a string.  Schema validation should
    describe the serialized document contract, not an implementation detail of
    the YAML parser.
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


@lru_cache(maxsize=None)
def _load_schema(schema_name: str) -> dict[str, Any]:
    filename = SCHEMA_FILES[schema_name]
    try:
        resource = resources.files("archmarshal.schemas").joinpath(filename)
        if resource.is_file():
            return yaml.safe_load(resource.read_text(encoding="utf-8"))
    except ModuleNotFoundError:
        pass

    repo_schema = Path(__file__).resolve().parents[2] / "schemas" / filename
    if repo_schema.exists():
        return yaml.safe_load(repo_schema.read_text(encoding="utf-8"))

    raise FileNotFoundError(f"Could not locate ArchMarshal schema '{filename}'.")


def _json_location(parts: Any) -> str:
    location = "$"
    for part in parts:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return location


def _suggestion_for_error(schema_name: str, validator: str) -> str:
    schema_label = f"schemas/{SCHEMA_FILES[schema_name]}"
    suggestions = {
        "required": f"Add the missing required field declared by {schema_label}.",
        "enum": f"Use one of the allowed values declared by {schema_label}.",
        "pattern": f"Use a value that matches the pattern declared by {schema_label}.",
        "additionalProperties": f"Remove unsupported keys or intentionally extend {schema_label}.",
        "type": f"Use the value type required by {schema_label}.",
        "minItems": f"Add at least the minimum number of items required by {schema_label}.",
        "minLength": f"Use a non-empty value that satisfies {schema_label}.",
        "uniqueItems": f"Remove duplicate values so the field satisfies {schema_label}.",
    }
    return suggestions.get(validator, f"Update the file so it conforms to {schema_label}.")


__all__ = ["SchemaIssue", "validate_schema"]
