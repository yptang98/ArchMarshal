from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ArchMarshalError, require_workspace_root
from .io import load_yaml_safe
from .layout_policy import (
    DEFAULT_NAMING as PLANNED_DEFAULT_NAMING,
)
from .layout_policy import (
    DEFAULT_SAVE_PATHS as PLANNED_DEFAULT_SAVE_PATHS,
)
from .layout_policy import (
    FORBIDDEN_SAVE_PARTS,
    SUPPORTED_PARTITIONS,
    SUPPORTED_STRATEGIES,
    WINDOWS_RESERVED_NAMES,
)
from .safety import ensure_managed_path

DEFAULT_PROJECT_FILE_PATHS = dict(PLANNED_DEFAULT_SAVE_PATHS["project_files"])
# Legacy workspaces did not declare a partition. Runtime callers retain their
# previous per-operation default until adoption writes the planned profile's
# explicit date_partition.
DEFAULT_NAMING = {
    key: value
    for key, value in PLANNED_DEFAULT_NAMING["project_files"].items()
    if key != "date_partition"
}
SUPPORTED_NAMING_STRATEGIES = set(SUPPORTED_STRATEGIES)
SUPPORTED_DATE_PARTITIONS = {
    "none": "",
    "YYYY": "%Y",
    "YYYY/MM": "%Y/%m",
    "YYYY/MM/DD": "%Y/%m/%d",
}
assert set(SUPPORTED_DATE_PARTITIONS) == set(SUPPORTED_PARTITIONS)


