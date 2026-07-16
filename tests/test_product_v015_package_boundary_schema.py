from __future__ import annotations

import json
from pathlib import Path

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.lint import lint_workspace
from archmarshal.schema_validation import validate_schema


def _skill(root: Path) -> None:
    package = root / "skills" / "demo"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: Exercise the portable package boundary contract.\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )


def test_adopted_portable_package_boundary_conforms_to_manifest_schema(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(root)

    preview = plan_adoption(root)
    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
    )

    assert applied["mode"] == "overlay_applied"
    head = (root / ".agent" / "skill-overlays" / ".archmarshal" / "HEAD").read_text(
        encoding="utf-8"
    ).strip()
    generation = json.loads(
        (
            root
            / ".agent"
            / "skill-overlays"
            / ".archmarshal"
            / "objects"
            / "sha256"
            / f"{head}.json"
        ).read_text(encoding="utf-8")
    )
    manifest = generation["skills"][0]["manifest"]

    assert manifest["source"]["package_boundary"] == "portable-source-v1"
    assert validate_schema(manifest, "skill-manifest") == []
    assert not [
        diagnostic
        for diagnostic in lint_workspace(root)
        if diagnostic.rule == "skill.manifest_schema_invalid"
    ]
