from __future__ import annotations

import os

import pytest

from archmarshal.safety import _native_link_path, create_bytes_exclusive


def test_atomic_publication_handles_long_paths_and_preserves_existing_file(tmp_path):
    directory = tmp_path / ("a" * 80) / ("b" * 80) / ("c" * 80)
    target = directory / ("d" * 64)
    assert len(str(target)) > 300
    create_bytes_exclusive(target, b"complete payload", mode=0o600)
    target = type(target)(_native_link_path(target))
    directory = target.parent
    assert target.read_bytes() == b"complete payload"
    with pytest.raises(FileExistsError):
        create_bytes_exclusive(target, b"must never replace")
    assert target.read_bytes() == b"complete payload"
    assert list(directory.iterdir()) == [target]


@pytest.mark.skipif(os.name == "nt", reason="POSIX umask contract")
def test_atomic_publication_respects_restrictive_umask(tmp_path):
    previous = os.umask(0o077)
    try:
        target = tmp_path / "private"
        create_bytes_exclusive(target, b"private", mode=0o644)
    finally:
        os.umask(previous)
    assert target.stat().st_mode & 0o777 == 0o600
