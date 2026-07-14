from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.errors import ArchMarshalError
from archmarshal.inventory import collect_inventory
from archmarshal.resolver import resolve_workspace
from archmarshal.skill_index import commit_skill_index, load_skill_index, plan_skill_index


def _discovered(source: str, summary: str) -> dict[str, object]:
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
                "skill_sha256": "a" * 64,
                "package_sha256": "b" * 64,
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
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(payload)
    (state / "HEAD").write_text(f"{digest}\n", encoding="ascii")
    return digest


def test_skill_index_compare_and_swap_rejects_stale_plan(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered("skills/demo", "initial")])
    committed = commit_skill_index(root, initial)
    first = plan_skill_index(root, [_discovered("skills/demo", "first")])
    stale = plan_skill_index(root, [_discovered("skills/demo", "stale")])

    commit_skill_index(root, first)
    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, stale)

    assert raised.value.code == "skill_index_stale_plan"
    assert load_skill_index(root)["head"] == first["digest"]
    assert load_skill_index(root)["generation"]["parent"] == committed["head"]
    assert not (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").exists()


def test_skill_index_lock_blocks_commit_without_changing_head(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered("skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered("skills/demo", "pending")])
    lock = root / ".agent/skill-overlays/.archmarshal/HEAD.lock"
    lock.write_text('{"token":"another-process"}', encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, pending)

    assert raised.value.code == "skill_index_locked"
    assert load_skill_index(root)["head"] == before


def test_skill_index_tamper_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    planned = plan_skill_index(root, [_discovered("skills/demo", "initial")])
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
    initial = plan_skill_index(root, [_discovered("skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered("skills/demo", "pending")])

    def fail_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        raise OSError("injected atomic swap failure")

    monkeypatch.setattr("archmarshal.skill_index.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected atomic swap failure"):
        commit_skill_index(root, pending)

    assert load_skill_index(root)["head"] == before
    assert (root / pending["object_path"]).exists()
    assert not (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").exists()


def test_skill_index_verification_failure_rolls_back_head_while_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered("skills/demo", "initial")])
    commit_skill_index(root, initial)
    before = load_skill_index(root)["head"]
    pending = plan_skill_index(root, [_discovered("skills/demo", "pending")])
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
    assert not (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").exists()


def test_skill_index_object_collision_never_publishes_head(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    pending = plan_skill_index(root, [_discovered("skills/demo", "pending")])
    object_path = root / pending["object_path"]
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"occupied by different content\n")

    with pytest.raises(ArchMarshalError) as raised:
        commit_skill_index(root, pending)

    assert raised.value.code == "skill_index_object_collision"
    assert load_skill_index(root)["head"] is None
    assert object_path.read_bytes() == b"occupied by different content\n"
    assert not (root / ".agent/skill-overlays/.archmarshal/HEAD.lock").exists()


def test_skill_index_rejects_invalid_commit_plans(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initial = plan_skill_index(root, [_discovered("skills/demo", "initial")])
    commit_skill_index(root, initial)
    unchanged = plan_skill_index(root, [_discovered("skills/demo", "initial")])

    assert commit_skill_index(root, unchanged)["mode"] == "unchanged"
    with pytest.raises(ArchMarshalError) as missing_generation:
        commit_skill_index(root, {"changed": True, "digest": "bad"})
    assert missing_generation.value.code == "skill_index_plan_invalid"

    tampered = plan_skill_index(root, [_discovered("skills/demo", "updated")])
    tampered["generation"]["created_at"] = "changed-after-plan"
    with pytest.raises(ArchMarshalError) as digest_mismatch:
        commit_skill_index(root, tampered)
    assert digest_mismatch.value.code == "skill_index_plan_invalid"


def test_skill_index_rejects_semantically_invalid_generations(tmp_path: Path) -> None:
    template_root = tmp_path / "template"
    template_root.mkdir()
    valid = plan_skill_index(
        template_root, [_discovered("skills/demo", "initial")]
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
        plan_skill_index(root, [_discovered(source, "unsafe")])

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
