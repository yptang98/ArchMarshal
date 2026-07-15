from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

import archmarshal.adoption as adoption_module
from archmarshal import __version__
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.audit import audit_workspace
from archmarshal.catalog import catalog_projects
from archmarshal.checkpoint import checkpoint_workspace
from archmarshal.cli import end_main, main, start_main
from archmarshal.closeout import closeout_workspace
from archmarshal.inventory import collect_inventory
from archmarshal.learning import learn_from_projects
from archmarshal.lifecycle import end_workspace, start_workspace
from archmarshal.lint import lint_workspace
from archmarshal.planner import plan_workspace
from archmarshal.resolver import resolve_workspace
from archmarshal.safety import sha256_file
from archmarshal.session import record_closeout
from archmarshal.skill_review import review_workspace_skill

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def _apply_learning(roots: list[Path]) -> dict[str, object]:
    preview = learn_from_projects(roots)
    return learn_from_projects(
        roots,
        reviewed_plan=preview["learning_plan"],
        expected_plan=preview["plan_digest"],
        apply=True,
    )


def copy_example(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(REPO_ROOT / "examples" / name, target)
    return target


def rules(root: Path) -> set[str]:
    return {diagnostic.rule for diagnostic in lint_workspace(root)}


def test_examples_are_clean_and_show_skill_categories(tmp_path: Path) -> None:
    simple = copy_example(tmp_path, "simple-project")
    monorepo = copy_example(tmp_path, "monorepo-project")

    simple_inventory = collect_inventory(simple)
    monorepo_inventory = collect_inventory(monorepo)

    assert lint_workspace(simple) == []
    assert lint_workspace(monorepo) == []
    assert {skill["kind"] for skill in simple_inventory.skills} == {
        "global_skill",
        "functional_skill",
    }
    assert {skill["kind"] for skill in monorepo_inventory.skills} == {
        "common_project_skill",
    }
    assert simple_inventory.save_paths["project_files"]["checkpoints"] == ".agent/inbox/checkpoints"
    assert simple_inventory.save_paths["skills"]["generated"] == ".agents/skills/generated"
    assert simple_inventory.naming["project_files"]["strategy"] == "time_topic_kind"
    assert len(simple_inventory.memory_stores) == 1
    assert len(simple_inventory.memory_records) == 1


def test_missing_project_entry_files_are_errors(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()

    diagnostics = lint_workspace(root)
    by_rule = {diagnostic.rule: diagnostic for diagnostic in diagnostics}

    assert by_rule["project.missing_workspace_yaml"].severity == "error"
    assert by_rule["project.missing_agent_index"].severity == "error"


def test_workspace_manifest_metadata_is_required(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    workspace = root / ".agent" / "workspace.yaml"
    workspace.write_text(
        """
workspace:
  name: ""
paths:
  project_root: .
  agent_root: .agent
""".strip(),
        encoding="utf-8",
    )

    result = rules(root)
    assert "project.workspace_missing_metadata" in result


def test_invalid_workspace_yaml_reports_structured_diagnostic(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    workspace = root / ".agent" / "workspace.yaml"
    workspace.write_text("workspace:\n  name: broken\n paths:\n", encoding="utf-8")

    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)

    assert inventory.workspace["_load_error"]
    assert {diagnostic.rule for diagnostic in diagnostics} >= {"project.workspace_yaml_invalid"}


def test_workspace_schema_errors_are_reported(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    workspace = root / ".agent" / "workspace.yaml"
    workspace.write_text(
        workspace.read_text(encoding="utf-8").replace("version: 0.1.0", "version: abc"),
        encoding="utf-8",
    )

    diagnostics = lint_workspace(root)
    by_rule = [diagnostic for diagnostic in diagnostics if diagnostic.rule == "project.workspace_schema_invalid"]

    assert by_rule
    assert by_rule[0].path is not None
    assert "#$.workspace.version" in by_rule[0].path
    assert by_rule[0].suggestion


def test_workspace_paths_warn_when_they_escape_root(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    workspace = root / ".agent" / "workspace.yaml"
    workspace.write_text(
        workspace.read_text(encoding="utf-8")
        + """

  external_memory:
    - ..
""",
        encoding="utf-8",
    )

    assert "project.workspace_path_outside_root" in rules(root)


def test_workspace_warns_when_project_file_save_paths_are_missing(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    workspace = root / ".agent" / "workspace.yaml"
    workspace.write_text(
        """
workspace:
  name: simple-project
  version: 0.1.0
paths:
  project_root: .
  agent_root: .agent
""".strip(),
        encoding="utf-8",
    )

    diagnostics = lint_workspace(root)
    by_rule = {diagnostic.rule: diagnostic for diagnostic in diagnostics}

    assert by_rule["project.save_paths_missing"].severity == "warning"


def test_project_file_save_paths_must_be_valid(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    workspace = root / ".agent" / "workspace.yaml"
    workspace.write_text(
        workspace.read_text(encoding="utf-8").replace(
            "checkpoints: .agent/inbox/checkpoints",
            "checkpoints: ''",
        ),
        encoding="utf-8",
    )

    assert "project.project_file_save_path_invalid" in rules(root)


def test_workspace_warns_when_project_file_naming_is_missing(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    workspace = root / ".agent" / "workspace.yaml"
    workspace.write_text(
        """
workspace:
  name: simple-project
  version: 0.1.0
save_paths:
  project_files:
    checkpoints: .agent/inbox/checkpoints
    reports: .agent/reports
    plans: .agent/plans
    history: .agent/history
    knowledge: .agent/knowledge
paths:
  project_root: .
  agent_root: .agent
""".strip(),
        encoding="utf-8",
    )

    assert "project.project_file_naming_missing" in rules(root)


def test_detects_overlapping_skill_trigger(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    skill_dir = root / ".agents" / "skills" / "functional" / "doc-summary-copy"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# Documentation Summary Copy\n\nUse this skill for documentation summaries.\n",
        encoding="utf-8",
    )
    (skill_dir / "manifest.yaml").write_text(
        """
id: skill.functional.doc-summary-copy
name: doc-summary-copy
kind: functional_skill
version: 0.1.0
status: active
priority: normal
scope: functional
summary: Duplicate trigger fixture.
tags:
  - documentation
triggers:
  - summarize documentation
negative_triggers:
  - release checklist
""".strip(),
        encoding="utf-8",
    )

    assert "skill.overlapping_trigger" in rules(root)


def test_invalid_registry_yaml_reports_structured_diagnostic(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    registry = root / ".agent" / "registry.yaml"
    registry.write_text("artifacts:\n  - id: broken\n    tags: [unterminated\n", encoding="utf-8")

    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)

    assert inventory.artifacts[0]["_load_error"]
    assert "project.registry_yaml_invalid" in {diagnostic.rule for diagnostic in diagnostics}


def test_registry_schema_enum_and_required_errors_are_reported(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    registry = root / ".agent" / "registry.yaml"
    registry.write_text(
        """
artifacts:
  - id: project.bad
    kind: report
    path: .agent/reports/bad.md
    status: raw
    read_policy: always
    tags:
      - audit
""".strip(),
        encoding="utf-8",
    )

    diagnostics = lint_workspace(root)
    schema_diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.rule == "project.registry_schema_invalid"
    ]

    assert len(schema_diagnostics) >= 3
    assert any("#$.artifacts[0].read_policy" in str(item.path) for item in schema_diagnostics)
    assert any("required" in item.suggestion.lower() for item in schema_diagnostics if item.suggestion)


def test_report_default_read_policy_is_error(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    registry = root / ".agent" / "registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8")
        + """

  - id: report.bad
    kind: report
    path: .agent/reports/bad.md
    status: raw
    read_policy: default
    update_policy: generated
    source_of_truth: false
    tags:
      - audit
""",
        encoding="utf-8",
    )

    assert "project.report_read_policy_not_explicit" in rules(root)


def test_detects_unregistered_memory_locations(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    rules_dir = root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "project.md").write_text("Always use the hidden local rule.\n", encoding="utf-8")

    assert "memory.store_unregistered" in rules(root)


def test_memory_records_require_evidence_before_activation(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    records = root / ".agent" / "memory-records.yaml"
    records.write_text(
        records.read_text(encoding="utf-8").replace(
            "confidence: reviewed\n    review_status: reviewed",
            "confidence: generated\n    review_status: pending_human",
        ),
        encoding="utf-8",
    )

    result = rules(root)
    assert "memory.generated_unreviewed" in result


def test_memory_record_unknown_store_is_error(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    records = root / ".agent" / "memory-records.yaml"
    records.write_text(
        records.read_text(encoding="utf-8").replace(
            "store_id: memory.project.context",
            "store_id: memory.unknown.context",
        ),
        encoding="utf-8",
    )

    assert "memory.record_unknown_store" in rules(root)


def test_common_project_skill_reproducibility_paths_are_verified(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")
    skill_dir = root / ".agents" / "skills" / "common-project" / "release-checklist"
    shutil.rmtree(skill_dir / "templates")

    assert "skill.local_path_missing" in rules(root)


def test_skill_paths_cannot_escape_skill_root(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")
    manifest = (
        root
        / ".agents"
        / "skills"
        / "common-project"
        / "release-checklist"
        / "manifest.yaml"
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("templates: templates", "templates: ../outside"),
        encoding="utf-8",
    )

    assert "skill.path_outside_skill_root" in rules(root)


def test_skill_dependency_files_stay_inside_skill_root(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")
    manifest = (
        root
        / ".agents"
        / "skills"
        / "common-project"
        / "release-checklist"
        / "manifest.yaml"
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("files: []", "files:\n    - ../secret.txt"),
        encoding="utf-8",
    )

    assert "skill.dependency_file_outside_skill_root" in rules(root)


def test_missing_command_dependency_is_warning(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")
    manifest = (
        root
        / ".agents"
        / "skills"
        / "common-project"
        / "release-checklist"
        / "manifest.yaml"
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("    - git", "    - archmarshal-definitely-missing-command"),
        encoding="utf-8",
    )

    diagnostics = lint_workspace(root)
    by_rule = {diagnostic.rule: diagnostic for diagnostic in diagnostics}
    assert by_rule["skill.command_dependency_missing"].severity == "warning"


def test_skill_memory_writes_require_memory_effects(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    manifest = root / ".agents" / "skills" / "functional" / "doc-summary" / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "    - project.knowledge",
            "    - memory.project.context",
        ),
        encoding="utf-8",
    )

    assert "skill.memory_side_effect_undeclared" in rules(root)


def test_invalid_skill_manifest_yaml_does_not_block_other_skills(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    manifest = root / ".agents" / "skills" / "functional" / "doc-summary" / "manifest.yaml"
    manifest.write_text("id: skill.functional.doc-summary\ntriggers: [broken\n", encoding="utf-8")

    inventory = collect_inventory(root)
    diagnostics = lint_workspace(root)

    assert "skill.global.lightweight-policy" in {skill.get("id") for skill in inventory.skills}
    assert "skill.invalid_manifest_yaml" in {diagnostic.rule for diagnostic in diagnostics}


def test_skill_manifest_schema_errors_are_reported(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    manifest = root / ".agents" / "skills" / "functional" / "doc-summary" / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("kind: functional_skill", "kind: weird_skill"),
        encoding="utf-8",
    )

    diagnostics = lint_workspace(root)
    schema_diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.rule == "skill.manifest_schema_invalid"
    ]

    assert schema_diagnostics
    assert "#$.kind" in str(schema_diagnostics[0].path)


def test_invalid_context_module_yaml_reports_structured_diagnostic(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    module = root / ".agent" / "context-modules" / "architecture" / "module.yaml"
    module.write_text("id: context.architecture\nsource_files: [broken\n", encoding="utf-8")

    diagnostics = lint_workspace(root)

    assert "project.context_module_invalid_yaml" in {diagnostic.rule for diagnostic in diagnostics}


def test_audit_and_plan_are_read_only_views(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")

    audit = audit_workspace(root)
    plan = plan_workspace(root)

    assert audit["summary"]["errors"] == 0
    assert plan["destructive"] is False
    assert plan["apply_supported"] is False


def test_resolve_suggests_task_relevant_modules(tmp_path: Path) -> None:
    simple = copy_example(tmp_path, "simple-project")
    monorepo = copy_example(tmp_path, "monorepo-project")

    doc_result = resolve_workspace(simple, "summarize documentation for durable knowledge")
    release_result = resolve_workspace(monorepo, "prepare release checklist")

    assert doc_result["suggested_skills"][0]["id"] == "skill.functional.doc-summary"
    assert release_result["suggested_skills"][0]["id"] == "skill.common-project.release-checklist"
    assert release_result["suggested_context_modules"][0]["id"] == "context.release"
    assert release_result["suggested_memory_records"][0]["id"] == "mem.monorepo.release"
    assert ".agent/reports" in release_result["explicit_only_paths"]


def test_closeout_reports_used_skills(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")

    result = closeout_workspace(root, ["skill.common-project.release-checklist", "skill.missing"])

    assert result["used_skills"][0]["id"] == "skill.common-project.release-checklist"
    assert result["missing_used_skills"] == ["skill.missing"]
    assert result["diagnostic_summary"]["error"] == 0
    assert result["original_preservation_policy"]["preserve_originals"] is True
    assert result["original_preservation_policy"]["delete_after_summary"] is False
    assert result["session_summary"]["used_skill_count"] == 1
    assert result["reproduction_checklist"]
    assert result["recording_policy"]["level"] == "deep"


def test_closeout_is_light_when_project_reuses_registered_skill(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")

    result = closeout_workspace(root, ["skill.common-project.release-checklist"])

    assert result["recording_policy"]["level"] == "light"
    assert "important_changes" in result["recording_policy"]["record"]
    assert "long_narrative_summary" in result["recording_policy"]["skip_by_default"]


def test_closeout_proposes_memory_candidates_from_reports(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    reports = root / ".agent" / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "learning.md").write_text("# Learning\n\nUseful durable lesson.\n", encoding="utf-8")
    registry = root / ".agent" / "registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8")
        + """

  - id: report.learning
    kind: report
    path: .agent/reports/learning.md
    status: raw
    read_policy: explicit_only
    update_policy: generated
    source_of_truth: false
    tags:
      - learning
""",
        encoding="utf-8",
    )

    result = closeout_workspace(root, [])
    assert result["candidate_memory_updates"][0]["source_artifact"] == "report.learning"
    assert result["candidate_memory_updates"][0]["preserve_original"] is True
    assert result["preservation_manifest"]["original_history_artifacts"][0]["id"] == "report.learning"
    assert result["recording_policy"]["level"] == "deep"
    assert "candidate_memory_updates" in result["recording_policy"]["novelty_signals"]


def test_checkpoint_preserves_compaction_summary_without_mutating_project(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    before_files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())

    result = checkpoint_workspace(
        root,
        summary="Implemented lightweight onboarding and kept raw history intact.",
        task="new project setup",
        decisions=["Use append-only checkpoints after context compression."],
        files=[".agent/registry.yaml", "src/archmarshal/checkpoint.py"],
        next_steps=["Review candidate memory before promotion."],
        used_skills=["skill.functional.doc-summary"],
        risks=["Do not delete raw reports after distillation."],
    )
    after_files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())

    assert before_files == after_files
    assert result["mode"] == "propose_only"
    assert result["recording_policy"]["mode"] == "auto"
    assert result["recording_policy"]["level"] == "light"
    assert result["save_path"]["source"] == "workspace"
    assert result["save_path"]["path"] == ".agent/inbox/checkpoints"
    assert result["checkpoint"]["filename"].endswith("-new-project-setup-checkpoint.md")
    assert result["checkpoint"]["filename"][:8].isdigit()
    assert result["original_preservation_policy"]["delete_after_summary"] is False
    assert result["registry_update_suggestions"][0]["update_policy"] == "append_only"
    assert result["registry_update_suggestions"][0]["read_policy"] == "explicit_only"
    assert result["registry_update_suggestions"][0]["preserve_original"] is True
    assert result["suggested_memory_record"]["status"] == "candidate"
    assert result["suggested_memory_record"]["review_status"] == "pending_human"


def test_start_workspace_returns_codex_contract(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")

    result = start_workspace(root)

    assert result["stage"] == "start"
    assert result["mode"] == "read_only"
    assert result["project_ready"] is True
    assert result["save_paths"]["project_files"]["checkpoints"] == ".agent/inbox/checkpoints"
    assert any("checkpoint" in item for item in result["codex_contract"])


def test_start_workspace_previews_safe_adoption_for_unmanaged_project(tmp_path: Path) -> None:
    root = tmp_path / "unmanaged"
    root.mkdir()

    result = start_workspace(root)

    assert result["project_ready"] is False
    assert result["adoption_preview"]["mode"] == "propose_only"
    assert not (root / ".agent").exists()


def test_start_workspace_preserves_existing_tags_and_blocks_relevant_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    _apply_adoption(root, tags=["vision", "python"])

    started = start_workspace(root)

    assert started["skill_sync_preview"]["project_tags"] == ["python", "vision"]
    assert started["adoption_preview"] is None

    skill_dir = root / "skills" / "beta"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Handle beta releases.\n---\n",
        encoding="utf-8",
    )
    built = adoption_module._build_adoption(root, [], "managed")
    beta = next(skill for skill in built["skills"] if skill["manifest"]["name"] == "beta")
    overlay = root / beta["overlay_manifest"]
    overlay.parent.mkdir(parents=True)
    overlay.write_text(built["writes"][overlay], encoding="utf-8")

    blocked = start_workspace(root, task="please handle beta releases")

    assert blocked["project_ready"] is False
    assert blocked["task_ready"] is False
    assert any(
        item["reason"] == "index_untracked" and item["task_relevant"]
        for item in blocked["resolution"]["blocked_skills"]
    )


def test_end_workspace_wraps_closeout(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")

    result = end_workspace(root, ["skill.common-project.release-checklist"])

    assert result["stage"] == "end"
    assert result["mode"] == "read_only"
    assert result["used_skills"][0]["id"] == "skill.common-project.release-checklist"
    assert result["original_preservation_policy"]["preserve_originals"] is True
    assert result["recording_policy"]["mode"] == "auto"
    assert result["recording_policy"]["level"] == "light"


def test_cli_start_and_end_entrypoints(tmp_path: Path, capsys) -> None:
    root = copy_example(tmp_path, "simple-project")

    assert start_main([str(root)]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["stage"] == "start"

    assert end_main([str(root)]) == 0
    end_payload = json.loads(capsys.readouterr().out)
    assert end_payload["stage"] == "end"


def test_all_cli_entrypoints_report_the_package_version(capsys) -> None:
    for entrypoint, program in (
        (main, "archmarshal"),
        (start_main, "archmarshal-start"),
        (end_main, "archmarshal-end"),
    ):
        with pytest.raises(SystemExit) as raised:
            entrypoint(["--version"])
        assert raised.value.code == 0
        assert capsys.readouterr().out.strip() == f"{program} {__version__}"


def test_cli_checkpoint_outputs_preservation_policy(tmp_path: Path, capsys) -> None:
    root = copy_example(tmp_path, "simple-project")

    assert main(
        [
            "checkpoint",
            str(root),
            "--summary",
            "Compressed context after architecture work.",
            "--task",
            "architecture checkpoint",
            "--decision",
            "Keep source notes intact.",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["stage"] == "context_checkpoint"
    assert payload["original_preservation_policy"]["preserve_originals"] is True
    assert payload["registry_update_suggestions"][0]["preserve_original"] is True


def test_cli_checkpoint_save_path_override_is_recorded(tmp_path: Path, capsys) -> None:
    root = copy_example(tmp_path, "simple-project")

    assert main(
        [
            "checkpoint",
            str(root),
            "--summary",
            "Compressed context after release work.",
            "--save-path",
            ".agent/history/release-checkpoints",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["save_path"]["source"] == "cli"
    assert payload["save_path"]["path"] == ".agent/history/release-checkpoints"
    assert payload["checkpoint"]["suggested_path"].startswith(".agent/history/release-checkpoints/")


def test_cli_inventory_serializes_yaml_dates(tmp_path: Path, capsys) -> None:
    root = copy_example(tmp_path, "simple-project")

    assert main(["inventory", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["memory_records"][0]["last_verified"] == "2026-07-09"


def test_cli_lint_exit_codes(tmp_path: Path, capsys) -> None:
    clean = copy_example(tmp_path, "simple-project")
    bare = tmp_path / "bare"
    bare.mkdir()

    assert main(["lint", str(clean)]) == 0
    assert main(["lint", str(bare)]) == 1
    output = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(output)
    assert payload["summary"]["error"] >= 1


def test_adoption_preview_is_read_only_and_apply_uses_skill_overlays(tmp_path: Path) -> None:
    root = tmp_path / "legacy-project"
    skill_dir = root / ".codex" / "skills" / "release-helper"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: release-helper\ndescription: Prepare a safe release.\n---\n\n# Release helper\n",
        encoding="utf-8",
    )
    source_hash = sha256_file(skill_md)
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())

    preview = plan_adoption(root, tags=["release", "python"])

    after_preview = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    assert before == after_preview
    assert preview["mode"] == "propose_only"
    assert preview["discovered_skills"][0]["source_will_change"] is False

    applied = _apply_adoption(root, tags=["release", "python"])

    assert applied["mode"] == "overlay_applied"
    assert applied["backup"]["verified"] is True
    assert sha256_file(skill_md) == source_hash
    assert not (skill_dir / "manifest.yaml").exists()
    overlay = root / applied["discovered_skills"][0]["overlay_manifest"]
    assert overlay.exists()
    inventory = collect_inventory(root)
    assert inventory.skills[0]["_skill_dir"] == ".codex/skills/release-helper"
    assert inventory.workspace["management_mode"] == "overlay"
    resolved = resolve_workspace(root, "release helper")
    assert resolved["suggested_skills"] == []
    assert resolved["blocked_skills"][0]["reason"] == "metadata_needs_review"
    assert inventory.skills[0]["source"]["managed"] is False
    assert "skill.review_required" in {item.rule for item in lint_workspace(root)}


def test_adoption_never_overwrites_conflicting_control_files(tmp_path: Path) -> None:
    root = tmp_path / "conflicted-project"
    workspace = root / ".agent" / "workspace.yaml"
    workspace.parent.mkdir(parents=True)
    workspace.write_text("owned_by: another-tool\n", encoding="utf-8")
    original = workspace.read_text(encoding="utf-8")

    result = _apply_adoption(root)

    assert result["mode"] == "blocked"
    assert ".agent/workspace.yaml" in result["conflicts"]
    assert workspace.read_text(encoding="utf-8") == original
    assert not (root / ".agent" / "INDEX.md").exists()


def test_skill_overlay_cannot_escape_project_root(tmp_path: Path) -> None:
    root = tmp_path / "overlay-project"
    skill_dir = root / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    result = _apply_adoption(root)
    overlay = root / result["discovered_skills"][0]["overlay_manifest"]
    payload = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    payload["source"]["skill_dir"] = "../../outside"
    overlay.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    assert "skill.overlay_source_outside_root" in rules(root)


def test_reproducible_closeout_is_append_only_and_snapshots_scripts(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    _apply_adoption(root)
    script = root / "run_demo.py"
    script.write_text("print('demo')\n", encoding="utf-8")
    source_hash = sha256_file(script)

    preview = record_closeout(
        root,
        level="reproducible",
        summary="Validated the release flow.",
        steps=["Run the validation script.", "Review the output."],
        scripts=["run_demo.py"],
        commands=["python run_demo.py"],
        tags=["release"],
        used_skills=["skill.functional.doc-summary"],
    )

    assert preview["mode"] == "propose_only"
    assert preview["reproducibility_ready"] is True
    assert not (root / preview["session_dir"]).exists()

    applied = _apply_closeout(
        root,
        level="reproducible",
        summary="Validated the release flow.",
        steps=["Run the validation script.", "Review the output."],
        scripts=["run_demo.py"],
        commands=["python run_demo.py"],
        tags=["release"],
        used_skills=["skill.functional.doc-summary"],
    )

    session_dir = root / applied["session_dir"]
    assert applied["mode"] == "append_only_applied"
    assert (session_dir / "SUMMARY.md").exists()
    assert (session_dir / "STEPS.md").exists()
    assert (session_dir / "reproduction.yaml").exists()
    run_script = "run.ps1" if os.name == "nt" else "run.sh"
    assert (session_dir / run_script).exists()
    snapshots = list((session_dir / "scripts").iterdir())
    assert len(snapshots) == 1
    assert sha256_file(snapshots[0]) == source_hash
    assert sha256_file(script) == source_hash


def test_reproducible_closeout_reports_missing_evidence(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")

    result = record_closeout(root, level="reproducible")

    assert result["reproducibility_ready"] is False
    assert len(result["reproducibility_gaps"]) == 4


def test_reproducible_closeout_blocks_inline_secrets(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    script = root / "run_demo.py"
    script.write_text("print('demo')\n", encoding="utf-8")

    result = record_closeout(
        root,
        level="reproducible",
        apply=True,
        summary="Sensitive command test.",
        steps=["Run demo."],
        scripts=["run_demo.py"],
        commands=["python run_demo.py --api-key sk-this-should-never-be-recorded"],
    )

    assert result["mode"] == "blocked"
    assert "inline secret" in result["script_errors"][0]
    assert not (root / result["session_dir"]).exists()


def test_learning_creates_review_only_candidates_from_repeated_sessions(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "simple-project")
    _apply_adoption(root)
    for index in range(2):
        _apply_closeout(
            root,
            level="standard",
            summary=f"Documentation pass {index}",
            steps=["Summarize documentation."],
            scripts=["src/README.md"],
            tags=["documentation"],
            used_skills=["skill.functional.doc-summary"],
        )

    result = _apply_learning([root])

    assert result["mode"] == "candidate_pack_created"
    assert result["common_skill_candidates"][0]["skill_id"] == "skill.functional.doc-summary"
    assert result["common_skill_candidates"][0]["candidate_id"].startswith("candidate.skill.")
    assert result["common_skill_candidates"][0]["promotion_policy"] == "human_review_required"
    assert result["preference_candidates"][0]["value"] == "documentation"
    candidate_pack = root / result["created"] / "candidates.yaml"
    payload = yaml.safe_load(candidate_pack.read_text(encoding="utf-8"))
    assert payload["limits"]["automatic_global_skill_mutation"] is False
    assert payload["limits"]["raw_history_included"] is False


def test_current_exact_skill_exclusion_blocks_historical_learning_candidate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    skill = root / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Reusable reviewed demo workflow.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    adoption = plan_adoption(root)
    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=adoption["plan_digest"],
    )
    skill_id = adoption["discovered_skills"][0]["id"]
    head = applied["skill_index_commit"]["head"]
    review = review_workspace_skill(
        root,
        "skills/demo",
        decision="approve",
        reviewer="reviewer",
        reason="learning fixture",
        expected_head=head,
    )
    review_workspace_skill(
        root,
        "skills/demo",
        decision="approve",
        reviewer="reviewer",
        reason="learning fixture",
        expected_head=head,
        expected_plan=review["plan_digest"],
        reviewed_plan=review["review_plan"],
        apply=True,
    )
    for index in range(2):
        _apply_closeout(
            root,
            level="standard",
            summary=f"Documentation pass {index}",
            steps=["Summarize documentation."],
            tags=["documentation"],
            used_skills=[skill_id],
        )
    source = "skills/demo"
    exclusion = plan_adoption(root, exclude_skills=[source])
    adopt_workspace(
        root,
        apply=True,
        expected_plan=exclusion["plan_digest"],
        exclude_skills=[source],
    )

    result = learn_from_projects([root])

    assert result["excluded_skill_usage_count"] == 2
    assert result["common_skill_candidates"] == []


def test_catalog_sorts_and_filters_projects_by_date_and_tags(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _apply_adoption(first, tags=["vision", "python"])
    _apply_adoption(second, tags=["nlp", "python"])
    first_workspace = first / ".agent" / "workspace.yaml"
    second_workspace = second / ".agent" / "workspace.yaml"
    first_payload = yaml.safe_load(first_workspace.read_text(encoding="utf-8"))
    second_payload = yaml.safe_load(second_workspace.read_text(encoding="utf-8"))
    first_payload["workspace"]["created_on"] = "2026-01-01"
    second_payload["workspace"]["created_on"] = "2026-02-01"
    first_workspace.write_text(yaml.safe_dump(first_payload, sort_keys=False), encoding="utf-8")
    second_workspace.write_text(yaml.safe_dump(second_payload, sort_keys=False), encoding="utf-8")

    result = catalog_projects([first, second], tags=["python"])
    filtered = catalog_projects([first, second], tags=["vision"])

    assert [item["name"] for item in result["projects"]] == ["second", "first"]
    assert [item["name"] for item in filtered["projects"]] == ["first"]
