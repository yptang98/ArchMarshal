from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path

import pytest

from archmarshal import adoption_tx, safety
from archmarshal.errors import ArchMarshalError
from archmarshal.io import StableBytesResult


def _json_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _active_transaction_fixture(root: Path) -> tuple[Path, Path, dict[str, object], bytes]:
    source = root / "user-source.txt"
    source.write_text("preserve me\n", encoding="utf-8")
    backup = safety.create_backup(
        root,
        [source],
        root / ".agent" / "backups" / "pre-adoption.zip",
        reason="Recovery test backup.",
    )

    transaction_id = "1" * 32
    transaction_root = root / adoption_tx.STATE_RELATIVE / transaction_id
    payload_root = transaction_root / "payloads"
    payload_root.mkdir(parents=True)
    payload = b"reviewed control bytes\n"
    (payload_root / "00000.bin").write_bytes(payload)
    journal: dict[str, object] = {
        "format": adoption_tx.FORMAT,
        "transaction_id": transaction_id,
        "created_at": "2026-07-15T00:00:00+00:00",
        "plan_digest": "2" * 64,
        "phase": "prepared",
        "files": [
            {
                "path": "reviewed-control.txt",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "staged": "payloads/00000.bin",
                "precondition": "absent",
            }
        ],
        "file_count": 1,
        "content_bytes": len(payload),
        "backup": {
            "path": backup["path"],
            "sha256": backup["sha256"],
            "bytes": backup["bytes"],
            "verified": True,
        },
        "skill_index_plan": {"changed": False, "disabled": True},
        "source_mutation": False,
    }
    journal_bytes = _json_bytes(journal)
    journal_path = transaction_root / adoption_tx.JOURNAL_NAME
    journal_path.write_bytes(journal_bytes)
    active_path = root / adoption_tx.STATE_RELATIVE / adoption_tx.ACTIVE_NAME
    active_path.write_bytes(
        _json_bytes(
            {
                "transaction_id": transaction_id,
                "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
            }
        )
    )
    return active_path, journal_path, journal, journal_bytes


def _replace_after_stable_read(path: Path, content: bytes) -> None:
    replacement = path.with_name(f".{path.name}.race")
    replacement.write_bytes(content)
    os.replace(replacement, path)


def test_active_and_journal_parse_the_same_stable_bytes_and_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    active_path, journal_path, journal, _ = _active_transaction_fixture(root)
    raced_journal = deepcopy(journal)
    raced_journal["files"][0]["path"] = "unreviewed-race.txt"  # type: ignore[index]

    real_reader = adoption_tx.read_bytes_safe
    replaced: list[str] = []

    def read_then_replace(path: Path, *, max_bytes: int, label: str) -> StableBytesResult:
        result = real_reader(path, max_bytes=max_bytes, label=label)
        if path == active_path:
            _replace_after_stable_read(path, result.data)
            replaced.append("active")
        elif path == journal_path:
            _replace_after_stable_read(path, _json_bytes(raced_journal))
            replaced.append("journal")
        return result

    monkeypatch.setattr(adoption_tx, "read_bytes_safe", read_then_replace)

    loaded = adoption_tx._load_active_journal(root)

    assert replaced == ["active", "journal"]
    assert loaded["files"][0]["path"] == "reviewed-control.txt"
    assert journal_path.read_bytes() == _json_bytes(raced_journal)
    with pytest.raises(ArchMarshalError) as raised:
        adoption_tx._clear_active(
            root,
            str(loaded["transaction_id"]),
            loaded["_active_identity"],
            loaded["_active_bytes"],
        )
    assert raised.value.code == "adoption_transaction_conflict"
    assert active_path.exists(), "a replaced ACTIVE marker must never be deleted"


