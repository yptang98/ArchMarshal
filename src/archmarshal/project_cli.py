"""Lightweight CLI routing for host projects and external deliverables."""
from __future__ import annotations

from typing import Any

COMMANDS = {"project-status", "project-record", "skill-host-status", "skill-host-probe"}


def register(subparsers: Any) -> None:
    for name in sorted(COMMANDS):
        parser = subparsers.add_parser(name, help="Inspect or record a host project outside its working files.")
        parser.add_argument("root", help="Explicit Codex project root; use the current directory when unassociated.")
        parser.add_argument("--pretty", action="store_true")
        if name == "skill-host-probe":
            parser.add_argument("--codex-binary", required=True, help="Absolute native Codex executable.")
            parser.add_argument("--host-id", default="local")
            parser.add_argument("--timeout", type=float, default=30)
            continue
        if name == "skill-host-status":
            parser.add_argument("--work-root")
            parser.add_argument("--host-id", default="local")
            parser.add_argument("--host-snapshot", help="Saved observed skills/list response envelope.")
            parser.add_argument("--max-age", type=int, default=300)
            parser.add_argument("--verify-source", action="append", default=[])
            parser.add_argument("--installed-skill", action="append", default=[],
                                help="Explicit SOURCE=ABSOLUTE/SKILL.md binding from an authorized installation.")
            parser.add_argument("--limit", type=int, default=100)
            continue
        parser.add_argument("--store-root", required=True, help="Authorized external record container.")
        if name == "project-status":
            parser.add_argument("--limit", type=int, default=5)
            parser.add_argument("--verify-artifacts", action="store_true")
        else:
            parser.add_argument("--work-root", help="Actual current checkout or worktree; defaults to root.")
            parser.add_argument("--project-id", default="")
            parser.add_argument("--thread-id", default="")
            parser.add_argument("--host-id", default="local")
            parser.add_argument("--kind", choices=("start", "note", "close"), required=True)
            parser.add_argument("--summary", required=True)
            parser.add_argument("--task", default="")
            for name in ("decision", "evidence", "next-step", "artifact"):
                parser.add_argument("--" + name, action="append", default=[])
            parser.add_argument("--plan-file", help="Complete saved project-record preview.")
            parser.add_argument("--expect-plan")
            parser.add_argument("--apply", action="store_true")


def dispatch(args: Any) -> dict[str, Any]:
    from pathlib import Path

    from .portable import decode, read

    if args.command == "skill-host-probe":
        from .codex_skill_probe import probe_codex_skills

        return probe_codex_skills(args.root, args.codex_binary, host_id=args.host_id, timeout=args.timeout)
    if args.command == "skill-host-status":
        from .host_skills import MAX_OBSERVATION_BYTES, reconcile_host_skills
        from .portable import fail

        snapshot = decode(read(Path(args.host_snapshot), MAX_OBSERVATION_BYTES)) if args.host_snapshot else None
        installed = {}
        for value in args.installed_skill:
            source, separator, target = value.partition("=")
            if not separator or source in installed:
                fail("Installed Skill bindings require unique SOURCE=ABSOLUTE/SKILL.md entries.", "host_skills_source_invalid")
            installed[source] = target
        return reconcile_host_skills(args.root, work_root=args.work_root, snapshot=snapshot,
                                     host_id=args.host_id, max_age=args.max_age,
                                     verify_sources=args.verify_source, installed_paths=installed, limit=args.limit)
    from .external_records import MAX_RECORD_BYTES, project_status, record_project

    if args.command == "project-status":
        return project_status(args.root, args.store_root, limit=args.limit,
                              verify_artifacts=args.verify_artifacts)
    reviewed = None
    if args.plan_file:
        reviewed = decode(read(Path(args.plan_file), MAX_RECORD_BYTES))
        reviewed = reviewed.get("plan", reviewed)
    return record_project(args.root, args.store_root, work_root=args.work_root,
                          project_id=args.project_id, thread_id=args.thread_id, host_id=args.host_id,
                          kind=args.kind, summary=args.summary, task=args.task,
                          decisions=args.decision, evidence=args.evidence, next_steps=args.next_step,
                          artifacts=args.artifact, reviewed_plan=reviewed,
                          expected_plan=args.expect_plan, apply=args.apply)
