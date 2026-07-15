from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

import archmarshal._doctor_core as doctor_core
import archmarshal._doctor_sessions as doctor_sessions
from archmarshal.cli import main
from archmarshal.doctor import doctor_workspace
from archmarshal.formats import DURABLE_FORMATS, find_format, format_registry
from archmarshal.io import StableBytesResult, YamlLoadResult
from archmarshal.user_store import _package_v2_commit, _snapshot_package_v2


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _workspace_ownership(root: Path) -> dict[str, object]:
    workspace_id = hashlib.sha256(
        f"archmarshal-workspace-v1\x00{root}".encode("utf-8")
    ).hexdigest()[:32]
    return {
        "format": "archmarshal-workspace-ownership-v1",
        "workspace_id": workspace_id,
        "managed_root": ".",
        "skill_index": "required",
        "source_mutation": False,
    }


def _store_ownership(root: Path) -> dict[str, object]:
    store_id = hashlib.sha256(
        f"archmarshal-user-store-v1\x00{root}".encode("utf-8")
    ).hexdigest()[:32]
    return {
        "format": "archmarshal-user-store-ownership-v1",
        "store_id": store_id,
        "managed_root": ".",
        "created_at": "2026-07-15T00:00:00+00:00",
        "source_mutation": False,
    }


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _tree_state(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
    state: dict[str, tuple[int, int, bytes | None]] = {}
    for path in sorted([root, *root.rglob("*")], key=lambda item: str(item)):
        metadata = path.lstat()
        content = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        state[path.relative_to(root).as_posix() or "."] = (
            metadata.st_mode,
            metadata.st_mtime_ns,
            content,
        )
    return state


def _skill_generation(parent: str | None, created_at: str) -> tuple[str, bytes]:
    payload = _json_bytes(
        {
            "format": "archmarshal-skill-index-v1",
            "created_at": created_at,
            "parent": parent,
            "skills": [],
            "changes": [],
        }
    )
    return hashlib.sha256(payload).hexdigest(), payload


def _user_generation(package_digest: str | None = None) -> tuple[str, bytes]:
    skills = [] if package_digest is None else [{"package_sha256": package_digest}]
    payload = _json_bytes(
        {
            "format": "archmarshal-user-store-generation-v1",
            "created_at": "2026-07-15T00:00:00+00:00",
            "parent": None,
            "common_skills": skills,
            "preferences": [],
            "candidate_decisions": [],
            "operation": {"kind": "test"},
        }
    )
    return hashlib.sha256(payload).hexdigest(), payload


def test_format_registry_records_versions_owners_migrations_and_bounds() -> None:
    registry = format_registry()
    assert registry["api_version"] == "archmarshal-format-registry-v1"
    assert [item["family"] for item in registry["formats"]] == [
        item.family for item in DURABLE_FORMATS
    ]
    assert len({item.family for item in DURABLE_FORMATS}) == len(DURABLE_FORMATS)
    for item in registry["formats"]:
        assert item["owner"].startswith("archmarshal.")
        assert item["readable_versions"]
        assert item["writable_versions"]
        assert item["migration_status"]
        assert item["boundedness"]

    package, package_status = find_format("archmarshal-user-skill-package-v1")
    session, session_status = find_format("archmarshal-session-v1")
    assert (package.family, package_status) == ("user_skill_package", "legacy")
    assert (session.family, session_status) == ("session", "legacy")
    assert find_format("archmarshal-user-skill-package-v99")[1] == "unsupported"
    assert find_format("totally-unknown")[1] == "unknown"
    assert find_format(None)[1] == "missing"
    candidate_formats = {
        value
        for item in DURABLE_FORMATS
        if item.owner == "archmarshal.candidate_draft"
        for value in item.readable_versions
    }
    assert candidate_formats == {
        "archmarshal-candidate-draft-plan-v1",
        "archmarshal-candidate-draft-preview-v1",
        "archmarshal-candidate-draft-commit-v1",
        "archmarshal-candidate-draft-binding-v1",
    }


def test_doctor_is_deterministic_and_does_not_change_tree_bytes_or_mtimes(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _write(root / ".agent/ownership.json", _json_bytes(_workspace_ownership(root)))
    _write(
        root / ".agent/workspace.yaml",
        b"workspace:\n  name: demo\n  version: 0.1.0\npaths:\n  project_root: .\n  agent_root: .agent\n",
    )
    before = _tree_state(root)

    first = doctor_workspace(root, history_limit=2)
    second = doctor_workspace(root, history_limit=2)

    assert first == second
    assert first["api_version"] == "archmarshal-doctor-v1"
    assert first["mode"] == "read_only"
    assert first["source_mutation"] is False
    assert first["filesystem_safety"] == {
        "static_link_reparse_rejection": True,
        "stable_file_identity_reads": True,
        "anchored_components": False,
        "concurrent_ancestor_rebinding_protected": False,
        "write_threat_model": "cooperative-only",
        "doctor_writes": False,
    }
    assert _tree_state(root) == before


def test_doctor_rejects_links_without_traversing_them(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _write(outside / "session.yaml", b"format: archmarshal-session-v1\n")
    (root / ".agent").mkdir()
    try:
        os.symlink(outside, root / ".agent/history", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    result = doctor_workspace(root)

    rejected = [item for item in result["findings"] if item["code"] == "session_link_rejected"]
    assert rejected
    assert rejected[0]["classification"] == "unsafe"
    assert rejected[0]["path"] == ".agent/history"
    assert not any(item["path"].endswith("session.yaml") for item in result["findings"])


def test_doctor_classifies_corrupt_legacy_orphan_and_partial_state(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    store = tmp_path / "store"
    root.mkdir()
    store.mkdir()
    _write(root / ".agent/ownership.json", b"{not-json\n")

    index_objects = root / ".agent/skill-overlays/.archmarshal/objects/sha256"
    orphan_digest, orphan_payload = _skill_generation(None, "2026-07-15T01:00:00+00:00")
    _write(index_objects / f"{orphan_digest}.json", orphan_payload)
    _write(
        root / ".agent/history/2026/07/15/legacy/session.yaml",
        b"format: archmarshal-session-v1\nused_skills: []\ntags: []\nkey_scripts: []\n",
    )

    _write(store / "ownership.json", _json_bytes(_store_ownership(store)))
    user_digest, user_payload = _user_generation()
    _write(store / f".archmarshal/objects/sha256/{user_digest}.json", user_payload)
    committed_package = "b" * 64
    partial_package = "c" * 64
    _write(
        store / f".archmarshal/packages/sha256/{committed_package}/COMMITTED.json",
        _json_bytes({"format": "archmarshal-user-skill-package-v1"}),
    )
    (store / f".archmarshal/packages/sha256/{partial_package}").mkdir(parents=True)

    result = doctor_workspace(root, user_store=store)
    classifications = {item["classification"] for item in result["findings"]}
    codes = {item["code"] for item in result["findings"]}

    assert {"corrupt", "legacy", "orphan_immutable_object", "orphan_package", "partial_package"} <= (
        classifications
    )
    assert "ownership_json_corrupt" in codes
    assert "session_format_legacy" in codes
    assert "skill_index_orphan_immutable_object" in codes
    assert "user_store_orphan_immutable_object" in codes
    assert "user_store_orphan_package" in codes
    assert "user_store_partial_package" in codes
    assert result["retention_suggestions"]
    assert all(item["automatic_action"] is False for item in result["retention_suggestions"])


def test_doctor_history_limit_is_exposed_and_prevents_false_orphan_claims(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    objects = root / ".agent/skill-overlays/.archmarshal/objects/sha256"
    parent_digest, parent_payload = _skill_generation(None, "2026-07-14T00:00:00+00:00")
    head_digest, head_payload = _skill_generation(
        parent_digest, "2026-07-15T00:00:00+00:00"
    )
    _write(objects / f"{parent_digest}.json", parent_payload)
    _write(objects / f"{head_digest}.json", head_payload)
    _write(root / ".agent/skill-overlays/.archmarshal/HEAD", f"{head_digest}\n".encode("ascii"))

    result = doctor_workspace(root, history_limit=1)

    assert result["budgets"]["limits"]["history_generations"] == 1
    assert result["budgets"]["truncated"] is True
    assert {
        item["reason"] for item in result["budgets"]["truncations"]
    } >= {"history_limit"}
    assert not any(
        item["classification"] == "orphan_immutable_object"
        for item in result["findings"]
    )


def test_doctor_tolerates_absent_workspace_and_store(tmp_path: Path) -> None:
    result = doctor_workspace(tmp_path / "missing", user_store=tmp_path / "missing-store")
    assert result["mode"] == "read_only"
    assert result["state"] == "absent"
    assert {item["code"] for item in result["findings"]} >= {
        "workspace_absent",
        "user_store_absent",
    }


def test_doctor_cli_keeps_absence_readable_and_returns_two_for_corruption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    assert main(["doctor", str(missing)]) == 0
    absent = json.loads(capsys.readouterr().out)
    assert absent["state"] == "absent"
    assert not missing.exists()

    root = tmp_path / "workspace"
    root.mkdir()
    _write(root / ".agent/ownership.json", b"{invalid\n")
    before = _tree_state(root)
    assert main(["doctor", str(root), "--history-limit", "2"]) == 2
    corrupt = json.loads(capsys.readouterr().out)
    assert corrupt["state"] == "error"
    assert _tree_state(root) == before


def test_doctor_reports_unknown_formats_and_invalid_control_structure(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    ownership = _workspace_ownership(root)
    ownership["format"] = "archmarshal-workspace-ownership-v99"
    _write(root / ".agent/ownership.json", _json_bytes(ownership))
    _write(root / ".agent/workspace.yaml", b"unexpected: true\n")

    result = doctor_workspace(root)
    codes = {item["code"] for item in result["findings"]}

    assert "workspace_ownership_format_unsupported" in codes
    assert "control_plane_structure_invalid" in codes


def test_doctor_validates_present_control_plane_against_packaged_schema(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _write(root / ".agent/workspace.yaml", b"workspace: {}\npaths: {}\n")

    result = doctor_workspace(root)
    finding = next(
        item for item in result["findings"] if item["code"] == "control_plane_schema_invalid"
    )

    assert finding["classification"] == "corrupt"
    assert finding["schema"] == "workspace"
    assert finding["issues"]


def test_doctor_classifies_stable_read_race_as_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    marker = root / ".agent/ownership.json"
    _write(marker, _json_bytes(_workspace_ownership(root)))
    real_read = doctor_core.read_bytes_safe

    def fail_marker(path: Path, **kwargs: object) -> StableBytesResult:
        if path == marker:
            return StableBytesResult(b"", "Doctor metadata changed while it was being read")
        return real_read(path, **kwargs)

    monkeypatch.setattr(doctor_core, "read_bytes_safe", fail_marker)
    result = doctor_workspace(root)

    finding = next(item for item in result["findings"] if item["code"] == "ownership_metadata_unreadable")
    assert finding["classification"] == "unreadable"


def test_doctor_classifies_yaml_permission_failure_as_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = root / ".agent/workspace.yaml"
    _write(workspace, b"workspace: {}\npaths: {}\n")
    real_load = doctor_core.load_yaml_safe

    def deny(path: Path) -> YamlLoadResult:
        if path == workspace:
            return YamlLoadResult({}, "Permission denied")
        return real_load(path)

    monkeypatch.setattr(doctor_core, "load_yaml_safe", deny)
    result = doctor_workspace(root)

    finding = next(
        item for item in result["findings"] if item["code"] == "control_plane_yaml_unreadable"
    )
    assert finding["classification"] == "unreadable"


def test_doctor_injected_reparse_rejection_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    history = root / ".agent/history"
    _write(history / "2026/07/15/session/session.yaml", b"format: archmarshal-session-v1\n")
    real_link_check = doctor_core.is_link_or_reparse
    monkeypatch.setattr(
        doctor_core,
        "is_link_or_reparse",
        lambda path: path == history or real_link_check(path),
    )

    first = doctor_workspace(root)
    second = doctor_workspace(root)

    assert first == second
    assert any(item["code"] == "session_link_rejected" for item in first["findings"])
    assert not any(item["path"].endswith("session.yaml") for item in first["findings"])


def test_doctor_exposes_byte_entry_and_depth_budget_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _write(root / ".agent/ownership.json", _json_bytes(_workspace_ownership(root)))
    _write(root / ".agent/history/a/b/c/session.yaml", b"format: archmarshal-session-v1\n")
    monkeypatch.setattr(doctor_core, "MAX_SCAN_BYTES", 8)
    monkeypatch.setattr(doctor_core, "MAX_DIRECTORY_ENTRIES", 2)
    monkeypatch.setattr(doctor_sessions, "MAX_SCAN_DEPTH", 0)

    result = doctor_workspace(root)
    reasons = {item["reason"] for item in result["budgets"]["truncations"]}

    assert "cumulative_byte_limit" in reasons
    assert "directory_entry_limit" in reasons or "recursion_depth" in reasons
    assert result["budgets"]["truncated"] is True


def test_doctor_inspects_transactions_and_committed_session_integrity(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    transaction = root / ".agent/transactions/adoption/tx-1"
    _write(
        root / ".agent/transactions/adoption/ACTIVE",
        _json_bytes({"transaction_id": "tx-1", "journal_sha256": "a" * 64}),
    )
    _write(
        transaction / "journal.json",
        _json_bytes({"format": "archmarshal-adoption-transaction-v1"}),
    )
    partial = root / ".agent/transactions/adoption/tx-2"
    partial.mkdir(parents=True)

    session_dir = root / ".agent/history/2026/07/15/committed"
    session_bytes = b"format: archmarshal-session-v2\n"
    _write(session_dir / "session.yaml", session_bytes)
    _write(
        session_dir / "COMMITTED.json",
        _json_bytes(
            {
                "format": "archmarshal-session-commit-v1",
                "file_count": 1,
                "files": [
                    {
                        "path": "session.yaml",
                        "bytes": len(session_bytes),
                        "sha256": "0" * 64,
                    }
                ],
            }
        ),
    )

    result = doctor_workspace(root)
    codes = {item["code"] for item in result["findings"]}

    assert "adoption_transaction_format_current" in codes
    assert "adoption_transaction_active" in codes
    assert "adoption_transaction_partial" in codes
    assert "session_commit_format_current" in codes
    assert "session_file_digest_mismatch" in codes


def test_doctor_reads_current_user_chain_and_flags_invalid_v2_commit(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    store = tmp_path / "store"
    root.mkdir()
    store.mkdir()
    _write(store / "ownership.json", _json_bytes(_store_ownership(store)))
    package_digest = "d" * 64
    generation_digest, generation = _user_generation(package_digest)
    _write(store / f".archmarshal/objects/sha256/{generation_digest}.json", generation)
    _write(store / ".archmarshal/HEAD", f"{generation_digest}\n".encode())
    _write(
        store / f".archmarshal/packages/sha256/{package_digest}/COMMITTED.json",
        _json_bytes(
            {
                "format": "archmarshal-user-skill-package-v2",
                "package_sha256": "e" * 64,
                "snapshot_format": "archmarshal-user-skill-snapshot-v2",
                "source_mutation": False,
            }
        ),
    )

    result = doctor_workspace(root, user_store=store)
    codes = {item["code"] for item in result["findings"]}

    assert "user_store_generation_format_current" in codes
    assert "user_store_generation_structure_invalid" in codes
    assert "user_skill_package_format_current" in codes
    assert "user_store_package_commit_invalid" in codes
    assert "user_store_package_integrity_failed" in codes
    assert "user_store_orphan_package" not in codes


def test_doctor_verifies_v2_package_content_without_mutating_store(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    store = tmp_path / "store"
    root.mkdir()
    store.mkdir()
    _write(store / "ownership.json", _json_bytes(_store_ownership(store)))
    staging = store / ".archmarshal/packages/sha256/staging"
    skill_bytes = (
        b"---\nname: verified\ndescription: Use when a verified reusable workflow is needed.\n"
        b"---\n\n# verified\n"
    )
    manifest = {
        "id": "skill.common-project.verified",
        "name": "verified",
        "kind": "common_project_skill",
        "version": "1.0.0",
        "status": "active",
        "priority": "normal",
        "scope": "common_project",
        "summary": "Verified reusable workflow.",
        "tags": ["verified"],
        "triggers": ["verified reusable workflow"],
        "negative_triggers": ["unrelated workflow"],
    }
    manifest_bytes = yaml.safe_dump(manifest, sort_keys=False).encode()
    _write(staging / "SKILL.md", skill_bytes)
    _write(staging / "manifest.yaml", manifest_bytes)
    snapshot = _snapshot_package_v2(staging, purpose="Doctor test package")
    manifest_digest = hashlib.sha256(
        json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    commit = _package_v2_commit(snapshot, manifest_digest=manifest_digest)
    package = staging.with_name(snapshot["sha256"])
    staging.rename(package)
    _write(package / "COMMITTED.json", _json_bytes(commit))
    before = _tree_state(store)

    verified = doctor_workspace(root, user_store=store)

    assert _tree_state(store) == before
    assert any(
        item["code"] == "user_store_package_integrity_verified"
        for item in verified["findings"]
    )
    (package / "SKILL.md").write_bytes(skill_bytes + b"tampered\n")
    corrupt = doctor_workspace(root, user_store=store)
    assert any(
        item["code"] == "user_store_package_integrity_failed"
        for item in corrupt["findings"]
    )


@pytest.mark.parametrize("value", [True, 0, 101, "2"])
def test_doctor_rejects_invalid_history_limits(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError):
        doctor_workspace(tmp_path, history_limit=value)  # type: ignore[arg-type]
