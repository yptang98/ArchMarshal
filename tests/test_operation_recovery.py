from __future__ import annotations

import errno
from pathlib import Path

import pytest
from test_portability import environment as environment
from test_portability import pull, upload

import archmarshal.operation_journal as journal_module
import archmarshal.session as session_module
import archmarshal.skill_sync as sync_module
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.operation_journal import load_operation, operation_status
from archmarshal.operation_recovery import recover_operation
from archmarshal.portable import canonical, decode, digest
from archmarshal.session import record_closeout, verify_committed_session
from archmarshal.skill_package import verify_package
from archmarshal.skill_sync import install_skill, pull_skill, snapshot_skill


def interrupt_pull(env, monkeypatch, boundary="RECEIPT.json"):
    roots, identities, store = env
    key = upload(env)
    args = dict(device_path=identities[1])
    preview = pull_skill(roots[1], key, store, **args)
    original = sync_module.publish
    def interrupted(path, content):
        if path.name == boundary:
            raise OSError(errno.ENOSPC, "injected capacity failure")
        return original(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(sync_module, "publish", interrupted)
        with pytest.raises(OSError, match="capacity failure"):
            pull_skill(roots[1], key, store, apply=True, expected_plan=preview["plan_digest"], **args)
    state = operation_status(roots[1])["operations"][0]
    return key, state, preview


@pytest.mark.parametrize("boundary", ["snapshot.json", "RECEIPT.json"])
def test_interrupted_import_resumes_offline_and_is_idempotent(environment, monkeypatch, boundary):
    roots, identities, _ = environment
    key, state, initial = interrupt_pull(environment, monkeypatch, boundary)
    assert state["stage"] == "writing"
    operation_id = state["operation_id"]
    preview = recover_operation(roots[1], operation_id)
    result = recover_operation(roots[1], operation_id, apply=True,
                               expected_plan=preview["plan_digest"])
    assert result["mode"] == "recovered" and result["stage"] == "complete"
    verify_package(Path(initial["package"]),
                   snapshot_skill(roots[0], ".agents/skills/demo", device_path=identities[0]))
    assert decode((Path(initial["package"]).parent / "RECEIPT.json").read_bytes())["object_id"] == key
    repeated = recover_operation(roots[1], operation_id)
    assert recover_operation(roots[1], operation_id, apply=True,
                             expected_plan=repeated["plan_digest"])["mode"] == "unchanged"


@pytest.mark.parametrize("display_name", ["demo", "Demo Skill"])
def test_install_recovery_preserves_lineage(environment, monkeypatch, display_name):
    roots, identities, store = environment
    snapshot = snapshot_skill(roots[0], ".agents/skills/demo", device_path=identities[0])
    snapshot["name"] = display_name
    key = digest(snapshot)
    store.put(key, canonical(snapshot))
    pull(environment, key)
    target = ".agents/skills/demo"
    preview = install_skill(roots[1], key, target)
    real_materialize = sync_module.materialize_package
    def interrupted(path, snapshot):
        real_materialize(path, snapshot)
        raise OSError("interrupted installation")
    with monkeypatch.context() as patch:
        patch.setattr(sync_module, "materialize_package", interrupted)
        with pytest.raises(OSError, match="interrupted installation"):
            install_skill(roots[1], key, target, apply=True, expected_plan=preview["plan_digest"])
    state = next(s for s in operation_status(roots[1])["operations"] if s["kind"] == "sync_install")
    recovery = recover_operation(roots[1], state["operation_id"])
    result = recover_operation(roots[1], state["operation_id"], apply=True,
                               expected_plan=recovery["plan_digest"])
    assert result["stage"] == "complete"
    origins = list((roots[1] / ".agent/skill-sync-origins").glob("*.json"))
    assert len(origins) == 1
    assert decode(origins[0].read_bytes())["object_id"] == key


@pytest.mark.parametrize("after_commit", [False, True])
def test_closeout_recovery_uses_sealed_script_even_after_source_changes(
        environment, monkeypatch, after_commit):
    roots, _, _ = environment
    root = roots[1]
    source = root / "source.py"
    old_content = source.read_bytes()
    args = dict(level="reproducible", summary="Reproduction evidence", steps=["Run script"],
                scripts=["source.py"], commands=["python source.py"])
    preview = record_closeout(root, **args)
    original = session_module._commit_session
    def interrupted(*args):
        if after_commit:
            original(*args)
        raise OSError("interrupted closeout")
    with monkeypatch.context() as patch:
        patch.setattr(session_module, "_commit_session", interrupted)
        with pytest.raises(OSError, match="interrupted closeout"):
            record_closeout(root, **args, apply=True, expected_plan=preview["plan_digest"])
    source.write_text("print('changed after interruption')\n")
    state = operation_status(root)["operations"][0]
    assert state["stage"] == ("commit-written" if after_commit else "writing")
    recovery = recover_operation(root, state["operation_id"])
    result = recover_operation(root, state["operation_id"], apply=True,
                               expected_plan=recovery["plan_digest"])
    assert result["stage"] == "complete"
    target = root / preview["session_dir"]
    session = verify_committed_session(target)["session"]
    assert session["reproducibility"]["execution_validated"] is False
    assert next((target / "scripts").iterdir()).read_bytes() == old_content
    assert b"changed after" in source.read_bytes()


@pytest.mark.parametrize("change", ["edited", "extra", "directory"])
def test_recovery_preserves_conflicting_outputs(environment, monkeypatch, change):
    roots, _, _ = environment
    _, state, initial = interrupt_pull(environment, monkeypatch)
    target = Path(initial["package"])
    if change == "edited":
        changed = target / "SKILL.md"
        changed.write_bytes(b"user edit")
    elif change == "extra":
        changed = target / "extra.txt"
        changed.write_bytes(b"user addition")
    else:
        changed = target / "user-directory"
        changed.mkdir()
    recovery = recover_operation(roots[1], state["operation_id"])
    assert recovery["mode"] == "blocked" and recovery["conflicts"]
    with pytest.raises(ArchMarshalError, match="preserved"):
        recover_operation(roots[1], state["operation_id"], apply=True,
                           expected_plan=recovery["plan_digest"])
    assert changed.exists()
    assert not (target.parent / "RECEIPT.json").exists()


def test_recovery_binds_current_state_and_requires_preview(environment, monkeypatch):
    roots, _, _ = environment
    _, state, initial = interrupt_pull(environment, monkeypatch)
    preview = recover_operation(roots[1], state["operation_id"])
    with pytest.raises(ArchMarshalError, match="Preview changed"):
        recover_operation(roots[1], state["operation_id"], apply=True)
    (Path(initial["package"]) / "SKILL.md").write_bytes(b"concurrent edit")
    with pytest.raises(ArchMarshalError, match="Preview changed"):
        recover_operation(roots[1], state["operation_id"], apply=True,
                           expected_plan=preview["plan_digest"])


def test_corrupt_staged_bytes_block_before_outputs(environment, monkeypatch):
    roots, _, _ = environment
    _, state, initial = interrupt_pull(environment, monkeypatch)
    base = roots[1] / ".agent/operations" / state["operation_id"]
    next((base / "payloads").iterdir()).write_bytes(b"broken")
    with pytest.raises(ArchMarshalError):
        recover_operation(roots[1], state["operation_id"])
    assert not (Path(initial["package"]).parent / "RECEIPT.json").exists()


def test_staging_failure_is_visible_and_creates_no_output(environment, monkeypatch):
    roots, identities, store = environment
    key = upload(environment)
    args = dict(device_path=identities[1])
    preview = pull_skill(roots[1], key, store, **args)
    original = journal_module._seal
    def interrupted(path, content):
        if path.name == "JOURNAL.json":
            raise OSError("staging interrupted")
        original(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(journal_module, "_seal", interrupted)
        with pytest.raises(OSError, match="staging interrupted"):
            pull_skill(roots[1], key, store, apply=True, expected_plan=preview["plan_digest"], **args)
    assert not Path(preview["package"]).parent.exists()
    assert operation_status(roots[1])["operations"][0]["stage"] == "unprepared"
    result = pull_skill(roots[1], key, store, apply=True, expected_plan=preview["plan_digest"], **args)
    assert result["mode"] == "imported"


def test_changed_inbox_blocks_recovery(environment, monkeypatch):
    import yaml
    roots, _, _ = environment
    _, state, _ = interrupt_pull(environment, monkeypatch)
    config = roots[1] / ".agent/workspace.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["paths"]["inbox"] = ["isolated-inbox"]
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ArchMarshalError, match="mapping changed"):
        recover_operation(roots[1], state["operation_id"])


def test_recovery_rejects_coherently_rehashed_escape(environment, monkeypatch):
    roots, _, _ = environment
    _, state, _ = interrupt_pull(environment, monkeypatch)
    root = roots[1]
    journal = load_operation(root, state["operation_id"])
    journal["files"][0]["path"] = "../escaped"
    operation_id = digest(journal)
    base = root / ".agent/operations" / operation_id
    base.mkdir()
    (base / "JOURNAL.json").write_bytes(canonical(journal))
    with pytest.raises(ArchMarshalError, match="relative path"):
        load_operation(root, operation_id)


def test_recovery_cli_reports_operation(environment, monkeypatch, capsys):
    roots, _, _ = environment
    _, state, _ = interrupt_pull(environment, monkeypatch)
    assert main(["operation-status", str(roots[1])]) == 0
    assert state["operation_id"] in capsys.readouterr().out
    assert main(["operation-recover", str(roots[1]), "--operation", state["operation_id"]]) == 0
    assert '"plan_digest"' in capsys.readouterr().out


def test_status_budgets_doctor_diagnostics_and_internal_inventory(environment, monkeypatch):
    from archmarshal.doctor import doctor_workspace
    from archmarshal.inventory import collect_inventory
    roots, _, _ = environment
    _, state, _ = interrupt_pull(environment, monkeypatch)
    root = roots[1]
    report = doctor_workspace(root)
    assert any(item["code"] == "operation_writing" for item in report["findings"])
    inventory = collect_inventory(root)
    assert not any(path.startswith(".agent/operations/") for path in inventory.unregistered_agent_files)
    preview = recover_operation(root, state["operation_id"])
    recover_operation(root, state["operation_id"], apply=True, expected_plan=preview["plan_digest"])
    assert any(item["code"] == "operation_complete" for item in doctor_workspace(root)["findings"])
    (root / ".agent/operations" / state["operation_id"] / "DONE.json").write_bytes(b"{}")
    assert any(item["code"] == "operation_integrity_failed" for item in doctor_workspace(root)["findings"])
    monkeypatch.setattr(journal_module, "MAX_STATUS_BYTES", 0)
    status = operation_status(root)
    assert status["operations"] == [] and status["omitted"] == [state["operation_id"]]
    assert any(item["code"] == "operation_inspection_limited" for item in doctor_workspace(root)["findings"])
    with pytest.raises(ArchMarshalError, match="between 1 and 1000"):
        operation_status(root, limit=0)
