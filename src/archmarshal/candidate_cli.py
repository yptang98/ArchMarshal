"""Lazy registration and dispatch for candidate review workflows."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

COMMANDS = {"candidate-review", "candidate-draft", "candidate-promote", "candidate-compare"}


def register(subparsers: Any, add_root: Callable, add_candidate: Callable, add_draft: Callable) -> None:
    candidate_review_parser = add_root(
        subparsers,
        "candidate-review",
        "Record a review decision for an exact committed learning candidate.",
    )
    add_candidate(candidate_review_parser)
    candidate_review_parser.add_argument(
        "--decision", required=True, choices=("accept", "reject", "defer")
    )
    candidate_draft_parser = add_root(
        subparsers,
        "candidate-draft",
        "Create a reviewed, disabled Skill draft envelope from an accepted candidate.",
    )
    add_draft(candidate_draft_parser)
    candidate_promote_parser = add_root(
        subparsers,
        "candidate-promote",
        "Promote an exact committed candidate into the isolated user store.",
    )
    add_candidate(candidate_promote_parser)
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
    compare = add_root(subparsers, "candidate-compare", "Compare duplicate or conflicting committed candidates without changing evidence.")
    compare.add_argument("--pack", action="append", required=True, help="Explicit committed pack; repeat 2 to 20 times.")


def dispatch(args: Any, root: Path) -> dict[str, Any]:
    if args.command == "candidate-compare":
        from .candidate_evidence import compare_candidates
        return compare_candidates(root, args.pack)
    from .promotion import load_reviewed_plan
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
            reviewed_plan=(load_reviewed_plan(args.plan_file) if args.plan_file else None),
            apply=args.apply,
        )
        return payload
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
        return payload
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
            reviewed_plan=(load_reviewed_plan(args.plan_file) if args.plan_file else None),
            replace_existing_skill=args.replace_existing_skill,
            replace_existing_preference=args.replace_existing_preference,
            apply=args.apply,
        )
        return payload
    raise ValueError("Unknown candidate command")
