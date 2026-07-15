from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce separate statement and branch gates.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--statements", type=float, required=True)
    parser.add_argument("--branches", type=float, required=True)
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        metavar="PATH=STATEMENTS,BRANCHES",
        help="Per-module non-regression gate. Repeat for safety-critical modules.",
    )
    args = parser.parse_args(argv)
    report: dict[str, Any] = json.loads(args.report.read_text(encoding="utf-8"))
    totals = report["totals"]
    statement = float(totals["percent_statements_covered"])
    branch = float(totals["percent_branches_covered"])
    print(f"statement={statement:.2f}% branch={branch:.2f}%")
    failed = statement < args.statements or branch < args.branches
    for specification in args.module:
        path, statement_gate, branch_gate = _parse_module_gate(specification)
        summary = _module_summary(report, path)
        module_statement = float(summary["percent_statements_covered"])
        module_branch = float(summary["percent_branches_covered"])
        print(
            f"module={path} statement={module_statement:.2f}% "
            f"branch={module_branch:.2f}%"
        )
        failed = failed or module_statement < statement_gate or module_branch < branch_gate
    return 1 if failed else 0


def _parse_module_gate(value: str) -> tuple[str, float, float]:
    try:
        path, thresholds = value.rsplit("=", 1)
        statement, branch = thresholds.split(",", 1)
        if not path.strip():
            raise ValueError
        return _portable_path(path), float(statement), float(branch)
    except ValueError as exc:
        raise SystemExit(
            f"invalid --module {value!r}; expected PATH=STATEMENTS,BRANCHES"
        ) from exc


def _module_summary(report: dict[str, Any], requested: str) -> dict[str, Any]:
    matches = [
        payload["summary"]
        for path, payload in report.get("files", {}).items()
        if _portable_path(path) == requested or _portable_path(path).endswith(f"/{requested}")
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"coverage module {requested!r} matched {len(matches)} files; expected exactly one"
        )
    return matches[0]


def _portable_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


if __name__ == "__main__":
    raise SystemExit(main())