@dataclass(frozen=True)
class WorkspaceLayout:
    """Validated, workspace-bound locations and project-file naming policy."""

    root: Path
    save_paths: dict[str, str]
    path_sources: dict[str, str]
    naming_strategy: str
    timezone_name: str
    effective_timezone: tzinfo
    timestamp_format: str
    max_slug_words: int
    date_partition: str | None
    naming_source: str

    @property
    def preserves_names(self) -> bool:
        return self.naming_strategy == "preserve"

    def now(self) -> datetime:
        return datetime.now(self.effective_timezone)

    def localize(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ArchMarshalError(
                "workspace_layout_naming_invalid",
                "Project-file timestamps must include a timezone.",
            )
        return value.astimezone(self.effective_timezone)

    def project_file_stem(
        self,
        kind: str,
        topic: str,
        when: datetime,
    ) -> str | None:
        """Return a safe generated stem, or None when names must be preserved."""
        if self.preserves_names:
            return None
        localized = self.localize(when)
        safe_kind = _slug(kind, max_words=3, fallback="artifact")
        safe_topic = _slug(topic, max_words=self.max_slug_words, fallback=safe_kind)
        if self.naming_strategy == "topic_kind":
            stem = f"{safe_topic}-{safe_kind}"
        elif self.naming_strategy == "date_topic_kind":
            stem = f"{localized.strftime('%Y%m%d')}-{safe_topic}-{safe_kind}"
        else:
            stem = f"{localized.strftime(self.timestamp_format)}-{safe_topic}-{safe_kind}"
        _require_safe_segment(stem, field="generated project-file name")
        return stem

    def save_dir(
        self,
        kind: str,
        when: datetime,
        *,
        override: str | None = None,
        default_partition: str = "none",
        append: Iterable[str] = (),
    ) -> Path:
        """Resolve a configured output directory below the workspace.

        ``default_partition`` preserves operation-specific legacy behavior when
        the workspace does not explicitly declare ``date_partition``.
        """
        base = self.configured_dir(kind, override=override)
        parts = list(base.relative_to(self.root).parts)
        for value in append:
            _require_safe_segment(value, field="layout path component")
            parts.append(value)
        partition = self.date_partition or default_partition
        if partition not in SUPPORTED_DATE_PARTITIONS:
            raise ArchMarshalError(
                "workspace_layout_naming_invalid",
                "Project-file date_partition is not supported.",
                details={"date_partition": partition},
            )
        format_string = SUPPORTED_DATE_PARTITIONS[partition]
        if format_string:
            partition_value = self.localize(when).strftime(format_string)
            parts.extend(PurePosixPath(partition_value).parts)
        candidate = self.root.joinpath(*parts)
        return ensure_managed_path(
            self.root,
            candidate,
            purpose=f"Workspace layout path for {kind}",
        )

    def configured_dir(self, kind: str, *, override: str | None = None) -> Path:
        """Return the safe configured base directory without date partitioning."""
        configured = override if override is not None else self.save_paths.get(kind)
        if configured is None:
            raise ArchMarshalError(
                "workspace_layout_invalid",
                f"No project-file save path is available for '{kind}'.",
                details={"kind": kind},
            )
        relative = _validated_relative_path(configured, field=f"save_paths.{kind}")
        candidate = ensure_managed_path(
            self.root,
            self.root.joinpath(*relative.parts),
            purpose=f"Workspace layout base path for {kind}",
        )
        if candidate.exists() and not candidate.is_dir():
            raise ArchMarshalError(
                "workspace_layout_path_unsafe",
                "A workspace layout destination conflicts with an existing file.",
                details={"kind": kind, "path": relative.as_posix()},
            )
        return candidate

    def relative(self, path: Path) -> str:
        safe = ensure_managed_path(
            self.root,
            path,
            purpose="Workspace layout output",
        )
        return safe.relative_to(self.root).as_posix()

    def source_for(self, kind: str) -> str:
        return self.path_sources.get(kind, "default")

    def to_dict(self) -> dict[str, Any]:
        return {
            "save_paths": dict(self.save_paths),
            "path_sources": dict(self.path_sources),
            "naming": {
                "strategy": self.naming_strategy,
                "timezone": self.timezone_name,
                "effective_timezone": str(self.effective_timezone),
                "timestamp_format": self.timestamp_format,
                "max_slug_words": self.max_slug_words,
                "date_partition": self.date_partition,
                "source": self.naming_source,
            },
        }


def load_workspace_layout(
    root: Path | str,
    *,
    save_paths: dict[str, Any] | None = None,
    naming: dict[str, Any] | None = None,
) -> WorkspaceLayout:
    """Load and validate the layout declared by ``.agent/workspace.yaml``.

    Explicit ``save_paths``/``naming`` inputs are useful while preparing a
    reviewed workspace plan that has not been written yet. They must have the
    same shape as the corresponding workspace.yaml sections.
    """
    root_path = require_workspace_root(root)
    raw_save_paths: object = save_paths
    raw_naming: object = naming
    source = "provided" if save_paths is not None or naming is not None else "default"
    if save_paths is None and naming is None:
        workspace_file = root_path / ".agent" / "workspace.yaml"
        if workspace_file.exists():
            ensure_managed_path(
                root_path,
                workspace_file,
                purpose="Workspace layout configuration",
            )
            loaded = load_yaml_safe(workspace_file)
            if loaded.error:
                raise ArchMarshalError(
                    "workspace_layout_invalid",
                    "Workspace layout configuration could not be read safely.",
                    details={"path": ".agent/workspace.yaml", "reason": loaded.error},
                )
            if not isinstance(loaded.data, dict):
                raise ArchMarshalError(
                    "workspace_layout_invalid",
                    "Workspace layout configuration must be a mapping.",
                    details={"path": ".agent/workspace.yaml"},
                )
            raw_save_paths = loaded.data.get("save_paths")
            raw_naming = loaded.data.get("naming")
            source = "workspace"

    project_paths, path_sources = _parse_save_paths(raw_save_paths, source=source)
    naming_policy, naming_source = _parse_naming(raw_naming, source=source)
    effective_timezone = _parse_timezone(naming_policy["timezone"])
    _validate_timestamp_format(
        naming_policy["timestamp_format"],
        effective_timezone,
    )
    layout = WorkspaceLayout(
        root=root_path,
        save_paths=project_paths,
        path_sources=path_sources,
        naming_strategy=naming_policy["strategy"],
        timezone_name=naming_policy["timezone"],
        effective_timezone=effective_timezone,
        timestamp_format=naming_policy["timestamp_format"],
        max_slug_words=naming_policy["max_slug_words"],
        date_partition=naming_policy.get("date_partition"),
        naming_source=naming_source,
    )
    # Validate every configured/default destination now, not only when a
    # particular command happens to use it.
    probe = datetime(2000, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    for kind in layout.save_paths:
        layout.save_dir(kind, probe)
    return layout


def _parse_save_paths(
    value: object,
    *,
    source: str,
) -> tuple[dict[str, str], dict[str, str]]:
    paths = dict(DEFAULT_PROJECT_FILE_PATHS)
    sources = {kind: "default" for kind in paths}
    if value is None:
        return paths, sources
    if not isinstance(value, dict):
        raise ArchMarshalError(
            "workspace_layout_invalid",
            "save_paths must be a mapping.",
        )
    project_files = value.get("project_files")
    if project_files is None:
        return paths, sources
    if not isinstance(project_files, dict):
        raise ArchMarshalError(
            "workspace_layout_invalid",
            "save_paths.project_files must be a mapping.",
        )
    for kind, configured in project_files.items():
        if not isinstance(kind, str) or kind not in DEFAULT_PROJECT_FILE_PATHS:
            # Unknown keys may be valid in a future schema, but cannot affect
            # this runtime until their semantics are explicit.
            continue
        if not isinstance(configured, str) or not configured.strip():
            raise ArchMarshalError(
                "workspace_layout_invalid",
                f"save_paths.project_files.{kind} must be a non-empty string.",
                details={"kind": kind},
            )
        normalized = _validated_relative_path(
            configured,
            field=f"save_paths.project_files.{kind}",
        ).as_posix()
        paths[kind] = normalized
        sources[kind] = source
    return paths, sources


def _parse_naming(value: object, *, source: str) -> tuple[dict[str, Any], str]:
    policy: dict[str, Any] = dict(DEFAULT_NAMING)
    naming_source = "default"
    if value is None:
        return policy, naming_source
    if not isinstance(value, dict):
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "naming must be a mapping.",
        )
    project_files = value.get("project_files")
    if project_files is None:
        return policy, naming_source
    if not isinstance(project_files, dict):
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "naming.project_files must be a mapping.",
        )
    if project_files:
        naming_source = source
    for key in (*DEFAULT_NAMING, "date_partition"):
        if key in project_files:
            policy[key] = project_files[key]
    strategy = policy["strategy"]
    if not isinstance(strategy, str) or strategy not in SUPPORTED_NAMING_STRATEGIES:
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "Project-file naming strategy is not supported.",
            details={"strategy": strategy},
        )
    timezone_name = policy["timezone"]
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ArchMarshalError(
            "workspace_layout_timezone_invalid",
            "Project-file timezone must be a non-empty timezone name.",
        )
    policy["timezone"] = timezone_name.strip()
    timestamp_format = policy["timestamp_format"]
    if not isinstance(timestamp_format, str) or not timestamp_format:
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "Project-file timestamp_format must be a non-empty string.",
        )
    max_slug_words = policy["max_slug_words"]
    if (
        isinstance(max_slug_words, bool)
        or not isinstance(max_slug_words, int)
        or not 1 <= max_slug_words <= 12
    ):
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "Project-file max_slug_words must be an integer between 1 and 12.",
            details={"max_slug_words": max_slug_words},
        )
    partition = policy.get("date_partition")
    if partition is not None and partition not in SUPPORTED_DATE_PARTITIONS:
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "Project-file date_partition is not supported.",
            details={"date_partition": partition},
        )
    return policy, naming_source


