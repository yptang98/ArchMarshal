from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce separate statement and branch gates.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--statements", type=float, required=True)
    parser.add_argument("--branches", type=float, required=True)
    args = parser.parse_args()
    totals = json.loads(args.report.read_text(encoding="utf-8"))["totals"]
    statement = float(totals["percent_statements_covered"])
    branch = float(totals["percent_branches_covered"])
    print(f"statement={statement:.2f}% branch={branch:.2f}%")
    if statement < args.statements or branch < args.branches:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
