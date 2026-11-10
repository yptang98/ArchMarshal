"""Domain checks and reviewed replay for interrupted local operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import parse_yaml_safe
from .operation_journal import (
    complete_operation,
    file_records,
    load_operation,
    observe_operation,
    payload,
    write_missing,
)
from .ownership import require_owned_workspace
from .portable import canonical, checked_plan, decode, digest, fail, portable_key
from .safety import EXCLUDED_BACKUP_PARTS, ensure_managed_path, ensure_unlinked_path
from .session import SESSION_COMMIT_FORMAT, SESSION_FORMAT, verify_committed_session
from .skill_index import load_skill_index
from .skill_package import validate_snapshot, verify_package
from .skill_sync import package_outputs
from .skill_validation import parse_skill_frontmatter
from .sync_policy import inbox_path, origin_path, validate_receipt
from .workspace_layout import load_workspace_layout
from .workspace_lock import workspace_mutation_lock


def _input(root: Path, journal: dict[str, Any], path: str) -> bytes:
    entry = next((e for e in journal["files"] if e["path"] == path), None)
    if entry is None:
        fail("Required recovery input is missing.", "operation_invalid")
    return payload(root, journal, entry)


def _check_sync(root: Path, journal: dict[str, Any]) -> dict[str, Any]:
    meta = journal["metadata"]
    if (set(meta) != {"object_id", "inbox"}
            or meta["inbox"] != inbox_path(root).relative_to(root).as_posix()):
        fail("Skill inbox mapping changed since this operation was prepared.", "operation_policy_changed")
    key, destination = meta["object_id"], journal["destination"]
    if journal["kind"] == "sync_pull":
        if destination != meta["inbox"] + "/skill-sync/" + key:
            fail("Recovery import destination differs from the quarantine policy.", "operation_invalid")
        raw = _input(root, journal, destination + "/snapshot.json")
        snapshot = validate_snapshot(raw, key)
        receipt_raw = _input(root, journal, destination + "/RECEIPT.json")
        validate_receipt(decode(receipt_raw), key)
        files, dirs = package_outputs(destination + "/package", snapshot)
        files[destination + "/snapshot.json"] = (raw, 0o644)
        commit = destination + "/RECEIPT.json"
        files[commit] = (receipt_raw, 0o644)
        dirs.append(destination)
    else:
        if any(part in EXCLUDED_BACKUP_PARTS | {".agent"}
               for part in portable_key(destination).split("/")):
            fail("Recovery install crosses a reserved boundary.", "operation_invalid")
        generation = load_skill_index(root).get("generation") or {}
        for excluded in generation.get("selection", {}).get("excluded_packages", []):
            if (portable_key(destination) == portable_key(excluded)
                    or portable_key(destination).startswith(portable_key(excluded) + "/")):
                fail("Recovery install destination is excluded.", "operation_policy_changed")
        commit = origin_path(root, destination).relative_to(root).as_posix()
        raw = _input(root, journal, commit)
        origin = decode(raw)
        if (set(origin) != {"format", "source", "object_id", "snapshot"}
                or origin["format"] != "archmarshal-sync-origin-v1"
                or origin["source"] != destination or origin["object_id"] != key):
            fail("Invalid recovery installation provenance.", "operation_invalid")
        snapshot = validate_snapshot(canonical(origin["snapshot"]), key)
        files, dirs = package_outputs(destination, snapshot)
        try:
            frontmatter, errors = parse_skill_frontmatter(
                files[destination + "/SKILL.md"][0].decode("utf-8"))
        except UnicodeDecodeError:
            fail("Recovery Skill entrypoint is not UTF-8.", "operation_invalid")
        if errors or Path(destination).name != frontmatter.get("name"):
            fail("Recovery install name differs from its Skill frontmatter.", "operation_invalid")
        files[commit] = (raw, 0o644)
    if (file_records(files) != journal["files"] or sorted(dirs) != journal["directories"]
            or journal["commit_files"] != [commit]):
        fail("Recovery outputs differ from the sealed Skill snapshot.", "operation_invalid")
    return snapshot


def _check_closeout(root: Path, journal: dict[str, Any]) -> None:
    layout = load_workspace_layout(root)
    if journal["metadata"] != {"layout": layout.to_dict()}:
        fail("Closeout layout changed after the operation was prepared.", "operation_policy_changed")
    destination = root / journal["destination"]
    ensure_managed_path(root, destination, purpose="Recovered closeout")
    history = layout.configured_dir("history")
    if destination == history or history not in destination.parents:
        fail("Recovery closeout is outside the configured history directory.", "operation_invalid")
    prefix = journal["destination"] + "/"
    commit_path = prefix + "COMMITTED.json"
    if journal["commit_files"] != [commit_path]:
        fail("Invalid closeout recovery commit order.", "operation_invalid")
    commit = decode(_input(root, journal, commit_path))
    records = []
    for entry in journal["files"]:
        if not entry["path"].startswith(prefix):
            fail("Recovery closeout output escapes the session.", "operation_invalid")
        if entry["path"] != commit_path:
            if entry["mode"] != 0o644:
                fail("Invalid closeout output mode.", "operation_invalid")
            records.append({k: entry[k] for k in ("path", "bytes", "sha256")})
            records[-1]["path"] = entry["path"][len(prefix):]
        elif entry["mode"] != 0o600:
            fail("Invalid closeout marker mode.", "operation_invalid")
    if (commit.get("format") != SESSION_COMMIT_FORMAT or commit.get("session_format") != SESSION_FORMAT
            or commit.get("file_count") != len(records) or commit.get("files") != records
            or commit.get("source_mutation") is not False):
        fail("Closeout recovery inputs differ from the commit manifest.", "operation_invalid")
    session = parse_yaml_safe(_input(root, journal, prefix + "session.yaml"))
    if (session.error or not isinstance(session.data, dict)
            or session.data.get("format") != SESSION_FORMAT):
        fail("Invalid sealed closeout session.", "operation_invalid")


def _check_policy(root: Path, journal: dict[str, Any]) -> dict[str, Any] | None:
    require_owned_workspace(root, operation="Recover operation")
    if journal["kind"] == "closeout":
        _check_closeout(root, journal)
        return None
    return _check_sync(root, journal)


def recover_operation(root: Path | str, operation_id: str, *, apply: bool = False,
                      expected_plan: str | None = None) -> dict[str, Any]:
    root = require_owned_workspace(ensure_unlinked_path(Path(root), purpose="Recovery workspace"),
                                   operation="Recover operation")
    journal = load_operation(root, operation_id)
    _check_policy(root, journal)
    state = observe_operation(root, journal)
    plan = {"operation": "operation_recover", "root": str(root), "state": state}
    result = {"mode": "blocked" if state["conflicts"] else "preview", "plan": plan,
              "plan_digest": digest(plan), **state,
              "policy": "Only missing sealed outputs are created; changed files are preserved."}
    if not apply:
        return result
    checked_plan(plan, expected_plan)
    with workspace_mutation_lock(root, operation="operation_recover") as held:
        journal = load_operation(root, operation_id)
        snapshot = _check_policy(root, journal)
        current = observe_operation(root, journal)
        checked_plan({"operation": "operation_recover", "root": str(root), "state": current},
                     expected_plan)
        if current["stage"] == "complete":
            result["mode"] = "unchanged"
            return result
        write_missing(root, journal, verify_lock=held.verify)
        held.verify()
        target = root / journal["destination"]
        if journal["kind"] == "closeout":
            verify_committed_session(target)
        else:
            verify_package(target / "package" if journal["kind"] == "sync_pull" else target, snapshot)
        complete_operation(root, journal)
        result.update(observe_operation(root, journal))
        result["mode"] = "recovered"
    return result
