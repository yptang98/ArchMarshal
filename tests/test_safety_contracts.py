from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

import archmarshal.adoption as adoption_module
import archmarshal.adoption_tx as adoption_tx_module
import archmarshal.safety as safety_module
import archmarshal.session as session_module
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.adoption_tx import (
    adoption_transaction_status,
    recover_adoption_transaction,
)
from archmarshal.cli import main, start_main
from archmarshal.errors import ArchMarshalError
from archmarshal.learning import learn_from_projects
from archmarshal.lint import lint_workspace
from archmarshal.resolver import resolve_workspace
from archmarshal.safety import (
    create_backup,
    create_text_exclusive,
    files_below_no_links,
    restore_backup,
    verify_backup,
)
from archmarshal.session import record_closeout, verify_committed_session
from archmarshal.skill_index import load_skill_index


def _apply_adoption(
    root: Path,
    *,
    tags: list[str] | None = None,
    backup_scope: str = "managed",
) -> dict[str, object]:
    preview = plan_adoption(root, tags=tags, backup_scope=backup_scope)
    return adopt_workspace(
        root,
        apply=True,
        tags=tags,
        backup_scope=backup_scope,
        expected_plan=preview["plan_digest"],
    )


def _apply_closeout(root: Path, **kwargs) -> dict[str, object]:  # type: ignore[no-untyped-def]
    preview = record_closeout(root, apply=False, **kwargs)
    return record_closeout(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        **kwargs,
    )


def _recover_active_adoption(root: Path) -> dict[str, object]:
    preview = recover_adoption_transaction(root)
    transaction = preview["transaction"]
    return recover_adoption_transaction(
        root,
        apply=True,
        expected_transaction=transaction["transaction_id"],
        expected_plan=transaction["plan_digest"],
    )


def _skill(root: Path, name: str = "demo") -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test {name}.\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _leave_interrupted_adoption(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
) -> tuple[dict[str, object], object]:
    preview = plan_adoption(root)
    real_verify = adoption_tx_module._verify_lock_identity
    calls = 0

    def interrupt_after_first_target(held):  # type: ignore[no-untyped-def]
        nonlocal calls
        real_verify(held)
        calls += 1
        if calls == 4:
            raise OSError("simulated process interruption")

    monkeypatch.setattr(
        adoption_tx_module,
        "_verify_lock_identity",
        interrupt_after_first_target,
    )
    with pytest.raises(OSError, match="simulated process interruption"):
        adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    monkeypatch.setattr(adoption_tx_module, "_verify_lock_identity", real_verify)
    return adoption_transaction_status(root), real_verify


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


def test_adoption_apply_requires_exact_reviewed_plan(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root, "alpha")
    preview = plan_adoption(root)

    unreviewed = adopt_workspace(root, apply=True)

    assert unreviewed["mode"] == "review_required"
    assert not (root / ".agent").exists()
    assert all("sha256" in item for item in preview["operations"] if item["action"] == "create")

    _skill(root, "beta")
    stale = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])

    assert stale["mode"] == "blocked"
    assert "plan_digest_changed" in stale["conflicts"]
    assert not (root / ".agent").exists()


def test_lookalike_workspace_file_does_not_grant_control_plane_ownership(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    workspace = root / ".agent" / "workspace.yaml"
    workspace.parent.mkdir(parents=True)
    workspace.write_text(
        "workspace:\n  name: foreign\npaths:\n  agent_root: .agent\n",
        encoding="utf-8",
    )

    preview = plan_adoption(root)

    assert preview["configured"] is False
    assert preview["blocked"] is True
    assert ".agent/workspace.yaml" in preview["conflicts"]
    blocked = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
    )
    assert blocked["mode"] == "blocked"
    assert not (root / ".agent" / "ownership.json").exists()
    assert workspace.read_text(encoding="utf-8").startswith("workspace:")


