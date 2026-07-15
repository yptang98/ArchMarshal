from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_pattern")
    parser.add_argument("--version")
    parser.add_argument("--check-schemas", action="store_true")
    args = parser.parse_args()
    expected_version = args.version or _project_version()
    matches = [Path(item).resolve() for item in glob.glob(args.artifact_pattern)]
    if len(matches) != 1:
        raise SystemExit(f"expected one artifact for {args.artifact_pattern!r}, found {matches}")

    with tempfile.TemporaryDirectory(prefix="archmarshal-package-smoke-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        scripts = environment / ("Scripts" if sys.platform == "win32" else "bin")
        subprocess.run(
            [str(python), "-m", "pip", "install", str(matches[0])],
            check=True,
        )
        for program in ("archmarshal", "archmarshal-start", "archmarshal-end"):
            executable = scripts / (f"{program}.exe" if sys.platform == "win32" else program)
            result = subprocess.run(
                [str(executable), "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
            expected = f"{program} {expected_version}"
            if result.stdout.strip() != expected:
                raise SystemExit(
                    f"{program} version mismatch: expected {expected!r}, got {result.stdout.strip()!r}"
                )
        if args.check_schemas:
            subprocess.run(
                [
                    str(python),
                    "-c",
                    "from importlib import resources; "
                    "from archmarshal.schema_validation import SCHEMA_FILES; "
                    "root=resources.files('archmarshal.schemas'); "
                    "missing=[n for n in SCHEMA_FILES.values() if not root.joinpath(n).is_file()]; "
                    "assert not missing, missing",
                ],
                check=True,
            )
    return 0


def _project_version() -> str:
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    match = re.search(
        r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        project.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise SystemExit("could not read project version from pyproject.toml")
    return match.group(1)


if __name__ == "__main__":
    raise SystemExit(main())