def _full_backup(root: Path) -> Path:
    root.mkdir()
    (root / "secret.txt").write_text("sensitive restore content\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(root, 0o755)
    created = safety.create_backup(
        root,
        [],
        root / ".agent" / "backups" / "full.zip",
        reason="Full restore safety test.",
        scope="full_workspace",
    )
    return root / str(created["path"])


def test_restore_target_competition_preserves_staging_as_0700(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _full_backup(tmp_path / "source")
    destination = tmp_path / "restored"
    preview = safety.restore_backup(archive, destination)

    def competing_publish(source: Path, target: Path) -> None:
        target.mkdir()
        raise ArchMarshalError(
            "restore_destination_exists",
            "Concurrent restore destination won publication.",
            details={"destination": str(target), "staging": str(source)},
        )

    monkeypatch.setattr(safety, "_publish_directory_exclusive", competing_publish)

    with pytest.raises(ArchMarshalError) as raised:
        safety.restore_backup(
            archive,
            destination,
            apply=True,
            expected_plan=preview["plan_digest"],
        )

    assert raised.value.code == "restore_destination_exists"
    assert raised.value.details["published"] is False
    assert raised.value.details["staging_identity_verified"] is True
    assert raised.value.details["staging_private"] is (os.name != "nt")
    staging = Path(str(raised.value.details["staging"]))
    assert staging.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(staging.stat().st_mode) == 0o700
    assert (staging / "secret.txt").read_text(encoding="utf-8").startswith("sensitive")


def test_restore_reports_post_publish_mode_failure_without_claiming_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _full_backup(tmp_path / "source")
    destination = tmp_path / "restored"
    preview = safety.restore_backup(archive, destination)
    recorded_mode = safety.verify_backup(archive)["manifest"]["root_mode"]

    def fail_final_mode(target: Path, identity: tuple[int, int], mode: int) -> None:
        assert target == destination
        assert identity == (target.stat().st_dev, target.stat().st_ino)
        assert mode == recorded_mode
        if os.name != "nt":
            assert stat.S_IMODE(target.stat().st_mode) == 0o700
        raise OSError("injected post-publication chmod failure")

    monkeypatch.setattr(safety, "_apply_published_root_mode", fail_final_mode)

    with pytest.raises(ArchMarshalError) as raised:
        safety.restore_backup(
            archive,
            destination,
            apply=True,
            expected_plan=preview["plan_digest"],
        )

    assert raised.value.code == "restore_published_incomplete"
    assert raised.value.details["published"] is True
    assert raised.value.details["staging"] is None
    assert raised.value.details["root_mode_applied"] is False
    assert raised.value.details["destination_identity_verified"] is True
    assert destination.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert (destination / "secret.txt").is_file()
    assert not list(tmp_path.glob(".amr-*"))


def test_restore_applies_root_mode_only_after_atomic_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _full_backup(tmp_path / "source")
    destination = tmp_path / "restored"
    preview = safety.restore_backup(archive, destination)
    real_apply = safety._apply_published_root_mode
    observed: list[int] = []

    def inspect_then_apply(target: Path, identity: tuple[int, int], mode: int) -> None:
        assert target == destination
        observed.append(
            stat.S_IMODE(target.stat().st_mode) if os.name != "nt" else 0o700
        )
        real_apply(target, identity, mode)

    monkeypatch.setattr(safety, "_apply_published_root_mode", inspect_then_apply)

    result = safety.restore_backup(
        archive,
        destination,
        apply=True,
        expected_plan=preview["plan_digest"],
    )

    assert result["mode"] == "restored"
    assert observed == [0o700]
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o755


def test_restore_identity_helpers_fail_closed_for_missing_or_replaced_roots(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    assert safety._path_has_identity(missing, None) is False
    assert safety._path_has_identity(missing, (1, 1)) is False
    assert safety._private_restore_staging(missing, (1, 1)) is False

    target = tmp_path / "published"
    target.mkdir()
    actual = target.stat()
    wrong_identity = (actual.st_dev, actual.st_ino + 1)
    assert safety._path_has_identity(target, wrong_identity) is False
    assert safety._private_restore_staging(target, wrong_identity) is False
    with pytest.raises(ArchMarshalError) as raised:
        safety._apply_published_root_mode(target, wrong_identity, 0o700)
    assert raised.value.code == "restore_destination_replaced"