def test_invalid_ownership_marker_blocks_without_reclaiming_workspace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    ownership = root / ".agent" / "ownership.json"
    ownership.write_text('{"format":"another-tool"}\n', encoding="utf-8")

    preview = plan_adoption(root)

    assert preview["configured"] is False
    assert preview["blocked"] is True
    assert ".agent/ownership.json" in preview["conflicts"]
    assert any(
        diagnostic.rule == "project.ownership_marker_invalid"
        for diagnostic in lint_workspace(root)
    )
    assert ownership.read_text(encoding="utf-8") == '{"format":"another-tool"}\n'


def test_interrupted_adoption_is_forward_recoverable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    status, _ = _leave_interrupted_adoption(monkeypatch, root)
    assert status["state"] == "recovery_required"
    assert any(item["state"] == "verified" for item in status["targets"])
    assert any(item["state"] == "missing" for item in status["targets"])

    recovered = _recover_active_adoption(root)

    assert recovered["mode"] == "recovered"
    assert adoption_transaction_status(root)["state"] == "none"
    assert (root / ".agent" / "ownership.json").exists()
    assert load_skill_index(root)["head"] is not None


def test_adoption_recovery_preserves_changed_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    status, _ = _leave_interrupted_adoption(monkeypatch, root)
    published = next(item["path"] for item in status["targets"] if item["state"] == "verified")
    target = root / published
    target.write_text("user replacement must survive\n", encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        _recover_active_adoption(root)

    assert raised.value.code == "adoption_recovery_conflict"
    assert target.read_text(encoding="utf-8") == "user replacement must survive\n"
    assert adoption_transaction_status(root)["state"] == "recovery_required"


def test_adoption_recovery_rejects_tampered_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    status, _ = _leave_interrupted_adoption(monkeypatch, root)
    journal = (
        root
        / ".agent"
        / "transactions"
        / "adoption"
        / status["transaction_id"]
        / "journal.json"
    )
    journal.write_bytes(journal.read_bytes() + b" ")

    invalid = adoption_transaction_status(root)

    assert invalid["state"] == "invalid"
    assert invalid["error"]["code"] == "adoption_transaction_invalid"
    with pytest.raises(ArchMarshalError) as raised:
        recover_adoption_transaction(
            root,
            apply=True,
            expected_transaction=status["transaction_id"],
            expected_plan=status["plan_digest"],
        )
    assert raised.value.code == "adoption_transaction_invalid"


def test_adoption_recovery_rejects_changed_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    status, _ = _leave_interrupted_adoption(monkeypatch, root)
    backup = root / status["backup"]["path"]
    backup.write_bytes(backup.read_bytes() + b"changed")

    with pytest.raises(ArchMarshalError) as raised:
        _recover_active_adoption(root)

    assert raised.value.code == "adoption_backup_changed"
    assert adoption_transaction_status(root)["state"] == "recovery_required"


def test_adoption_recovery_is_idempotent_after_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    preview = plan_adoption(root)
    real_clear = adoption_tx_module._clear_active

    def interrupt_before_finalize(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("simulated interruption after receipt")

    monkeypatch.setattr(adoption_tx_module, "_clear_active", interrupt_before_finalize)
    with pytest.raises(OSError, match="after receipt"):
        adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])

    status = adoption_transaction_status(root)
    assert status["state"] == "committed_pending_finalize"
    monkeypatch.setattr(adoption_tx_module, "_clear_active", real_clear)

    recovered = _recover_active_adoption(root)

    assert recovered["mode"] == "recovered"
    assert recovered["result"]["skill_index_commit"]["mode"] == "already_committed"
    assert adoption_transaction_status(root)["state"] == "none"


def test_adoption_recovery_rechecks_unchanged_required_skill_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    _apply_adoption(root)
    (root / ".agent" / "cache" / ".gitkeep").unlink()
    preview = plan_adoption(root)
    assert preview["skill_index"]["changed"] is False
    real_clear = adoption_tx_module._clear_active
    monkeypatch.setattr(
        adoption_tx_module,
        "_clear_active",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("after receipt")),
    )
    with pytest.raises(OSError, match="after receipt"):
        adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    status = adoption_transaction_status(root)
    (root / ".agent" / "skill-overlays" / ".archmarshal" / "HEAD").unlink()
    monkeypatch.setattr(adoption_tx_module, "_clear_active", real_clear)

    with pytest.raises(ArchMarshalError) as raised:
        recover_adoption_transaction(
            root,
            apply=True,
            expected_transaction=status["transaction_id"],
            expected_plan=status["plan_digest"],
        )

    assert raised.value.code == "adoption_skill_head_conflict"
    assert adoption_transaction_status(root)["state"] == "committed_pending_finalize"


