from __future__ import annotations

import os
from pathlib import Path

import pytest

import archmarshal.adoption as adoption_module
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.errors import ArchMarshalError
from archmarshal.safety import (
    fingerprint_directory,
    fingerprint_regular_file,
    verify_backup,
)


def _skill(root: Path, relative: str = "custom/tools/demo") -> Path:
    package = root / Path(relative)
    (package / "scripts").mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: Run a repeatable demo from a non-default Skill root.\n"
        "---\n\n"
        "# Demo\n\n"
        "Review and run `scripts/run.py`.\n",
        encoding="utf-8",
    )
    (package / "scripts" / "run.py").write_text("print('safe')\n", encoding="utf-8")
    return package


def _archive_records(root: Path, applied: dict[str, object]) -> dict[str, dict[str, object]]:
    backup = applied["backup"]
    assert isinstance(backup, dict)
    verification = verify_backup(root / str(backup["path"]))
    return {record["path"]: record for record in verification["manifest"]["files"]}


def test_explicit_skill_root_binds_complete_package_backup_and_replay(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = _skill(root)
    source_before = fingerprint_directory(package, purpose="test source")

    preview = plan_adoption(root, skill_roots=["custom/tools"])

    assert preview["skill_discovery"] == {
        "effective_roots": ["custom/tools"],
        "additional_roots": ["custom/tools"],
        "root_count": 1,
        "discovered_package_count": 1,
    }
    assert preview["skill_backup_coverage"]["complete"] is True
    assert preview["skill_backup_coverage"]["covered_package_count"] == 1
    preview_records = {item["path"]: item for item in preview["backup_file_preview"]}
    assert set(preview_records) == {
        "custom/tools/demo/SKILL.md",
        "custom/tools/demo/scripts/run.py",
    }
    assert all({"bytes", "mode", "sha256"} <= set(item) for item in preview_records.values())
    assert "--skill-root" in preview["next_actions"][0]["command"]

    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        skill_roots=["custom/tools"],
    )

    assert applied["mode"] == "overlay_applied"
    assert fingerprint_directory(package, purpose="test source") == source_before
    archived = _archive_records(root, applied)
    assert {path: archived[path] for path in preview_records} == preview_records
    workspace = (root / ".agent" / "workspace.yaml").read_text(encoding="utf-8")
    assert "source_skill_roots:" in workspace
    assert "- custom/tools" in workspace
    later = plan_adoption(root)
    assert later["skill_discovery"]["effective_roots"] == ["custom/tools"]
    assert later["discovered_skills"][0]["source"] == "custom/tools/demo"


def test_apply_without_reviewed_explicit_root_is_blocked_before_project_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    preview = plan_adoption(root, skill_roots=["custom/tools"])

    blocked = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])

    assert blocked["mode"] == "blocked"
    assert "plan_digest_changed" in blocked["conflicts"]
    assert not (root / ".agent").exists()


def test_explicit_skill_content_drift_invalidates_exact_plan_without_project_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = _skill(root)
    preview = plan_adoption(root, skill_roots=["custom/tools"])
    (package / "scripts" / "run.py").write_text("print('changed')\n", encoding="utf-8")

    blocked = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        skill_roots=["custom/tools"],
    )

    assert blocked["mode"] == "blocked"
    assert "plan_digest_changed" in blocked["conflicts"]
    assert not (root / ".agent").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows does not preserve POSIX chmod bits")
def test_explicit_skill_mode_drift_invalidates_exact_plan(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = _skill(root)
    script = package / "scripts" / "run.py"
    preview = plan_adoption(root, skill_roots=["custom/tools"])
    script.chmod(script.stat().st_mode ^ 0o100)

    blocked = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        skill_roots=["custom/tools"],
    )

    assert blocked["mode"] == "blocked"
    assert "plan_digest_changed" in blocked["conflicts"]


def test_plugin_skill_root_is_discovered_by_default(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root, "plugins/example/skills/demo")

    preview = plan_adoption(root)

    assert "plugins" in preview["skill_discovery"]["effective_roots"]
    assert preview["discovered_skills"][0]["source"] == "plugins/example/skills/demo"
    assert preview["skill_backup_coverage"]["complete"] is True


