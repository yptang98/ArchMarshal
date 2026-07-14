from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

import archmarshal.skill_index as skill_index_module
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.errors import ArchMarshalError
from archmarshal.inventory import collect_inventory
from archmarshal.resolver import resolve_workspace
from archmarshal.safety import fingerprint_directory
from archmarshal.skill_index import (
    commit_skill_index,
    load_skill_index,
    plan_skill_index,
    rollback_skill_index,
    skill_index_status,
)


def _discovered(root: Path, source: str, summary: str) -> dict[str, object]:
    safe_source = bool(source) and not source.startswith(("/", "..")) and "\\" not in source
    skill_hash = "a" * 64
    package_hash = "b" * 64
    if safe_source:
        skill_dir = root / source
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            skill_md.write_text("# Demo\n", encoding="utf-8")
        skill_hash = hashlib.sha256(skill_md.read_bytes()).hexdigest()
        package_hash = fingerprint_directory(skill_dir)["sha256"]
    return {
        "source": source,
        "manifest": {
            "id": "skill.project.demo",
            "name": "demo",
            "kind": "project_skill",
            "version": "0.1.0",
            "status": "active",
            "priority": "normal",
            "scope": "project",
            "summary": summary,
            "tags": ["demo"],
            "triggers": ["demo"],
            "negative_triggers": ["not demo"],
            "source": {
                "skill_dir": source,
                "skill_md": f"{source}/SKILL.md",
                "skill_sha256": skill_hash,
                "package_sha256": package_hash,
                "package_file_count": 1,
                "package_content_bytes": 10,
                "original_manifest": None,
                "managed": False,
                "mutation_policy": "never",
            },
        },
    }


def _source_skill(root: Path, *, script: str = "v1") -> Path:
    skill = root / "skills" / "demo"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo workflow.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    (skill / "scripts" / "run.py").write_text(f"print('{script}')\n", encoding="utf-8")
    return skill


