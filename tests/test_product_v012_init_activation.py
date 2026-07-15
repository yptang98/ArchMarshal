from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.skill_review import review_workspace_skill

SCAFFOLD_PATHS = {
    ".agents/skills/README.md",
    ".agents/skills/project/.gitkeep",
    ".agents/skills/generated/.gitkeep",
}


def _skill(root: Path, *, valid: bool = True) -> Path:
    directory = root / "skills" / "demo"
    directory.mkdir(parents=True)
    name = "demo" if valid else "different-folder-name"
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Use when a reviewed demo workflow is needed.\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )
    return directory


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _apply_adoption(root: Path) -> dict:
    preview = plan_adoption(root)
    return adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])


def _review(root: Path, source: str, decision: str) -> dict:
    head = (root / ".agent/skill-overlays/.archmarshal/HEAD").read_text(encoding="ascii").strip()
    preview = review_workspace_skill(
        root,
        source,
        decision=decision,
        expected_head=head,
    )
    return review_workspace_skill(
        root,
        source,
        decision=decision,
        expected_head=head,
        expected_plan=preview["plan_digest"],
        reviewed_plan=preview["review_plan"],
        apply=True,
    )


def test_cli_init_preview_and_exact_apply_create_project_skill_scaffold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "greenfield"
    root.mkdir()

    assert main(["init", str(root)]) == 0
    preview = json.loads(capsys.readouterr().out)
    operations = {item["path"]: item for item in preview["operations"]}
    assert preview["stage"] == "init"
    assert preview["project_initialization"] is True
    assert preview["mutation_scope"] == ("archmarshal_control_plane_and_project_skill_scaffold")
    assert SCAFFOLD_PATHS <= set(operations)
    assert all(operations[path]["overwrite"] is False for path in SCAFFOLD_PATHS)
    assert preview["project_skill_scaffold"]["planned_create"] == sorted(SCAFFOLD_PATHS)
    assert preview["next_actions"][0]["kind"] == "apply_project_initialization"
    assert preview["next_actions"][0]["command_args"][2] == str(root.resolve())

    assert (
        main(
            [
                "init",
                str(root),
                "--expect-plan",
                preview["plan_digest"],
                "--apply",
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["mode"] == "project_initialization_applied"
    assert SCAFFOLD_PATHS <= set(applied["created"])
    for relative in SCAFFOLD_PATHS:
        assert (root / relative).is_file()
    index = (root / ".agent/INDEX.md").read_text(encoding="utf-8")
    assert index.startswith(f"# {root.name} - ArchMarshal Index\n")
    assert "`.agents/skills/project/`" in index
    assert "`.agents/skills/generated/`" in index
    assert "路" not in index


def test_ordinary_adopt_does_not_create_project_skill_scaffold(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    preview = plan_adoption(root)

    assert preview["project_initialization"] is False
    assert not (SCAFFOLD_PATHS & {item["path"] for item in preview["operations"]})
    applied = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    assert applied["mode"] == "overlay_applied"
    assert not (root / ".agents").exists()


def test_init_supplements_managed_project_without_rewriting_existing_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    project_file = root / "README.md"
    project_file.write_text("human project\n", encoding="utf-8")
    _apply_adoption(root)
    scaffold_root = root / ".agents/skills"
    (scaffold_root / "project").mkdir(parents=True)
    existing_readme = scaffold_root / "README.md"
    existing_keep = scaffold_root / "project/.gitkeep"
    existing_readme.write_text("human Skill guide\n", encoding="utf-8")
    existing_keep.write_text("keep human bytes\n", encoding="utf-8")
    before = {path: _sha256(path) for path in (project_file, existing_readme, existing_keep)}

    preview = plan_adoption(root, project_initialization=True)
    assert preview["project_skill_scaffold"]["preserved_existing"] == [
        ".agents/skills/README.md",
        ".agents/skills/project/.gitkeep",
    ]
    assert preview["project_skill_scaffold"]["planned_create"] == [
        ".agents/skills/generated/.gitkeep"
    ]
    applied = adopt_workspace(
        root,
        project_initialization=True,
        apply=True,
        expected_plan=preview["plan_digest"],
    )

    assert applied["mode"] == "project_initialization_applied"
    assert (scaffold_root / "generated/.gitkeep").is_file()
    assert before == {
        path: _sha256(path) for path in (project_file, existing_readme, existing_keep)
    }


def test_init_stale_plan_and_ancestor_collision_write_nothing(tmp_path: Path) -> None:
    stale_root = tmp_path / "stale"
    stale_root.mkdir()
    preview = plan_adoption(stale_root, project_initialization=True)
    existing = stale_root / ".agents/skills/README.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("appeared after preview\n", encoding="utf-8")
    before = _sha256(existing)

    blocked = adopt_workspace(
        stale_root,
        project_initialization=True,
        apply=True,
        expected_plan=preview["plan_digest"],
    )
    assert blocked["mode"] == "blocked"
    assert "plan_digest_changed" in blocked["conflicts"]
    assert _sha256(existing) == before
    assert not (stale_root / ".agent").exists()
    assert not (stale_root / "AGENTS.md").exists()

    conflict_root = tmp_path / "ancestor-conflict"
    conflict_root.mkdir()
    (conflict_root / ".agents").write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(ArchMarshalError) as raised:
        plan_adoption(conflict_root, project_initialization=True)
    assert raised.value.code == "project_initialization_path_conflict"
    assert not (conflict_root / ".agent").exists()
    assert not (conflict_root / "AGENTS.md").exists()


def test_imported_skill_activation_is_stable_through_approve_and_reject(
    tmp_path: Path,
) -> None:
    root = tmp_path / "imported"
    root.mkdir()
    skill = _skill(root)
    source_hash = _sha256(skill / "SKILL.md")

    preview = plan_adoption(root)
    pending = preview["discovered_skills"][0]
    assert "status" not in pending
    assert pending["source_declared_status"] is None
    assert pending["normalized_source_status"] == "active"
    assert pending["review_state"] == "needs_review"
    assert pending["activation_state"] == "quarantined_needs_review"
    assert pending["review_required"] is True
    assert preview["skill_reviews_required"] == [
        {
            "id": pending["id"],
            "source": "skills/demo",
            "review_state": "needs_review",
            "activation_state": "quarantined_needs_review",
            "expected_head": preview["skill_index"]["proposed_head"],
            "available_after": "adoption_apply",
        }
    ]
    pending_action = next(
        item for item in preview["next_actions"] if item["kind"] == "review_skill"
    )
    assert pending_action["available"] is False
    assert pending_action["expected_head"] == preview["skill_index"]["proposed_head"]
    assert pending_action["preview_command_args"][2] == str(root.resolve())

    applied = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    applied_pending = applied["discovered_skills"][0]
    assert applied_pending["review_state"] == pending["review_state"]
    assert applied_pending["activation_state"] == pending["activation_state"]
    assert (
        applied["skill_reviews_required"][0]["expected_head"]
        == applied["skill_index_commit"]["head"]
    )
    assert (
        next(item for item in applied["next_actions"] if item["kind"] == "review_skill")[
            "available"
        ]
        is True
    )

    _review(root, "skills/demo", "approve")
    approved = plan_adoption(root)["discovered_skills"][0]
    assert approved["source_declared_status"] is None
    assert approved["normalized_source_status"] == "active"
    assert approved["review_state"] == "approved"
    assert approved["activation_state"] == "active_approved"
    assert approved["review_required"] is False
    assert plan_adoption(root)["skill_reviews_required"] == []

    _review(root, "skills/demo", "reject")
    rejected_plan = plan_adoption(root)
    rejected = rejected_plan["discovered_skills"][0]
    assert rejected["source_declared_status"] is None
    assert rejected["normalized_source_status"] == "active"
    assert rejected["review_state"] == "rejected"
    assert rejected["activation_state"] == "quarantined_rejected"
    assert rejected["review_required"] is False
    assert rejected_plan["skill_reviews_required"] == []
    assert _sha256(skill / "SKILL.md") == source_hash


def test_invalid_import_is_explicitly_disabled_not_reviewable(tmp_path: Path) -> None:
    root = tmp_path / "invalid"
    root.mkdir()
    _skill(root, valid=False)

    preview = plan_adoption(root)
    invalid = preview["discovered_skills"][0]
    assert invalid["source_declared_status"] is None
    assert invalid["normalized_source_status"] == "disabled"
    assert invalid["review_state"] == "needs_review"
    assert invalid["activation_state"] == "disabled_invalid"
    assert invalid["review_required"] is False
    assert preview["skill_reviews_required"] == []
    assert not any(item["kind"] == "review_skill" for item in preview["next_actions"])


def test_invalid_declared_status_is_reported_without_becoming_effective(
    tmp_path: Path,
) -> None:
    root = tmp_path / "invalid-status"
    root.mkdir()
    skill = _skill(root)
    (skill / "manifest.yaml").write_text("status: bogus\n", encoding="utf-8")

    preview = plan_adoption(root)
    discovered = preview["discovered_skills"][0]

    assert discovered["source_declared_status"] == "bogus"
    assert discovered["normalized_source_status"] == "disabled"
    assert discovered["activation_state"] == "disabled_invalid"
    assert discovered["review_required"] is False