def test_explicit_root_nested_under_default_root_is_normalized(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root, "plugins/example/skills/demo")

    preview = plan_adoption(root, skill_roots=["plugins/example/skills"])

    assert preview["skill_discovery"]["effective_roots"] == ["plugins"]
    assert preview["skill_discovery"]["additional_roots"] == []
    assert preview["discovered_skills"][0]["source"] == "plugins/example/skills/demo"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("../outside", "skill_root_outside_project"),
        ("C:/outside", "skill_root_outside_project"),
        (".agent", "skill_root_managed_state"),
        ("missing", "skill_root_missing"),
    ],
)
def test_unsafe_explicit_skill_roots_fail_closed(
    tmp_path: Path,
    value: str,
    code: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(root, skill_roots=[value])

    assert raised.value.code == code
    assert not (root / ".agent").exists()


def test_explicit_file_root_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "not-a-directory").write_text("data\n", encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(root, skill_roots=["not-a-directory"])

    assert raised.value.code == "skill_root_invalid"


def test_linked_explicit_skill_root_fails_closed_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    linked = root / "linked"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(root, skill_roots=["linked"])

    assert raised.value.code in {"managed_path_escape", "skill_root_invalid"}


def test_full_backup_still_reports_complete_skill_package_coverage(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    (root / "notes.txt").write_text("human project file\n", encoding="utf-8")

    preview = plan_adoption(
        root,
        backup_scope="full",
        skill_roots=["custom/tools"],
    )

    assert preview["skill_backup_coverage"]["complete"] is True
    assert {item["path"] for item in preview["backup_file_preview"]} == {
        "custom/tools/demo/SKILL.md",
        "custom/tools/demo/scripts/run.py",
        "notes.txt",
    }


def test_skill_package_with_backup_excluded_content_fails_before_writes(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = _skill(root)
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "generated.pyc").write_bytes(b"not portable source evidence")

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(root, skill_roots=["custom/tools"])

    assert raised.value.code == "skill_backup_coverage_incomplete"
    packages = raised.value.details["packages"]
    assert packages[0]["missing"] == ["custom/tools/demo/__pycache__/generated.pyc"]
    assert not (root / ".agent").exists()


def test_owned_invalid_workspace_config_blocks_skill_sync(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    preview = plan_adoption(root, skill_roots=["custom/tools"])
    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        skill_roots=["custom/tools"],
    )
    assert applied["mode"] == "overlay_applied"
    head = (root / ".agent/skill-overlays/.archmarshal/HEAD").read_bytes()
    (root / ".agent/workspace.yaml").write_text("paths: [unterminated\n", encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(root)

    assert raised.value.code == "skill_root_config_invalid"
    assert (root / ".agent/skill-overlays/.archmarshal/HEAD").read_bytes() == head


def test_backup_must_match_exact_reviewed_source_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    instructions = root / "AGENTS.md"
    instructions.write_text("# Reviewed instructions\n", encoding="utf-8")
    preview = plan_adoption(root)
    real_create_backup = adoption_module.create_backup

    def mutate_then_backup(*args, **kwargs):  # type: ignore[no-untyped-def]
        instructions.write_text("# Changed concurrently\n", encoding="utf-8")
        return real_create_backup(*args, **kwargs)

    monkeypatch.setattr(adoption_module, "create_backup", mutate_then_backup)

    with pytest.raises(ArchMarshalError) as raised:
        adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])

    assert raised.value.code == "backup_plan_mismatch"
    assert not (root / ".agent/ownership.json").exists()
    assert list((root / ".agent/backups").glob("*.zip"))


def test_adoption_backup_plan_hashing_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    instructions = root / "AGENTS.md"
    instructions.write_text("four", encoding="utf-8")
    monkeypatch.setattr(adoption_module, "MAX_BACKUP_CONTENT_BYTES", 3)

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(root)

    assert raised.value.code == "fingerprint_limit_exceeded"
    assert not (root / ".agent").exists()

    record = fingerprint_regular_file(
        root,
        instructions,
        purpose="bounded test file",
        max_bytes=4,
    )
    assert record["bytes"] == 4
