from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.inventory import collect_inventory
from archmarshal.learning import learn_from_projects, verify_learning_pack
from archmarshal.lint import lint_workspace
from archmarshal.promotion import promote_learning_candidate, review_learning_candidate
from archmarshal.resolver import resolve_workspace
from archmarshal.safety import fingerprint_directory
from archmarshal.session import record_closeout
from archmarshal.skill_review import review_workspace_skill
from archmarshal.skill_validation import validate_skill_package
from archmarshal.user_store import (
    apply_user_store_forward_rollback,
    apply_user_store_initialization,
    plan_user_store_forward_rollback,
    plan_user_store_initialization,
    user_store_status,
)
from archmarshal.workspace_lock import workspace_mutation_lock


def _skill(root: Path, name: str = "demo", *, global_policy: bool = False) -> Path:
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Use when {name} tasks need a reviewed helper.\n"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )
    if global_policy:
        (directory / "manifest.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": f"skill.global.{name}",
                    "name": name,
                    "summary": f"Reviewed {name} policy.",
                    "version": "1.0.0",
                    "kind": "global_skill",
                    "scope": "global",
                    "status": "active",
                    "priority": "highest",
                    "tags": [name],
                    "triggers": [name],
                    "negative_triggers": [f"unrelated to {name}"],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    return directory


def _adopt(root: Path) -> dict:
    preview = plan_adoption(root)
    return adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])


def _approve(root: Path, source: str, *, allow_global_policy: bool = False) -> dict:
    head = _head(root)
    preview = review_workspace_skill(
        root,
        source,
        decision="approve",
        allow_global_policy=allow_global_policy,
        expected_head=head,
    )
    return review_workspace_skill(
        root,
        source,
        decision="approve",
        allow_global_policy=allow_global_policy,
        expected_head=head,
        expected_plan=preview["plan_digest"],
        apply=True,
    )


def _head(root: Path) -> str:
    return (root / ".agent/skill-overlays/.archmarshal/HEAD").read_text(
        encoding="ascii"
    ).strip()


def _closeout(
    root: Path,
    summary: str,
    skill_id: str,
    *,
    tags: list[str] | None = None,
) -> dict:
    preview = record_closeout(
        root,
        level="standard",
        summary=summary,
        steps=["Run the reviewed workflow."],
        used_skills=[skill_id],
        tags=tags,
    )
    return record_closeout(
        root,
        level="standard",
        summary=summary,
        steps=["Run the reviewed workflow."],
        used_skills=[skill_id],
        tags=tags,
        expected_plan=preview["plan_digest"],
        apply=True,
    )


