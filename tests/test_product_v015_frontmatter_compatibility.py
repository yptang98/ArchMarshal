from __future__ import annotations

from pathlib import Path

import pytest

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.lint import lint_workspace
from archmarshal.skill_validation import validate_skill_package


def _skill(root: Path, name: str, extra: str) -> Path:
    package = root / "skills" / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Exercise {name} compatibility metadata.\n"
        f"{extra}"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )
    return package


@pytest.mark.parametrize(
    ("name", "extra", "extension"),
    [
        ("licensed", "license: Apache-2.0\n", "license"),
        ("tool-string", "allowed-tools: Bash(docker:*)\n", "allowed-tools"),
        ("tool-list", "allowed-tools: [Read, Bash(docker:*)]\n", "allowed-tools"),
        (
            "described",
            "metadata:\n  short-description: Create or update a skill\n",
            "metadata",
        ),
    ],
)
def test_skill_validation_accepts_bounded_compatibility_frontmatter(
    tmp_path: Path,
    name: str,
    extra: str,
    extension: str,
) -> None:
    package = _skill(tmp_path, name, extra)

    result = validate_skill_package(package)

    assert result["valid"] is True
    assert result["frontmatter"]["extensions"] == [extension]


@pytest.mark.parametrize(
    ("name", "extra", "expected"),
    [
        ("bad-license", "license: [MIT]\n", "skill_frontmatter_license_invalid"),
        ("bad-tools", "allowed-tools: {}\n", "skill_frontmatter_allowed_tools_invalid"),
        ("empty-tools", "allowed-tools: []\n", "skill_frontmatter_allowed_tools_invalid"),
        ("bad-metadata", "metadata: text\n", "skill_frontmatter_metadata_invalid"),
        ("unknown", "unexpected: true\n", "skill_frontmatter_extra_fields"),
    ],
)
def test_skill_validation_rejects_invalid_or_unknown_compatibility_frontmatter(
    tmp_path: Path,
    name: str,
    extra: str,
    expected: str,
) -> None:
    package = _skill(tmp_path, name, extra)

    result = validate_skill_package(package)

    assert result["valid"] is False
    assert expected in {item["code"] for item in result["errors"]}


def test_adoption_keeps_common_frontmatter_extensions_reviewable(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _skill(
        root,
        "skill-creator",
        "metadata:\n  short-description: Create or update a skill\n",
    )
    _skill(root, "skill-installer", "metadata:\n  short-description: Install skills\n")
    _skill(root, "docker", "allowed-tools: Bash(docker:*)\n")

    preview = plan_adoption(root)
    applied = adopt_workspace(
        root,
        apply=True,
        expected_plan=preview["plan_digest"],
    )
    diagnostics = lint_workspace(root)

    assert applied["mode"] == "overlay_applied"
    assert {item["activation_state"] for item in preview["discovered_skills"]} == {
        "quarantined_needs_review"
    }
    assert not [
        item
        for item in diagnostics
        if item.rule
        in {
            "skill_frontmatter_extra_fields",
            "skill_frontmatter_license_invalid",
            "skill_frontmatter_allowed_tools_invalid",
            "skill_frontmatter_metadata_invalid",
        }
    ]
