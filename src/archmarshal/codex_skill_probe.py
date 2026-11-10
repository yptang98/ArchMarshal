"""One bounded, read-only stdio query to an explicitly selected Codex binary."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .errors import ArchMarshalError
from .host_skills import MAX_OBSERVATION_BYTES, OBSERVATION_FORMAT
from .portable import decode, fail, text
from .project_context import absolute_directory
from .safety import ensure_unlinked_path
from .skill_index import load_skill_index


def probe_codex_skills(work_root: Path | str, binary: Path | str, *,
                       host_id: str = "local", timeout: float = 30) -> dict[str, Any]:
    work = absolute_directory(work_root)
    executable = Path(binary)
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        fail("Select an absolute native Codex executable, not a shell wrapper.", "host_skills_binary_invalid")
    ensure_unlinked_path(executable, purpose="Explicit native Codex executable")
    text(host_id, "host_id", 200, required=True)
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 1 <= timeout <= 60:
        fail("Codex Skill probe timeout must be between 1 and 60 seconds.", "host_skills_invalid")
    generation = load_skill_index(work).get("generation") or {}
    if (generation.get("selection") or {}).get("excluded_packages"):
        fail("Native reload may read excluded Skills; use an already available host observation.",
             "host_skills_probe_excluded")
    return _query([str(executable), "app-server", "--listen", "stdio://"], work, host_id, timeout)


def _query(command: list[str], work: Path, host_id: str, timeout: float) -> dict[str, Any]:
    messages: queue.Queue[bytes | Exception | None] = queue.Queue(maxsize=100)
    overflow = threading.Event()
    process = subprocess.Popen(command, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert process.stdin is not None and process.stdout is not None
    def receive() -> None:
        remaining = MAX_OBSERVATION_BYTES * 2
        try:
            while True:
                raw = process.stdout.readline(min(MAX_OBSERVATION_BYTES, remaining) + 1)
                if not raw:
                    messages.put_nowait(None)
                    return
                remaining -= len(raw)
                if remaining < 0 or len(raw) > MAX_OBSERVATION_BYTES or not raw.endswith(b"\n"):
                    messages.put_nowait(ValueError("Native response exceeded its read budget."))
                    return
                messages.put_nowait(raw)
        except queue.Full:
            overflow.set()
        except (OSError, ValueError) as exc:
            try:
                messages.put_nowait(exc)
            except queue.Full:
                overflow.set()
    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    invalidated = False
    count = 0
    def send(value: dict[str, Any]) -> None:
        process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
        process.stdin.flush()
    def response(identifier: int) -> dict[str, Any]:
        nonlocal invalidated, count
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("Codex Skill probe timed out.", "host_skills_timeout")
            if overflow.is_set() or count >= 100:
                fail("Codex Skill probe exceeded its message budget.", "host_skills_invalid")
            try:
                raw = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise ArchMarshalError("host_skills_timeout", "Codex Skill probe timed out.") from exc
            if raw is None or isinstance(raw, Exception):
                fail("Codex Skill probe ended before a bounded response arrived.", "host_skills_unavailable")
            count += 1
            message = decode(raw)
            if message.get("method") == "skills/changed":
                invalidated = True
            if "method" in message and "id" in message:
                fail("Read-only Skill probe cannot service host action requests.", "host_skills_unavailable")
            if message.get("id") == identifier:
                if "error" in message or not isinstance(message.get("result"), dict):
                    fail("Codex rejected the read-only Skill probe request.", "host_skills_unavailable")
                return message["result"]
    try:
        send({"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "archmarshal_skill_status", "version": __version__}}})
        initialized = response(0)
        send({"method": "initialized", "params": {}})
        send({"id": 1, "method": "skills/list", "params": {"cwds": [str(work)], "forceReload": True}})
        skills = response(1)
        return {"format": OBSERVATION_FORMAT, "origin": "codex-cli-app-server",
                "observed_at": datetime.now(timezone.utc).isoformat(), "host_id": host_id,
                "invalidated": invalidated, "response": skills,
                "server": {key: initialized.get(key) for key in ("userAgent", "platformOs", "platformFamily")}}
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        reader.join(timeout=2)
        process.stdout.close()
