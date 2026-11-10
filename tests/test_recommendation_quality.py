from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_portability import environment as environment

import archmarshal.lifecycle as lifecycle
import archmarshal.resolver as resolver
from archmarshal.errors import ArchMarshalError
from archmarshal.inventory import collect_inventory
from archmarshal.recommendation import match_skills

CORPUS = json.loads((Path(__file__).parent / "fixtures/recommendation_tasks.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CORPUS["tasks"], ids=[case["task"] for case in CORPUS["tasks"]])
def test_task_recommendations_match_reviewed_corpus(case):
    actual = match_skills(CORPUS["skills"], case["task"])
    assert {item["id"] for item in actual} == set(case["expected"])
    for item in actual:
        assert item["trigger_matches"] or item["tag_matches"]
        assert "priority_bonus" in item["match_evidence"]


def test_start_reuses_one_inventory_for_lint_and_resolution(environment, monkeypatch):
    roots, _, _ = environment
    calls = []
    original = lifecycle.collect_inventory
    def collect(root):
        calls.append(root)
        return original(root)
    monkeypatch.setattr(lifecycle, "collect_inventory", collect)
    def repeated(_):
        raise AssertionError("resolution must reuse the operation inventory")
    monkeypatch.setattr(resolver, "collect_inventory", repeated)
    result = lifecycle.start_workspace(roots[0], task="demo")
    assert len(calls) == 1
    assert result["resolution"]["adoption_transaction"]["state"] == "none"


def test_shared_inventory_cannot_cross_workspace_boundaries(environment):
    roots, _, _ = environment
    with pytest.raises(ArchMarshalError, match="different workspace"):
        resolver.resolve_workspace(roots[1], "demo", inventory=collect_inventory(roots[0]))


def test_shared_inventory_does_not_hide_observed_incomplete_transaction(environment):
    roots, _, _ = environment
    result = resolver.resolve_workspace(
        roots[0], "demo", inventory=collect_inventory(roots[0]),
        observed_transaction={"state": "incomplete"})
    assert result["suggested_skills"] == []
    assert result["adoption_transaction"]["state"] == "incomplete"
