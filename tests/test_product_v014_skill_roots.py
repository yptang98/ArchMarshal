from __future__ import annotations

import os
from pathlib import Path

import pytest

import archmarshal.adoption as adoption_module
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.errors import ArchMarshalError
from archmarshal.safety import (
    fingerprint_directory,
    fingerprint_directory_matches,
    fingerprint_regular_file,
    verify_backup,
)
from archmarshal.skill_review import review_workspace_skill


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

    assert preview["skill_discovery"]["effective_roots"] == ["custom/tools"]
    assert preview["skill_discovery"]["additional_roots"] == ["custom/tools"]
    assert preview["skill_discovery"]["root_count"] == 1
    assert preview["skill_discovery"]["discovered_package_count"] == 1
    assert preview["skill_discovery"]["prepared_management_packages"] == [
        "custom/tools/demo"
    ]
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

    assert raised.value.code in {
        "managed_path_escape",
        "skill_root_invalid",
        "unsafe_managed_link",
    }


def test_mode_aware_skill_fingerprint_does_not_change_legacy_contract(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = _skill(root)

    legacy = fingerprint_directory(package, purpose="legacy package")
    mode_aware = fingerprint_directory(
        package,
        purpose="managed Skill package",
        include_modes=True,
    )

    assert all("mode" not in record for record in legacy["files"])
    assert all("mode" in record for record in mode_aware["files"])
    assert legacy["sha256"] != mode_aware["sha256"]
    assert fingerprint_directory_matches(mode_aware, legacy["sha256"])


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


def test_skill_package_preserves_generated_artifacts_without_blocking_management(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = _skill(root)
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "generated.pyc").write_bytes(b"not portable source evidence")

    repository = package / ".git"
    repository.mkdir()
    (repository / "config").write_text("private repository metadata\n", encoding="utf-8")

    preview = plan_adoption(root, skill_roots=["custom/tools"])

    assert preview["skill_backup_coverage"]["complete"] is True
    assert preview["skill_discovery"]["boundary_confirmation_required"] is True
    artifacts = preview["skill_discovery"]["preserved_artifacts"]
    assert {item["path"] for item in artifacts} == {
        "custom/tools/demo/.git",
        "custom/tools/demo/__pycache__",
    }
    assert all(item["contents_inspected"] is False for item in artifacts)
    assert all(item["policy"] == "preserve_unmanaged" for item in artifacts)
    assert not {
        "custom/tools/demo/.git/config",
        "custom/tools/demo/__pycache__/generated.pyc",
    } & {item["path"] for item in preview["backup_file_preview"]}

    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        skill_roots=["custom/tools"],
    )

    assert applied["mode"] == "overlay_applied"
    assert (repository / "config").read_text(encoding="utf-8") == "private repository metadata\n"
    assert (cache / "generated.pyc").read_bytes() == b"not portable source evidence"


def test_exact_skill_exclusions_persist_and_can_be_reversed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    excluded = _skill(root, "skills/costmarshal")
    included = _skill(root, "skills/keep")
    excluded_before = fingerprint_directory(excluded, purpose="excluded before")
    included_before = fingerprint_directory(included, purpose="included before")

    preview = plan_adoption(root, exclude_skills=["skills/costmarshal"])

    discovery = preview["skill_discovery"]
    assert discovery["prepared_management_packages"] == ["skills/keep"]
    assert discovery["excluded_package_count"] == 1
    assert discovery["excluded_packages"] == [
        {
            "source": "skills/costmarshal",
            "state": "excluded_present",
            "entrypoint_present": True,
            "contents_inspected": False,
            "backup_included": False,
            "indexed": False,
            "learning_included": False,
            "source_mutation": False,
        }
    ]
    assert {item["kind"] for item in preview["skill_index"]["changes"]} == {
        "initialized",
        "added",
        "excluded",
    }
    assert not any(
        item["path"].startswith("skills/costmarshal/")
        for item in preview["backup_file_preview"]
    )
    assert "--exclude-skill" in preview["next_actions"][0]["command"]

    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        exclude_skills=["skills/costmarshal"],
    )
    assert applied["mode"] == "overlay_applied"
    assert fingerprint_directory(excluded, purpose="excluded after") == excluded_before
    assert fingerprint_directory(included, purpose="included after") == included_before

    persisted = plan_adoption(root)
    assert persisted["skill_discovery"]["prepared_management_packages"] == ["skills/keep"]
    assert persisted["skill_discovery"]["excluded_package_count"] == 1
    assert persisted["skill_index"]["changed"] is False

    restored = plan_adoption(root, manage_skills=["skills/costmarshal"])
    assert restored["skill_discovery"]["prepared_management_packages"] == [
        "skills/costmarshal",
        "skills/keep",
    ]
    assert restored["skill_discovery"]["excluded_package_count"] == 0
    assert "included" in {item["kind"] for item in restored["skill_index"]["changes"]}
    assert "--manage-skill" in restored["next_actions"][0]["command"]

    restored_apply = adopt_workspace(
        root,
        apply=True,
        expected_plan=restored["plan_digest"],
        manage_skills=["skills/costmarshal"],
    )
    assert restored_apply["skill_index"]["excluded_package_count"] == 0
    assert plan_adoption(root)["skill_discovery"]["excluded_package_count"] == 0


