from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("benchmark_gate", Path(__file__).parents[1] / "scripts/check_benchmark.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def measurement(seconds=1.0):
    return {"format": "archmarshal-scale-benchmark-v1", "parameters": {"files": 10000},
            "python": "3.11", "platform": "same runner",
            "mutation_check": {"unchanged": True, "before_sha256": "a", "after_sha256": "a"},
            "scenarios": {"inventory": {"observation": {"files": 10000},
                                        "median_seconds": seconds, "p95_seconds": seconds}}}


def test_benchmark_gate_rejects_regression_and_accepts_bounded_noise():
    assert MODULE.check(measurement(4), measurement(5))["passed"]
    assert not MODULE.check(measurement(4), measurement(8))["passed"]
    assert MODULE.check(measurement(0.1), measurement(0.2))["passed"]


@pytest.mark.parametrize("field,value", [("platform", "other runner"), ("parameters", {}),
    ("mutation_check", {"unchanged": False}), ("scenarios", {})])
def test_benchmark_gate_rejects_incomparable_or_invalid_evidence(field, value):
    before, after = measurement(), copy.deepcopy(measurement())
    after[field] = value
    with pytest.raises(ValueError):
        MODULE.check(before, after)