def test_adoption_recovery_requires_exact_reviewed_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    status, _ = _leave_interrupted_adoption(monkeypatch, root)

    unreviewed = recover_adoption_transaction(root, apply=True)
    assert unreviewed["mode"] == "review_required"
    with pytest.raises(ArchMarshalError) as raised:
        recover_adoption_transaction(
            root,
            apply=True,
            expected_transaction=status["transaction_id"],
            expected_plan="0" * 64,
        )

    assert raised.value.code == "adoption_recovery_stale_plan"
    assert adoption_transaction_status(root)["transaction_id"] == status["transaction_id"]


@pytest.mark.skipif(os.name == "nt", reason="Windows prevents unlinking an open lock file")
def test_adoption_lock_replacement_stops_publication(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    held = adoption_tx_module._acquire_lock(root)
    held.path.unlink()
    held.path.write_bytes(b"replacement")
    try:
        with pytest.raises(ArchMarshalError) as raised:
            adoption_tx_module._verify_lock_identity(held)
    finally:
        adoption_tx_module._release_lock(held)

    assert raised.value.code == "adoption_transaction_lock_replaced"
    assert held.path.read_bytes() == b"replacement"


def test_adoption_linked_lock_stops_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    held = adoption_tx_module._acquire_lock(root)
    real_is_link = adoption_tx_module.is_link_or_reparse
    monkeypatch.setattr(
        adoption_tx_module,
        "is_link_or_reparse",
        lambda path: path == held.path or real_is_link(path),
    )
    try:
        with pytest.raises(ArchMarshalError) as raised:
            adoption_tx_module._verify_lock_identity(held)
    finally:
        adoption_tx_module._release_lock(held)

    assert raised.value.code == "adoption_transaction_lock_replaced"


def test_memory_schema_errors_are_diagnostics_not_tracebacks(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
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
    _apply_adoption(root)
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


def test_atomic_create_failure_never_deletes_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "managed.txt"

    def replace_then_fail(_directory: Path) -> None:
        target.unlink()
        target.write_text("concurrent user content\n", encoding="utf-8")
        raise OSError("simulated directory flush failure")

    monkeypatch.setattr(safety_module, "fsync_directory", replace_then_fail)
    with pytest.raises(OSError, match="directory flush"):
        create_text_exclusive(target, "archmarshal content\n")

    assert target.read_text(encoding="utf-8") == "concurrent user content\n"
    assert not list(tmp_path.glob(".am-*.tmp"))


def test_failed_restore_preserves_concurrent_foreign_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    archive = root / "backup.zip"
    create_backup(root, [first, second], archive, reason="restore race test")
    destination = tmp_path / "restored"
    original_open = Path.open

    def fail_second_output(path: Path, mode: str = "r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if path.name == "second.txt" and mode == "xb" and destination in path.parents:
            (destination / "foreign.txt").write_text("foreign\n", encoding="utf-8")
            raise OSError("simulated restore failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_second_output)
    with pytest.raises(OSError, match="simulated restore failure"):
        restore_backup(archive, destination, apply=True)

    assert (destination / "foreign.txt").read_text(encoding="utf-8") == "foreign\n"
    assert (destination / "first.txt").read_text(encoding="utf-8") == "first\n"


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
    adopted = _apply_adoption(root)
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


def test_adoption_preserves_explicit_disabled_source_manifest(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    skill = _skill(root, "disabled-demo")
    source_manifest = skill / "manifest.yaml"
    source_manifest.write_text(
        yaml.safe_dump(
            {
                "id": "skill.functional.disabled-demo",
                "name": "disabled-demo",
                "kind": "functional_skill",
                "scope": "functional",
                "version": "2.1.0",
                "status": "disabled",
                "priority": "low",
                "summary": "Explicitly disabled source skill.",
                "tags": ["disabled", "demo"],
                "triggers": ["disabled demo"],
                "negative_triggers": ["all tasks until enabled"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    before = source_manifest.read_bytes()

    applied = _apply_adoption(root)
    resolution = resolve_workspace(root, "disabled demo")

    discovered = applied["discovered_skills"][0]
    assert discovered["kind"] == "functional_skill"
    assert resolution["suggested_skills"] == []
    assert resolution["blocked_skills"][0]["reason"] == "status_disabled"
    assert source_manifest.read_bytes() == before


def test_root_skill_fingerprint_excludes_unrelated_project_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    skill = root / "SKILL.md"
    skill.write_text("# Root skill\n", encoding="utf-8")
    project_file = root / "data.txt"
    project_file.write_text("v1\n", encoding="utf-8")
    _apply_adoption(root)

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
    _apply_adoption(root)
    new_skill = _skill(root, "新增技能")
    before = (new_skill / "SKILL.md").read_bytes()

    preview = plan_adoption(root)
    applied = _apply_adoption(root)

    assert preview["stage"] == "sync"
    assert len(preview["discovered_skills"]) == 1
    assert applied["mode"] == "overlay_synced"
    assert (new_skill / "SKILL.md").read_bytes() == before
    overlay = root / applied["discovered_skills"][0]["overlay_manifest"]
    assert overlay.exists()
    index = load_skill_index(root)
    assert applied["skill_index_commit"]["mode"] == "committed"
    assert index["head"] == applied["skill_index_commit"]["head"]
    assert index["generation"]["skills"][0]["manifest"]["name"] == "新增技能"


def test_unindexed_overlay_is_quarantined_until_committed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    _skill(root, "beta")
    built = adoption_module._build_adoption(root, [], "managed")
    beta = next(skill for skill in built["skills"] if skill["manifest"]["name"] == "beta")
    overlay = root / beta["overlay_manifest"]
    overlay.parent.mkdir(parents=True)
    overlay.write_text(built["writes"][overlay], encoding="utf-8")
    head_before = load_skill_index(root)["head"]

    resolution = resolve_workspace(root, "beta")

    assert load_skill_index(root)["head"] == head_before
    assert resolution["suggested_skills"] == []
    assert any(
        item["reason"] == "index_untracked" and item["path"] == "skills/beta"
        for item in resolution["blocked_skills"]
    )


def test_cli_start_apply_and_quick_end_apply(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    preview = plan_adoption(root, tags=["安全"])
    assert (
        main(
            [
                "start",
                str(root),
                "--apply",
                "--expect-plan",
                preview["plan_digest"],
                "--tag",
                "安全",
            ]
        )
        == 0
    )
    started = json.loads(capsys.readouterr().out)
    assert started["adoption"]["mode"] == "overlay_applied"
    assert started["mode"] == "overlay_applied"
    assert "安全" in started["adoption"]["project_tags"]

    closeout_preview = record_closeout(root, level="quick", summary="完成")
    assert (
        main(
            [
                "end",
                str(root),
                "--level",
                "quick",
                "--summary",
                "完成",
                "--expect-plan",
                closeout_preview["plan_digest"],
                "--apply",
            ]
        )
        == 0
    )
    ended = json.loads(capsys.readouterr().out)
    assert ended["mode"] == "append_only_applied"


def test_cli_start_sync_and_dedicated_entrypoint_apply(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    _skill(root, "later")

    preview = plan_adoption(root)
    assert main(["start", str(root), "--apply", "--expect-plan", preview["plan_digest"]]) == 0
    synced = json.loads(capsys.readouterr().out)
    assert synced["mode"] == "overlay_synced"

    second = tmp_path / "second"
    second.mkdir()
    second_preview = plan_adoption(second)
    assert (
        start_main(
            [str(second), "--apply", "--expect-plan", second_preview["plan_digest"]]
        )
        == 0
    )
    dedicated = json.loads(capsys.readouterr().out)
    assert dedicated["mode"] == "overlay_applied"


def test_cli_skill_index_status_and_reviewed_rollback(capsys, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = _apply_adoption(root)
    target = initial["skill_index_commit"]["head"]
    _skill(root)
    synced = _apply_adoption(root)
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
                "--expect-plan",
                preview["plan_digest"],
                "--apply",
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["mode"] == "rolled_back"
    assert (root / "skills/demo/SKILL.md").exists()


def test_managed_overlay_blocks_skills_when_head_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    _apply_adoption(root)
    (root / ".agent" / "skill-overlays" / ".archmarshal" / "HEAD").unlink()
    workspace_path = root / ".agent" / "workspace.yaml"
    workspace = yaml.safe_load(workspace_path.read_text(encoding="utf-8"))
    for key in (
        "global_skills",
        "functional_skills",
        "common_project_skills",
        "project_skills",
        "generated_skills",
    ):
        workspace["paths"][key] = ["skills"]
    workspace_path.write_text(
        yaml.safe_dump(workspace, sort_keys=False),
        encoding="utf-8",
    )
    (root / "skills" / "demo" / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "skill.project.demo",
                "name": "demo",
                "kind": "project_skill",
                "version": "0.1.0",
                "status": "active",
                "priority": "normal",
                "scope": "project",
                "summary": "demo",
                "tags": ["demo"],
                "triggers": ["demo"],
                "negative_triggers": ["not demo"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    resolution = resolve_workspace(root, "demo")
    adoption = plan_adoption(root)

    assert resolution["suggested_skills"] == []
    assert any(item["reason"] == "index_untracked" for item in resolution["blocked_skills"])
    assert adoption["blocked"] is True
    assert ".agent/skill-overlays/.archmarshal/HEAD" in adoption["conflicts"]
    assert any(
        item.rule == "project.required_skill_index_missing"
        for item in lint_workspace(root)
    )


def test_ownership_index_mode_conflict_blocks_sync(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    ownership = root / ".agent" / "ownership.json"
    marker = json.loads(ownership.read_text(encoding="utf-8"))
    marker["skill_index"] = "disabled"
    ownership.write_text(json.dumps(marker) + "\n", encoding="utf-8")

    preview = plan_adoption(root)
    diagnostics = lint_workspace(root)

    assert preview["blocked"] is True
    assert ".agent/ownership.json#skill_index" in preview["conflicts"]
    assert any(
        item.rule == "project.ownership_index_mode_conflict" for item in diagnostics
    )


def test_resolver_rechecks_adoption_transaction_after_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    _apply_adoption(root)
    states = iter(
        [
            {"state": "none", "active": False},
            {"state": "recovery_required", "active": True, "transaction_id": "a" * 32},
        ]
    )
    monkeypatch.setattr(
        "archmarshal.resolver.adoption_transaction_status",
        lambda _root: next(states),
    )

    resolution = resolve_workspace(root, "demo")

    assert resolution["suggested_skills"] == []
    assert resolution["blocked_skills"]
    assert all(
        item["reason"] == "adoption_transaction_incomplete"
        for item in resolution["blocked_skills"]
    )


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_indexed_skill_source_link_replacement_is_quarantined(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = _skill(root)
    _apply_adoption(root)
    relocated = source.with_name("real")
    source.rename(relocated)
    try:
        os.symlink(relocated, source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    resolution = resolve_workspace(root, "demo")

    assert resolution["suggested_skills"] == []
    assert any(item["reason"] == "source_unsafe" for item in resolution["blocked_skills"])


def test_closeout_blocks_missing_evidence_and_high_confidence_secrets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)

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
    _apply_adoption(root)
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


def test_closeout_plan_binds_stable_path_and_exact_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    instants = iter(
        [
            datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
            datetime(2026, 7, 15, 1, 2, 4, tzinfo=timezone.utc),
            datetime(2026, 7, 15, 1, 2, 5, tzinfo=timezone.utc),
        ]
    )

    class SequencedDateTime:
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return next(instants)

    monkeypatch.setattr(session_module, "datetime", SequencedDateTime)
    first = record_closeout(root, level="quick", summary="Stable plan")
    second = record_closeout(root, level="quick", summary="Stable plan")

    assert first["plan_digest"] == second["plan_digest"]
    assert first["session_dir"] == second["session_dir"]
    assert first["operations"] == second["operations"]
    assert all("sha256" in item and "bytes" in item for item in first["operations"])

    applied = record_closeout(
        root,
        level="quick",
        summary="Stable plan",
        expected_plan=first["plan_digest"],
        apply=True,
    )
    assert applied["mode"] == "append_only_applied"


def test_closeout_concurrent_claim_blocks_without_writing(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    claimed = root / ".agent" / "history" / "claimed"
    claimed.mkdir(parents=True)
    monkeypatch.setattr("archmarshal.session.unique_path", lambda _path: claimed)

    preview = record_closeout(root, level="quick", summary="done")
    result = record_closeout(
        root,
        level="quick",
        apply=True,
        summary="done",
        expected_plan=preview["plan_digest"],
    )

    assert result["mode"] == "blocked"
    assert list(claimed.iterdir()) == []


def test_incomplete_closeout_is_preserved_but_never_learned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    real_create = session_module.create_text_exclusive

    def interrupt_before_steps(path: Path, content: str) -> None:
        if path.name == "STEPS.md":
            (path.parent / "SUMMARY.md").write_text(
                "user changed partial summary\n", encoding="utf-8"
            )
            raise OSError("simulated closeout interruption")
        real_create(path, content)

    monkeypatch.setattr(session_module, "create_text_exclusive", interrupt_before_steps)
    with pytest.raises(OSError, match="closeout interruption"):
        preview = record_closeout(
            root,
            level="standard",
            summary="Partial session",
            steps=["First step"],
            tags=["partial"],
        )
        record_closeout(
            root,
            level="standard",
            apply=True,
            expected_plan=preview["plan_digest"],
            summary="Partial session",
            steps=["First step"],
            tags=["partial"],
        )

    incomplete = next((root / ".agent" / "history").rglob("session.yaml")).parent
    assert not (incomplete / "COMMITTED.json").exists()
    assert (incomplete / "SUMMARY.md").read_text(encoding="utf-8") == (
        "user changed partial summary\n"
    )
    learned = learn_from_projects([root])
    assert learned["source_session_count"] == 0


def test_tampered_committed_session_is_not_learning_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    recorded = _apply_closeout(
        root,
        level="standard",
        summary="Committed session",
        steps=["One step"],
        tags=["evidence"],
    )
    session_dir = root / recorded["session_dir"]
    (session_dir / "session.yaml").write_text("tampered: true\n", encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        verify_committed_session(session_dir)
    assert raised.value.code == "session_integrity_failed"
    assert learn_from_projects([root])["source_session_count"] == 0


def test_duplicate_roots_and_skill_ids_do_not_fake_learning_threshold(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)
    _apply_adoption(root)
    skill_id = plan_adoption(root)["discovered_skills"][0]
    overlay = yaml.safe_load((root / skill_id["overlay_manifest"]).read_text(encoding="utf-8"))
    _apply_closeout(
        root,
        level="standard",
        summary="One session only.",
        steps=["Run once."],
        used_skills=[overlay["id"], overlay["id"]],
    )

    learned = learn_from_projects([root, root])

    assert learned["source_session_count"] == 1
    assert learned["common_skill_candidates"] == []


def test_learning_reports_legacy_unverified_sessions(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _apply_adoption(root)
    legacy = root / ".agent" / "history" / "2026" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "session.yaml").write_text(
        "format: archmarshal-session-v1\nused_skills: []\ntags: []\nkey_scripts: []\n",
        encoding="utf-8",
    )

    learned = learn_from_projects([root])

    assert learned["source_session_count"] == 0
    assert learned["legacy_unverified_session_count"] == 1


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
        _apply_adoption(root)

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
