from __future__ import annotations

import json
import shutil
from pathlib import Path

from archmarshal.audit import audit_workspace
from archmarshal.checkpoint import checkpoint_workspace
from archmarshal.cli import end_main, main, start_main
from archmarshal.closeout import closeout_workspace
from archmarshal.inventory import collect_inventory
from archmarshal.lifecycle import end_workspace, start_workspace
from archmarshal.lint import lint_workspace
from archmarshal.planner import plan_workspace
from archmarshal.resolver import resolve_workspace


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_end_workspace_wraps_closeout(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")

    result = end_workspace(root, ["skill.common-project.release-checklist"])

    assert result["stage"] == "end"
    assert result["mode"] == "read_only"
    assert result["used_skills"][0]["id"] == "skill.common-project.release-checklist"
    assert result["original_preservation_policy"]["preserve_originals"] is True


def test_cli_start_and_end_entrypoints(tmp_path: Path, capsys) -> None:
    root = copy_example(tmp_path, "simple-project")

    assert start_main([str(root)]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["stage"] == "start"

    assert end_main([str(root)]) == 0
    end_payload = json.loads(capsys.readouterr().out)
    assert end_payload["stage"] == "end"


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
