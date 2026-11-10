"""Lazy CLI registration/dispatch for device sync and parallel workstreams."""
from __future__ import annotations

from typing import Any

COMMANDS = {"device-init", "sync-status", "sync-push", "sync-pull", "sync-install", "context-save",
            "context-merge", "context-resume", "context-read"}


def register(subparsers: Any) -> None:
    for name in sorted(COMMANDS):
        parser = subparsers.add_parser(name, help={
            "device-init": "Create this machine's persistent device identity.",
            "sync-status": "List remote Skill variants and device origins.",
            "sync-push": "Preview/upload one indexed Skill with device provenance.",
            "sync-pull": "Preview/import an exact Skill snapshot into quarantine.",
            "sync-install": "Copy a reviewed quarantine snapshot to a new explicit Skill directory.",
            "context-save": "Save an append-only workstream handoff.",
            "context-merge": "Record a synthesis citing multiple preserved workstreams.",
            "context-resume": "Restore workstream context by directory or known GitHub address.",
            "context-read": "Read one exact verified historical or omitted handoff.",
        }[name])
        parser.add_argument("--pretty", action="store_true")
        if name not in {"sync-status", "context-resume", "context-read"}:
            parser.add_argument("--apply", action="store_true")
            parser.add_argument("--expect-plan")
            parser.add_argument("--device-file", help="Machine-local device profile path.")
        if name == "device-init":
            parser.add_argument("--name", required=True)
        elif name == "sync-install":
            parser.add_argument("root")
            parser.add_argument("--object", required=True)
            parser.add_argument("--destination", required=True)
        elif name.startswith("sync-"):
            parser.add_argument("--remote", required=True, help="GitHub owner/repo or folder path.")
            parser.add_argument("--backend", default="github")
            parser.add_argument("--branch", help="GitHub branch; defaults to repository default.")
            if name != "sync-status":
                parser.add_argument("root")
            if name == "sync-push":
                parser.add_argument("--skill", required=True, help="Exact indexed relative package path.")
                parser.add_argument("--allow-public", action="store_true")
            if name == "sync-pull":
                parser.add_argument("--object", required=True, help="Exact object ID from sync-status.")
        else:
            parser.add_argument("root", help="Project directory; resume also accepts known GitHub URL.")
            parser.add_argument("--include-root", action="append", default=[])
            parser.add_argument("--task", default="")
            if name == "context-read":
                parser.add_argument("--record", required=True)
            elif name == "context-resume":
                parser.add_argument("--max-chars", type=int, default=24000)
            else:
                parser.add_argument("--stream", required=True, help="Stable window/workstream name.")
                parser.add_argument("--summary", required=True)
                parser.add_argument("--status", choices=["active", "completed"], default="active")
                for option in ("decision", "next-step", "risk", "evidence", "file", "integrate",
                               "constraint", "rejected-option"):
                    parser.add_argument("--" + option, action="append", default=[])


def dispatch(args: Any) -> dict[str, Any]:
    if args.command == "device-init":
        from .portable import init_device
        return init_device(args.name, path=args.device_file, apply=args.apply,
                           expected_plan=args.expect_plan)
    if args.command == "sync-install":
        from .skill_sync import install_skill
        return install_skill(args.root, args.object, args.destination, apply=args.apply,
                             expected_plan=args.expect_plan)
    if args.command.startswith("sync-"):
        from .skill_sync import pull_skill, push_skill, sync_status
        from .sync_transport import transport
        store = transport(args.remote, backend=args.backend, branch=args.branch)
        if args.command == "sync-status":
            return sync_status(store)
        common = {"device_path": args.device_file, "apply": args.apply,
                  "expected_plan": args.expect_plan}
        if args.command == "sync-push":
            return push_skill(args.root, args.skill, store, allow_public=args.allow_public, **common)
        return pull_skill(args.root, args.object, store, **common)
    from .handoff import read_context, resume_context, save_context
    if args.command == "context-read":
        return read_context(args.root, args.record)
    if args.command == "context-resume":
        return resume_context(args.root, include_roots=args.include_root,
                              task=args.task, max_chars=args.max_chars)
    if args.command == "context-merge" and len(set(args.integrate)) < 2:
        from .portable import fail
        fail("context-merge requires at least two distinct --integrate record IDs.")
    return save_context(args.root, stream=args.stream, summary=args.summary, task=args.task,
                        decisions=args.decision, next_steps=args.next_step, risks=args.risk,
                        evidence=args.evidence, files=args.file, status=args.status,
                        constraints=args.constraint, rejected_options=args.rejected_option,
                        integrates=args.integrate, include_roots=args.include_root,
                        device_path=args.device_file, apply=args.apply,
                        expected_plan=args.expect_plan)
