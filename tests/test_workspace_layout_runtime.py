from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

import archmarshal.checkpoint as checkpoint_module
import archmarshal.learning as learning_module
import archmarshal.session as session_module
from archmarshal.catalog import catalog_projects
from archmarshal.checkpoint import checkpoint_workspace
from archmarshal.errors import ArchMarshalError
from archmarshal.learning import learn_from_projects
from archmarshal.session import record_closeout
from archmarshal.workspace_layout import load_workspace_layout


def _write_workspace(
    root: Path,
    *,
    checkpoints: str = ".agent/inbox/checkpoints",
    history: str = ".agent/history",
    strategy: str = "time_topic_kind",
    timezone_name: str = "UTC",
    timestamp_format: str = "%Y%m%d-%H%M%S",
    date_partition: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    policy: dict[str, object] = {
        "strategy": strategy,
        "timezone": timezone_name,
        "timestamp_format": timestamp_format,
        "max_slug_words": 6,
    }
    if date_partition is not None:
        policy["date_partition"] = date_partition
    payload = {
        "workspace": {"name": root.name, "version": "0.1.0", "tags": ["layout"]},
        "save_paths": {
            "project_files": {
                "checkpoints": checkpoints,
                "reports": ".agent/reports",
                "plans": ".agent/plans",
                "history": history,
                "knowledge": ".agent/knowledge",
                "artifacts": ".agent/inbox",
            }
        },
        "naming": {"project_files": policy},
        "paths": {"project_root": ".", "agent_root": ".agent"},
    }
    target = root / ".agent/workspace.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _fixed_datetime(instant: datetime):  # type: ignore[no-untyped-def]
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return instant if tz is None else instant.astimezone(tz)

    return FixedDateTime


def test_checkpoint_uses_one_effective_zone_for_partition_and_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    _write_workspace(
        root,
        checkpoints="records/checkpoints",
        timezone_name="Asia/Shanghai",
        date_partition="YYYY/MM/DD",
    )
    instant = datetime(2026, 7, 17, 16, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(checkpoint_module, "datetime", _fixed_datetime(instant))

    result = checkpoint_workspace(root, summary="Layout proof", task="timezone boundary")

    assert result["mode"] == "propose_only"
    assert result["checkpoint"]["created_at"].startswith("2026-07-18T00:30:00")
    assert result["checkpoint"]["suggested_path"] == (
        "records/checkpoints/2026/07/18/20260718-003000-timezone-boundary-checkpoint.md"
    )
    assert result["layout_profile"]["naming"]["effective_timezone"] == "Asia/Shanghai"


def test_explicit_none_partition_keeps_checkpoint_in_configured_base(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_workspace(
        root,
        checkpoints="notes/checkpoints",
        date_partition="none",
    )

    result = checkpoint_workspace(root, summary="No date folders")

    assert result["save_path"]["path"] == "notes/checkpoints"
    assert result["checkpoint"]["suggested_path"].startswith("notes/checkpoints/")


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("../outside", "workspace_layout_path_unsafe"),
        (r"C:\outside", "workspace_layout_path_unsafe"),
        (".git/archmarshal", "workspace_layout_path_unsafe"),
    ],
)
def test_layout_rejects_unsafe_destinations(
    tmp_path: Path,
    path: str,
    code: str,
) -> None:
    root = tmp_path / "project"
    _write_workspace(root, history=path)

    with pytest.raises(ArchMarshalError) as raised:
        load_workspace_layout(root)

    assert raised.value.code == code


@pytest.mark.parametrize("timestamp_format", ["%Y/%m/%d", r"%Y\%m", "../%Y", "C:%Y"])
def test_layout_rejects_timestamp_formats_with_path_semantics(
    tmp_path: Path,
    timestamp_format: str,
) -> None:
    root = tmp_path / "project"
    _write_workspace(root, timestamp_format=timestamp_format)

    with pytest.raises(ArchMarshalError) as raised:
        load_workspace_layout(root)

    assert raised.value.code == "workspace_layout_naming_invalid"


def test_layout_rejects_linked_destination(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_workspace(root, history="linked-history")
    try:
        os.symlink(outside, root / "linked-history", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    with pytest.raises(ArchMarshalError) as raised:
        load_workspace_layout(root)

    assert raised.value.code == "unsafe_managed_link"


def test_preserve_policy_does_not_guess_checkpoint_or_closeout_names(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_workspace(root, strategy="preserve")

    checkpoint = checkpoint_workspace(root, summary="Keep my naming")
    closeout = record_closeout(root, level="quick", summary="Keep my naming")

    assert checkpoint["mode"] == "requires_user_input"
    assert checkpoint["checkpoint"]["filename"] is None
    assert checkpoint["checkpoint"]["suggested_path"] is None
    assert closeout["mode"] == "requires_user_input"
    assert closeout["session_dir"] is None
    assert closeout["operations"] == []


def test_closeout_uses_configured_history_and_local_date_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    _write_workspace(
        root,
        history="project-records",
        timezone_name="Asia/Shanghai",
        date_partition="YYYY/MM/DD",
    )
    instant = datetime(2026, 7, 17, 16, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(session_module, "datetime", _fixed_datetime(instant))

    result = record_closeout(root, level="quick", summary="Timezone closeout")

    assert result["session_dir"].startswith("project-records/2026/07/18/")
    assert result["session_preview"]["recorded_on"] == "2026-07-18"
    assert result["mode"] == "blocked"  # This fixture intentionally has no ownership marker.


def test_learning_discovers_sessions_under_configured_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    _write_workspace(root, history="project-records", date_partition="YYYY/MM/DD")
    marker = root / "project-records/2026/07/18/session/COMMITTED.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        learning_module,
        "verify_committed_session",
        lambda _path: {
            "commit_sha256": "a" * 64,
            "session": {
                "format": "archmarshal-session-v2",
                "used_skills": [],
                "skill_usage": [],
                "tags": [],
                "key_scripts": [],
            },
        },
    )

    learned = learn_from_projects([root])

    assert learned["source_session_count"] == 1
    assert (
        learned["learning_plan"]["source_layouts"][str(root.resolve())]["save_paths"]["history"]
        == "project-records"
    )


def test_catalog_exposes_validated_layout_profile(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_workspace(root)

    result = catalog_projects([root])

    assert result["projects"][0]["layout_profile"]["save_paths"]["history"] == (".agent/history")
    assert result["projects"][0]["layout_profile"]["path_sources"]["history"] == ("workspace")
