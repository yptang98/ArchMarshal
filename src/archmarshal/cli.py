from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .audit import audit_workspace
from .checkpoint import checkpoint_workspace
from .closeout import closeout_workspace
from .diagnostics import Diagnostic, severity_counts
from .inventory import collect_inventory
from .lint import lint_workspace
from .planner import plan_workspace
from .resolver import resolve_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archmarshal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_root_command(subparsers, "inventory", "Scan workspace structure without modifying files.")
    _add_root_command(subparsers, "audit", "Summarize governance risks.")
    _add_root_command(subparsers, "plan", "Generate read-only remediation actions.")
    checkpoint_parser = _add_root_command(
        subparsers,
        "checkpoint",
        "Create a read-only context checkpoint after summarization or compaction.",
    )
    checkpoint_parser.add_argument("--summary", required=True, help="Compact summary to preserve.")
    checkpoint_parser.add_argument("--task", default="", help="Task or project stage being checkpointed.")
    checkpoint_parser.add_argument(
        "--decision",
        action="append",
        default=[],
        help="Important decision to preserve. Repeat as needed.",
    )
    checkpoint_parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="Important project file touched or relied on. Repeat as needed.",
    )
    checkpoint_parser.add_argument(
        "--next-step",
        action="append",
        default=[],
        help="Follow-up work that should survive context compression. Repeat as needed.",
    )
    checkpoint_parser.add_argument(
        "--used-skill",
        action="append",
        default=[],
        help="Skill id used before this checkpoint. Repeat as needed.",
    )
    checkpoint_parser.add_argument(
        "--risk",
        action="append",
        default=[],
        help="Known risk, caveat, or unresolved uncertainty. Repeat as needed.",
    )
    closeout_parser = _add_root_command(
        subparsers,
        "closeout",
        "Summarize used skills and cleanup actions after project work.",
    )
    closeout_parser.add_argument(
        "--used-skill",
        action="append",
        default=[],
        help="Skill id used in the project session. Repeat as needed.",
    )
    resolve_parser = _add_root_command(
        subparsers,
        "resolve",
        "Suggest relevant skills and context modules for a task.",
    )
    resolve_parser.add_argument("--task", required=True, help="Task description to resolve.")
    lint_parser = _add_root_command(subparsers, "lint", "Run governance lint rules.")
    lint_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return exit code 1 when any diagnostic is emitted, including warnings.",
    )

    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command == "inventory":
        _print_json(collect_inventory(root).to_dict(), args.pretty)
        return 0
    if args.command == "lint":
        diagnostics = lint_workspace(root)
        payload = {
            "tool": "archmarshal",
            "root": str(root.resolve()),
            "summary": severity_counts(diagnostics),
            "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
        }
        _print_json(payload, args.pretty)
        if any(diagnostic.severity == "error" for diagnostic in diagnostics):
            return 1
        if args.strict and diagnostics:
            return 1
        return 0
    if args.command == "audit":
        _print_json(audit_workspace(root), args.pretty)
        return 0
    if args.command == "plan":
        _print_json(plan_workspace(root), args.pretty)
        return 0
    if args.command == "checkpoint":
        _print_json(
            checkpoint_workspace(
                root,
                summary=args.summary,
                task=args.task,
                decisions=args.decision,
                files=args.file,
                next_steps=args.next_step,
                used_skills=args.used_skill,
                risks=args.risk,
            ),
            args.pretty,
        )
        return 0
    if args.command == "closeout":
        _print_json(closeout_workspace(root, args.used_skill), args.pretty)
        return 0
    if args.command == "resolve":
        _print_json(resolve_workspace(root, args.task), args.pretty)
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


def _add_root_command(subparsers: Any, name: str, help_text: str) -> argparse.ArgumentParser:
    command = subparsers.add_parser(name, help=help_text)
    command.add_argument("root", nargs="?", default=".", help="Workspace root to inspect.")
    command.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return command


def _print_json(payload: Any, pretty: bool) -> None:
    print(json.dumps(payload, default=_json_default, indent=2 if pretty else None, sort_keys=True))


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


__all__ = ["main"]
