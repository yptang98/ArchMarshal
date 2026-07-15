from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from archmarshal import __version__

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HEAVY_MODULES = {
    "yaml",
    "jsonschema",
    "archmarshal.safety",
    "archmarshal.user_store",
    "archmarshal.adoption",
    "archmarshal.lifecycle",
    "archmarshal.session",
}


@pytest.mark.parametrize(
    ("entrypoint", "program", "argument"),
    [
        ("main", "archmarshal", "--version"),
        ("main", "archmarshal", "--help"),
        ("start_main", "archmarshal-start", "--version"),
        ("start_main", "archmarshal-start", "--help"),
        ("end_main", "archmarshal-end", "--version"),
        ("end_main", "archmarshal-end", "--help"),
    ],
)
def test_bootstrap_commands_do_not_import_domain_modules(
    entrypoint: str,
    program: str,
    argument: str,
) -> None:
    script = f"""
import contextlib
import io
import json
import sys

stdout = io.StringIO()
stderr = io.StringIO()
from archmarshal.cli import {entrypoint}
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    try:
        returned = {entrypoint}([{argument!r}])
        exit_code = returned
    except SystemExit as exc:
        exit_code = exc.code
heavy = sorted(name for name in {sorted(HEAVY_MODULES)!r} if name in sys.modules)
print(json.dumps({{
    "exit_code": exit_code,
    "stdout": stdout.getvalue(),
    "stderr": stderr.getvalue(),
    "heavy": heavy,
}}, sort_keys=True))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout)

    assert result["exit_code"] == 0
    assert result["stderr"] == ""
    assert result["heavy"] == []
    if argument == "--version":
        assert result["stdout"] == f"{program} {__version__}\n"
    else:
        assert result["stdout"].startswith(f"usage: {program}")
        if entrypoint == "main":
            assert "skill-review" in result["stdout"]