def test_multiple_skill_exclusions_are_exact_and_full_backup_respects_them(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root, "skills/one")
    _skill(root, "skills/two")
    _skill(root, "skills/three")
    (root / "notes.txt").write_text("keep project evidence\n", encoding="utf-8")

    preview = plan_adoption(
        root,
        backup_scope="full",
        exclude_skills=["skills/one", "skills/two"],
    )

    assert preview["skill_discovery"]["prepared_management_packages"] == ["skills/three"]
    assert preview["skill_discovery"]["selection_added"] == ["skills/one", "skills/two"]
    paths = {item["path"] for item in preview["backup_file_preview"]}
    assert "notes.txt" in paths
    assert any(path.startswith("skills/three/") for path in paths)
    assert not any(path.startswith("skills/one/") for path in paths)
    assert not any(path.startswith("skills/two/") for path in paths)
    assert preview["backup_archive_scope"] == "managed_workspace"

    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        backup_scope="full",
        exclude_skills=["skills/one", "skills/two"],
    )
    verification = verify_backup(root / applied["backup"]["path"])
    assert verification["manifest"]["scope"] == "managed_workspace"
    archived = {item["path"] for item in verification["manifest"]["files"]}
    assert not any(path.startswith("skills/one/") for path in archived)
    assert not any(path.startswith("skills/two/") for path in archived)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("../outside", "skill_selection_outside_project"),
        ("C:/outside", "skill_selection_outside_project"),
        (".", "skill_selection_not_portable"),
        (".agent/private", "skill_selection_managed_state"),
        ("skills/missing", "skill_selection_missing"),
    ],
)
def test_unsafe_skill_exclusions_fail_before_writes(
    tmp_path: Path,
    value: str,
    code: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(root, exclude_skills=[value])

    assert raised.value.code == code
    assert not (root / ".agent").exists()


def test_same_skill_cannot_be_excluded_and_managed_in_one_plan(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root, "skills/demo")

    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(
            root,
            exclude_skills=["skills/demo"],
            manage_skills=["skills/demo"],
        )

    assert raised.value.code == "skill_selection_conflict"


def test_excluded_skill_contents_are_not_fingerprinted_or_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    excluded = _skill(root, "skills/costmarshal")
    _skill(root, "skills/keep")
    real_fingerprint = adoption_module.fingerprint_directory
    real_validate = adoption_module.validate_skill_package

    def guarded_fingerprint(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        assert Path(path).resolve() != excluded.resolve()
        return real_fingerprint(path, *args, **kwargs)

    def guarded_validate(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        assert Path(path).resolve() != excluded.resolve()
        return real_validate(path, *args, **kwargs)

    monkeypatch.setattr(adoption_module, "fingerprint_directory", guarded_fingerprint)
    monkeypatch.setattr(adoption_module, "validate_skill_package", guarded_validate)

    preview = plan_adoption(root, exclude_skills=["skills/costmarshal"])

    assert preview["skill_discovery"]["prepared_management_packages"] == ["skills/keep"]


def test_excluded_skill_cannot_be_reviewed_around_the_management_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root, "skills/costmarshal")
    preview = plan_adoption(root, exclude_skills=["skills/costmarshal"])
    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        exclude_skills=["skills/costmarshal"],
    )

    with pytest.raises(ArchMarshalError) as raised:
        review_workspace_skill(
            root,
            "skills/costmarshal",
            decision="approve",
            expected_head=applied["skill_index_commit"]["head"],
        )

    assert raised.value.code == "skill_review_source_excluded"


def test_reviewing_managed_skill_preserves_other_package_exclusions(tmp_path: Path) -> None:
    root = tmp_path / "project"
    for name in ("demo", "private"):
        package = root / "skills" / name
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Reviewed {name} workflow.\n---\n\n# {name}\n",
            encoding="utf-8",
        )
    preview = plan_adoption(root, exclude_skills=["skills/private"])
    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        exclude_skills=["skills/private"],
    )
    head = applied["skill_index_commit"]["head"]
    review = review_workspace_skill(
        root,
        "skills/demo",
        decision="approve",
        reason="preserve selection",
        expected_head=head,
    )
    reviewed = review_workspace_skill(
        root,
        "skills/demo",
        decision="approve",
        reason="preserve selection",
        expected_head=head,
        expected_plan=review["plan_digest"],
        reviewed_plan=review["review_plan"],
        apply=True,
    )

    assert reviewed["mode"] == "review_recorded"
    later = plan_adoption(root)
    assert later["skill_discovery"]["excluded_package_count"] == 1
    assert later["skill_discovery"]["excluded_packages"][0]["source"] == "skills/private"


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
