from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from ._doctor_core import (
    MAX_METADATA_FILE_BYTES,
    Report,
    display,
    is_link,
    list_directory,
    load_json,
    load_yaml,
    path_exists,
    report_format,
    unsafe_finding,
)
from .formats import DURABLE_FORMATS
from .ownership import MAX_OWNERSHIP_BYTES, workspace_id
from .schema_validation import validate_schema

CONTROL_FILES = (
    (".agent/workspace.yaml", "workspace_config_schema", "workspace", "workspace"),
    (".agent/registry.yaml", "artifact_registry_schema", "artifacts", "artifact-registry"),
    (".agent/memory-stores.yaml", "memory_stores_schema", "memory_stores", "memory-stores"),
    (".agent/memory-records.yaml", "memory_records_schema", "memory_records", "memory-records"),
)


def inspect_ownership(root: Path, report: Report) -> None:
    path = root / ".agent" / "ownership.json"
    marker, _ = load_json(path, root, "ownership", "workspace", report, MAX_OWNERSHIP_BYTES)
    if marker is None:
        if not path_exists(path):
            report.add(
                "ownership",
                "workspace_unowned",
                "info",
                "absent",
                "workspace",
                display(path, root),
                "No workspace ownership marker is present.",
            )
        return
    report_format(
        report,
        "ownership",
        "workspace",
        display(path, root),
        marker.get("format") if isinstance(marker, dict) else None,
        "workspace_ownership",
    )
    if not _valid_workspace_ownership(root, marker):
        report.add(
            "ownership",
            "workspace_ownership_binding_invalid",
            "error",
            "corrupt",
            "workspace",
            display(path, root),
            "Ownership marker is not safely bound to this exact workspace root.",
        )


def inspect_control_plane(root: Path, report: Report) -> None:
    present = 0
    for relative, family, required_key, schema_name in CONTROL_FILES:
        path = root / PurePosixPath(relative)
        if not path_exists(path):
            continue
        present += 1
        loaded = load_yaml(path, root, "control_plane", "workspace", report)
        if not isinstance(loaded, dict) or required_key not in loaded:
            if loaded is not None:
                report.add(
                    "control_plane",
                    "control_plane_structure_invalid",
                    "error",
                    "corrupt",
                    "workspace",
                    relative,
                    "Control-plane YAML lacks its required top-level mapping.",
                    expected_key=required_key,
                )
            continue
        issues = validate_schema(loaded, schema_name)
        if issues:
            report.add(
                "control_plane",
                "control_plane_schema_invalid",
                "error",
                "corrupt",
                "workspace",
                relative,
                "Control-plane YAML does not satisfy its packaged schema.",
                schema=schema_name,
                issues=[
                    {"location": item.location, "message": item.message}
                    for item in issues[:20]
                ],
                issues_truncated=len(issues) > 20,
            )
            continue
        spec = next(item for item in DURABLE_FORMATS if item.family == family)
        report.add(
            "control_plane",
            "control_plane_format_current",
            "info",
            "current",
            "workspace",
            relative,
            "Control-plane document is readable under its current schema family.",
            format_family=family,
            format=spec.writable_versions[0],
            owner=spec.owner,
        )
    if not present:
        report.add(
            "control_plane",
            "control_plane_absent",
            "info",
            "absent",
            "workspace",
            ".agent",
            "No control-plane YAML files are present.",
        )


def inspect_transactions(root: Path, report: Report) -> None:
    base = root / ".agent" / "transactions" / "adoption"
    entries = list_directory(base, root, "transaction", "workspace", report)
    if entries is None:
        if not path_exists(base):
            report.add(
                "transaction",
                "adoption_transactions_absent",
                "info",
                "absent",
                "workspace",
                display(base, root),
                "No adoption transaction state is present.",
            )
        return
    active_id = _active_transaction_id(base / "ACTIVE", root, report)
    transaction_dirs = [item for item in entries if item.name not in {"ACTIVE", "LOCK"}]
    if len(transaction_dirs) > report.budget.history_limit:
        report.budget.truncate("transaction", "workspace", display(base, root), "history_limit")
        transaction_dirs = transaction_dirs[: report.budget.history_limit]
    seen = 0
    for directory in transaction_dirs:
        if is_link(directory):
            unsafe_finding(report, "transaction", "workspace", directory, root)
            continue
        if not directory.is_dir():
            continue
        seen += 1
        journal_path, receipt_path = directory / "journal.json", directory / "COMMITTED.json"
        journal, _ = load_json(
            journal_path, root, "transaction", "workspace", report, MAX_METADATA_FILE_BYTES
        )
        if journal is not None:
            report_format(
                report,
                "transaction",
                "workspace",
                display(journal_path, root),
                journal.get("format") if isinstance(journal, dict) else None,
                "adoption_transaction",
            )
        elif not path_exists(journal_path):
            report.add(
                "transaction",
                "adoption_transaction_partial",
                "warning",
                "partial",
                "workspace",
                display(directory, root),
                "Transaction directory has no journal.",
            )
            report.suggest(
                "workspace",
                display(directory, root),
                "partial_transaction",
                "Retain and review this incomplete transaction before manual cleanup.",
            )
        _inspect_receipt(receipt_path, directory.name == active_id, root, report)
    if not seen and active_id is None:
        report.add(
            "transaction",
            "adoption_transactions_empty",
            "info",
            "absent",
            "workspace",
            display(base, root),
            "Adoption transaction area contains no records.",
        )


def fill_unavailable_workspace_areas(root: Path, report: Report) -> None:
    classification = "absent" if not path_exists(root) else "not_inspected"
    for area in ("control_plane", "transaction", "skill_index", "session"):
        report.add(
            area,
            f"{area}_not_inspected",
            "info",
            classification,
            "workspace",
            ".",
            "Area was not traversed because the workspace root was unavailable or unsafe.",
        )


def _active_transaction_id(path: Path, root: Path, report: Report) -> str | None:
    if not path_exists(path):
        return None
    active, _ = load_json(path, root, "transaction", "workspace", report, 512)
    transaction_id = active.get("transaction_id") if isinstance(active, dict) else None
    if isinstance(transaction_id, str):
        return transaction_id
    if active is not None:
        report.add(
            "transaction",
            "adoption_active_invalid",
            "error",
            "corrupt",
            "workspace",
            display(path, root),
            "ACTIVE marker does not identify a transaction.",
        )
    return None


def _inspect_receipt(path: Path, active: bool, root: Path, report: Report) -> None:
    if path_exists(path):
        receipt, _ = load_json(
            path, root, "transaction", "workspace", report, MAX_METADATA_FILE_BYTES
        )
        if receipt is not None:
            report_format(
                report,
                "transaction",
                "workspace",
                display(path, root),
                receipt.get("format") if isinstance(receipt, dict) else None,
                "adoption_receipt",
            )
    elif active:
        report.add(
            "transaction",
            "adoption_transaction_active",
            "warning",
            "incomplete",
            "workspace",
            display(path.parent, root),
            "Active transaction has not published a commit receipt.",
        )


def _valid_workspace_ownership(root: Path, marker: Any) -> bool:
    return (
        isinstance(marker, dict)
        and marker.get("workspace_id") == workspace_id(root)
        and marker.get("managed_root") == "."
        and marker.get("skill_index") in {"required", "disabled"}
        and marker.get("source_mutation") is False
    )


__all__ = [
    "fill_unavailable_workspace_areas",
    "inspect_control_plane",
    "inspect_ownership",
    "inspect_transactions",
]
