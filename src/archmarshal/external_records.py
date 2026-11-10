"""External workflow records and real deliverable copies; no project control writes.

The record is the commit marker. Artifact copies without that record are incomplete
and are never advertised as delivered. Exact saved plans permit create-only retries.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .errors import ArchMarshalError
from .external_artifacts import inspect_files, inspect_local_delivery
from .portable import canonical, decode, digest, fail, portable_key, read, relative, sha, text
from .project_context import absolute_directory, canonical_path, resolve_project_context
from .safety import create_bytes_exclusive, ensure_unlinked_path
from .sensitive_data import sensitive_text_errors
from .workspace_lock import workspace_mutation_lock

BINDING_FORMAT = "codex-external-project-binding-v1"
LEGACY_FORMAT = "codex-external-project-record-v1"
RECORD_FORMAT = "archmarshal-external-project-record-v1"
PLAN_FORMAT = "archmarshal-external-project-plan-v1"
MAX_RECORD_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACTS = 100
MAX_LIST_ENTRIES = 10_000
MAX_STATUS_BYTES = 128 * 1024 * 1024
TEXT_FIELDS = ("decisions", "evidence", "next_steps")


def record_location(project_root: Path | str, store_root: Path | str) -> tuple[Path, Path]:
    """Use the local adapter's path-hash layout without migrating its existing data."""
    project = absolute_directory(project_root, existing=False)
    store = absolute_directory(store_root, existing=False)
    if store == project or project in store.parents or store in project.parents:
        fail("Project content and external records must be disjoint.", "external_store_overlap")
    key = hashlib.sha256(canonical_path(project).encode("utf-8")).hexdigest()[:16]
    label = re.sub(r"[^\w.-]+", "-", project.name).strip(".-")[:48] or "project"
    location = ensure_unlinked_path(store / f"{label}-{key}", purpose="External project records")
    if store.exists() and not store.is_dir():
        fail("External record storage is not a directory.", "external_store_invalid")
    return project, location


def _binding(project: Path) -> dict[str, Any]:
    return {"format": BINDING_FORMAT, "project_root": str(project),
            "canonical_project_root": canonical_path(project)}


def _existing_binding(project: Path, location: Path) -> bytes | None:
    path = ensure_unlinked_path(location / "project.json", purpose="External project binding")
    if not path.exists():
        if location.exists() and any(location.iterdir()):
            fail("External record directory has content without a verified binding.", "external_binding_invalid")
        return None
    raw = read(path, MAX_RECORD_BYTES)
    value = decode(raw)
    if (set(value) != {"format", "project_root", "canonical_project_root"}
            or value["format"] != BINDING_FORMAT
            or value["canonical_project_root"] != canonical_path(project)
            or not isinstance(value["project_root"], str)
            or not Path(value["project_root"]).is_absolute()
            or canonical_path(Path(value["project_root"]).resolve()) != canonical_path(project)):
        fail("External records belong to another project or the binding is invalid.", "external_binding_invalid")
    return raw


