from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from archmarshal import safety
from archmarshal.errors import ArchMarshalError


def _backup(root: Path, name: str, content: str) -> Path:
    root.mkdir()
    source = root / "payload.txt"
    source.write_text(content, encoding="utf-8")
    result = safety.create_backup(
        root,
        [source],
        root / ".agent" / "backups" / name,
        reason="Descriptor-bound verification test.",
    )
    return root / str(result["path"])


def test_verify_backup_derives_manifest_size_and_hash_from_one_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _backup(tmp_path / "project", "snapshot.zip", "reviewed bytes\n")
    expected_bytes = archive.read_bytes()
    real_stat = Path.stat
    real_sha256_file = safety.sha256_file

    def reject_archive_path_stat(path: Path, *args, **kwargs):
        if (
            path.absolute() == archive.absolute()
            and kwargs.get("follow_symlinks", True) is not False
        ):
            raise AssertionError("verify_backup reopened archive metadata by path")
        return real_stat(path, *args, **kwargs)

    def reject_archive_path_hash(path: Path, *args, **kwargs):
        if Path(path).absolute() == archive.absolute():
            raise AssertionError("verify_backup rehashed archive by path")
        return real_sha256_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", reject_archive_path_stat)
    monkeypatch.setattr(safety, "sha256_file", reject_archive_path_hash)

    verified = safety.verify_backup(archive)

    assert verified["verified"] is True
    assert verified["archive_bytes"] == len(expected_bytes)
    assert verified["sha256"] == hashlib.sha256(expected_bytes).hexdigest()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits replacing an open path entry")
def test_verify_backup_rejects_path_replacement_after_descriptor_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _backup(tmp_path / "first", "snapshot.zip", "first archive\n")
    replacement = _backup(tmp_path / "second", "replacement.zip", "second archive\n")
    first_bytes = archive.read_bytes()
    second_bytes = replacement.read_bytes()
    real_open = safety.os.open
    replaced = False

    def open_then_replace(path, flags, *args, **kwargs):
        nonlocal replaced
        descriptor = real_open(path, flags, *args, **kwargs)
        if not replaced and Path(path).absolute() == archive.absolute():
            os.replace(replacement, archive)
            replaced = True
        return descriptor

    monkeypatch.setattr(safety.os, "open", open_then_replace)

    with pytest.raises(ArchMarshalError) as raised:
        safety.verify_backup(archive)

    assert replaced is True
    assert raised.value.code == "backup_archive_changed"
    assert first_bytes != second_bytes
    assert archive.read_bytes() == second_bytes
