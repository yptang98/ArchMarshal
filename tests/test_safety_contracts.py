from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
import yaml

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.cli import main, start_main
from archmarshal.errors import ArchMarshalError
from archmarshal.learning import learn_from_projects
from archmarshal.lint import lint_workspace
from archmarshal.resolver import resolve_workspace
from archmarshal.safety import (
    create_backup,
    files_below_no_links,
    restore_backup,
    verify_backup,
)
from archmarshal.session import record_closeout
from archmarshal.skill_index import load_skill_index


def _skill(root: Path, name: str = "demo") -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test {name}.\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_missing_workspace_is_structured_error(capsys, tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    assert main(["inventory", str(missing)]) == 2

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "workspace_not_found"
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_invalid_backup_is_structured_error(capsys, tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"not a zip")

    assert main(["backup-verify", str(invalid)]) == 2

    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "backup_integrity_failed"


def test_blocked_adoption_returns_nonzero(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    control = root / ".agent" / "workspace.yaml"
    control.parent.mkdir(parents=True)
    control.write_text("owned_by: another-tool\n", encoding="utf-8")

    assert main(["adopt", str(root), "--apply"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "blocked"
    assert control.read_text(encoding="utf-8") == "owned_by: another-tool\n"


def test_memory_schema_errors_are_diagnostics_not_tracebacks(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    adopt_workspace(root, apply=True)
    (root / ".agent" / "memory-stores.yaml").write_text(
        yaml.safe_dump(
            {
                "memory_stores": [
                    {
                        "id": "memory.bad",
                        "name": "bad",
                        "scope": "invalid",
                        "store_type": "filesystem",
                        "path": ".agent/knowledge",
                        "read_policy": "default",
                        "write_policy": "append_only",
                        "owner": "human",
                        "privacy": "private",
                        "default_token_budget": "not-a-number",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert main(["lint", str(root)]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert any(item["rule"] == "memory.store_schema_invalid" for item in payload["diagnostics"])
    assert "Traceback" not in captured.err


def test_external_workspace_skill_path_is_reported_without_scanning(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _skill(outside, "private")
    adopt_workspace(root, apply=True)
    workspace = root / ".agent" / "workspace.yaml"
    payload = yaml.safe_load(workspace.read_text(encoding="utf-8"))
    payload["paths"]["project_skills"] = ["../outside/skills"]
    workspace.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    diagnostics = lint_workspace(root)

    assert "project.workspace_path_outside_root" in {item.rule for item in diagnostics}
    assert not any(item.rule.startswith("skill.") for item in diagnostics)


def test_backup_round_trip_requires_new_destination(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "notes.txt"
    source.write_text("important\n", encoding="utf-8")
    archive = root / "backup.zip"

    created = create_backup(root, [source], archive, reason="test")
    verified = verify_backup(archive)
    preview = restore_backup(archive, tmp_path / "restored")
    restored = restore_backup(archive, tmp_path / "restored", apply=True)

    assert created["verified"] is True
    assert verified["file_count"] == 1
    assert preview["mode"] == "propose_only"
    assert restored["mode"] == "restored"
    assert (tmp_path / "restored" / "notes.txt").read_text(encoding="utf-8") == "important\n"
    with pytest.raises(ArchMarshalError, match="must not already exist"):
        restore_backup(archive, tmp_path / "restored", apply=True)


def test_backup_cli_verify_and_restore_contract(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "notes.txt"
    source.write_text("important\n", encoding="utf-8")
    archive = root / "backup.zip"
    create_backup(root, [source], archive, reason="test")
    destination = tmp_path / "restored"

    assert main(["backup-verify", str(archive)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["mode"] == "verified"
    assert "manifest" not in verified

    assert main(["backup-restore", str(archive), str(destination)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "propose_only"

    assert main(["backup-restore", str(archive), str(destination), "--apply"]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["mode"] == "restored"


def test_backup_rejects_tampered_payload(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "notes.txt"
    source.write_text("important\n", encoding="utf-8")
    archive = root / "backup.zip"
    create_backup(root, [source], archive, reason="test")

    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "a") as handle:
            handle.writestr("files/notes.txt", "tampered\n")

    with pytest.raises(ArchMarshalError, match="duplicate archive member"):
        verify_backup(archive)


def test_backup_rejects_unsafe_or_undeclared_members(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.zip"
    content = b"x"
    manifest = {
        "format": "archmarshal-backup-v1",
        "file_count": 1,
        "files": [
            {"path": "../escape.txt", "bytes": 1, "sha256": hashlib.sha256(content).hexdigest()}
        ],
    }
    with zipfile.ZipFile(unsafe, "w") as handle:
        handle.writestr("ARCHMARSHAL-BACKUP.json", json.dumps(manifest))
        handle.writestr("files/../escape.txt", content)

    with pytest.raises(ArchMarshalError, match="unsafe relative path"):
        verify_backup(unsafe)

    root = tmp_path / "project"
    root.mkdir()
    source = root / "notes.txt"
    source.write_text("important\n", encoding="utf-8")
    archive = root / "backup.zip"
    create_backup(root, [source], archive, reason="test")
    with zipfile.ZipFile(archive, "a") as handle:
        handle.writestr("undeclared.bin", b"junk")

    with pytest.raises(ArchMarshalError, match="undeclared"):
        verify_backup(archive)


def test_failed_backup_publish_leaves_no_partial_archive(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "notes.txt"
    source.write_text("important\n", encoding="utf-8")
    archive = root / "backup.zip"
    original = zipfile.ZipFile.writestr

    def fail_manifest(self, name, data, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "ARCHMARSHAL-BACKUP.json":
            raise OSError("simulated disk failure")
        return original(self, name, data, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail_manifest)
    with pytest.raises(OSError, match="simulated disk failure"):
        create_backup(root, [source], archive, reason="test")

    assert not archive.exists()
    assert not list(root.glob(".*.tmp"))


def test_directory_scan_permission_error_is_never_treated_as_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    def denied_walk(path, *, topdown, onerror, followlinks):  # type: ignore[no-untyped-def]
        onerror(PermissionError(13, "permission denied", str(path)))
        return iter(())

    monkeypatch.setattr("archmarshal.safety.os.walk", denied_walk)
    with pytest.raises(ArchMarshalError) as raised:
        files_below_no_links(root, purpose="Permission test")

    assert raised.value.code == "directory_scan_failed"


def test_complete_skill_package_drift_is_detected_without_source_mutation(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    skill = _skill(root)
    scripts = skill / "scripts"
    scripts.mkdir()
    script = scripts / "run.py"
    script.write_text("print('v1')\n", encoding="utf-8")
    adopted = adopt_workspace(root, apply=True)
    backup = verify_backup(root / adopted["backup"]["path"])

    assert {
        "skills/demo/SKILL.md",
        "skills/demo/scripts/run.py",
    }.issubset({item["path"] for item in backup["manifest"]["files"]})

    script.write_text("print('v2')\n", encoding="utf-8")
    preview = plan_adoption(root)

    assert preview["review_required"] is True
    assert preview["discovered_skills"][0]["source_drift"] == "changed"
    assert any(item["action"] == "review_source_change" for item in preview["operations"])
    assert "skill.overlay_source_changed" in {item.rule for item in lint_workspace(root)}
    resolution = resolve_workspace(root, "demo")
    assert resolution["suggested_skills"] == []
    assert resolution["blocked_skills"][0]["reason"] == "source_changed"
    assert script.read_text(encoding="utf-8") == "print('v2')\n"


def test_root_skill_fingerprint_excludes_unrelated_project_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    skill = root / "SKILL.md"
    skill.write_text("# Root skill\n", encoding="utf-8")
    project_file = root / "data.txt"
    project_file.write_text("v1\n", encoding="utf-8")
    adopt_workspace(root, apply=True)

    project_file.write_text("v2\n", encoding="utf-8")
    unchanged = plan_adoption(root)
    skill.write_text("# Root skill v2\n", encoding="utf-8")
    changed = plan_adoption(root)

    assert unchanged["review_required"] is False
    assert unchanged["discovered_skills"][0]["source_drift"] == "unchanged"
    assert changed["review_required"] is True


def test_managed_project_discovers_new_skill_create_only(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    adopt_workspace(root, apply=True)
    new_skill = _skill(root, "新增技能")
    before = (new_skill / "SKILL.md").read_bytes()

    preview = plan_adoption(root)
    applied = adopt_workspace(root, apply=True)

    assert preview["stage"] == "sync"
    assert len(preview["discovered_skills"]) == 1
    assert applied["mode"] == "overlay_synced"
    assert (new_skill / "SKILL.md").read_bytes() == before
    overlay = root / applied["discovered_skills"][0]["overlay_manifest"]
    assert not overlay.exists()
    index = load_skill_index(root)
    assert applied["skill_index_commit"]["mode"] == "committed"
    assert index["head"] == applied["skill_index_commit"]["head"]
    assert index["generation"]["skills"][0]["manifest"]["name"] == "新增技能"


def test_cli_start_apply_and_quick_end_apply(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    assert main(["start", str(root), "--apply", "--tag", "安全"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["adoption"]["mode"] == "overlay_applied"
    assert started["mode"] == "overlay_applied"
    assert "安全" in started["adoption"]["project_tags"]

    assert main(
        ["end", str(root), "--level", "quick", "--summary", "完成", "--apply"]
    ) == 0
    ended = json.loads(capsys.readouterr().out)
    assert ended["mode"] == "append_only_applied"


def test_cli_start_sync_and_dedicated_entrypoint_apply(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    adopt_workspace(root, apply=True)
    _skill(root, "later")

    assert main(["start", str(root), "--apply"]) == 0
    synced = json.loads(capsys.readouterr().out)
    assert synced["mode"] == "overlay_synced"

    second = tmp_path / "second"
    second.mkdir()
    assert start_main([str(second), "--apply"]) == 0
    dedicated = json.loads(capsys.readouterr().out)
    assert dedicated["mode"] == "overlay_applied"


def test_cli_skill_index_status_and_reviewed_rollback(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = adopt_workspace(root, apply=True)
    target = initial["skill_index_commit"]["head"]
    _skill(root)
    synced = adopt_workspace(root, apply=True)
    expected_head = synced["skill_index_commit"]["head"]

    assert main(["skill-index-status", str(root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["chain_status"] == "healthy"

    assert main(["skill-index-rollback", str(root), "--to", target]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "propose_only"
    assert preview["expected_head"] == expected_head

    assert (
        main(
            [
                "skill-index-rollback",
                str(root),
                "--to",
                target,
                "--expect-head",
                expected_head,
                "--apply",
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["mode"] == "rolled_back"
    assert (root / "skills/demo/SKILL.md").exists()


def test_closeout_blocks_missing_evidence_and_high_confidence_secrets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    adopt_workspace(root, apply=True)

    incomplete = record_closeout(root, level="standard", apply=True)
    jwt = "eyJabcdefghijklmno.abcdefghijklmno.abcdefghijklmno"
    sensitive = record_closeout(root, level="quick", apply=True, summary=f"token {jwt}")

    assert incomplete["mode"] == "blocked"
    assert sensitive["mode"] == "blocked"
    assert not (root / incomplete["session_dir"]).exists()
    assert not (root / sensitive["session_dir"]).exists()


def test_closeout_allows_environment_variable_secret_reference(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    adopt_workspace(root, apply=True)
    script = root / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    result = record_closeout(
        root,
        level="reproducible",
        summary="Run with an injected credential.",
        steps=["Execute the script."],
        scripts=["run.py"],
        commands=["python run.py --token $env:API_TOKEN"],
    )

    assert result["mode"] == "propose_only"
    assert result["script_errors"] == []
    assert result["reproduction_evidence_ready"] is True


def test_closeout_concurrent_claim_blocks_without_writing(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    adopt_workspace(root, apply=True)
    claimed = root / ".agent" / "history" / "claimed"
    claimed.mkdir(parents=True)
    monkeypatch.setattr("archmarshal.session.unique_path", lambda _path: claimed)

    result = record_closeout(root, level="quick", apply=True, summary="done")

    assert result["mode"] == "blocked"
    assert list(claimed.iterdir()) == []


def test_duplicate_roots_and_skill_ids_do_not_fake_learning_threshold(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    adopt_workspace(root, apply=True)
    skill_id = plan_adoption(root)["discovered_skills"][0]
    overlay = yaml.safe_load((root / skill_id["overlay_manifest"]).read_text(encoding="utf-8"))
    record_closeout(
        root,
        level="standard",
        apply=True,
        summary="One session only.",
        steps=["Run once."],
        used_skills=[overlay["id"], overlay["id"]],
    )

    learned = learn_from_projects([root, root])

    assert learned["source_session_count"] == 1
    assert learned["common_skill_candidates"] == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_linked_agent_directory_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, root / ".agent", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ArchMarshalError) as raised:
        adopt_workspace(root, apply=True)

    assert raised.value.code in {"unsafe_managed_link", "unsafe_path_escape"}
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_nested_linked_skill_directory_is_not_scanned(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    (root / "skills").mkdir()
    outside_skill = _skill(outside, "private")
    marker = outside_skill / "scripts" / "secret.txt"
    marker.parent.mkdir()
    marker.write_text("must remain outside\n", encoding="utf-8")
    try:
        os.symlink(outside_skill, root / "skills" / "linked", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    preview = plan_adoption(root)

    assert preview["discovered_skills"] == []
    assert marker.read_text(encoding="utf-8") == "must remain outside\n"
