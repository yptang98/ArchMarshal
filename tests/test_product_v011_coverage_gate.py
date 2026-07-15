from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _report(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "totals": {
                    "percent_statements_covered": 90.0,
                    "percent_branches_covered": 80.0,
                },
                "files": {
                    "src\\archmarshal\\safety.py": {
                        "summary": {
                            "percent_statements_covered": 86.0,
                            "percent_branches_covered": 76.0,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_coverage_gate_enforces_cross_platform_per_module_thresholds(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    _report(report)
    script = Path(__file__).parents[1] / "scripts" / "check_coverage.py"

    passing = subprocess.run(
        [
            sys.executable,
            str(script),
            str(report),
            "--statements",
            "85",
            "--branches",
            "75",
            "--module",
            "src/archmarshal/safety.py=85,75",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    failing = subprocess.run(
        [
            sys.executable,
            str(script),
            str(report),
            "--statements",
            "85",
            "--branches",
            "75",
            "--module",
            "src/archmarshal/safety.py=87,77",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert passing.returncode == 0
    assert "module=src/archmarshal/safety.py statement=86.00% branch=76.00%" in passing.stdout
    assert failing.returncode == 1
