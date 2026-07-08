from __future__ import annotations

import json
import shutil
from pathlib import Path

from archmarshal.audit import audit_workspace
from archmarshal.cli import main
from archmarshal.closeout import closeout_workspace
from archmarshal.inventory import collect_inventory
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
    assert ".agent/reports" in release_result["explicit_only_paths"]


def test_closeout_reports_used_skills(tmp_path: Path) -> None:
    root = copy_example(tmp_path, "monorepo-project")

    result = closeout_workspace(root, ["skill.common-project.release-checklist", "skill.missing"])

    assert result["used_skills"][0]["id"] == "skill.common-project.release-checklist"
    assert result["missing_used_skills"] == ["skill.missing"]
    assert result["diagnostic_summary"]["error"] == 0


def test_cli_lint_exit_codes(tmp_path: Path, capsys) -> None:
    clean = copy_example(tmp_path, "simple-project")
    bare = tmp_path / "bare"
    bare.mkdir()

    assert main(["lint", str(clean)]) == 0
    assert main(["lint", str(bare)]) == 1
    output = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(output)
    assert payload["summary"]["error"] >= 1