def _parse_timezone(value: str) -> tzinfo:
    if value == "UTC":
        return timezone.utc
    if value.casefold() == "local":
        local = datetime.now().astimezone().tzinfo
        if local is None:
            raise ArchMarshalError(
                "workspace_layout_timezone_invalid",
                "The local timezone could not be determined.",
            )
        return local
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ArchMarshalError(
            "workspace_layout_timezone_invalid",
            "Project-file timezone is not available on this system.",
            details={"timezone": value},
        ) from exc


def _validate_timestamp_format(value: str, zone: tzinfo) -> None:
    if (
        len(value) > 128
        or any(char in value for char in ("/", "\\", ":", "\x00", "\n", "\r"))
        or ".." in value
        or PureWindowsPath(value).drive
    ):
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "timestamp_format must produce one safe filename component.",
            details={"timestamp_format": value},
        )
    try:
        rendered = datetime(2000, 1, 2, 3, 4, 5, tzinfo=zone).strftime(value)
    except (ValueError, OSError) as exc:
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            "timestamp_format could not be rendered.",
            details={"timestamp_format": value},
        ) from exc
    _require_safe_segment(rendered, field="timestamp_format output")


def _validated_relative_path(value: str, *, field: str) -> PurePosixPath:
    stripped = value.strip().replace("\\", "/")
    posix = PurePosixPath(stripped)
    windows = PureWindowsPath(value.strip())
    if (
        not stripped
        or "\x00" in stripped
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ArchMarshalError(
            "workspace_layout_path_unsafe",
            f"{field} must be a normalized project-relative path.",
            details={"field": field, "path": value},
        )
    if unicodedata.normalize("NFC", stripped) != stripped:
        raise ArchMarshalError(
            "workspace_layout_path_unsafe",
            f"{field} must use portable normalized Unicode.",
            details={"field": field, "path": value},
        )
    forbidden = {item.casefold() for item in FORBIDDEN_SAVE_PARTS}
    for part in posix.parts:
        windows_base = part.split(".", 1)[0].rstrip(" .").upper()
        if (
            part.endswith((" ", "."))
            or ":" in part
            or windows_base in WINDOWS_RESERVED_NAMES
            or part.casefold() in forbidden
        ):
            raise ArchMarshalError(
                "workspace_layout_path_unsafe",
                f"{field} crosses a reserved or unmanaged path boundary.",
                details={"field": field, "path": value, "component": part},
            )
    return posix


def _require_safe_segment(value: str, *, field: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or any(ord(char) < 32 for char in value)
        or PureWindowsPath(value).drive
    ):
        raise ArchMarshalError(
            "workspace_layout_naming_invalid",
            f"{field} is not a safe filename component.",
            details={"value": value},
        )


def _slug(value: str, *, max_words: int, fallback: str) -> str:
    normalized = "".join(
        char.lower() if char.isascii() and char.isalnum() else " " for char in value
    )
    words = [word for word in normalized.split() if word]
    return "-".join(words[:max_words]) or fallback


__all__ = [
    "DEFAULT_NAMING",
    "DEFAULT_PROJECT_FILE_PATHS",
    "SUPPORTED_DATE_PARTITIONS",
    "SUPPORTED_NAMING_STRATEGIES",
    "WorkspaceLayout",
    "load_workspace_layout",
]