def _publish_raw_generation(root: Path, generation: dict[str, object]) -> str:
    payload = (
        json.dumps(generation, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    state = root / ".agent/skill-overlays/.archmarshal"
    object_path = state / "objects" / "sha256" / f"{digest}.json"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    (state / "HEAD").write_text(f"{digest}\n", encoding="ascii")
    return digest


def test_skill_index_compare_and_swap_rejects_stale_plan(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    committed = commit_skill_index(root, initial)
    first = plan_skill_index(root, [_discovered(root, "skills/demo", "first")])
    stale = plan_skill_index(root, [_discovered(root, "skills/demo", "stale")])

    commit_skill_index(root, first)
    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, stale)

    assert raised.value.code == "skill_index_stale_plan"
    assert load_skill_index(root)["head"] == first["digest"]
    assert load_skill_index(root)["generation"]["parent"] == committed["head"]
    assert (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").read_bytes() == b""


def test_skill_index_lock_blocks_commit_without_changing_head(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    lock = root / ".agent/skill-overlays/.archmarshal/HEAD.lock"
    lock.write_text('{"token":"another-process"}', encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, pending)

    assert raised.value.code == "skill_index_legacy_lock"
    assert load_skill_index(root)["head"] == before


def test_skill_index_commit_rechecks_active_source_inside_lock(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    (root / "skills/demo/SKILL.md").write_text("# Changed after plan\n", encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, pending)

    assert raised.value.code == "skill_index_source_changed"
    assert load_skill_index(root)["head"] == before


def test_skill_index_commit_rechecks_removed_source_inside_lock(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    shutil.rmtree(root / "skills/demo")
    pending = plan_skill_index(root, [])
    _discovered(root, "skills/demo", "restored after plan")

    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, pending)

    assert raised.value.code == "skill_index_source_changed"
    assert load_skill_index(root)["head"] == before


def test_skill_index_tamper_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    planned = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    committed = commit_skill_index(root, planned)
    object_path = root / committed["object_path"]
    object_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        load_skill_index(root)

    assert raised.value.code == "skill_index_integrity_failed"


def test_skill_index_head_swap_failure_preserves_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])

    def fail_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        raise OSError("injected atomic swap failure")

    monkeypatch.setattr("archmarshal.skill_index.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected atomic swap failure"):
        commit_skill_index(root, pending)

    assert load_skill_index(root)["head"] == before
    assert (root / pending["object_path"]).exists()
    assert (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").read_bytes() == b""


def test_skill_index_verification_failure_rolls_back_head_while_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    real_load = load_skill_index

    def reject_published_generation(project: Path) -> dict[str, object]:
        raise ArchMarshalError(
            "skill_index_integrity_failed",
            "injected post-publication verification failure",
        )

    monkeypatch.setattr(
        "archmarshal.skill_index.load_skill_index", reject_published_generation
    )
    with pytest.raises(ArchMarshalError, match="injected post-publication"):
        commit_skill_index(root, pending)

    assert real_load(root)["head"] == before
    assert (root / pending["object_path"]).exists()
    assert (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").read_bytes() == b""


def test_skill_index_object_collision_never_publishes_head(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    object_path = root / pending["object_path"]
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"occupied by different content\n")

    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, pending)

    assert raised.value.code == "skill_index_object_collision"
    assert load_skill_index(root)["head"] is None
    assert object_path.read_bytes() == b"occupied by different content\n"
    assert (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").read_bytes() == b""


def test_skill_index_rejects_invalid_commit_plans(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    unchanged = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])

    assert commit_skill_index(root, unchanged)["mode"] == "unchanged"
    with pytest.raises(ArchMarshalError) as missing_generation:
        commit_skill_index(root, {"changed": True, "digest": "bad"})
    assert missing_generation.value.code == "skill_index_plan_invalid"

    tampered = plan_skill_index(root, [_discovered(root, "skills/demo", "updated")])
    tampered["generation"]["created_at"] = "changed-after-plan"
    with pytest.raises(ArchMarshalError) as digest_mismatch:
        commit_skill_index(root, tampered)
    assert digest_mismatch.value.code == "skill_index_plan_invalid"


def test_skill_index_rejects_semantically_invalid_generations(tmp_path: Path) -> None:
    template_root = tmp_path / "template"
    template_root.mkdir()
    valid = plan_skill_index(
        template_root, [_discovered(template_root, "skills/demo", "initial")]
    )["generation"]
    cases: list[dict[str, object]] = []

    unsupported = copy.deepcopy(valid)
    unsupported["format"] = "unsupported"
    cases.append(unsupported)
    invalid_parent = copy.deepcopy(valid)
    invalid_parent["parent"] = "not-a-digest"
    cases.append(invalid_parent)
    invalid_skills = copy.deepcopy(valid)
    invalid_skills["skills"] = {}
    cases.append(invalid_skills)
    invalid_changes = copy.deepcopy(valid)
    invalid_changes["changes"] = {}
    cases.append(invalid_changes)
    invalid_record = copy.deepcopy(valid)
    invalid_record["skills"][0]["state"] = "unknown"
    cases.append(invalid_record)
    duplicate = copy.deepcopy(valid)
    duplicate["skills"].append(copy.deepcopy(duplicate["skills"][0]))
    cases.append(duplicate)
    mismatched_manifest = copy.deepcopy(valid)
    mismatched_manifest["skills"][0]["manifest"]["source"]["skill_dir"] = "skills/other"
    cases.append(mismatched_manifest)

    for index, generation in enumerate(cases):
        root = tmp_path / f"invalid-{index}"
        root.mkdir()
        _publish_raw_generation(root, generation)
        with pytest.raises(ArchMarshalError) as raised:
            load_skill_index(root)
        assert raised.value.code == "skill_index_integrity_failed"


def test_skill_index_rejects_invalid_head_and_missing_object(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized"
    oversized.mkdir()
    state = oversized / ".agent/skill-overlays/.archmarshal"
    state.mkdir(parents=True)
    (state / "HEAD").write_text("a" * 256, encoding="ascii")
    with pytest.raises(ArchMarshalError) as invalid_head:
        load_skill_index(oversized)
    assert invalid_head.value.code == "skill_index_head_invalid"

    missing = tmp_path / "missing"
    missing.mkdir()
    missing_state = missing / ".agent/skill-overlays/.archmarshal"
    missing_state.mkdir(parents=True)
    (missing_state / "HEAD").write_text(f"{'a' * 64}\n", encoding="ascii")
    with pytest.raises(ArchMarshalError) as missing_object:
        load_skill_index(missing)
    assert missing_object.value.code == "skill_index_object_missing"


@pytest.mark.parametrize("source", ["../outside", "/absolute", "skills\\demo", ""])
def test_skill_index_rejects_unsafe_source_paths(tmp_path: Path, source: str) -> None:
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(ArchMarshalError) as raised:
        plan_skill_index(root, [_discovered(root, source, "unsafe")])

    assert raised.value.code == "skill_index_plan_invalid"


def test_adoption_keeps_immutable_history_for_modify_remove_restore(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    skill = _source_skill(root)
    adopted = adopt_workspace(root, apply=True)
    first_head = adopted["skill_index_commit"]["head"]
    first_object = root / adopted["skill_index_commit"]["object_path"]

    (skill / "scripts" / "run.py").write_text("print('v2')\n", encoding="utf-8")
    modified_preview = plan_adoption(root)
    modified = adopt_workspace(root, apply=True)

    assert {item["kind"] for item in modified_preview["skill_index"]["changes"]} == {"modified"}
    assert modified["skill_index_commit"]["head"] != first_head
    assert first_object.exists()
    assert collect_inventory(root).skills[0]["_source_drift"] == "unchanged"
    assert plan_adoption(root)["review_required"] is False

    shutil.rmtree(skill)
    removed_preview = plan_adoption(root)
    removed = adopt_workspace(root, apply=True)

    assert {item["kind"] for item in removed_preview["skill_index"]["changes"]} == {"removed"}
    assert removed["skill_index_commit"]["mode"] == "committed"
    assert collect_inventory(root).skills == []
    assert resolve_workspace(root, "demo")["suggested_skills"] == []

    _source_skill(root, script="v3")
    restored_preview = plan_adoption(root)
    restored = adopt_workspace(root, apply=True)

    assert {item["kind"] for item in restored_preview["skill_index"]["changes"]} == {"restored"}
    assert restored["skill_index_commit"]["mode"] == "committed"
    assert collect_inventory(root).skills[0]["name"] == "demo"


def test_skill_index_status_is_read_only_when_uninitialized(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    status = skill_index_status(root)

    assert status["chain_status"] == "uninitialized"
    assert status["head"] is None
    assert status["history"] == []
    assert status["lock"]["state"] == "absent"
    assert list(root.iterdir()) == []


def test_skill_index_rollback_creates_audited_generation_without_source_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = adopt_workspace(root, apply=True)
    target = initial["skill_index_commit"]["head"]
    skill = _source_skill(root)
    source_before = {
        path.relative_to(skill).as_posix(): path.read_bytes()
        for path in skill.rglob("*")
        if path.is_file()
    }
    synced = adopt_workspace(root, apply=True)
    expected_head = synced["skill_index_commit"]["head"]

    preview = rollback_skill_index(root, target, reason="hide newly added skill")
    applied = rollback_skill_index(
        root,
        target,
        expected_head=expected_head,
        reason="hide newly added skill",
        apply=True,
    )
    status = skill_index_status(root, history_limit=10)
    limited_status = skill_index_status(root, history_limit=2)
    continued_status = skill_index_status(root, history_limit=2, history_from=target)
    source_after = {
        path.relative_to(skill).as_posix(): path.read_bytes()
        for path in skill.rglob("*")
        if path.is_file()
    }

    assert preview["mode"] == "propose_only"
    assert preview["expected_head"] == expected_head
    assert applied["mode"] == "rolled_back"
    assert applied["commit"]["head"] not in {target, expected_head}
    assert applied["backup"]["verified"] is True
    assert source_after == source_before
    assert collect_inventory(root).skills == []
    assert status["chain_status"] == "healthy"
    assert [item["digest"] for item in status["history"][:3]] == [
        applied["commit"]["head"],
        expected_head,
        target,
    ]
    rollback_change = status["history"][0]["changes"][-1]
    assert rollback_change["kind"] == "rollback"
    assert rollback_change["target"] == target
    assert limited_status["continuation"] == target
    assert continued_status["history"][0]["digest"] == target
    assert plan_adoption(root)["review_required"] is True


def test_skill_index_rollback_blocks_source_package_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    skill = _source_skill(root, script="v1")
    initial = adopt_workspace(root, apply=True)
    target = initial["skill_index_commit"]["head"]
    (skill / "scripts/run.py").write_text("print('v2')\n", encoding="utf-8")
    updated = adopt_workspace(root, apply=True)
    before = updated["skill_index_commit"]["head"]

    with pytest.raises(ArchMarshalError) as raised:
        rollback_skill_index(root, target)

    assert raised.value.code == "skill_index_source_changed"
    assert load_skill_index(root)["head"] == before


def test_skill_index_rollback_requires_reviewed_expected_head(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = adopt_workspace(root, apply=True)
    target = initial["skill_index_commit"]["head"]
    _source_skill(root)
    synced = adopt_workspace(root, apply=True)

    with pytest.raises(ArchMarshalError) as missing_expectation:
        rollback_skill_index(root, target, apply=True)
    assert missing_expectation.value.code == "skill_index_expected_head_required"

    with pytest.raises(ArchMarshalError) as stale:
        rollback_skill_index(root, target, expected_head="a" * 64)
    assert stale.value.code == "skill_index_stale_plan"
    assert load_skill_index(root)["head"] == synced["skill_index_commit"]["head"]


def test_skill_index_rollback_backup_failure_leaves_head_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = adopt_workspace(root, apply=True)
    target = initial["skill_index_commit"]["head"]
    _source_skill(root)
    synced = adopt_workspace(root, apply=True)
    expected_head = synced["skill_index_commit"]["head"]

    def fail_backup(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("injected rollback backup failure")

    monkeypatch.setattr("archmarshal.skill_index.create_backup", fail_backup)
    with pytest.raises(OSError, match="injected rollback backup failure"):
        rollback_skill_index(
            root,
            target,
            expected_head=expected_head,
            apply=True,
        )

    assert load_skill_index(root)["head"] == expected_head


def test_skill_index_status_rejects_incorrect_transition_claim(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    committed = commit_skill_index(root, initial)
    invalid_child = copy.deepcopy(initial["generation"])
    invalid_child["created_at"] = "2026-07-15T00:00:00+00:00"
    invalid_child["parent"] = committed["head"]
    invalid_child["changes"] = [{"kind": "modified", "source": "skills/demo"}]
    _publish_raw_generation(root, invalid_child)

    with pytest.raises(ArchMarshalError) as raised:
        skill_index_status(root)

    assert raised.value.code == "skill_index_history_invalid"


def test_skill_index_status_reports_lock_without_guessing_staleness(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    adopt_workspace(root, apply=True)
    lock = root / ".agent/skill-overlays/.archmarshal/HEAD.lock"
    lock.write_text(
        json.dumps(
            {
                "token": "manual-token",
                "pid": 123,
                "created_at": "2026-07-15T00:00:00+00:00",
                "expected_head": load_skill_index(root)["head"],
            }
        ),
        encoding="utf-8",
    )

    status = skill_index_status(root)

    assert status["lock"]["state"] == "legacy_manual_review"
    assert status["lock"]["automatic_recovery"] is False
    assert status["lock"]["reason"] == "skill_index_legacy_lock"


def test_skill_index_os_lock_blocks_concurrent_writer(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    lock_path = root / ".agent/skill-overlays/.archmarshal/HEAD.lock"
    held = skill_index_module._acquire_lock(
        root,
        lock_path,
        "held-token",
        pending["expected_head"],
        pending["digest"],
    )
    try:
        assert skill_index_status(root)["lock"]["state"] == "held"
        with pytest.raises(ArchMarshalError) as raised:
            commit_skill_index(root, pending)
        assert raised.value.code == "skill_index_locked"
    finally:
        skill_index_module._release_lock(held)


def test_skill_index_recovers_released_os_lock_with_audit_record(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    lock_path = root / ".agent/skill-overlays/.archmarshal/HEAD.lock"
    lock_path.write_text(
        json.dumps(
            {
                "format": "archmarshal-skill-index-lock-v2",
                "token": "crashed-token",
                "pid": 999999,
                "hostname": "crashed-host",
                "created_at": "2026-07-15T00:00:00+00:00",
                "expected_head": pending["expected_head"],
                "proposed_head": pending["digest"],
            }
        ),
        encoding="utf-8",
    )

    status = skill_index_status(root)
    committed = commit_skill_index(root, pending)

    assert status["lock"]["state"] == "recoverable"
    assert status["lock"]["classification"] == "abandoned_before_publish"
    assert committed["head"] == pending["digest"]
    recovery_records = list(
        (root / ".agent/skill-overlays/.archmarshal/recovery").glob("*.json")
    )
    assert len(recovery_records) == 1
    recovery = json.loads(recovery_records[0].read_text(encoding="utf-8"))
    assert recovery["classification"] == "abandoned_before_publish"
    assert recovery["prior_lock"]["token"] == "crashed-token"


def test_skill_index_refuses_released_lock_with_conflicting_head(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    lock_path = root / ".agent/skill-overlays/.archmarshal/HEAD.lock"
    lock_path.write_text(
        json.dumps(
            {
                "format": "archmarshal-skill-index-lock-v2",
                "token": "conflicting-token",
                "pid": 999999,
                "hostname": "crashed-host",
                "created_at": "2026-07-15T00:00:00+00:00",
                "expected_head": "b" * 64,
                "proposed_head": "c" * 64,
            }
        ),
        encoding="utf-8",
    )

    assert skill_index_status(root)["lock"]["state"] == "recovery_blocked"
    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, pending)

    assert raised.value.code == "skill_index_recovery_required"
    assert load_skill_index(root)["head"] == before


def test_skill_index_recovers_transaction_published_before_writer_exit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    committed = commit_skill_index(root, initial)
    pending = plan_skill_index(root, [_discovered(root, "skills/demo", "pending")])
    lock_path = root / ".agent/skill-overlays/.archmarshal/HEAD.lock"
    lock_path.write_text(
        json.dumps(
            {
                "format": "archmarshal-skill-index-lock-v2",
                "token": "published-token",
                "pid": 999999,
                "hostname": "crashed-host",
                "created_at": "2026-07-15T00:00:00+00:00",
                "expected_head": None,
                "proposed_head": committed["head"],
            }
        ),
        encoding="utf-8",
    )

    status = skill_index_status(root)
    commit_skill_index(root, pending)
    recovery_path = next(
        (root / ".agent/skill-overlays/.archmarshal/recovery").glob("*.json")
    )
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))

    assert status["lock"]["classification"] == "published_before_exit"
    assert recovery["classification"] == "published_before_exit"


def test_skill_index_status_rejects_missing_parent_object(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered(root, "skills/demo", "initial")])
    committed = commit_skill_index(root, initial)
    updated = plan_skill_index(root, [_discovered(root, "skills/demo", "updated")])
    commit_skill_index(root, updated)
    (root / committed["object_path"]).unlink()

    with pytest.raises(ArchMarshalError) as raised:
        skill_index_status(root)

    assert raised.value.code == "skill_index_object_missing"


def test_skill_index_rollback_rejects_non_ancestor_object(tmp_path: Path) -> None:
    root = tmp_path / "project"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    adopt_workspace(root, apply=True)
    _source_skill(other)
    other_adoption = adopt_workspace(other, apply=True)
    target = other_adoption["skill_index_commit"]["head"]
    other_object = other / other_adoption["skill_index_commit"]["object_path"]
    local_object = root / other_adoption["skill_index_commit"]["object_path"]
    local_object.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(other_object, local_object)
    before = load_skill_index(root)["head"]

    with pytest.raises(ArchMarshalError) as raised:
        rollback_skill_index(root, target)

    assert raised.value.code == "skill_index_target_not_ancestor"
    assert load_skill_index(root)["head"] == before
