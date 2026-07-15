from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import archmarshal.user_store as user_store_module
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.safety import fingerprint_directory
from archmarshal.user_store import (
    _apply_user_store_decision as apply_user_store_decision,
)
from archmarshal.user_store import (
    _apply_user_store_promotion as apply_user_store_promotion,
)
from archmarshal.user_store import (
    _plan_user_store_decision as plan_user_store_decision,
)
from archmarshal.user_store import (
    _plan_user_store_promotion as plan_user_store_promotion,
)
from archmarshal.user_store import (
    apply_user_store_forward_rollback,
    apply_user_store_initialization,
    plan_user_store_forward_rollback,
    plan_user_store_initialization,
    read_user_store_active,
    user_store_status,
)

PROVENANCE = [
    {
        "kind": "committed_session",
        "ref": "project-a:session-001",
        "digest": "a" * 64,
    }
]


def _initialize(store: Path) -> dict[str, object]:
    plan = plan_user_store_initialization(store, created_at="2026-07-15T00:00:00+00:00")
    return apply_user_store_initialization(
        store,
        plan,
        expected_plan=plan["plan_digest"],
    )


def _draft(root: Path, name: str) -> Path:
    draft = root / name
    draft.mkdir(parents=True)
    manifest = {
        "id": f"skill.common-project.{name}",
        "name": name,
        "kind": "common_project_skill",
        "version": "1.0.0",
        "status": "active",
        "priority": "normal",
        "scope": "common_project",
        "summary": f"Reusable {name} workflow.",
        "tags": [name, "reusable"],
        "triggers": [f"run {name}"],
        "negative_triggers": [f"skip {name}"],
        "paths": {"skill_root": ".", "scripts": "scripts"},
    }
    (draft / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    (draft / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Use this reviewed {name} workflow and its local scripts.\n"
        "---\n\n"
        f"# {name}\n\nUse scripts/run.py for the reviewed workflow.\n",
        encoding="utf-8",
    )
    scripts = draft / "scripts"
    scripts.mkdir()
    (scripts / "run.py").write_text("print('ok')\n", encoding="utf-8")
    return draft


def _promotion_plan(store: Path, draft: Path, candidate: str, digest: str) -> dict[str, object]:
    return plan_user_store_promotion(
        store,
        candidate_id=candidate,
        candidate_digest=digest,
        provenance=PROVENANCE,
        skill_draft=draft,
        created_at="2026-07-15T01:00:00+00:00",
    )


def _apply_promotion(store: Path, plan: dict[str, object]) -> dict[str, object]:
    return apply_user_store_promotion(
        store,
        plan,
        expected_head=plan["expected_head"],
        expected_plan=plan["plan_digest"],
    )


def test_initialization_is_create_only_and_root_bound(tmp_path: Path) -> None:
    store = tmp_path / "user-store"
    preview = plan_user_store_initialization(
        store,
        created_at="2026-07-15T00:00:00+00:00",
    )

    assert user_store_status(store)["state"] == "absent"
    assert not store.exists()

    applied = apply_user_store_initialization(
        store,
        preview,
        expected_plan=preview["plan_digest"],
    )
    ownership = json.loads((store / "ownership.json").read_text(encoding="utf-8"))

    assert applied["mode"] == "initialized"
    assert ownership["store_id"] == applied["store_id"]
    assert user_store_status(store)["state"] == "uninitialized"
    with pytest.raises(ArchMarshalError) as raised:
        plan_user_store_initialization(store)
    assert raised.value.code == "user_store_already_initialized"

    moved = tmp_path / "moved-store"
    moved.mkdir()
    (moved / "ownership.json").write_bytes((store / "ownership.json").read_bytes())
    assert user_store_status(moved)["state"] == "invalid"


def test_initialization_rejects_linked_parent_without_touching_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this platform")

    with pytest.raises(ArchMarshalError) as raised:
        plan_user_store_initialization(alias / "store")
    assert raised.value.code == "user_store_root_invalid"
    assert list(outside.iterdir()) == []


def test_initialization_does_not_claim_concurrently_populated_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = tmp_path / "user-store"
    store.mkdir()
    plan = plan_user_store_initialization(store)
    real_create = user_store_module.create_bytes_exclusive

    def populate_before_marker(path, content, **kwargs):  # type: ignore[no-untyped-def]
        if path.name == "ownership.json":
            (store / "external.txt").write_text("external\n", encoding="utf-8")
        return real_create(path, content, **kwargs)

    monkeypatch.setattr(user_store_module, "create_bytes_exclusive", populate_before_marker)
    with pytest.raises(ArchMarshalError) as raised:
        apply_user_store_initialization(store, plan, expected_plan=plan["plan_digest"])

    assert raised.value.code == "user_store_stale_plan"
    assert (store / "external.txt").read_text(encoding="utf-8") == "external\n"
    assert not (store / "ownership.json").exists()


def test_decision_apply_requires_exact_plan_and_head(tmp_path: Path) -> None:
    store = tmp_path / "user-store"
    _initialize(store)
    plan = plan_user_store_decision(
        store,
        candidate_id="candidate.reject-one",
        candidate_digest="b" * 64,
        decision="rejected",
        provenance=PROVENANCE,
        reason="Project-specific behavior should stay local.",
        created_at="2026-07-15T01:00:00+00:00",
    )

    with pytest.raises(ArchMarshalError) as raised:
        apply_user_store_decision(
            store,
            plan,
            expected_head=plan["expected_head"],
            expected_plan="0" * 64,
        )
    assert raised.value.code == "user_store_stale_plan"
    assert user_store_status(store)["head"] is None

    applied = apply_user_store_decision(
        store,
        plan,
        expected_head=plan["expected_head"],
        expected_plan=plan["plan_digest"],
    )
    status = user_store_status(store)

    assert applied["head"] == status["head"]
    assert status["candidate_decisions"] == 1
    assert read_user_store_active(store)["common_skills"] == []


def test_promotion_rejects_stale_draft_and_never_mutates_source(tmp_path: Path) -> None:
    store = tmp_path / "user-store"
    _initialize(store)
    draft = _draft(tmp_path, "release-helper")
    stale = _promotion_plan(store, draft, "candidate.release", "c" * 64)
    (draft / "SKILL.md").write_text(
        "---\nname: release-helper\ndescription: Changed after review.\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(ArchMarshalError) as raised:
        _apply_promotion(store, stale)
    assert raised.value.code == "user_store_source_changed"
    assert user_store_status(store)["head"] is None

    reviewed = _promotion_plan(store, draft, "candidate.release-v2", "d" * 64)
    before = fingerprint_directory(draft, purpose="test source")
    applied = _apply_promotion(store, reviewed)
    after = fingerprint_directory(draft, purpose="test source")
    active = read_user_store_active(store)

    assert before == after
    assert applied["head"] == active["head"]
    assert active["common_skills"][0]["id"] == "skill.common-project.release-helper"
    assert Path(active["common_skills"][0]["package_dir"]).is_dir()


def test_concurrent_reviewed_plans_use_expected_head_cas(tmp_path: Path) -> None:
    store = tmp_path / "user-store"
    _initialize(store)
    first_draft = _draft(tmp_path, "first")
    second_draft = _draft(tmp_path, "second")
    first = _promotion_plan(store, first_draft, "candidate.first", "1" * 64)
    second = _promotion_plan(store, second_draft, "candidate.second", "2" * 64)

    first_result = _apply_promotion(store, first)
    with pytest.raises(ArchMarshalError) as raised:
        _apply_promotion(store, second)

    assert raised.value.code == "user_store_stale_head"
    assert user_store_status(store)["head"] == first_result["head"]
    assert [item["id"] for item in read_user_store_active(store)["common_skills"]] == [
        "skill.common-project.first"
    ]


def test_committed_orphan_package_is_not_activated(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "user-store"
    _initialize(store)
    draft = _draft(tmp_path, "orphan")
    plan = _promotion_plan(store, draft, "candidate.orphan", "3" * 64)
    real_publish = user_store_module._publish_generation_object

    def interrupt_after_package(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("simulated interruption before generation publication")

    monkeypatch.setattr(user_store_module, "_publish_generation_object", interrupt_after_package)
    with pytest.raises(OSError, match="simulated interruption"):
        _apply_promotion(store, plan)

    package = store / str(plan["package"]["package_path"])
    assert (package / "COMMITTED.json").is_file()
    assert user_store_status(store)["head"] is None
    assert read_user_store_active(store)["common_skills"] == []

    monkeypatch.setattr(user_store_module, "_publish_generation_object", real_publish)
    recovered = _apply_promotion(store, plan)
    assert user_store_status(store)["head"] == recovered["head"]


def test_package_staging_orphan_does_not_block_safe_promotion(tmp_path: Path) -> None:
    store = tmp_path / "user-store"
    _initialize(store)
    staging = store / ".archmarshal" / "staging"
    staging.mkdir(parents=True)
    (staging / ".am-deadbeef.tmp").write_text("orphan\n", encoding="utf-8")
    draft = _draft(tmp_path, "staging-safe")
    plan = _promotion_plan(store, draft, "candidate.staging", "6" * 64)

    applied = _apply_promotion(store, plan)
    active = read_user_store_active(store)
    assert active["head"] == applied["head"]
    assert active["common_skills"][0]["id"] == "skill.common-project.staging-safe"
    assert (staging / ".am-deadbeef.tmp").is_file()


def test_preferences_reject_secrets_and_absolute_paths(tmp_path: Path) -> None:
    store = tmp_path / "user-store"
    _initialize(store)

    for value, code in [
        ("D:\\projects\\private", "user_store_absolute_path_rejected"),
        ("token=sk-abcdefghijklmnopqrstuvwxyz", "user_store_secret_rejected"),
    ]:
        with pytest.raises(ArchMarshalError) as raised:
            plan_user_store_promotion(
                store,
                candidate_id=f"candidate.preference.{hashlib.sha256(value.encode()).hexdigest()[:8]}",
                candidate_digest=hashlib.sha256(value.encode()).hexdigest(),
                provenance=PROVENANCE,
                preference={"key": "preferred.workflow", "value": value},
            )
        assert raised.value.code == code


def test_forward_rollback_publishes_new_head_and_keeps_old_objects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "user-store"
    _initialize(store)
    draft = _draft(tmp_path, "review")
    first_plan = _promotion_plan(store, draft, "candidate.review", "4" * 64)
    first = _apply_promotion(store, first_plan)

    preference_plan = plan_user_store_promotion(
        store,
        candidate_id="candidate.preference.shell",
        candidate_digest="5" * 64,
        provenance=PROVENANCE,
        preference={"key": "preferred.shell", "value": "powershell"},
        created_at="2026-07-15T02:00:00+00:00",
    )
    second = _apply_promotion(store, preference_plan)
    assert read_user_store_active(store)["preference_values"] == {
        "preferred.shell": "powershell"
    }

    rollback_plan = plan_user_store_forward_rollback(
        store,
        str(first["head"]),
        reason="Preference needs another review.",
        expected_head=str(second["head"]),
        created_at="2026-07-15T03:00:00+00:00",
    )
    reviewed = tmp_path / "rollback.json"
    reviewed.write_text(json.dumps({"user_store_plan": rollback_plan}), encoding="utf-8")
    assert (
        main(
            [
                "user-store-rollback",
                str(store),
                "--to",
                "f" * 64,
                "--reason",
                "Preference needs another review.",
                "--plan-file",
                str(reviewed),
                "--expect-head",
                str(second["head"]),
                "--expect-plan",
                str(rollback_plan["plan_digest"]),
                "--apply",
            ]
        )
        == 2
    )
    capsys.readouterr()
    assert user_store_status(store)["head"] == second["head"]
    rolled_back = apply_user_store_forward_rollback(
        store,
        rollback_plan,
        expected_head=rollback_plan["expected_head"],
        expected_plan=rollback_plan["plan_digest"],
    )
    active = read_user_store_active(store)

    assert rolled_back["head"] not in {first["head"], second["head"]}
    assert active["preference_values"] == {}
    assert [item["id"] for item in active["common_skills"]] == [
        "skill.common-project.review"
    ]
    assert (store / ".archmarshal" / "objects" / "sha256" / f"{second['head']}.json").is_file()
    assert user_store_status(store)["generation_count"] == 3


def test_raw_candidate_publication_primitives_are_not_supported_public_api() -> None:
    assert "plan_user_store_promotion" not in user_store_module.__all__
    assert "apply_user_store_promotion" not in user_store_module.__all__
