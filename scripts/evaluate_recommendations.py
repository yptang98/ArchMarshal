"""Run a fixed task corpus against the production Skill matcher."""
# ruff: noqa: E402 -- permit running directly from a source checkout.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from archmarshal.recommendation import match_skills


def evaluate(fixture: dict) -> dict:
    true_positive = false_positive = false_negative = 0
    failures = []
    for case in fixture["tasks"]:
        expected = set(case["expected"])
        actual = {item["id"] for item in match_skills(fixture["skills"], case["task"])}
        true_positive += len(actual & expected)
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
        if actual != expected:
            failures.append({"task": case["task"], "unexpected": sorted(actual - expected),
                             "omitted": sorted(expected - actual)})
    return {"format": "archmarshal-recommendation-eval-v1", "tasks": len(fixture["tasks"]),
            "true_positive": true_positive, "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0,
            "recall": true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0,
            "failures": failures, "scope": fixture["description"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=REPO / "tests/fixtures/recommendation_tasks.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(json.loads(args.fixture.read_text(encoding="utf-8")))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
