from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_scale_benchmark_is_bounded_read_only_and_machine_readable() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_scale.py",
            "--files",
            "20",
            "--skills",
            "3",
            "--projects",
            "3",
            "--iterations",
            "1",
            "--warmups",
            "0",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(completed.stdout)

    assert payload["format"] == "archmarshal-scale-benchmark-v1"
    assert payload["parameters"] == {
        "files": 20,
        "skills": 3,
        "projects": 3,
        "iterations": 1,
        "warmups": 0,
    }
    assert payload["mutation_check"]["unchanged"] is True
    assert payload["mutation_check"]["before_sha256"] == payload["mutation_check"][
        "after_sha256"
    ]
    assert payload["scenarios"]["inventory_10k_files"]["observation"] == {
        "observed_files": 20
    }
    assert payload["scenarios"]["adoption_preview_100_skills"]["observation"][
        "observed_skills"
    ] == 3
    assert payload["scenarios"]["catalog_multi_project"]["observation"] == {
        "observed_projects": 3
    }


def test_scale_benchmark_rejects_unbounded_fixture_requests() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/benchmark_scale.py", "--files", "50001"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert "--files must be between 1 and 50000" in completed.stderr
