from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .adoption import adopt_workspace
from .adoption_tx import adoption_transaction_status, recover_adoption_transaction
from .audit import audit_workspace
from .catalog import catalog_projects
from .checkpoint import checkpoint_workspace
from .closeout import closeout_workspace
from .diagnostics import severity_counts
from .errors import ArchMarshalError, require_workspace_root
from .inventory import collect_inventory
from .learning import learn_from_projects
from .lifecycle import end_workspace, start_workspace
from .lint import lint_workspace
from .planner import plan_workspace
from .resolver import resolve_workspace
from .safety import restore_backup, verify_backup
from .session import CLOSEOUT_LEVELS, record_closeout
from .skill_index import rollback_skill_index, skill_index_status


def main(argv: list[str] | None = None) -> int:
    return _guard_cli(_main_impl, argv)


def _main_impl(argv: list[str] | None = None) -> int:
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
    _add_root_command(
        subparsers,
        "adoption-status",
        "Inspect an incomplete adoption transaction without writing.",
    )
    adoption_recover_parser = _add_root_command(
        subparsers,
        "adoption-recover",
        "Preview or safely complete a durable create-only adoption transaction.",
    )
    adoption_recover_parser.add_argument(
        "--apply",
        action="store_true",
        help="Complete verified missing targets; changed targets remain untouched and block recovery.",
    )
    adoption_recover_parser.add_argument(
        "--expect-transaction",
        help="Exact transaction id from the reviewed recovery preview.",
    )
    adoption_recover_parser.add_argument(
        "--expect-plan",
        help="Exact adoption plan digest from the reviewed recovery preview.",
    )
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
    verify_backup_parser = subparsers.add_parser(
        "backup-verify", help="Verify every archived file against an ArchMarshal backup manifest."
    )
    verify_backup_parser.add_argument("archive", help="Backup zip to verify.")
    verify_backup_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    restore_backup_parser = subparsers.add_parser(
        "backup-restore", help="Restore a verified backup into a new, non-existing directory."
    )
    restore_backup_parser.add_argument("archive", help="Backup zip to restore.")
    restore_backup_parser.add_argument("destination", help="New directory to create.")
    restore_backup_parser.add_argument("--apply", action="store_true", help="Create the destination.")
    restore_backup_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    skill_status_parser = _add_root_command(
        subparsers,
        "skill-index-status",
        "Verify the immutable skill-index chain and inspect lock metadata without writing.",
    )
    skill_status_parser.add_argument(
        "--history-limit",
        type=int,
        default=20,
        help="Number of newest verified generations to return (1-100).",
    )
    skill_status_parser.add_argument(
        "--history-from",
        help="Reachable generation digest at which to start the verified history page.",
    )
    skill_rollback_parser = _add_root_command(
        subparsers,
        "skill-index-rollback",
        "Create a new audited generation using an ancestor metadata snapshot.",
    )
    skill_rollback_parser.add_argument(
        "--to",
        required=True,
        help="Full SHA-256 digest of an ancestor generation.",
    )
    skill_rollback_parser.add_argument(
        "--expect-head",
        help="Exact HEAD from the reviewed preview; required with --apply.",
    )
    skill_rollback_parser.add_argument(
        "--expect-plan",
        help="Exact rollback plan digest from the reviewed preview; required with --apply.",
    )
    skill_rollback_parser.add_argument(
        "--reason",
        default="",
        help="Optional short audit reason; do not include secrets.",
    )
    skill_rollback_parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up the current index and publish the reviewed rollback generation.",
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
    if args.command == "backup-verify":
        verification = verify_backup(args.archive)
        verification.update({"tool": "archmarshal", "stage": "backup_verify", "mode": "verified"})
        verification.pop("manifest", None)
        _print_json(verification, args.pretty)
        return 0
    if args.command == "backup-restore":
        payload = restore_backup(args.archive, args.destination, apply=args.apply)
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    root = require_workspace_root(args.root)

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
    if args.command == "skill-index-status":
        _print_json(
            skill_index_status(
                root,
                history_limit=args.history_limit,
                history_from=args.history_from,
            ),
            args.pretty,
        )
        return 0
    if args.command == "adoption-status":
        payload = adoption_transaction_status(root)
        _print_json(payload, args.pretty)
        return 0 if payload.get("state") == "none" else 2
    if args.command == "adoption-recover":
        payload = recover_adoption_transaction(
            root,
            apply=args.apply,
            expected_transaction=args.expect_transaction,
            expected_plan=args.expect_plan,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "skill-index-rollback":
        payload = rollback_skill_index(
            root,
            args.to,
            expected_head=args.expect_head,
            expected_plan=args.expect_plan,
            reason=args.reason,
            apply=args.apply,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "adopt":
        payload = adopt_workspace(
            root,
            apply=args.apply,
            tags=args.tag,
            backup_scope=args.backup_scope,
            expected_plan=args.expect_plan,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "start":
        if args.apply:
            adoption = adopt_workspace(
                root,
                apply=True,
                tags=args.tag,
                backup_scope=args.backup_scope,
                expected_plan=args.expect_plan,
            )
            payload = start_workspace(root)
            payload["adoption"] = adoption
            if adoption["mode"] in {"overlay_applied", "overlay_synced", "review_required"}:
                payload["mode"] = adoption["mode"]
            _print_json(payload, args.pretty)
            return _payload_exit_code(adoption)
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
            payload = record_closeout(
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
                expected_plan=args.expect_plan,
            )
            _print_json(payload, args.pretty)
            return _payload_exit_code(payload)
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
    return _guard_cli(_start_main_impl, argv)


def _start_main_impl(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archmarshal-start")
    parser.add_argument("root", nargs="?", default=".", help="Workspace root to inspect.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    _add_adoption_arguments(parser)
    args = parser.parse_args(argv)
    root = require_workspace_root(args.root)
    payload = start_workspace(root)
    if args.apply:
        payload["adoption"] = adopt_workspace(
            root,
            apply=True,
            tags=args.tag,
            backup_scope=args.backup_scope,
            expected_plan=args.expect_plan,
        )
        payload = start_workspace(root) | {"adoption": payload["adoption"]}
        if payload["adoption"]["mode"] in {
            "overlay_applied",
            "overlay_synced",
            "review_required",
        }:
            payload["mode"] = payload["adoption"]["mode"]
    _print_json(payload, args.pretty)
    return _payload_exit_code(payload.get("adoption") or payload)


def end_main(argv: list[str] | None = None) -> int:
    return _guard_cli(_end_main_impl, argv)


def _end_main_impl(argv: list[str] | None = None) -> int:
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
            require_workspace_root(args.root),
            level=args.level,
            apply=args.apply,
            summary=args.summary,
            steps=args.step,
            scripts=args.script,
            commands=args.command_line,
            tags=args.tag,
            used_skills=args.used_skill,
            shell=args.shell,
            expected_plan=args.expect_plan,
        )
    else:
        if args.apply:
            parser.error("--apply requires --level quick, standard, or reproducible")
        payload = end_workspace(require_workspace_root(args.root), args.used_skill)
    _print_json(payload, args.pretty)
    return _payload_exit_code(payload)


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
        "--expect-plan",
        help="Exact plan digest from a reviewed preview; required before any adoption write.",
    )
    parser.add_argument(
        "--backup-scope",
        choices=["managed", "full"],
        default="managed",
        help="Back up managed metadata/skills (default) or the full project before adoption.",
    )


def _add_recording_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--level", choices=CLOSEOUT_LEVELS, help="Closeout recording depth.")
    parser.add_argument("--apply", action="store_true", help="Write a new append-only closeout directory.")
    parser.add_argument(
        "--expect-plan",
        help="Exact closeout plan digest from a reviewed preview; required before writing.",
    )
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
        default=None,
        help="Reference run-script shell; defaults to PowerShell on Windows and Bash elsewhere.",
    )


def _payload_exit_code(payload: dict[str, Any]) -> int:
    return (
        2
        if payload.get("mode") in {"blocked", "review_required", "recovery_required"}
        or payload.get("blocked") is True
        else 0
    )


def _guard_cli(function: Any, argv: list[str] | None) -> int:
    tokens = argv if argv is not None else sys.argv[1:]
    pretty = "--pretty" in tokens
    try:
        return int(function(argv))
    except ArchMarshalError as exc:
        _print_json(
            {"tool": "archmarshal", "mode": "error", "error": exc.to_dict()},
            pretty,
            stream=sys.stderr,
        )
        return 2
    except (OSError, ValueError) as exc:
        _print_json(
            {
                "tool": "archmarshal",
                "mode": "error",
                "error": {"code": "operation_failed", "message": str(exc)},
            },
            pretty,
            stream=sys.stderr,
        )
        return 2


def _print_json(payload: Any, pretty: bool, *, stream: Any = None) -> None:
    print(
        json.dumps(payload, default=_json_default, indent=2 if pretty else None, sort_keys=True),
        file=stream,
    )


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


__all__ = ["end_main", "main", "start_main"]
