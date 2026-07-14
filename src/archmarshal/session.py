from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .closeout import closeout_workspace
from .errors import require_workspace_root
from .safety import create_text_exclusive, ensure_managed_path, sha256_file, unique_path

CLOSEOUT_LEVELS = ("quick", "standard", "reproducible")
MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024


def record_closeout(
    root: Path | str,
    *,
    level: str,
    apply: bool = False,
    summary: str = "",
    steps: list[str] | None = None,
    scripts: list[str] | None = None,
    commands: list[str] | None = None,
    tags: list[str] | None = None,
    used_skills: list[str] | None = None,
    shell: str | None = None,
) -> dict[str, Any]:
    root_path = require_workspace_root(root)
    if level not in CLOSEOUT_LEVELS:
        raise ValueError(f"level must be one of: {', '.join(CLOSEOUT_LEVELS)}")
    shell = shell or ("powershell" if os.name == "nt" else "bash")
    if shell not in {"powershell", "bash"}:
        raise ValueError("shell must be 'powershell' or 'bash'")
    steps = [item.strip() for item in steps or [] if item.strip()]
    commands = [item.rstrip() for item in commands or [] if item.strip()]
    used_skills = [item.strip() for item in used_skills or [] if item.strip()]
    normalized_tags = sorted(
        {_human_slug(item, fallback="") for item in tags or [] if item.strip()},
        key=str.casefold,
    )
    script_records, script_errors = _script_records(root_path, scripts or [])
    sensitive_errors = _sensitive_text_errors(
        summary=summary,
        steps=steps,
        commands=commands,
        tags=normalized_tags,
        used_skills=used_skills,
    )
    script_errors.extend(sensitive_errors)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    topic = _human_slug(summary, fallback="project-closeout")[:48]
    base_dir = root_path / ".agent" / "history" / now.strftime("%Y/%m/%d")
    session_dir = unique_path(base_dir / f"{timestamp}-{topic}-{level}")
    ensure_managed_path(root_path, session_dir, purpose="Closeout session directory")
    git = _git_snapshot(root_path)
    environment = _environment_snapshot(root_path)
    readiness_gaps = _readiness_gaps(level, summary, steps, script_records, commands)
    closeout = closeout_workspace(root_path, used_skills)
    session = {
        "format": "archmarshal-session-v1",
        "recorded_at": now.isoformat(),
        "level": level,
        "summary": summary.strip(),
        "tags": normalized_tags,
        "used_skills": used_skills,
        "steps": steps if level != "quick" else [],
        "key_scripts": script_records if level != "quick" else [],
        "commands": commands if level == "reproducible" else [],
        "git": git,
        "environment": environment if level == "reproducible" else {},
        "reproducibility": {
            "ready": not readiness_gaps,
            "gaps": readiness_gaps,
            "execution_validated": False,
            "environment_variables_captured": False,
            "known_inline_secret_patterns_blocked": True,
            "selected_script_content_may_be_sensitive": bool(script_records),
            "existing_files_modified": False,
        },
        "governance": {
            "recording_policy": closeout["recording_policy"],
            "diagnostic_summary": closeout["diagnostic_summary"],
            "candidate_memory_updates": closeout["candidate_memory_updates"],
            "promotion_candidates": closeout["promotion_candidates"],
        },
    }
    files = _session_files(session, shell)
    operations = [
        {
            "action": "create",
            "path": (session_dir / relative).relative_to(root_path).as_posix(),
            "overwrite": False,
        }
        for relative in files
    ]
    if level == "reproducible":
        operations.extend(
            {
                "action": "copy",
                "path": (session_dir / "scripts" / record["snapshot_name"])
                .relative_to(root_path)
                .as_posix(),
                "source": record["path"],
                "overwrite": False,
            }
            for record in script_records
        )
    payload = {
        "tool": "archmarshal",
        "stage": "record_closeout",
        "root": str(root_path),
        "mode": "propose_only",
        "level": level,
        "session_dir": session_dir.relative_to(root_path).as_posix(),
        "recording_ready": not readiness_gaps,
        "reproducibility_ready": not readiness_gaps,
        "reproduction_evidence_ready": not readiness_gaps,
        "reproducibility_gaps": readiness_gaps,
        "script_errors": script_errors,
        "operations": operations,
        "notes": [
            "Closeout writes only to a new date-organized session directory.",
            "No existing project or skill file is overwritten, moved, renamed, or deleted.",
            "Environment variables are not captured; known inline-secret patterns are blocked.",
            "User-selected summaries, steps, and script snapshots may still contain sensitive content and must be reviewed.",
            "Generated run scripts are references and must be reviewed before execution.",
            "Readiness means required evidence is present; commands were not executed or validated.",
        ],
    }
    if script_errors:
        payload["mode"] = "blocked"
        return payload
    if apply and readiness_gaps:
        payload["mode"] = "blocked"
        payload["notes"].append(
            "--apply was blocked because the requested recording level is missing required evidence."
        )
        return payload
    if not apply:
        return payload

    created: list[Path] = []
    try:
        session_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        payload["mode"] = "blocked"
        payload["notes"].append(
            "The session directory was claimed concurrently; rerun to receive a new path."
        )
        return payload
    try:
        for relative, content in files.items():
            target = session_dir / relative
            create_text_exclusive(target, content)
            created.append(target)
        if level == "reproducible":
            for record in script_records:
                source = root_path / record["path"]
                target = session_dir / "scripts" / record["snapshot_name"]
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as source_handle, target.open("xb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
                created.append(target)
                if sha256_file(target) != record["sha256"]:
                    raise OSError(f"Snapshot hash mismatch for {record['path']}")
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        shutil.rmtree(session_dir, ignore_errors=True)
        raise

    payload["mode"] = "append_only_applied"
    payload["created"] = [path.relative_to(root_path).as_posix() for path in created]
    return payload


def _session_files(session: dict[str, Any], shell: str) -> dict[str, str]:
    files = {
        "SUMMARY.md": _summary_markdown(session),
        "session.yaml": yaml.safe_dump(session, sort_keys=False, allow_unicode=True),
    }
    if session["level"] in {"standard", "reproducible"}:
        files["STEPS.md"] = _steps_markdown(session)
    if session["level"] == "reproducible":
        files["reproduction.yaml"] = yaml.safe_dump(
            {
                "git": session["git"],
                "environment": session["environment"],
                "key_scripts": session["key_scripts"],
                "commands": session["commands"],
                "readiness": session["reproducibility"],
            },
            sort_keys=False,
            allow_unicode=True,
        )
        if session["commands"]:
            extension = "ps1" if shell == "powershell" else "sh"
            files[f"run.{extension}"] = _run_script(session["commands"], shell)
    return files


def _summary_markdown(session: dict[str, Any]) -> str:
    lines = [
        "# Project closeout",
        "",
        f"- Recorded: {session['recorded_at']}",
        f"- Level: `{session['level']}`",
        f"- Tags: {', '.join(session['tags']) or 'none'}",
        f"- Git commit: `{session['git'].get('commit') or 'unavailable'}`",
        f"- Reproducibility ready: `{str(session['reproducibility']['ready']).lower()}`",
        "",
        "## Summary",
        "",
        session["summary"] or "No summary was supplied.",
        "",
    ]
    if session["reproducibility"]["gaps"]:
        lines.extend(
            [
                "## Reproducibility gaps",
                "",
                *[f"- {item}" for item in session["reproducibility"]["gaps"]],
                "",
            ]
        )
    lines.extend(
        [
            "## Safety",
            "",
            "This session is append-only. It did not overwrite or reorganize existing project",
            "files or skills. It did not capture environment variables, blocked known inline-secret",
            "patterns, and still requires review of user-selected text and script snapshots.",
            "",
        ]
    )
    return "\n".join(lines)


def _steps_markdown(session: dict[str, Any]) -> str:
    lines = ["# Recorded steps", ""]
    if session["steps"]:
        lines.extend(f"{index}. {step}" for index, step in enumerate(session["steps"], start=1))
    else:
        lines.append("No explicit steps were supplied.")
    lines.extend(["", "## Key scripts", ""])
    if session["key_scripts"]:
        lines.extend(
            f"- `{item['path']}` · SHA-256 `{item['sha256']}`" for item in session["key_scripts"]
        )
    else:
        lines.append("No key scripts were supplied.")
    lines.append("")
    return "\n".join(lines)


def _run_script(commands: list[str], shell: str) -> str:
    warning = "Generated reference script. Review every command before running."
    if shell == "powershell":
        return "\n".join([f"# {warning}", '$ErrorActionPreference = "Stop"', "", *commands, ""])
    return "\n".join(["#!/usr/bin/env bash", f"# {warning}", "set -euo pipefail", "", *commands, ""])


def _script_records(root: Path, scripts: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for value in scripts:
        candidate = (root / value).resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            errors.append(f"Script escapes project root: {value}")
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if not candidate.is_file():
            errors.append(f"Script does not exist or is not a file: {relative}")
            continue
        size = candidate.stat().st_size
        if size > MAX_SNAPSHOT_BYTES:
            errors.append(f"Script exceeds the {MAX_SNAPSHOT_BYTES}-byte snapshot limit: {relative}")
            continue
        suffix = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8]
        records.append(
            {
                "path": relative,
                "bytes": size,
                "sha256": sha256_file(candidate),
                "snapshot_name": f"{candidate.stem}-{suffix}{candidate.suffix}",
            }
        )
    return records, errors


def _readiness_gaps(
    level: str,
    summary: str,
    steps: list[str],
    scripts: list[dict[str, Any]],
    commands: list[str],
) -> list[str]:
    gaps: list[str] = []
    if not summary.strip():
        gaps.append(f"A summary is required for {level} closeout.")
    if level in {"standard", "reproducible"} and not steps:
        gaps.append("Record the ordered trajectory with at least one --step.")
    if level == "reproducible" and not scripts:
        gaps.append("Record and snapshot at least one key --script.")
    if level == "reproducible" and not commands:
        gaps.append("Record at least one exact rerun --command.")
    return gaps


def _sensitive_text_errors(**fields: object) -> list[str]:
    token_patterns = (
        r"https?://[^\s/:]+:[^\s/@]+@",
        r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
        r"\bglpat-[A-Za-z0-9_-]{20,}\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bsk-[A-Za-z0-9_-]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    )
    assignments = re.compile(
        r"(?i)(?:(?:--?)(?:password|passwd|token|secret|api[-_]?key)\s*(?:=|\s)|"
        r"(?:password|passwd|token|secret|api[-_]?key)\s*=)\s*([^\s]+)"
    )
    errors: list[str] = []
    for field, raw_values in fields.items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        for index, raw in enumerate(values, start=1):
            value = str(raw or "")
            has_token = any(re.search(pattern, value) for pattern in token_patterns)
            has_assignment = any(
                not _is_environment_reference(match.group(1))
                for match in assignments.finditer(value)
            )
            if has_token or has_assignment:
                errors.append(
                    f"{field} item {index} appears to contain an inline secret; use an environment-variable reference instead."
                )
    return errors


def _is_environment_reference(value: str) -> bool:
    normalized = value.strip("\"'")
    return (
        normalized.startswith(("$", "%", "${", "$env:"))
        or normalized.endswith("%") and normalized.startswith("%")
    )


def _git_snapshot(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    status = run("status", "--porcelain=v1")
    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(status),
        "changed_paths": [line[3:] for line in status.splitlines()] if status else [],
    }


def _environment_snapshot(root: Path) -> dict[str, Any]:
    dependency_files = []
    for name in ("pyproject.toml", "requirements.txt", "uv.lock", "poetry.lock", "package-lock.json"):
        path = root / name
        if path.is_file():
            dependency_files.append(
                {"path": name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            )
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "dependency_files": dependency_files,
        "environment_variables_captured": False,
    }


def _human_slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^\w]+", "-", value.strip().casefold(), flags=re.UNICODE).strip("-_")
    return slug or fallback


__all__ = ["CLOSEOUT_LEVELS", "record_closeout"]