def _texts(values: list[str] | None, field: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 100:
        fail("External record text exceeds its structure limit.", "external_record_invalid")
    for value in values:
        text(value, field, 4000, required=True)
    return values


def _artifact_inputs(work: Path, paths: list[str]) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if not isinstance(paths, list) or len(paths) > MAX_ARTIFACTS:
        fail("Select at most 100 deliverable files.", "external_artifact_limit")
    if any(not isinstance(path, str) for path in paths):
        fail("Deliverable paths must be text.", "external_artifact_invalid")
    entries, contents = [], {}
    seen = set()
    total = 0
    for source in sorted(paths):
        source = relative(source)
        key = portable_key(source)
        if key in seen:
            fail("Deliverable paths collide on a supported platform.", "external_artifact_invalid")
        seen.add(key)
        if (set(key.split("/")) & {".git", ".hg", ".svn", ".agent", ".codex", ".venv", "node_modules"}
                or Path(key).name in {".env", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_ed25519"}):
            fail("Deliverable selection crosses a control, dependency or credential boundary.",
                 "external_artifact_invalid")
        raw = read(work / source, MAX_ARTIFACT_BYTES - total)
        total += len(raw)
        entries.append({"source": source, "sha256": sha(raw), "bytes": len(raw)})
        contents[source] = raw
    return entries, contents


def _record_path(record: dict[str, Any]) -> str:
    try:
        moment = datetime.fromisoformat(record["recorded_at"])
    except ValueError as exc:
        raise ArchMarshalError("external_record_invalid", "Invalid external record timestamp.") from exc
    if moment.utcoffset() != datetime.now(ZoneInfo("Asia/Shanghai")).utcoffset():
        fail("External records require the Asia/Shanghai time zone.", "external_record_invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", record["event_id"]):
        fail("Invalid external record event ID.", "external_record_invalid")
    return f"history/{moment:%Y/%m/%d}/{moment:%H%M%S-%f}-{digest(record)}.json"


def _seal(path: Path, raw: bytes) -> None:
    ensure_unlinked_path(path, purpose="External append-only output")
    if path.exists():
        if read(path, max(len(raw), MAX_RECORD_BYTES)) != raw:
            fail("Existing external output differs; it was preserved.", "external_output_changed")
    else:
        create_bytes_exclusive(path, raw, mode=0o600)


def _check_bundle(location: Path, record: dict[str, Any], *, verify: bool) -> list[dict[str, Any]]:
    return inspect_files(location, record["artifacts"], verify=verify)


def record_project(project_root: Path | str, store_root: Path | str, *,
                   work_root: Path | str | None = None, kind: str, summary: str,
                   task: str = "", project_id: str = "", thread_id: str = "", host_id: str = "local",
                   decisions: list[str] | None = None, evidence: list[str] | None = None,
                   next_steps: list[str] | None = None, artifacts: list[str] | None = None,
                   reviewed_plan: dict[str, Any] | None = None,
                   expected_plan: str | None = None, apply: bool = False) -> dict[str, Any]:
    project, location = record_location(project_root, store_root)
    context = resolve_project_context(project, work_root, project_id=project_id,
                                      thread_id=thread_id, host_id=host_id)
    work = Path(context["work_root"])
    store = absolute_directory(store_root, existing=False)
    if store == work or work in store.parents or store in work.parents:
        fail("Actual worktree and external records must be disjoint.", "external_store_overlap")
    if not isinstance(kind, str) or kind not in {"start", "note", "close"}:
        fail("External record kind must be start, note or close.", "external_record_invalid")
    text(summary, "summary", 8000, required=True)
    text(task, "task", 4000)
    fields = {"kind": kind, "summary": summary, "task": task,
              "decisions": _texts(decisions, "decisions"), "evidence": _texts(evidence, "evidence"),
              "next_steps": _texts(next_steps, "next_steps")}
    if sensitive_text_errors(**fields):
        fail("External record contains an apparent inline secret.", "external_record_invalid")
    binding = _existing_binding(project, location)
    if artifacts is not None and not isinstance(artifacts, list):
        fail("Deliverable selection must be a list.", "external_artifact_invalid")
    if apply:
        if (not isinstance(reviewed_plan, dict) or reviewed_plan.get("format") != PLAN_FORMAT
                or len(canonical(reviewed_plan)) > MAX_RECORD_BYTES or digest(reviewed_plan) != expected_plan):
            fail("Apply requires the complete reviewed external plan and its exact digest.", "external_plan_required")
        plan = reviewed_plan
        record = plan.get("record", {})
        if not isinstance(record, dict):
            fail("Reviewed external record must be an object.", "external_record_invalid")
        _validate_record(project, record)
        if (set(plan) != {"format", "context", "store_root", "record_root", "binding_sha256", "record", "record_path"}
                or plan["store_root"] != str(store) or plan["record_root"] != str(location)
                or any(record.get(key) != value for key, value in fields.items())
                or [item["source"] for item in record.get("artifacts", [])] != sorted(artifacts or [])):
            fail("Reviewed external plan does not match this request.", "external_plan_changed")
        if plan["record_path"] != _record_path(record):
            fail("Reviewed history path differs from its record.", "external_plan_changed")
        target = location / plan["record_path"]
        if target.exists():
            if read(target, MAX_RECORD_BYTES) != canonical(record):
                fail("Committed external record changed.", "external_output_changed")
            _check_bundle_tree(location, record)
            delivered = _check_bundle(location, record, verify=True)
            if any(item["state"] != "verified" for item in delivered):
                fail("Committed deliverables are missing or changed.", "external_output_changed")
            return {"mode": "unchanged", "record_root": str(location), "record": str(target),
                    "artifacts": delivered, "native_archmarshal_state": False}
        if plan["context"] != context or record["context"] != context:
            fail("Project/worktree identity or Git state changed after preview.", "external_plan_changed")
        actual_binding = sha(binding) if binding else None
        if (actual_binding != plan["binding_sha256"]
                and not (plan["binding_sha256"] is None and binding == canonical(_binding(project)))):
            fail("External binding changed after preview.", "external_plan_changed")
        entries, contents = _artifact_inputs(work, artifacts or [])
        expected = [{key: entry[key] for key in ("source", "sha256", "bytes")} for entry in record["artifacts"]]
        if entries != expected:
            fail("Deliverable bytes changed after preview.", "external_plan_changed")
        with workspace_mutation_lock(project, operation="external_record") as held:
            if resolve_project_context(project, work, project_id=project_id, thread_id=thread_id,
                                       host_id=host_id) != context:
                fail("Host checkout changed before publication.", "external_plan_changed")
            current = _existing_binding(project, location)
            if current not in (binding, canonical(_binding(project)) if binding is None else binding):
                fail("External binding changed before publication.", "external_plan_changed")
            _seal(location / "project.json", current or canonical(_binding(project)))
            if entries:
                _seal(location / "artifacts" / record["event_id"] / "PLAN.json", canonical(plan))
                _check_bundle_tree(location, record)
            for entry in record["artifacts"]:
                held.verify()
                _seal(location / entry["path"], contents[entry["source"]])
            held.verify()
            if any(item["state"] != "verified" for item in _check_bundle(location, record, verify=True)):
                fail("Deliverables could not be verified before the history commit.", "external_output_changed")
            _seal(target, canonical(record))  # Commit last; readers never advertise partial copies.
        return {"mode": "recorded", "record_root": str(location), "record": str(target),
                "artifacts": _check_bundle(location, record, verify=True),
                "native_archmarshal_state": False, "project_files_modified": False}
    entries, _ = _artifact_inputs(work, artifacts or [])
    event = uuid.uuid4().hex
    record = {"format": RECORD_FORMAT, **fields, "context": context,
              "project_root": str(project), "work_root": str(work), "event_id": event,
              "recorded_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
              "native_archmarshal_state": False,
              "artifacts": [{**entry, "path": f"artifacts/{event}/files/{entry['source']}"} for entry in entries]}
    _validate_record(project, record)
    plan = {"format": PLAN_FORMAT, "context": context, "store_root": str(store),
            "record_root": str(location), "binding_sha256": sha(binding) if binding else None,
            "record": record, "record_path": _record_path(record)}
    if len(canonical(plan)) > MAX_RECORD_BYTES:
        fail("External record plan exceeds its byte limit.", "external_record_invalid")
    return {"mode": "preview", "plan": plan, "plan_digest": digest(plan),
            "record_root": str(location), "native_archmarshal_state": False,
            "project_files_modified": False, "artifact_bytes": sum(item["bytes"] for item in entries)}


def _validate_record(project: Path, record: dict[str, Any]) -> None:
    required = {"format", "kind", "summary", "task", *TEXT_FIELDS, "context", "project_root",
                "work_root", "event_id", "recorded_at", "native_archmarshal_state", "artifacts"}
    if (not isinstance(record, dict) or set(record) != required or record["format"] != RECORD_FORMAT
            or record["project_root"] != str(project) or record["native_archmarshal_state"] is not False
            or not isinstance(record["kind"], str) or record["kind"] not in {"start", "note", "close"}
            or not isinstance(record["context"], dict)
            or record["context"].get("project_root") != str(project)
            or record["context"].get("work_root") != record["work_root"]
            or not isinstance(record["recorded_at"], str) or not isinstance(record["event_id"], str)):
        fail("Invalid external workflow record.", "external_record_invalid")
    _record_path(record)
    text(record["summary"], "summary", 8000, required=True)
    text(record["task"], "task", 4000)
    for key in TEXT_FIELDS:
        if not isinstance(record[key], list):
            fail("External record evidence fields must be lists.", "external_record_invalid")
        _texts(record[key], key)
    entries = record["artifacts"]
    if not isinstance(entries, list) or len(entries) > MAX_ARTIFACTS:
        fail("Invalid external artifact list.", "external_record_invalid")
    total = 0
    names = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"source", "path", "bytes", "sha256"}
                or type(entry["bytes"]) is not int or entry["bytes"] < 0
                or not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            fail("Invalid external artifact metadata.", "external_record_invalid")
        source = relative(entry["source"])
        if entry["path"] != f"artifacts/{record['event_id']}/files/{source}" or portable_key(source) in names:
            fail("External artifact path is invalid or duplicated.", "external_record_invalid")
        names.add(portable_key(source))
        total += entry["bytes"]
    if total > MAX_ARTIFACT_BYTES or len(canonical(record)) > MAX_RECORD_BYTES:
        fail("External record exceeds its byte budget.", "external_record_invalid")


def _children(path: Path, budget: list[int]) -> list[Path]:
    ensure_unlinked_path(path, purpose="External record catalog")
    result = []
    for child in path.iterdir():
        if budget[0] <= 0:
            fail("External record directory exceeds its listing budget.", "external_status_limited")
        budget[0] -= 1
        ensure_unlinked_path(child, purpose="External record entry")
        result.append(child)
    return sorted(result, key=lambda item: item.name, reverse=True)


def _check_bundle_tree(location: Path, record: dict[str, Any]) -> None:
    if not record["artifacts"]:
        return
    directory = location / "artifacts" / record["event_id"]
    expected = {directory / "PLAN.json", *(location / entry["path"] for entry in record["artifacts"])}
    directories = {directory}
    for path in expected:
        directories.update(p for p in path.parents if p == directory or directory in p.parents)
    pending = [directory]
    budget = [MAX_LIST_ENTRIES]
    while pending:
        for path in _children(pending.pop(), budget):
            if path.is_dir() and path in directories:
                pending.append(path)
            elif not path.is_file() or path not in expected:
                fail("Unexpected artifact bundle content was preserved.", "external_output_changed")


def _recent_paths(directory: Path, limit: int) -> list[Path]:
    if not directory.exists():
        return []
    results = []
    budget = [MAX_LIST_ENTRIES]
    # Date partitions bound traversal and preserve compatibility with the local adapter.
    for year in _children(directory, budget):
        if not re.fullmatch(r"\d{4}", year.name) or not year.is_dir():
            continue
        for month in _children(year, budget):
            if not re.fullmatch(r"\d{2}", month.name) or not month.is_dir():
                continue
            for day in _children(month, budget):
                if not re.fullmatch(r"\d{2}", day.name) or not day.is_dir():
                    continue
                for path in _children(day, budget):
                    if path.suffix == ".json":
                        results.append(path)
                        if len(results) == limit:
                            return results
    return results


def _pending_bundles(project: Path, location: Path, limit: int, *,
                     verify: bool, budget: list[int]) -> dict[str, Any]:
    directory = ensure_unlinked_path(location / "artifacts", purpose="External deliverable catalog")
    if not directory.exists():
        return {"uncommitted": [], "local_deliveries": [], "unmanaged_bundles": [], "omitted_bundles": 0}
    children = _children(directory, [MAX_LIST_ENTRIES])
    pending = []
    deliveries = []
    unmanaged = []
    for child in children[:limit]:
        plan_file = child / "PLAN.json"
        if not plan_file.exists():
            if (child / "MANIFEST.json").exists():
                try:
                    deliveries.append(inspect_local_delivery(project, location, child, verify=verify, budget=budget))
                except (ArchMarshalError, OSError):
                    deliveries.append({"path": str(child), "state": "invalid_manifest"})
            elif re.fullmatch(r"[0-9a-f]{32}", child.name):
                pending.append({"path": str(child), "stage": "invalid_or_unprepared"})
            else:
                unmanaged.append({"path": str(child), "state": "unmanaged"})
            continue
        try:
            plan = decode(read(plan_file, MAX_RECORD_BYTES))
            record = plan["record"]
            _validate_record(project, record)
            if (plan.get("format") != PLAN_FORMAT or plan.get("record_root") != str(location)
                    or plan.get("record_path") != _record_path(record) or child.name != record["event_id"]):
                fail("Unrecognized artifact plan.")
            if not (location / plan["record_path"]).exists():
                pending.append({"path": str(child), "plan_file": str(plan_file), "stage": "uncommitted"})
        except (ArchMarshalError, OSError, KeyError, TypeError, ValueError):
            pending.append({"path": str(child), "stage": "invalid_or_unprepared"})
    return {"uncommitted": pending, "local_deliveries": deliveries, "unmanaged_bundles": unmanaged,
            "omitted_bundles": max(0, len(children) - limit)}


def project_status(project_root: Path | str, store_root: Path | str, *, limit: int = 5,
                   verify_artifacts: bool = False) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100:
        fail("External status limit must be between 1 and 100.", "external_status_limited")
    project, location = record_location(project_root, store_root)
    binding = _existing_binding(project, location)
    latest = []
    budget = MAX_STATUS_BYTES
    for path in _recent_paths(location / "history", limit):
        raw = read(path, MAX_RECORD_BYTES)
        record = decode(raw)
        if record.get("format") == RECORD_FORMAT:
            _validate_record(project, record)
            if path.relative_to(location).as_posix() != _record_path(record):
                fail("External history record was changed or relocated.", "external_record_invalid")
            size = sum(entry["bytes"] for entry in record["artifacts"])
            inspected = verify_artifacts and size <= budget
            artifacts = _check_bundle(location, record, verify=inspected)
            budget -= size if inspected else 0
            integrity = "content_address_verified"
        elif (record.get("format") == LEGACY_FORMAT and record.get("project_root") == str(project)
              and record.get("native_archmarshal_state") is False):
            artifacts, integrity = [], "legacy_unsealed"
        else:
            fail("Unknown or relocated external record.", "external_record_invalid")
        latest.append({"path": str(path), "record": record, "integrity": integrity, "artifacts": artifacts})
    return {"mode": "external_host_records", "project_root": str(project), "record_root": str(location),
            "project_available": project.is_dir(), "registered": binding is not None,
            "latest": latest, "limit": limit, "native_archmarshal_state": False,
            "project_files_modified": False,
            **_pending_bundles(project, location, limit, verify=verify_artifacts, budget=[budget])}