def _common_draft(root: Path, name: str = "demo-common") -> Path:
    draft = root / name
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Use when a run demo task needs the reviewed reusable workflow.\n"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )
    (draft / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "id": f"skill.common-project.{name}",
                "name": name,
                "summary": "Reusable reviewed demo workflow.",
                "version": "1.0.0",
                "kind": "common_project_skill",
                "scope": "common_project",
                "status": "active",
                "priority": "normal",
                "tags": ["demo"],
                "triggers": ["run demo"],
                "negative_triggers": ["skip demo"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return draft


def test_invalid_existing_skill_is_quarantined_and_cannot_be_approved(tmp_path: Path) -> None:
    root = tmp_path / "project"
    skill = root / "skills" / "bad"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Missing frontmatter\n", encoding="utf-8")
    _adopt(root)

    resolution = resolve_workspace(root, "bad")
    assert resolution["suggested_skills"] == []
    assert resolution["blocked_skills"][0]["reason"] == "status_disabled"
    assert "skill_frontmatter_missing" in {item.rule for item in lint_workspace(root)}
    with pytest.raises(ArchMarshalError, match="cannot be approved"):
        review_workspace_skill(
            root,
            "skills/bad",
            decision="approve",
            expected_head=_head(root),
        )


def test_skill_validation_rejects_linked_optional_metadata_directory(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "linked-metadata")
    external = tmp_path / "external-agents"
    external.mkdir()
    (external / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")
    try:
        (skill / "agents").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this platform")

    result = validate_skill_package(skill)
    assert result["valid"] is False
    assert "skill_agents_metadata_invalid" in {
        item["code"] for item in result["errors"]
    }


def test_project_manifest_cannot_self_promote_to_global_policy(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _skill(root, "policy", global_policy=True)
    _adopt(root)

    assert resolve_workspace(root, "anything")["required_policy_skills"] == []
    with pytest.raises(ArchMarshalError, match="allow-global-policy"):
        review_workspace_skill(
            root,
            "skills/policy",
            decision="approve",
            expected_head=_head(root),
        )
    _approve(root, "skills/policy", allow_global_policy=True)
    required = resolve_workspace(root, "anything")["required_policy_skills"]
    assert required[0]["id"] == "skill.global.policy"


def test_unreviewed_skill_usage_cannot_become_learning_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _skill(root)
    _adopt(root)
    skill_id = collect_inventory(root).skills[0]["id"]
    _closeout(root, "Unreviewed run one", skill_id)
    _closeout(root, "Unreviewed run two", skill_id)

    learned = learn_from_projects([root])
    assert learned["common_skill_candidates"] == []
    assert learned["unreviewed_skill_usage_count"] == 2


def test_drifted_approved_skill_usage_cannot_become_learning_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    skill = _skill(root)
    _adopt(root)
    _approve(root, "skills/demo")
    skill_id = resolve_workspace(root, "demo")["suggested_skills"][0]["id"]
    (skill / "SKILL.md").write_text(
        (skill / "SKILL.md").read_text(encoding="utf-8") + "\nUnreviewed drift.\n",
        encoding="utf-8",
    )
    _closeout(root, "Drifted run one", skill_id)
    _closeout(root, "Drifted run two", skill_id)

    learned = learn_from_projects([root])
    assert learned["common_skill_candidates"] == []
    assert learned["ineligible_skill_usage_count"] == 2


def test_review_is_invalidated_by_exact_package_change(tmp_path: Path) -> None:
    root = tmp_path / "project"
    skill = _skill(root)
    _adopt(root)
    _approve(root, "skills/demo")
    assert resolve_workspace(root, "demo")["suggested_skills"]

    (skill / "SKILL.md").write_text(
        (skill / "SKILL.md").read_text(encoding="utf-8") + "\nChanged behavior.\n",
        encoding="utf-8",
    )
    assert resolve_workspace(root, "demo")["blocked_skills"][0]["reason"] == "source_changed"
    preview = plan_adoption(root)
    adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    assert (
        resolve_workspace(root, "demo")["blocked_skills"][0]["reason"]
        == "metadata_needs_review"
    )


def test_unowned_workspace_cannot_write_closeout_or_learning(tmp_path: Path) -> None:
    root = tmp_path / "unowned"
    root.mkdir()
    preview = record_closeout(root, level="quick", summary="Do not claim this project")
    with pytest.raises(ArchMarshalError, match="root-bound ownership"):
        record_closeout(
            root,
            level="quick",
            summary="Do not claim this project",
            expected_plan=preview["plan_digest"],
            apply=True,
        )
    with pytest.raises(ArchMarshalError, match="root-bound ownership"):
        learn_from_projects([root], apply=True)
    assert not (root / ".agent/history").exists()
    assert not (root / ".agent/inbox/learning").exists()


def test_learning_binds_usage_to_historical_package_not_current_source(tmp_path: Path) -> None:
    root = tmp_path / "project"
    skill = _skill(root)
    adopted = _adopt(root)
    _approve(root, "skills/demo")
    skill_id = adopted["discovered_skills"][0]["overlay_manifest"]
    # Read the stable id after review rather than deriving it from the path.
    skill_id = resolve_workspace(root, "demo")["suggested_skills"][0]["id"]
    old_hash = resolve_workspace(root, "demo")["suggested_skills"][0]
    for index in range(2):
        _closeout(root, f"Reviewed demo run {index}", skill_id)
    session = next((root / ".agent/history").rglob("session.yaml"))
    session_payload = yaml.safe_load(session.read_text(encoding="utf-8"))
    observed_hash = session_payload["skill_usage"][0]["package_sha256"]

    (skill / "SKILL.md").write_text(
        (skill / "SKILL.md").read_text(encoding="utf-8") + "\nUnobserved revision.\n",
        encoding="utf-8",
    )
    result = learn_from_projects([root])
    assert result["common_skill_candidates"][0]["implementation_sha256"] == observed_hash
    assert old_hash["id"] == skill_id


def test_learning_pack_is_commit_last_and_tamper_evident(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _skill(root)
    _adopt(root)
    _approve(root, "skills/demo")
    skill_id = resolve_workspace(root, "demo")["suggested_skills"][0]["id"]
    _closeout(root, "Demo run one", skill_id)
    _closeout(root, "Demo run two", skill_id)
    result = learn_from_projects([root], apply=True)
    pack = root / result["created"]

    verified = verify_learning_pack(pack)
    assert verified["profile"]["format"] == "archmarshal-learning-candidates-v2"
    assert str(root) not in (pack / "candidates.yaml").read_text(encoding="utf-8")
    (pack / "candidates.yaml").write_text("format: tampered\n", encoding="utf-8")
    with pytest.raises(ArchMarshalError, match="do not match"):
        verify_learning_pack(pack)


def test_workspace_lifetime_lock_blocks_overlapping_mutations(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with workspace_mutation_lock(root, operation="first"):
        with pytest.raises(ArchMarshalError, match="Another ArchMarshal mutation"):
            with workspace_mutation_lock(root, operation="second"):
                pass


def test_candidate_to_user_store_to_new_project_is_reversible_and_source_safe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-project"
    _skill(source)
    _adopt(source)
    _approve(source, "skills/demo")
    skill_id = resolve_workspace(source, "demo")["suggested_skills"][0]["id"]
    _closeout(source, "Demo run one", skill_id, tags=["release"])
    _closeout(source, "Demo run two", skill_id, tags=["release"])
    learned = learn_from_projects([source], apply=True)
    pack = source / learned["created"]
    skill_candidate = learned["common_skill_candidates"][0]["candidate_id"]
    preference_candidate = learned["preference_candidates"][0]["candidate_id"]

    store = tmp_path / "user-store"
    init = plan_user_store_initialization(store)
    apply_user_store_initialization(store, init, expected_plan=init["plan_digest"])

    decision_preview = review_learning_candidate(
        source,
        pack,
        preference_candidate,
        store,
        decision="defer",
        reason="Keep an auditable ancestor before promotion.",
    )
    decision_plan = decision_preview["user_store_plan"]
    decision = review_learning_candidate(
        source,
        pack,
        preference_candidate,
        store,
        decision="defer",
        reason="Keep an auditable ancestor before promotion.",
        expected_head_token=decision_preview["expected_head_token"],
        expected_plan=decision_preview["plan_digest"],
        reviewed_plan=decision_plan,
        apply=True,
    )
    ancestor = decision["user_store"]["head"]

    draft = _common_draft(tmp_path / "drafts")
    source_before = fingerprint_directory(source, purpose="source project safety check")
    draft_before = fingerprint_directory(draft, purpose="draft safety check")
    promotion_preview = promote_learning_candidate(
        source,
        pack,
        skill_candidate,
        store,
        draft=draft,
        reason="Reviewed for reusable demo tasks.",
    )
    with pytest.raises(ArchMarshalError) as reason_mismatch:
        promote_learning_candidate(
            source,
            pack,
            skill_candidate,
            store,
            draft=draft,
            reason="A different apply-time reason.",
            expected_head_token=promotion_preview["expected_head_token"],
            expected_plan=promotion_preview["plan_digest"],
            reviewed_plan=promotion_preview["user_store_plan"],
            apply=True,
        )
    assert reason_mismatch.value.code == "reviewed_plan_candidate_mismatch"
    assert user_store_status(store)["head"] == ancestor
    other_draft = _common_draft(tmp_path / "other-drafts", name="other-common")
    with pytest.raises(ArchMarshalError) as mismatch:
        promote_learning_candidate(
            source,
            pack,
            skill_candidate,
            store,
            draft=other_draft,
            reason="Reviewed for reusable demo tasks.",
            expected_head_token=promotion_preview["expected_head_token"],
            expected_plan=promotion_preview["plan_digest"],
            reviewed_plan=promotion_preview["user_store_plan"],
            apply=True,
        )
    assert mismatch.value.code == "reviewed_plan_draft_mismatch"
    assert user_store_status(store)["head"] == ancestor
    promoted = promote_learning_candidate(
        source,
        pack,
        skill_candidate,
        store,
        draft=draft,
        reason="Reviewed for reusable demo tasks.",
        expected_head_token=promotion_preview["expected_head_token"],
        expected_plan=promotion_preview["plan_digest"],
        reviewed_plan=promotion_preview["user_store_plan"],
        apply=True,
    )
    promoted_head = promoted["user_store"]["head"]

    assert fingerprint_directory(source, purpose="source project safety check") == source_before
    assert fingerprint_directory(draft, purpose="draft safety check") == draft_before
    new_project = tmp_path / "new-project"
    new_project.mkdir()
    resolved = resolve_workspace(new_project, "run demo", user_store=store)
    assert resolved["suggested_skills"][0]["origin"] == "user_store"
    assert resolved["suggested_skills"][0]["id"] == "skill.common-project.demo-common"

    rollback = plan_user_store_forward_rollback(
        store,
        ancestor,
        reason="Verify promotion can be deactivated without deletion.",
        expected_head=promoted_head,
    )
    rolled_back = apply_user_store_forward_rollback(
        store,
        rollback,
        expected_head=promoted_head,
        expected_plan=rollback["plan_digest"],
    )
    assert rolled_back["head"] not in {ancestor, promoted_head}
    assert resolve_workspace(new_project, "run demo", user_store=store)["suggested_skills"] == []
    assert (
        store / ".archmarshal" / "objects" / "sha256" / f"{promoted_head}.json"
    ).is_file()
    assert user_store_status(store)["generation_count"] == 3


def test_user_store_cli_apply_requires_complete_saved_preview(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "user-store"
    assert main(["user-store-init", str(store)]) == 0
    preview_text = capsys.readouterr().out
    preview = json.loads(preview_text)

    assert main(["user-store-init", str(store), "--apply"]) == 2
    assert not store.exists()
    capsys.readouterr()

    plan_file = tmp_path / "reviewed-init.json"
    plan_file.write_text(preview_text, encoding="utf-8")
    assert (
        main(
            [
                "user-store-init",
                str(store),
                "--plan-file",
                str(plan_file),
                "--expect-plan",
                preview["plan_digest"],
                "--apply",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["mode"] == "initialized"
