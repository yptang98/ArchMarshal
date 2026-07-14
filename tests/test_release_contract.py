from __future__ import annotations

import re
from pathlib import Path

from archmarshal import __version__
from archmarshal.schema_validation import SCHEMA_FILES

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_version_is_consistent_and_changelog_has_entry() -> None:
    project_text = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', project_text, flags=re.MULTILINE)

    assert match and match.group(1) == __version__
    assert f"## {__version__} " in (REPOSITORY_ROOT / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )


def test_packaged_schema_mirrors_are_complete_and_identical() -> None:
    source = REPOSITORY_ROOT / "schemas"
    packaged = REPOSITORY_ROOT / "src" / "archmarshal" / "schemas"

    assert {path.name for path in packaged.glob("*.yaml")} == set(SCHEMA_FILES.values())
    for filename in SCHEMA_FILES.values():
        assert (packaged / filename).read_bytes() == (source / filename).read_bytes()
