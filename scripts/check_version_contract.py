from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _match(path: Path, pattern: str, label: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise SystemExit(f"missing {label} in {path.relative_to(ROOT)}")
    return match.group(1)


def main() -> int:
    package_version = _match(
        ROOT / "pyproject.toml",
        r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        "project version",
    )
    runtime_version = _match(
        ROOT / "src" / "archmarshal" / "__init__.py",
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        "runtime version",
    )
    if runtime_version != package_version:
        raise SystemExit(
            f"version mismatch: pyproject={package_version}, runtime={runtime_version}"
        )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {package_version} - " not in changelog:
        raise SystemExit(f"CHANGELOG.md has no dated {package_version} entry")
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        expected_tag = f"v{package_version}"
        actual_tag = os.environ.get("GITHUB_REF_NAME")
        if actual_tag != expected_tag:
            raise SystemExit(
                f"release tag mismatch: expected {expected_tag!r}, got {actual_tag!r}"
            )
    print(f"version contract ok: {package_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
