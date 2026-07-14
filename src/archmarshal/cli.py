from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .adoption import adopt_workspace
from .audit import audit_workspace
from .catalog import catalog_projects
from .checkpoint import checkpoint_workspace
from .closeout import closeout_workspace
from .diagnostics import Diagnostic, severity_counts
from .inventory import collect_inventory
from .learning import learn_from_projects
from .lint import lint_workspace
from .lifecycle import end_workspace, start_workspace
from .planner import plan_workspace
from .resolver import resolve_workspace
from .session import CLOSEOUT_LEVELS, record_closeout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archmarshal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_root_command(subparsers, "inventory", "Scan workspace structure without modifying files.")
    _add_root_command(subparsers, "audit", "Summarize governance risks.")
    _add_root_command(subparsers, "plan", "Generate read-only remediation actions.")
    adopt_parser = _add_root_command(
        subparsers,
        "adopt",
        "Safely add an ArchMarshal overlay without changing existing project or skill files.",
    )
    _add_adoption_arguments(adopt_parser)
    start_parser = _add_root_command(subparsers, "start", "Start ArchMarshal project governance.")
    _add_adoption_arguments(start_parser)
    catalog_parser = _add_root_command(
        subparsers,
        "catalog",
        "List managed projects by recorded date and tags without loading raw history.",
    )
    catalog_parser.add_argument(
        "--include-root",
        action="append",
        default=[],
        help="Additional project to include. Repeat as needed.",
    )
    catalog_parser.add_argument("--tag", action="append", default=[], help="Required tag. Repeat for AND filtering.")
    learn_parser = _add_root_command(
        subparsers,
        "learn",
        "Extract review-only common-skill and user-preference candidates from session manifests.",
    )
    learn_parser.add_argument("--apply", action="store_true", help="Write a new candidate pack to inbox.")
    learn_parser.add_argument(
        "--include-root",
        action="append",
        default=[],
        help="Additional ArchMarshal project to aggregate. Repeat as needed.",
    )
    end_parser = _add_root_command(subparsers, "end", "Close out ArchMarshal project governance.")
    end_parser.add_argument(
        "--used-skill",
        action="append",
        default=[],
        help="Skill id used in the project session. Repeat as needed.",
    )
    _add_recording_arguments(end_parser)
    checkpoint_parser = _add_root_command(
        subparsers,
        "checkpoint",
        "Create a read-only context checkpoint after summarization or compaction.",
    )
    checkpoint_parser.add_argument("--summary", required=True, help="Compact summary to preserve.")
    checkpoint_parser.add_argument("--task", default="", help="Task or project stage being checkpointed.")
    checkpoint_parser.add_argument(
        "--save-path",
        help="User-approved project-file directory for this checkpoint. Overrides workspace save_paths.",
    )
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
    if args.command == "adopt":
        _print_json(
            adopt_workspace(
                root,
                apply=args.apply,
                tags=args.tag,
                backup_scope=args.backup_scope,
            ),
            args.pretty,
        )
        return 0
    if args.command == "start":
        if args.apply:
            adoption = adopt_workspace(
                root,
                apply=True,
                tags=args.tag,
                backup_scope=args.backup_scope,
            )
            payload = start_workspace(root)
            payload["adoption"] = adoption
            payload["mode"] = "overlay_applied" if adoption["mode"] == "overlay_applied" else payload["mode"]
            _print_json(payload, args.pretty)
        else:
            _print_json(start_workspace(root), args.pretty)
        return 0
    if args.command == "catalog":
        _print_json(
            catalog_projects([root, *[Path(item) for item in args.include_root]], tags=args.tag),
            args.pretty,
        )
        return 0
    if args.command == "learn":
        _print_json(
            learn_from_projects([root, *[Path(item) for item in args.include_root]], apply=args.apply),
            args.pretty,
        )
        return 0
    if args.command == "end":
        if args.level:
            _print_json(
                record_closeout(
                    root,
                    level=args.level,
                    apply=args.apply,
                    summary=args.summary,
                    steps=args.step,
                    scripts=args.script,
                    commands=args.command_line,
                    tags=args.tag,
                    used_skills=args.used_skill,
                    shell=args.shell,
                ),
                args.pretty,
            )
        else:
            if args.apply:
                parser.error("--apply for end requires --level quick, standard, or reproducible")
            _print_json(end_workspace(root, args.used_skill), args.pretty)
        return 0
    if args.command == "checkpoint":
        _print_json(
            checkpoint_workspace(
                root,
                summary=args.summary,
                task=args.task,
                save_path=args.save_path,
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


def start_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archmarshal-start")
    parser.add_argument("root", nargs="?", default=".", help="Workspace root to inspect.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    _add_adoption_arguments(parser)
    args = parser.parse_args(argv)
    root = Path(args.root)
    payload = start_workspace(root)
    if args.apply:
        payload["adoption"] = adopt_workspace(
            root,
            apply=True,
            tags=args.tag,
            backup_scope=args.backup_scope,
        )
        payload = start_workspace(root) | {"adoption": payload["adoption"]}
    _print_json(payload, args.pretty)
    return 0


def end_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archmarshal-end")
    parser.add_argument("root", nargs="?", default=".", help="Workspace root to inspect.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument(
        "--used-skill",
        action="append",
        default=[],
        help="Skill id used in the project session. Repeat as needed.",
    )
    _add_recording_arguments(parser)
    args = parser.parse_args(argv)
    if args.level:
        payload = record_closeout(
            Path(args.root),
            level=args.level,
            apply=args.apply,
            summary=args.summary,
            steps=args.step,
            scripts=args.script,
            commands=args.command_line,
            tags=args.tag,
            used_skills=args.used_skill,
            shell=args.shell,
        )
    else:
        if args.apply:
            parser.error("--apply requires --level quick, standard, or reproducible")
        payload = end_workspace(Path(args.root), args.used_skill)
    _print_json(payload, args.pretty)
    return 0


def _add_root_command(subparsers: Any, name: str, help_text: str) -> argparse.ArgumentParser:
    command = subparsers.add_parser(name, help=help_text)
    command.add_argument("root", nargs="?", default=".", help="Workspace root to inspect.")
    command.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return command


def _add_adoption_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create only missing management overlay files after a verified backup.",
    )
    parser.add_argument("--tag", action="append", default=[], help="Project tag. Repeat as needed.")
    parser.add_argument(
        "--backup-scope",
        choices=["managed", "full"],
        default="managed",
        help="Back up managed metadata/skills (default) or the full project before adoption.",
    )


def _add_recording_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--level", choices=CLOSEOUT_LEVELS, help="Closeout recording depth.")
    parser.add_argument("--apply", action="store_true", help="Write a new append-only closeout directory.")
    parser.add_argument("--summary", default="", help="Project or phase outcome summary.")
    parser.add_argument("--step", action="append", default=[], help="Ordered work step. Repeat as needed.")
    parser.add_argument("--script", action="append", default=[], help="Key script path. Repeat as needed.")
    parser.add_argument(
        "--command",
        dest="command_line",
        action="append",
        default=[],
        help="Exact reproduction command. Repeat as needed.",
    )
    parser.add_argument("--tag", action="append", default=[], help="Session tag. Repeat as needed.")
    parser.add_argument(
        "--shell",
        choices=["powershell", "bash"],
        default="powershell",
        help="Reference run-script shell for reproducible closeout.",
    )


def _print_json(payload: Any, pretty: bool) -> None:
    print(json.dumps(payload, default=_json_default, indent=2 if pretty else None, sort_keys=True))


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


__all__ = ["end_main", "main", "start_main"]
