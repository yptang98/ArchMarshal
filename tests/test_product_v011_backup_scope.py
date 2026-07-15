from __future__ import annotations

from pathlib import Path, PurePosixPath

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.safety import verify_backup

EXCLUDED_RUNTIME_ROOTS = {
    "backups",
    "cache",
    "history",
    "inbox",
    "transactions",
}


def _skill(root: Path) -> Path:
    skill = root / "skills" / "demo"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: Use the bundled scripts for repeatable demo work.\n"
        "---\n\n"
        "# Demo\n\n"
        "Run `scripts/run.py` only after reviewing it.\n",
        encoding="utf-8",
    )
    (skill / "scripts" / "run.py").write_text("print('v1')\n", encoding="utf-8")
    return skill


def _seed_excluded_runtime_files(root: Path) -> dict[str, Path]:
    paths = {
        name: root / ".agent" / name / f"must-not-back-up-{name}.txt"
        for name in EXCLUDED_RUNTIME_ROOTS
    }
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"runtime-only {name}\n", encoding="utf-8")
    return paths


def _manifest_paths(archive: Path | str) -> set[str]:
    verification = verify_backup(archive)
    return {record["path"] for record in verification["manifest"]["files"]}


def _is_excluded_runtime_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return len(parts) >= 2 and parts[0] == ".agent" and parts[1] in EXCLUDED_RUNTIME_ROOTS


def test_initial_managed_backup_excludes_runtime_stores_but_keeps_user_control_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "existing-project"
    root.mkdir()
    _skill(root)
    (root / "AGENTS.md").write_text("# Existing instructions\n", encoding="utf-8")
    report = root / ".agent" / "reports" / "existing-report.md"
    report.parent.mkdir(parents=True)
    report.write_text("human-owned report\n", encoding="utf-8")
    _seed_excluded_runtime_files(root)

    preview = plan_adoption(root)
    preview_paths = {record["path"] for record in preview["backup_file_preview"]}
    assert "AGENTS.md" in preview_paths
    assert ".agent/reports/existing-report.md" in preview_paths
    assert "skills/demo/SKILL.md" in preview_paths
    assert "skills/demo/scripts/run.py" in preview_paths
    assert not any(_is_excluded_runtime_path(path) for path in preview_paths)

    applied = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    archived_paths = _manifest_paths(root / applied["backup"]["path"])
    assert preview_paths == archived_paths
    assert not any(_is_excluded_runtime_path(path) for path in archived_paths)


def test_managed_skill_sync_keeps_control_recovery_and_changed_skill_package(
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed-project"
    root.mkdir()
    skill = _skill(root)
    initial_preview = plan_adoption(root)
    initial = adopt_workspace(
        root,
        apply=True,
        expected_plan=initial_preview["plan_digest"],
    )
    assert initial["mode"] == "overlay_applied"

    _seed_excluded_runtime_files(root)
    retained_recovery = (
        root
        / ".agent"
        / "skill-overlays"
        / ".archmarshal"
        / "recovery"
        / "completed.json"
    )
    retained_recovery.parent.mkdir(parents=True)
    retained_recovery.write_text('{"state":"recovered"}\n', encoding="utf-8")
    report = root / ".agent" / "reports" / "retained.md"
    report.write_text("retain this managed report\n", encoding="utf-8")
    (skill / "scripts" / "run.py").write_text("print('v2')\n", encoding="utf-8")

    preview = plan_adoption(root)
    preview_paths = {record["path"] for record in preview["backup_file_preview"]}
    assert preview["skill_index"]["changed"] is True
    assert "skills/demo/scripts/run.py" in preview_paths
    assert ".agent/ownership.json" in preview_paths
    assert ".agent/workspace.yaml" in preview_paths
    assert ".agent/skill-overlays/.archmarshal/HEAD" in preview_paths
    assert (
        ".agent/skill-overlays/.archmarshal/recovery/completed.json" in preview_paths
    )
    assert ".agent/reports/retained.md" in preview_paths
    assert not any(_is_excluded_runtime_path(path) for path in preview_paths)

    applied = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    assert applied["mode"] == "overlay_synced"
    archived_paths = _manifest_paths(root / applied["backup"]["path"])
    assert preview_paths == archived_paths
    assert "skills/demo/scripts/run.py" in archived_paths
    assert ".agent/skill-overlays/.archmarshal/HEAD" in archived_paths
    assert not any(_is_excluded_runtime_path(path) for path in archived_paths)
