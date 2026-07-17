from __future__ import annotations

from pathlib import Path

import pytest

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.inventory import collect_inventory


def _skill(root: Path) -> Path:
    package = root / "skills" / "preserved-artifacts"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        "name: preserved-artifacts\n"
        "description: Exercise preserved package artifact handling.\n"
        "---\n\n"
        "# Preserved artifacts\n",
        encoding="utf-8",
    )
    return package


@pytest.mark.parametrize(
    "preserved_directory",
    [".git", "__pycache__", "venv", "node_modules"],
)
def test_inventory_uses_adoption_preserved_artifact_fingerprint_boundary(
    tmp_path: Path,
    preserved_directory: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = _skill(root)
    artifact = package / preserved_directory
    artifact.mkdir()
    generated = artifact / "generated.bin"
    generated.write_bytes(b"before adoption")

    preview = plan_adoption(root)
    adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])

    generated.write_bytes(b"changed after adoption")
    inventory = collect_inventory(root)

    assert len(inventory.skills) == 1
    managed_skill = inventory.skills[0]
    assert managed_skill["_source_drift"] == "unchanged"
    assert managed_skill["_current_package_sha256"] == managed_skill["source"]["package_sha256"]
    assert managed_skill["_package_file_count"] == 1
