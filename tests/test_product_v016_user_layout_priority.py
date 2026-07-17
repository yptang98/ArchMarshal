from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

import archmarshal.layout_policy as layout_policy
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.cli import main
from archmarshal.inventory import collect_inventory
from archmarshal.layout_policy import build_layout_plan
from archmarshal.learning import learn_from_projects
from archmarshal.session import record_closeout


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _apply(root: Path, **kwargs: object) -> dict[str, object]:
    preview = plan_adoption(root, **kwargs)
    return adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
        **kwargs,
    )


def _record_quick_session(root: Path, summary: str) -> None:
    preview = record_closeout(root, level="quick", summary=summary)
    applied = record_closeout(
        root,
        level="quick",
        summary=summary,
        apply=True,
        expected_plan=preview["plan_digest"],
    )
    assert applied["mode"] == "append_only_applied"


def test_detected_layout_requires_exact_confirmation_and_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    reports = root / "reports"
    reports.mkdir(parents=True)
    human_file = reports / "human.md"
    human_file.write_text("human-owned\n", encoding="utf-8")
    before = _sha256(human_file)

    preview = plan_adoption(root)

    assert preview["layout"]["foundation"] == "detected"
    assert preview["layout"]["quality"] == "reasonable"
    assert preview["layout"]["decision"] == "preserve"
    assert preview["layout"]["source"] == "detected"
    assert preview["layout"]["requires_confirmation"] is True
    assert (
        preview["layout"]["effective_profile"]["save_paths"]["project_files"]["reports"]
        == "reports"
    )
    assert not root.joinpath(".agent").exists()

    review_required = adopt_workspace(root, apply=True)
    assert review_required["mode"] == "review_required"
    assert not root.joinpath(".agent").exists()

    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
    )
    assert applied["mode"] == "overlay_applied"
    assert _sha256(human_file) == before
    assert not root.joinpath(".agent/reports/.gitkeep").exists()
    workspace = yaml.safe_load((root / ".agent/workspace.yaml").read_text(encoding="utf-8"))
    assert workspace["layout"] == {
        "foundation": "detected",
        "source": "detected",
        "confirmed": False,
    }
    assert workspace["save_paths"]["project_files"]["reports"] == "reports"
    index = (root / ".agent/INDEX.md").read_text(encoding="utf-8")
    assert "Reports: `reports/`" in index


def test_no_foundation_initializes_clear_dynamic_skill_scaffold(tmp_path: Path) -> None:
    root = tmp_path / "new"
    root.mkdir()
    arguments = {
        "project_initialization": True,
        "save_paths": [
            "reports=project-notes/reports",
            "skills.project=.codex/skills/project",
            "skills.generated=.codex/skills/generated",
        ],
        "naming_timezone": "Asia/Shanghai",
        "date_partition": "YYYY/MM/DD",
    }

    preview = plan_adoption(root, **arguments)
    assert preview["layout"]["foundation"] == "confirmed"
    assert preview["layout"]["source"] == "cli"
    assert preview["layout"]["requires_confirmation"] is False
    scaffold = preview["project_skill_scaffold"]["paths"]
    assert scaffold == [
        ".codex/skills/README.md",
        ".codex/skills/generated/.gitkeep",
        ".codex/skills/project/.gitkeep",
    ]
    assert not {operation["action"] for operation in preview["operations"]} & {
        "move",
        "rename",
        "delete",
        "overwrite",
    }

    applied = _apply(root, **arguments)
    assert applied["mode"] == "project_initialization_applied"
    assert root.joinpath(".codex/skills/README.md").is_file()
    assert root.joinpath(".codex/skills/project/.gitkeep").is_file()
    assert root.joinpath(".codex/skills/generated/.gitkeep").is_file()
    index = root.joinpath(".agent/INDEX.md").read_text(encoding="utf-8")
    assert "`.codex/skills/project/`" in index
    assert "`project-notes/reports/`" in index
    assert "Asia/Shanghai" in index


def test_unsafe_layout_blocks_without_writing(tmp_path: Path) -> None:
    for index, assignment in enumerate(
        ("reports=../outside", "reports=.git/reports", "reports=node_modules/reports")
    ):
        root = tmp_path / f"unsafe-{index}"
        root.mkdir()
        preview = plan_adoption(root, save_paths=[assignment])
        assert preview["layout"]["quality"] == "unsafe"
        assert preview["blocked"] is True
        assert any(item.startswith("layout:") for item in preview["conflicts"])
        blocked = adopt_workspace(
            root,
            apply=True,
            expected_plan=preview["plan_digest"],
            save_paths=[assignment],
        )
        assert blocked["mode"] == "blocked"
        assert list(root.iterdir()) == []


def test_project_configuration_has_field_level_priority(tmp_path: Path) -> None:
    root = tmp_path / "configured"
    root.mkdir()
    _apply(
        root,
        save_paths=["reports=project-reports"],
        naming_timezone="Asia/Shanghai",
    )

    later = plan_adoption(
        root,
        save_paths=["reports=ignored-cli-reports", "plans=ignored-cli-plans"],
        naming_timezone="UTC",
    )
    effective = later["layout"]["effective_profile"]
    provenance = later["layout"]["field_provenance"]
    assert later["layout"]["source"] == "project_config"
    assert effective["save_paths"]["project_files"]["reports"] == "project-reports"
    assert effective["save_paths"]["project_files"]["plans"] == ".agent/plans"
    assert effective["naming"]["project_files"]["timezone"] == "Asia/Shanghai"
    assert provenance["save_paths.project_files.reports"] == "project_config"
    assert provenance["naming.project_files.timezone"] == "project_config"


