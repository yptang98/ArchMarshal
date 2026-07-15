from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import archmarshal.candidate_draft as candidate_draft_module
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.candidate_draft import candidate_to_skill_draft
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.learning import LEARNING_FORMAT
from archmarshal.promotion import promote_learning_candidate, review_learning_candidate
from archmarshal.schema_validation import validate_schema
from archmarshal.user_store import (
    apply_user_store_initialization,
    plan_user_store_initialization,
)


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _setup(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    root = tmp_path / "source"
    root.mkdir()
    adoption = plan_adoption(root)
    adopt_workspace(root, apply=True, expected_plan=adoption["plan_digest"])
    candidate_id = "candidate.skill." + "a" * 24
    candidate = {
        "candidate_id": candidate_id,
        "candidate_type": "common_skill",
        "skill_id": "skill.functional.recurring-helper",
        "name": "recurring-helper",
        "kind": "functional_skill",
        "source": "skills/recurring-helper",
        "workspace_id": "workspace-test",
        "implementation_sha256": "b" * 64,
        "observed_sessions": 2,
        "evidence_refs": [],
        "suggested_kind": "common_project_skill",
        "status": "candidate",
        "promotion_policy": "human_review_required",
        "reason": "Observed in multiple reviewed sessions.",
    }
    profile = {
        "format": LEARNING_FORMAT,
        "generated_at": "2026-07-15T00:00:00+00:00",
        "source_project_count": 1,
        "source_session_count": 2,
        "limits": {"automatic_global_skill_mutation": False},
        "common_skill_candidates": [candidate],
        "repeated_scripts": [],
        "preference_candidates": [],
        "skill_usage": [],
    }
    profile_bytes = yaml.safe_dump(profile, sort_keys=False).encode("utf-8")
    pack = root / ".agent/inbox/learning/2026/07/15/fixture"
    pack.mkdir(parents=True)
    (pack / "candidates.yaml").write_bytes(profile_bytes)
    commit = {
        "format": "archmarshal-learning-candidate-commit-v1",
        "candidate_format": LEARNING_FORMAT,
        "file": "candidates.yaml",
        "bytes": len(profile_bytes),
        "sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "candidate_ids": [candidate_id],
        "source_mutation": False,
    }
    (pack / "COMMITTED.json").write_text(
        json.dumps(commit, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    store = tmp_path / "user-store"
    initialization = plan_user_store_initialization(store)
    apply_user_store_initialization(
        store,
        initialization,
        expected_plan=initialization["plan_digest"],
    )
    acceptance = review_learning_candidate(
        root,
        pack,
        candidate_id,
        store,
        decision="accept",
        reason="Approved for drafting only.",
    )
    review_learning_candidate(
        root,
        pack,
        candidate_id,
        store,
        decision="accept",
        reason="Approved for drafting only.",
        expected_head_token=acceptance["expected_head_token"],
        expected_plan=acceptance["plan_digest"],
        reviewed_plan=acceptance["user_store_plan"],
        apply=True,
    )
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    return root, pack, candidate_id, store


def _apply(preview: dict, root: Path, pack: Path, candidate: str, store: Path) -> dict:
    return candidate_to_skill_draft(
        root,
        pack,
        candidate,
        store,
        preview["destination"],
        reviewed_preview=preview,
        expected_plan=preview["plan_digest"],
        expected_head_token=preview["expected_head_token"],
        apply=True,
    )


def test_candidate_draft_preview_apply_is_create_only_and_source_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, pack, candidate, store = _setup(tmp_path)
    destination = tmp_path / "drafts" / "envelope"
    preview = candidate_to_skill_draft(root, pack, candidate, store, destination)
    source_before = _tree_hashes(root)
    store_before = _tree_hashes(store)
    publication_order: list[str] = []
    real_create = candidate_draft_module.create_bytes_exclusive

    def record_create(path: Path, content: bytes, **kwargs: object) -> None:
        publication_order.append(path.relative_to(destination).as_posix())
        real_create(path, content, **kwargs)

    monkeypatch.setattr(candidate_draft_module, "create_bytes_exclusive", record_create)
    applied = _apply(preview, root, pack, candidate, store)

    assert _tree_hashes(root) == source_before
    assert _tree_hashes(store) == store_before
    assert publication_order[-1] == "COMMITTED.json"
    assert preview["draft_plan"]["publication_order"] == publication_order
    promotion = Path(applied["promotion_path"])
    assert promotion == destination / "recurring-helper"
    assert sorted(path.name for path in promotion.iterdir()) == [
        "SKILL.md.draft",
        "manifest.yaml",
    ]
    assert not list(destination.rglob("SKILL.md"))
    skill_text = (promotion / "SKILL.md.draft").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(skill_text.split("---", 2)[1])
    assert set(frontmatter) == {"name", "description"}
    manifest = yaml.safe_load((promotion / "manifest.yaml").read_text(encoding="utf-8"))
    assert validate_schema(manifest, "skill-manifest") == []
    assert manifest["status"] == "disabled"
    assert manifest["promotion"]["candidate_digest"] == preview["candidate_digest"]
    assert (destination / "REVIEW.md").is_file()
    receipt = json.loads((destination / "COMMITTED.json").read_text(encoding="utf-8"))
    assert receipt["receipt_published_last"] is True
    assert receipt["auto_promoted"] is False
    assert applied["next_actions"][1]["command_args"][-1] == str(promotion)
    with pytest.raises(ArchMarshalError) as blocked:
        promote_learning_candidate(root, pack, candidate, store, draft=promotion)
    assert blocked.value.code == "user_store_skill_draft_invalid"


def test_candidate_draft_rejects_latest_rejection_and_stale_acceptance(
    tmp_path: Path,
) -> None:
    root, pack, candidate, store = _setup(tmp_path)
    destination = tmp_path / "drafts" / "rejected"
    preview = candidate_to_skill_draft(root, pack, candidate, store, destination)
    rejection = review_learning_candidate(
        root, pack, candidate, store, decision="reject", reason="Needs more work."
    )
    review_learning_candidate(
        root,
        pack,
        candidate,
        store,
        decision="reject",
        reason="Needs more work.",
        expected_head_token=rejection["expected_head_token"],
        expected_plan=rejection["plan_digest"],
        reviewed_plan=rejection["user_store_plan"],
        apply=True,
    )
    with pytest.raises(ArchMarshalError) as rejected:
        _apply(preview, root, pack, candidate, store)
    assert rejected.value.code == "candidate_draft_candidate_not_accepted"
    assert not destination.exists()
    with pytest.raises(ArchMarshalError) as new_preview:
        candidate_to_skill_draft(root, pack, candidate, store, destination)
    assert new_preview.value.code == "candidate_draft_candidate_not_accepted"


def test_candidate_draft_preserves_collision_link_and_interrupted_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, pack, candidate, store = _setup(tmp_path)
    collision = tmp_path / "drafts" / "collision"
    collision_preview = candidate_to_skill_draft(root, pack, candidate, store, collision)
    collision.mkdir()
    sentinel = collision / "external.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    with pytest.raises(ArchMarshalError) as collided:
        _apply(collision_preview, root, pack, candidate, store)
    assert collided.value.code == "candidate_draft_destination_exists"
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"

    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(ArchMarshalError) as linked:
            candidate_to_skill_draft(root, pack, candidate, store, alias / "linked")
        assert linked.value.code == "unsafe_path_link"
        assert list(outside.iterdir()) == []

    partial = tmp_path / "drafts" / "partial"
    partial_preview = candidate_to_skill_draft(root, pack, candidate, store, partial)
    real_create = candidate_draft_module.create_bytes_exclusive
    calls = 0

    def interrupt(path: Path, content: bytes, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interruption")
        real_create(path, content, **kwargs)

    monkeypatch.setattr(candidate_draft_module, "create_bytes_exclusive", interrupt)
    with pytest.raises(OSError, match="simulated interruption"):
        _apply(partial_preview, root, pack, candidate, store)
    monkeypatch.setattr(candidate_draft_module, "create_bytes_exclusive", real_create)
    partial_before = _tree_hashes(partial)
    assert partial_before
    assert not (partial / "COMMITTED.json").exists()
    with pytest.raises(ArchMarshalError) as retry:
        _apply(partial_preview, root, pack, candidate, store)
    assert retry.value.code == "candidate_draft_destination_exists"
    assert _tree_hashes(partial) == partial_before


def test_candidate_draft_cli_requires_complete_exact_saved_preview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, pack, candidate, store = _setup(tmp_path)
    destination = tmp_path / "drafts" / "cli-envelope"
    base = [
        "candidate-draft",
        str(root),
        "--pack",
        str(pack),
        "--candidate",
        candidate,
        "--user-store",
        str(store),
        "--destination",
        str(destination),
    ]
    assert main(base) == 0
    preview = json.loads(capsys.readouterr().out)
    plan_file = tmp_path / "candidate-draft-preview.json"
    plan_file.write_text(json.dumps(preview), encoding="utf-8")
    wrong = [
        *base,
        "--apply",
        "--plan-file",
        str(plan_file),
        "--expect-head",
        preview["expected_head_token"],
        "--expect-plan",
        "0" * 64,
    ]
    assert main(wrong) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "candidate_draft_plan_digest_mismatch"
    assert not destination.exists()
    exact = [*wrong[:-1], preview["plan_digest"]]
    assert main(exact) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["promotion_path"] == str(destination / "recurring-helper")
    assert (destination / "COMMITTED.json").is_file()
