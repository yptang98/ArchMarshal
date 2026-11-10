"""Compare like-for-like scale measurements on the same CI runner."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def check(baseline: dict, current: dict, *, ratio: float = 1.75,
           floor_seconds: float = 2.0, ceiling_seconds: float = 60.0) -> dict:
    for field in ("parameters", "python", "platform"):
        if baseline.get(field) != current.get(field):
            raise ValueError(f"Benchmark {field} differ; measurements are not comparable")
    if baseline.get("format") != "archmarshal-scale-benchmark-v1" or current.get("format") != baseline["format"]:
        raise ValueError("Unsupported benchmark format")
    if set(baseline["scenarios"]) != set(current["scenarios"]) or not current["scenarios"]:
        raise ValueError("Benchmark scenario sets differ or are empty")
    for run in (baseline, current):
        mutation = run.get("mutation_check", {})
        if (mutation.get("unchanged") is not True or not mutation.get("before_sha256")
                or mutation["before_sha256"] != mutation.get("after_sha256")):
            raise ValueError("Benchmark fixture mutation check failed")
    results = []
    for name, scenario in sorted(current["scenarios"].items()):
        previous = baseline["scenarios"][name]
        if previous["observation"] != scenario["observation"]:
            raise ValueError(f"Benchmark observation changed: {name}")
        for field in ("median_seconds", "p95_seconds"):
            actual, before = scenario[field], previous[field]
            if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in (actual, before)):
                raise ValueError("Invalid timing measurement")
            limit = min(ceiling_seconds, max(floor_seconds, before * ratio))
            results.append({"scenario": name, "metric": field, "baseline": before,
                            "current": actual, "limit": limit, "passed": actual <= limit})
    return {"passed": all(item["passed"] for item in results), "comparisons": results,
            "policy": {"maximum_ratio": ratio, "floor_seconds": floor_seconds,
                       "absolute_ceiling_seconds": ceiling_seconds}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    args = parser.parse_args()
    result = check(*[json.loads(path.read_text(encoding="utf-8-sig")) for path in (args.baseline, args.current)])
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
