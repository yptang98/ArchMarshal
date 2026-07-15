from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .errors import ArchMarshalError, require_workspace_root

CLOSEOUT_LEVELS = ("quick", "standard", "reproducible")


class _ArgumentParser(argparse.ArgumentParser):
    """Keep usage failures inside the versioned JSON CLI contract."""

    def error(self, message: str) -> None:
        raise ArchMarshalError(
            "cli_usage_error",
            message,
            details={"usage": self.format_usage().strip()},
        )


def main(argv: list[str] | None = None) -> int:
    return _guard_cli(_main_impl, argv)


def _main_impl(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(prog="archmarshal")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_root_command(subparsers, "inventory", "Scan workspace structure without modifying files.")
    _add_root_command(subparsers, "audit", "Summarize governance risks.")
    _add_root_command(subparsers, "plan", "Generate read-only remediation actions.")
    doctor_parser = _add_root_command(
        subparsers,
        "doctor",
        "Inspect bounded ArchMarshal workspace and user-store health without writing.",
    )
    doctor_parser.add_argument(
        "--user-store",
        help="Optional user Skill store root to inspect in the same read-only report.",
    )
    doctor_parser.add_argument(
        "--history-limit",
        type=int,
        default=20,
        help="Maximum active generations, transactions, and sessions to inspect (1-100).",
    )
    adopt_parser = _add_root_command(
        subparsers,
        "adopt",
        "Safely add an ArchMarshal overlay without changing existing project or skill files.",
    )
    _add_adoption_arguments(adopt_parser)
    init_parser = _add_root_command(
        subparsers,
        "init",
        "Create a missing project Skill scaffold through the safe adoption transaction.",
    )
    _add_adoption_arguments(init_parser)
    start_parser = _add_root_command(subparsers, "start", "Start ArchMarshal project governance.")
    _add_adoption_arguments(start_parser)
    _add_resolution_arguments(start_parser, task_required=False)
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
    catalog_parser.add_argument(
        "--tag", action="append", default=[], help="Required tag. Repeat for AND filtering."
    )
    learn_parser = _add_root_command(
        subparsers,
        "learn",
        "Extract review-only common-skill and user-preference candidates from session manifests.",
    )
    learn_parser.add_argument(
        "--apply", action="store_true", help="Write a new candidate pack to inbox."
    )
    learn_parser.add_argument(
        "--plan-file",
        help="Saved JSON preview containing learning_plan; required with --apply.",
    )
    learn_parser.add_argument(
        "--expect-plan",
        help="Exact learning plan digest from preview; required with --apply.",
    )
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
    verify_backup_parser.add_argument(
        "--show-files",
        action="store_true",
        help="Include the complete verified backup manifest and file list.",
    )
    verify_backup_parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output."
    )
    restore_backup_parser = subparsers.add_parser(
        "backup-restore", help="Restore a verified backup into a new, non-existing directory."
    )
    restore_backup_parser.add_argument("archive", help="Backup zip to restore.")
    restore_backup_parser.add_argument("destination", help="New directory to create.")
    restore_backup_parser.add_argument(
        "--apply", action="store_true", help="Create the destination."
    )
    restore_backup_parser.add_argument(
        "--rebind-workspace",
        action="store_true",
        help="After exact restore and backup, root-bind a verified ArchMarshal ownership marker to the new directory.",
    )
    restore_backup_parser.add_argument(
        "--expect-plan", help="Exact plan digest from the reviewed restore preview."
    )
    restore_backup_parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output."
    )
    user_store_init_parser = subparsers.add_parser(
        "user-store-init",
        help="Preview or initialize an isolated, ArchMarshal-owned user Skill store.",
    )
    _add_user_store_plan_arguments(user_store_init_parser, include_head=False)
    user_store_status_parser = subparsers.add_parser(
        "user-store-status",
        help="Verify an isolated user Skill store without modifying it.",
    )
    user_store_status_parser.add_argument("user_store", help="User Skill store root.")
    user_store_status_parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output."
    )
    user_store_rollback_parser = subparsers.add_parser(
        "user-store-rollback",
        help="Publish a forward rollback generation from a reviewed ancestor.",
    )
    _add_user_store_plan_arguments(user_store_rollback_parser, include_head=True)
    user_store_rollback_parser.add_argument(
        "--to", required=True, help="Full SHA-256 digest of an ancestor generation."
    )
    user_store_rollback_parser.add_argument(
        "--reason", default="", help="Short rollback reason; do not include secrets."
    )
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
    skill_review_parser = _add_root_command(
        subparsers,
        "skill-review",
        "Approve or reject an exact indexed Skill package and routing revision.",
    )
    skill_review_parser.add_argument(
        "--source",
        required=True,
        help="Indexed workspace-relative Skill source path.",
    )
    skill_review_parser.add_argument(
        "--decision",
        required=True,
        choices=("approve", "reject"),
        help="Human decision for this exact package and routing digest.",
    )
    skill_review_parser.add_argument("--reviewer", default="human", help="Short reviewer identity.")
    skill_review_parser.add_argument(
        "--reason", default="", help="Short review reason; do not include secrets."
    )
    skill_review_parser.add_argument(
        "--allow-global-policy",
        action="store_true",
        help="Separately confirm global or highest-priority policy activation.",
    )
    skill_review_parser.add_argument(
        "--expect-head",
        help="Exact Skill index HEAD from preview; required with --apply.",
    )
    skill_review_parser.add_argument(
        "--expect-plan",
        help="Exact review plan digest from preview; required with --apply.",
    )
    skill_review_parser.add_argument(
        "--plan-file",
        help="Complete saved Skill review preview JSON; required with --apply.",
    )
    skill_review_parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up the index and publish the reviewed immutable decision.",
    )
    candidate_review_parser = _add_root_command(
        subparsers,
        "candidate-review",
        "Record a review decision for an exact committed learning candidate.",
    )
    _add_candidate_arguments(candidate_review_parser)
    candidate_review_parser.add_argument(
        "--decision", required=True, choices=("accept", "reject", "defer")
    )
    candidate_draft_parser = _add_root_command(
        subparsers,
        "candidate-draft",
        "Create a reviewed, disabled Skill draft envelope from an accepted candidate.",
    )
    _add_candidate_draft_arguments(candidate_draft_parser)
    candidate_promote_parser = _add_root_command(
        subparsers,
        "candidate-promote",
        "Promote an exact committed candidate into the isolated user store.",
    )
    _add_candidate_arguments(candidate_promote_parser)
    candidate_promote_parser.add_argument(
        "--draft", help="Reviewed common-Skill draft directory; required for Skill candidates."
    )
    candidate_promote_parser.add_argument(
        "--replace-existing-skill",
        action="store_true",
        help="Explicitly confirm replacement when the draft id already has a different active user-store record.",
    )
    candidate_promote_parser.add_argument(
        "--replace-existing-preference",
        action="store_true",
        help="Explicitly confirm replacement when the key already has a different active user-store preference.",
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
    checkpoint_parser.add_argument(
        "--task", default="", help="Task or project stage being checkpointed."
    )
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
    _add_resolution_arguments(resolve_parser, task_required=True)
    lint_parser = _add_root_command(subparsers, "lint", "Run governance lint rules.")
    lint_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return exit code 1 when any diagnostic is emitted, including warnings.",
    )

    args = parser.parse_args(argv)
    if args.command == "backup-verify":
        from .safety import verify_backup

        verification = verify_backup(args.archive)
        verification.update({"tool": "archmarshal", "stage": "backup_verify", "mode": "verified"})
        if not args.show_files:
            manifest = verification.pop("manifest")
            files = manifest.get("files") or []
            verification["manifest_summary"] = {
                key: manifest.get(key)
                for key in ("format", "scope", "project_root", "created_at", "reason", "file_count")
            }
            verification["file_preview"] = files[:100]
            verification["file_preview_truncated"] = len(files) > 100
        _print_json(verification, args.pretty)
        return 0
    if args.command == "backup-restore":
        from .safety import restore_backup

        payload = restore_backup(
            args.archive,
            args.destination,
            apply=args.apply,
            expected_plan=args.expect_plan,
            rebind_workspace=args.rebind_workspace,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "user-store-status":
        from .user_store import user_store_status

        payload = user_store_status(args.user_store)
        _print_json(payload, args.pretty)
        return 0 if payload.get("state") in {"absent", "initialized_empty", "healthy"} else 2
    if args.command == "user-store-init":
        from .user_store import (
            apply_user_store_initialization,
            plan_user_store_initialization,
        )

        if args.apply:
            plan = _required_reviewed_plan(args.plan_file)
            payload = apply_user_store_initialization(
                args.user_store,
                plan,
                expected_plan=_required_arg(args.expect_plan, "--expect-plan"),
            )
        else:
            plan = plan_user_store_initialization(args.user_store)
            payload = _user_store_plan_envelope(plan, "user_store_initialize")
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "user-store-rollback":
        from .user_store import (
            apply_user_store_forward_rollback,
            plan_user_store_forward_rollback,
        )

        if args.apply:
            plan = _required_reviewed_plan(args.plan_file)
            _verify_user_store_rollback_request(plan, target=args.to, reason=args.reason)
            expected_head = _expected_head_from_token(
                _required_arg(args.expect_head, "--expect-head")
            )
            payload = apply_user_store_forward_rollback(
                args.user_store,
                plan,
                expected_head=expected_head,
                expected_plan=_required_arg(args.expect_plan, "--expect-plan"),
            )
        else:
            plan = plan_user_store_forward_rollback(args.user_store, args.to, reason=args.reason)
            payload = _user_store_plan_envelope(plan, "user_store_rollback")
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "doctor":
        from .doctor import doctor_workspace

        payload = doctor_workspace(
            args.root,
            user_store=args.user_store,
            history_limit=args.history_limit,
        )
        _print_json(payload, args.pretty)
        return 2 if payload.get("state") == "error" else 0
    root = require_workspace_root(args.root)

    if args.command == "inventory":
        from .inventory import collect_inventory

        _print_json(collect_inventory(root).to_dict(), args.pretty)
        return 0
    if args.command == "lint":
        from .diagnostics import severity_counts
        from .lint import lint_workspace

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
        from .audit import audit_workspace

        _print_json(audit_workspace(root), args.pretty)
        return 0
    if args.command == "plan":
        from .planner import plan_workspace

        _print_json(plan_workspace(root), args.pretty)
        return 0
    if args.command == "skill-index-status":
        from .skill_index import skill_index_status

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
        from .adoption_tx import adoption_transaction_status

        payload = adoption_transaction_status(root)
        _print_json(payload, args.pretty)
        return 0 if payload.get("state") == "none" else 2
    if args.command == "adoption-recover":
        from .adoption_tx import recover_adoption_transaction

        payload = recover_adoption_transaction(
            root,
            apply=args.apply,
            expected_transaction=args.expect_transaction,
            expected_plan=args.expect_plan,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "skill-index-rollback":
        from .skill_index import rollback_skill_index

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
    if args.command == "skill-review":
        from .skill_review import load_reviewed_skill_plan, review_workspace_skill

        payload = review_workspace_skill(
            root,
            args.source,
            decision=args.decision,
            reviewer=args.reviewer,
            reason=args.reason,
            allow_global_policy=args.allow_global_policy,
            expected_head=args.expect_head,
            expected_plan=args.expect_plan,
            reviewed_plan=(load_reviewed_skill_plan(args.plan_file) if args.plan_file else None),
            apply=args.apply,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "candidate-review":
        from .promotion import review_learning_candidate

        payload = review_learning_candidate(
            root,
            args.pack,
            args.candidate,
            args.user_store,
            decision=args.decision,
            reason=args.reason,
            expected_head_token=args.expect_head,
            expected_plan=args.expect_plan,
            reviewed_plan=_optional_reviewed_plan(args.plan_file),
            apply=args.apply,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "candidate-draft":
        from .candidate_draft import (
            candidate_to_skill_draft,
            load_candidate_draft_preview,
        )

        payload = candidate_to_skill_draft(
            root,
            args.pack,
            args.candidate,
            args.user_store,
            args.destination,
            reviewed_preview=(
                load_candidate_draft_preview(args.plan_file) if args.plan_file else None
            ),
            expected_head_token=args.expect_head,
            expected_plan=args.expect_plan,
            apply=args.apply,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "candidate-promote":
        from .promotion import promote_learning_candidate

        payload = promote_learning_candidate(
            root,
            args.pack,
            args.candidate,
            args.user_store,
            draft=args.draft,
            reason=args.reason,
            expected_head_token=args.expect_head,
            expected_plan=args.expect_plan,
            reviewed_plan=_optional_reviewed_plan(args.plan_file),
            replace_existing_skill=args.replace_existing_skill,
            replace_existing_preference=args.replace_existing_preference,
            apply=args.apply,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "init":
        from .lifecycle import initialize_workspace

        payload = initialize_workspace(
            root,
            apply=args.apply,
            tags=args.tag,
            backup_scope=args.backup_scope,
            expected_plan=args.expect_plan,
            skill_roots=args.skill_root,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "adopt":
        from .adoption import adopt_workspace

        payload = adopt_workspace(
            root,
            apply=args.apply,
            tags=args.tag,
            backup_scope=args.backup_scope,
            expected_plan=args.expect_plan,
            skill_roots=args.skill_root,
        )
        _print_json(payload, args.pretty)
        return _payload_exit_code(payload)
    if args.command == "start":
        from .adoption import adopt_workspace
        from .lifecycle import start_workspace

        if args.apply:
            adoption = adopt_workspace(
                root,
                apply=True,
                tags=args.tag,
                backup_scope=args.backup_scope,
                expected_plan=args.expect_plan,
                skill_roots=args.skill_root,
            )
            payload = start_workspace(
                root,
                task=args.task,
                user_store=args.user_store,
                tags=args.tag,
                backup_scope=args.backup_scope,
                skill_roots=args.skill_root,
            )
            payload["adoption"] = adoption
            _mark_start_apply(payload, adoption)
            if adoption["mode"] in {"overlay_applied", "overlay_synced", "review_required"}:
                payload["mode"] = adoption["mode"]
            _print_json(payload, args.pretty)
            return _payload_exit_code(adoption)
        else:
            _print_json(
                start_workspace(
                    root,
                    task=args.task,
                    user_store=args.user_store,
                    tags=args.tag,
                    backup_scope=args.backup_scope,
                    skill_roots=args.skill_root,
                ),
                args.pretty,
            )
        return 0
    if args.command == "catalog":
        from .catalog import catalog_projects

        _print_json(
            catalog_projects([root, *[Path(item) for item in args.include_root]], tags=args.tag),
            args.pretty,
        )
        return 0
    if args.command == "learn":
        from .learning import learn_from_projects

        reviewed = _optional_reviewed_plan(args.plan_file)
        if isinstance(reviewed, dict) and isinstance(reviewed.get("learning_plan"), dict):
            reviewed = reviewed["learning_plan"]
        _print_json(
            learn_from_projects(
                [root, *[Path(item) for item in args.include_root]],
                reviewed_plan=reviewed,
                expected_plan=args.expect_plan,
                apply=args.apply,
            ),
            args.pretty,
        )
        return 0
    if args.command == "end":
        if args.level:
            from .session import record_closeout

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
            from .lifecycle import end_workspace

            _print_json(end_workspace(root, args.used_skill), args.pretty)
        return 0
    if args.command == "checkpoint":
        from .checkpoint import checkpoint_workspace

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
        from .closeout import closeout_workspace

        _print_json(closeout_workspace(root, args.used_skill), args.pretty)
        return 0
    if args.command == "resolve":
        from .resolver import resolve_workspace

        _print_json(resolve_workspace(root, args.task, user_store=args.user_store), args.pretty)
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


def start_main(argv: list[str] | None = None) -> int:
    return _guard_cli(_start_main_impl, argv)


def _start_main_impl(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(prog="archmarshal-start")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("root", nargs="?", default=".", help="Workspace root to inspect.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    _add_adoption_arguments(parser)
    _add_resolution_arguments(parser, task_required=False)
    args = parser.parse_args(argv)
    from .adoption import adopt_workspace
    from .lifecycle import start_workspace

    root = require_workspace_root(args.root)
    payload = start_workspace(
        root,
        task=args.task,
        user_store=args.user_store,
        tags=args.tag,
        backup_scope=args.backup_scope,
        skill_roots=args.skill_root,
    )
    if args.apply:
        payload["adoption"] = adopt_workspace(
            root,
            apply=True,
            tags=args.tag,
            backup_scope=args.backup_scope,
            expected_plan=args.expect_plan,
            skill_roots=args.skill_root,
        )
        payload = start_workspace(
            root,
            task=args.task,
            user_store=args.user_store,
            tags=args.tag,
            backup_scope=args.backup_scope,
            skill_roots=args.skill_root,
        ) | {"adoption": payload["adoption"]}
        _mark_start_apply(payload, payload["adoption"])
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
    parser = _ArgumentParser(prog="archmarshal-end")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
        from .session import record_closeout

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
        from .lifecycle import end_workspace

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
        "--skill-root",
        action="append",
        default=[],
        help=(
            "Additional project-relative source Skill root. Repeat as needed; "
            "defaults and workspace-declared roots remain in scope."
        ),
    )
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


def _add_resolution_arguments(
    parser: argparse.ArgumentParser,
    *,
    task_required: bool,
) -> None:
    parser.add_argument(
        "--task",
        required=task_required,
        default=None,
        help="Task description used for read-only Skill and context resolution.",
    )
    parser.add_argument(
        "--user-store",
        help="Optional isolated ArchMarshal user Skill store to resolve alongside the project.",
    )


def _add_user_store_plan_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_head: bool,
) -> None:
    parser.add_argument("user_store", help="User Skill store root.")
    parser.add_argument("--apply", action="store_true", help="Apply the exact saved preview plan.")
    parser.add_argument(
        "--plan-file",
        help="UTF-8, UTF-8 BOM, or BOM-marked UTF-16 JSON reviewed preview; required with --apply.",
    )
    parser.add_argument(
        "--expect-plan", help="Exact plan digest from the saved preview; required with --apply."
    )
    if include_head:
        parser.add_argument(
            "--expect-head",
            help="Exact HEAD from preview, or 'none'; required with --apply.",
        )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")


def _add_candidate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pack",
        required=True,
        help="Committed pack directory under the owned project's .agent/inbox/learning/.",
    )
    parser.add_argument("--candidate", required=True, help="Exact candidate id from the pack.")
    parser.add_argument("--user-store", required=True, help="Owned user Skill store root.")
    parser.add_argument(
        "--reason", default="", help="Short decision reason; do not include secrets."
    )
    parser.add_argument("--apply", action="store_true", help="Apply the exact saved preview plan.")
    parser.add_argument(
        "--plan-file",
        help="UTF-8, UTF-8 BOM, or BOM-marked UTF-16 JSON reviewed preview; required with --apply.",
    )
    parser.add_argument(
        "--expect-head",
        help="Exact user-store HEAD from preview, or 'none'; required with --apply.",
    )
    parser.add_argument(
        "--expect-plan", help="Exact plan digest from the saved preview; required with --apply."
    )


def _add_candidate_draft_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pack",
        required=True,
        help="Committed pack directory under the owned project's .agent/inbox/learning/.",
    )
    parser.add_argument("--candidate", required=True, help="Exact common-Skill candidate id.")
    parser.add_argument("--user-store", required=True, help="Owned user Skill store root.")
    parser.add_argument(
        "--destination",
        required=True,
        help="Absent draft-envelope path under an existing real parent directory.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Create only the exact saved draft preview."
    )
    parser.add_argument(
        "--plan-file",
        help="Complete saved candidate-draft preview JSON; required with --apply.",
    )
    parser.add_argument(
        "--expect-head",
        help="Exact accepted user-store HEAD from preview; required with --apply.",
    )
    parser.add_argument(
        "--expect-plan", help="Exact plan digest from the saved preview; required with --apply."
    )


def _add_recording_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--level", choices=CLOSEOUT_LEVELS, help="Closeout recording depth.")
    parser.add_argument(
        "--apply", action="store_true", help="Write a new append-only closeout directory."
    )
    parser.add_argument(
        "--expect-plan",
        help="Exact closeout plan digest from a reviewed preview; required before writing.",
    )
    parser.add_argument("--summary", default="", help="Project or phase outcome summary.")
    parser.add_argument(
        "--step", action="append", default=[], help="Ordered work step. Repeat as needed."
    )
    parser.add_argument(
        "--script", action="append", default=[], help="Key script path. Repeat as needed."
    )
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


def _user_store_plan_envelope(plan: dict[str, Any], stage: str) -> dict[str, Any]:
    return {
        "tool": "archmarshal",
        "stage": stage,
        "mode": "propose_only",
        "expected_head": plan.get("expected_head"),
        "expected_head_token": plan.get("expected_head") or "none",
        "plan_digest": plan["plan_digest"],
        "apply_precondition": (
            "--plan-file <saved-preview.json> --expect-plan <plan_digest> --apply"
            if plan.get("kind") == "initialize"
            else (
                "--plan-file <saved-preview.json> --expect-head <head|none> "
                "--expect-plan <plan_digest> --apply"
            )
        ),
        "user_store_plan": plan,
        "source_mutation": False,
        "notes": [
            "Save and review this complete JSON preview before apply.",
            "Apply fails closed if the plan, current HEAD, root identity, or package bytes change.",
        ],
    }


def _optional_reviewed_plan(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    from .promotion import load_reviewed_plan

    return load_reviewed_plan(path)


def _required_reviewed_plan(path: str | None) -> dict[str, Any]:
    if not path:
        raise ArchMarshalError(
            "reviewed_plan_required",
            "Apply requires --plan-file with the complete saved preview.",
        )
    from .promotion import load_reviewed_plan

    return load_reviewed_plan(path)


def _required_arg(value: str | None, flag: str) -> str:
    if not value:
        raise ArchMarshalError(
            "reviewed_plan_required", f"Apply requires {flag} from the saved preview."
        )
    return value


def _expected_head_from_token(token: str) -> str | None:
    if token == "none":
        return None
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise ArchMarshalError(
            "expected_head_invalid", "Expected HEAD must be a lowercase SHA-256 digest or 'none'."
        )
    return token


def _verify_user_store_rollback_request(
    plan: dict[str, Any],
    *,
    target: str,
    reason: str,
) -> None:
    generation = plan.get("generation")
    operation = generation.get("operation") if isinstance(generation, dict) else None
    if (
        plan.get("kind") != "rollback"
        or not isinstance(operation, dict)
        or operation.get("kind") != "rollback"
        or operation.get("target") != target
        or operation.get("reason") != reason.strip()
    ):
        raise ArchMarshalError(
            "reviewed_plan_argument_mismatch",
            "Apply target and reason must exactly match the saved rollback preview.",
        )


def _payload_exit_code(payload: dict[str, Any]) -> int:
    return (
        2
        if payload.get("mode") in {"blocked", "review_required", "recovery_required"}
        or payload.get("blocked") is True
        else 0
    )


def _mark_start_apply(payload: dict[str, Any], adoption: dict[str, Any]) -> None:
    applied = adoption.get("mode") in {"overlay_applied", "overlay_synced"}
    payload["mutation"] = {
        "requested": True,
        "performed": applied,
        "scope": "archmarshal_control_plane_only" if applied else "none",
        "source_files_modified": False,
    }
    notes = [
        item
        for item in payload.get("notes") or []
        if item != "Start is read-only and does not modify files."
    ]
    notes.insert(
        0,
        (
            "Start --apply created only the reviewed ArchMarshal control-plane and immutable index changes."
            if applied
            else "Start --apply requested a change, but no control-plane mutation was completed."
        ),
    )
    payload["notes"] = notes


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
    if isinstance(payload, dict):
        payload = dict(payload)
        schema_version = payload.pop("api_version", None)
        payload = {"api_version": "archmarshal-cli-v1", **payload}
        if schema_version is not None:
            payload["payload_schema_version"] = schema_version
    print(
        json.dumps(payload, default=_json_default, indent=2 if pretty else None, sort_keys=True),
        file=stream,
    )


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


__all__ = ["end_main", "main", "start_main"]
