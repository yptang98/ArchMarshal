from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .closeout import closeout_workspace
from .errors import ArchMarshalError, require_workspace_root
from .io import load_yaml_safe, read_bytes_safe
from .ownership import require_owned_workspace
from .safety import (
    create_bytes_exclusive,
    create_text_exclusive,
    ensure_managed_path,
    files_below_no_links,
    is_link_or_reparse,
    sha256_file,
    unique_path,
)
from .workspace_lock import workspace_mutation_lock

CLOSEOUT_LEVELS = ("quick", "standard", "reproducible")
MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024
MAX_SESSION_FILES = 1_000
MAX_SESSION_COMMIT_BYTES = 4 * 1024 * 1024
SESSION_FORMAT = "archmarshal-session-v2"
SESSION_COMMIT_FORMAT = "archmarshal-session-commit-v1"


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
    expected_plan: str | None = None,
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
    topic = _human_slug(summary, fallback="project-closeout")[:48]
    git = _git_snapshot(root_path)
    environment = _environment_snapshot(root_path)
    readiness_gaps = _readiness_gaps(level, summary, steps, script_records, commands)
    closeout = closeout_workspace(root_path, used_skills)
    session = {
        "format": SESSION_FORMAT,
        "recorded_on": now.date().isoformat(),
        "level": level,
        "summary": summary.strip(),
        "tags": normalized_tags,
        "used_skills": used_skills,
        "skill_usage": closeout["used_skills"],
        "steps": steps if level != "quick" else [],
        "key_scripts": script_records if level != "quick" else [],
        "commands": commands if level == "reproducible" else [],
        "git": git,
        "environment": environment if level == "reproducible" else {},
        "reproducibility": {
            "ready": not readiness_gaps,
            "evidence_complete": not readiness_gaps,
            "execution_observed": False,
            "gaps": readiness_gaps,
            "execution_validated": False,
            "reproducible_claim": False,
            "claim_mode": "reference_only",
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
    session_key = _session_intent_digest(session, shell)[:12]
    base_dir = root_path / ".agent" / "history" / now.strftime("%Y/%m/%d")
    session_dir = unique_path(base_dir / f"{topic}-{level}-{session_key}")
    ensure_managed_path(root_path, session_dir, purpose="Closeout session directory")
    planned_records = _planned_session_records(files, script_records, level=level)
    commit_content = _session_commit_bytes(session["recorded_on"], planned_records)
    plan_digest = _session_plan_digest(
        root_path,
        session,
        shell,
        session_dir,
        files,
        script_records,
        commit_content,
    )
    operations = [
        {
            "action": "create",
            "path": (session_dir / relative).relative_to(root_path).as_posix(),
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "overwrite": False,
        }
        for relative, content in files.items()
    ]
    operations.append(
        {
            "action": "commit_manifest_last",
            "path": (session_dir / "COMMITTED.json").relative_to(root_path).as_posix(),
            "bytes": len(commit_content),
            "sha256": hashlib.sha256(commit_content).hexdigest(),
            "overwrite": False,
        }
    )
    if level == "reproducible":
        operations.extend(
            {
                "action": "copy",
                "path": (session_dir / "scripts" / record["snapshot_name"])
                .relative_to(root_path)
                .as_posix(),
                "source": record["path"],
                "bytes": record["bytes"],
                "sha256": record["sha256"],
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
        "plan_digest": plan_digest,
        "apply_precondition": "--expect-plan <plan_digest>",
        "operations": operations,
        "notes": [
            "Closeout writes only to a new date-organized session directory.",
            "No existing project or skill file is overwritten, moved, renamed, or deleted.",
            "Environment variables are not captured; known inline-secret patterns are blocked.",
            "User-selected summaries, steps, and script snapshots may still contain sensitive content and must be reviewed.",
            "Generated run scripts are references and must be reviewed before execution.",
            "Readiness means required evidence is present; commands were not executed or validated.",
            "Learning ignores this session unless COMMITTED.json and every declared file hash verify.",
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
    if expected_plan is None:
        payload["mode"] = "review_required"
        payload["notes"].append(
            "Closeout apply requires --expect-plan from this exact preview; nothing was written."
        )
        return payload
    if expected_plan != plan_digest:
        payload["mode"] = "blocked"
        payload["notes"].append(
            "The reviewed closeout plan no longer matches the requested evidence or workspace state."
        )
        payload["expected_plan"] = expected_plan
        payload["actual_plan"] = plan_digest
        return payload

    require_owned_workspace(root_path, operation="Closeout recording")

    with workspace_mutation_lock(root_path, operation="closeout") as held:
        try:
            session_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            payload["mode"] = "blocked"
            payload["notes"].append(
                "The session directory was claimed concurrently; rerun to receive a new path."
            )
            return payload
        held.verify()
        created: list[Path] = []
        for relative, content in files.items():
            target = session_dir / relative
            create_text_exclusive(target, content)
            created.append(target)
            held.verify()
        if level == "reproducible":
            for record in script_records:
                source = root_path / record["path"]
                content = source.read_bytes()
                if (
                    len(content) != record["bytes"]
                    or hashlib.sha256(content).hexdigest() != record["sha256"]
                ):
                    raise ArchMarshalError(
                        "session_source_changed",
                        "A selected script changed after closeout planning; the incomplete session was preserved.",
                        details={"path": record["path"]},
                    )
                target = session_dir / "scripts" / record["snapshot_name"]
                create_bytes_exclusive(target, content)
                created.append(target)
                held.verify()
        commit_path = _commit_session(session_dir, created, commit_content)
        created.append(commit_path)
        held.verify()

    payload["mode"] = "append_only_applied"
    payload["created"] = [path.relative_to(root_path).as_posix() for path in created]
    return payload


def verify_committed_session(session_dir: Path | str) -> dict[str, Any]:
    directory = Path(session_dir).resolve()
    marker = directory / "COMMITTED.json"
    if not marker.is_file() or is_link_or_reparse(marker):
        raise ArchMarshalError(
            "session_commit_invalid",
            "Session commit marker is missing, linked, or exceeds the safe size limit.",
            details={"path": str(marker)},
        )
    loaded_marker = read_bytes_safe(
        marker,
        max_bytes=MAX_SESSION_COMMIT_BYTES,
        label="Session commit marker",
    )
    try:
        if loaded_marker.error:
            raise ValueError(loaded_marker.error)
        commit = json.loads(loaded_marker.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ArchMarshalError(
            "session_commit_invalid",
            "Session commit marker is not valid UTF-8 JSON.",
            details={"path": str(marker)},
        ) from exc
    records = commit.get("files") if isinstance(commit, dict) else None
    if (
        not isinstance(commit, dict)
        or commit.get("format") != SESSION_COMMIT_FORMAT
        or not isinstance(records, list)
        or len(records) > MAX_SESSION_FILES
        or commit.get("file_count") != len(records)
    ):
        raise ArchMarshalError(
            "session_commit_invalid",
            "Session commit marker structure is invalid.",
        )
    declared: set[str] = set()
    declared_records: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ArchMarshalError("session_commit_invalid", "Session file record is invalid.")
        relative = _safe_session_relative(record.get("path"))
        if (
            relative in declared
            or not isinstance(record.get("bytes"), int)
            or record["bytes"] < 0
            or not _is_sha256(record.get("sha256"))
        ):
            raise ArchMarshalError("session_commit_invalid", "Session file record is invalid.")
        path = directory.joinpath(*Path(relative).parts)
        if (
            not path.is_file()
            or is_link_or_reparse(path)
            or path.stat().st_size != record["bytes"]
            or sha256_file(path) != record["sha256"]
        ):
            raise ArchMarshalError(
                "session_integrity_failed",
                "Committed session file is missing or does not match its hash.",
                details={"path": relative},
            )
        declared.add(relative)
        declared_records[relative] = record
    actual = {
        path.relative_to(directory).as_posix()
        for path in files_below_no_links(directory, purpose="Committed session verification")
        if path.is_file() and path.name != "COMMITTED.json"
    }
    if actual != declared:
        raise ArchMarshalError(
            "session_integrity_failed",
            "Committed session contains undeclared or missing files.",
            details={"undeclared": sorted(actual - declared), "missing": sorted(declared - actual)},
        )
    session_path = directory / "session.yaml"
    session_result = load_yaml_safe(session_path)
    session_record = declared_records.get("session.yaml")
    if (
        session_result.error
        or session_record is None
        or session_result.byte_count != session_record.get("bytes")
        or session_result.sha256 != session_record.get("sha256")
        or not isinstance(session_result.data, dict)
        or session_result.data.get("format") != SESSION_FORMAT
    ):
        raise ArchMarshalError(
            "session_integrity_failed",
            "Committed session.yaml is invalid or has an unsupported format.",
        )
    return {
        "commit": commit,
        "commit_sha256": loaded_marker.sha256,
        "session": session_result.data,
    }


def _commit_session(session_dir: Path, created: list[Path], content: bytes) -> Path:
    actual_records = _records_for_paths(session_dir, created)
    expected = json.loads(content.decode("utf-8"))
    if actual_records != expected.get("files"):
        raise ArchMarshalError(
            "session_integrity_failed",
            "Session files no longer match the exact reviewed commit manifest.",
        )
    marker = session_dir / "COMMITTED.json"
    create_bytes_exclusive(marker, content, mode=0o600)
    verify_committed_session(session_dir)
    return marker


def _records_for_paths(session_dir: Path, paths: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(session_dir).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(paths, key=lambda item: item.relative_to(session_dir).as_posix())
    ]


def _planned_session_records(
    files: dict[str, str],
    script_records: list[dict[str, Any]],
    *,
    level: str,
) -> list[dict[str, Any]]:
    records = [
        {
            "path": relative,
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        for relative, content in files.items()
    ]
    if level == "reproducible":
        records.extend(
            {
                "path": f"scripts/{record['snapshot_name']}",
                "bytes": record["bytes"],
                "sha256": record["sha256"],
            }
            for record in script_records
        )
    return sorted(records, key=lambda item: item["path"])


def _session_commit_bytes(recorded_on: str, records: list[dict[str, Any]]) -> bytes:
    commit = {
        "format": SESSION_COMMIT_FORMAT,
        "committed_on": recorded_on,
        "session_format": SESSION_FORMAT,
        "file_count": len(records),
        "files": records,
        "source_mutation": False,
    }
    content = (
        json.dumps(commit, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(content) > MAX_SESSION_COMMIT_BYTES:
        raise ArchMarshalError(
            "session_commit_limit_exceeded",
            "Session commit marker exceeds the safe size limit.",
        )
    return content


def _safe_session_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ArchMarshalError("session_commit_invalid", "Session file path is invalid.")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchMarshalError("session_commit_invalid", "Session file path is invalid.")
    return path.as_posix()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


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


def _session_intent_digest(session: dict[str, Any], shell: str) -> str:
    canonical = json.dumps(
        {
            "format": "archmarshal-closeout-intent-v1",
            "shell": shell,
            "session": session,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _session_plan_digest(
    root: Path,
    session: dict[str, Any],
    shell: str,
    session_dir: Path,
    files: dict[str, str],
    script_records: list[dict[str, Any]],
    commit_content: bytes,
) -> str:
    writes = [
        {
            "path": (session_dir / relative).relative_to(root).as_posix(),
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        for relative, content in files.items()
    ]
    if session["level"] == "reproducible":
        writes.extend(
            {
                "path": (session_dir / "scripts" / record["snapshot_name"])
                .relative_to(root)
                .as_posix(),
                "bytes": record["bytes"],
                "sha256": record["sha256"],
            }
            for record in script_records
        )
    writes.append(
        {
            "path": (session_dir / "COMMITTED.json").relative_to(root).as_posix(),
            "bytes": len(commit_content),
            "sha256": hashlib.sha256(commit_content).hexdigest(),
        }
    )
    canonical = json.dumps(
        {
            "format": "archmarshal-closeout-plan-v2",
            "root": str(root),
            "shell": shell,
            "session": session,
            "session_dir": session_dir.relative_to(root).as_posix(),
            "writes": sorted(writes, key=lambda item: item["path"]),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _summary_markdown(session: dict[str, Any]) -> str:
    lines = [
        "# Project closeout",
        "",
        f"- Recorded: {session['recorded_on']}",
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


__all__ = ["CLOSEOUT_LEVELS", "record_closeout", "verify_committed_session"]