def test_confirmed_user_profile_is_inherited_but_unconfirmed_is_ignored(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    root = tmp_path / "next-project"
    root.mkdir()

    def confirmed(_store: object) -> dict[str, object]:
        return {
            "root": str(tmp_path / "store"),
            "head": "a" * 64,
            "preference_values": {
                "preferred.workspace_layout": {
                    "confirmed": True,
                    "profile": {
                        "save_paths": {
                            "project_files": {"reports": "team/reports"},
                            "skills": {"project": ".codex/skills/project"},
                        },
                        "naming": {"project_files": {"timezone": "Asia/Shanghai"}},
                    },
                }
            },
        }

    monkeypatch.setattr(layout_policy, "read_user_store_active", confirmed)  # type: ignore[attr-defined]
    inherited = build_layout_plan(root, configured=False, user_store=tmp_path / "store")
    assert inherited["foundation"] == "confirmed"
    assert inherited["source"] == "confirmed_user_profile"
    assert (
        inherited["effective_profile"]["save_paths"]["project_files"]["reports"] == "team/reports"
    )
    assert inherited["field_provenance"]["save_paths.project_files.reports"] == (
        "confirmed_user_profile"
    )

    def unconfirmed(_store: object) -> dict[str, object]:
        state = confirmed(_store)
        value = state["preference_values"]["preferred.workspace_layout"]  # type: ignore[index]
        value["confirmed"] = False  # type: ignore[index]
        return state

    monkeypatch.setattr(layout_policy, "read_user_store_active", unconfirmed)  # type: ignore[attr-defined]
    ignored = build_layout_plan(root, configured=False, user_store=tmp_path / "store")
    assert ignored["foundation"] == "none"
    assert ignored["source"] == "archmarshal_default"


def test_layout_is_bound_into_plan_digest_and_cli_json(tmp_path: Path, capsys: object) -> None:
    root = tmp_path / "cli"
    root.mkdir()
    first = plan_adoption(root, save_paths=["reports=one"])
    second = plan_adoption(root, save_paths=["reports=two"])
    assert first["plan_digest"] != second["plan_digest"]

    stale = adopt_workspace(
        root,
        apply=True,
        expected_plan=first["plan_digest"],
        save_paths=["reports=two"],
    )
    assert stale["mode"] == "blocked"
    assert "plan_digest_changed" in stale["conflicts"]
    assert not root.joinpath(".agent").exists()

    exit_code = main(
        [
            "adopt",
            str(root),
            "--save-path",
            "reports=cli-reports",
            "--timezone",
            "Asia/Shanghai",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["layout"]["source"] == "cli"
    assert (
        payload["layout"]["effective_profile"]["save_paths"]["project_files"]["reports"]
        == "cli-reports"
    )
    assert payload["human_review"]["entrypoint"] == ".agent/INDEX.md"


def test_nested_excluded_skill_is_isolated_from_parent_package(tmp_path: Path) -> None:
    root = tmp_path / "nested"
    parent = root / "skills/parent"
    child = parent / "private"
    child.mkdir(parents=True)
    parent.joinpath("SKILL.md").write_text(
        "---\nname: parent\ndescription: Parent orchestration Skill.\n---\n",
        encoding="utf-8",
    )
    child.joinpath("SKILL.md").write_text(
        "---\nname: private\ndescription: User-private nested Skill.\n---\n",
        encoding="utf-8",
    )
    secret = child / "secret.txt"
    secret.write_text("one\n", encoding="utf-8")

    arguments = {"exclude_skills": ["skills/parent/private"]}
    preview = plan_adoption(root, **arguments)
    assert preview["skill_discovery"]["prepared_management_packages"] == ["skills/parent"]
    assert preview["skill_discovery"]["excluded_packages"][0]["contents_inspected"] is False
    assert not any(
        item["path"].startswith("skills/parent/private/") for item in preview["backup_file_preview"]
    )
    _apply(root, **arguments)

    secret.write_text("two\n", encoding="utf-8")
    later = plan_adoption(root)
    assert later["skill_index"]["changed"] is False
    parent_inventory = next(
        skill for skill in collect_inventory(root).skills if skill["_skill_dir"] == "skills/parent"
    )
    assert parent_inventory["_source_drift"] == "unchanged"


def test_only_repeated_confirmed_layouts_become_review_candidates(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    detected = tmp_path / "detected"
    for root in (first, second, detected):
        root.mkdir()
    arguments = {
        "save_paths": ["reports=team/reports"],
        "naming_timezone": "Asia/Shanghai",
    }
    _apply(first, **arguments)
    _apply(second, **arguments)
    detected.joinpath("reports").mkdir()
    _apply(detected)
    _record_quick_session(first, "first confirmed layout")
    _record_quick_session(second, "second confirmed layout")
    _record_quick_session(detected, "detected layout only")

    one_project = learn_from_projects([first])
    assert not any(
        item["key"] == "preferred.workspace_layout" for item in one_project["preference_candidates"]
    )

    repeated = learn_from_projects([first, second, detected])
    layout_candidates = [
        item
        for item in repeated["preference_candidates"]
        if item["key"] == "preferred.workspace_layout"
    ]
    assert len(layout_candidates) == 1
    candidate = layout_candidates[0]
    assert candidate["observed_projects"] == 2
    assert candidate["promotion_policy"] == "human_review_required"
    assert candidate["value"]["confirmed"] is True
    assert candidate["value"]["profile"]["save_paths"]["project_files"]["reports"] == "team/reports"
