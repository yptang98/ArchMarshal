"""Opt-in real kernel ENOSPC/EACCES checks on an explicitly bounded tmpfs."""
from __future__ import annotations

import errno
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Reuse the real two-project fixture when this file is run independently in CI.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_portability import environment as environment
from test_portability import upload

import archmarshal.skill_sync as skill_sync
from archmarshal.operation_journal import operation_status
from archmarshal.operation_recovery import recover_operation
from archmarshal.skill_sync import pull_skill

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("ARCHMARSHAL_FILESYSTEM_INTEGRATION") != "1",
    reason="Requires the dedicated Linux filesystem-failure CI job")


@pytest.fixture
def tmp_path():
    assert os.geteuid() != 0, "Permission failures must be exercised as an unprivileged user"
    mount = Path(os.environ["ARCHMARSHAL_FAILURE_FS"]).resolve(strict=True)
    mounts = Path("/proc/mounts").read_text().splitlines()
    assert any(row.split()[1] == str(mount) and row.split()[2] == "tmpfs" for row in mounts)
    capacity = os.statvfs(mount)
    assert 0 < capacity.f_blocks * capacity.f_frsize <= 32 * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="failure-check-", dir=mount) as directory:
        yield Path(directory)


def fill_tmpfs(path):
    written = 0
    with path.open("wb", buffering=0) as handle:
        try:
            while written <= 32 * 1024 * 1024:
                written += handle.write(b"x" * (64 * 1024))
        except OSError as exc:
            assert exc.errno == errno.ENOSPC
            return
    raise AssertionError("The bounded tmpfs did not produce a real ENOSPC")


@pytest.mark.parametrize("failure", ["disk-full", "permission-denied"])
@pytest.mark.parametrize("stage", ["before-staging", "after-staging"])
def test_real_filesystem_failure_preserves_input_and_allows_reviewed_retry(
        environment, monkeypatch, tmp_path, failure, stage):
    roots, profiles, store = environment
    root = roots[1]
    key = upload(environment)
    args = dict(device_path=profiles[1])
    preview = pull_skill(root, key, store, **args)
    filler = tmp_path / "explicit-tmpfs-filler"
    protected = root / ".agent"
    if stage == "after-staging":
        protected = root / ".agent/inbox"
    protected.mkdir(exist_ok=True)
    original_mode = protected.stat().st_mode & 0o777
    def induce():
        if failure == "disk-full":
            fill_tmpfs(filler)
        else:
            protected.chmod(0o500)
    original = skill_sync.prepare_operation
    def prepared(*a, **kw):
        result = original(*a, **kw)
        induce()
        return result
    try:
        if stage == "before-staging":
            induce()
        else:
            monkeypatch.setattr(skill_sync, "prepare_operation", prepared)
        with pytest.raises(OSError) as caught:
            pull_skill(root, key, store, **args, apply=True, expected_plan=preview["plan_digest"])
        assert caught.value.errno == (errno.ENOSPC if failure == "disk-full" else errno.EACCES)
    finally:
        protected.chmod(original_mode)
        if filler.exists():
            with filler.open("wb"):
                pass  # Release only the bytes created by this test.
        monkeypatch.setattr(skill_sync, "prepare_operation", original)
    assert not (Path(preview["package"]).parent / "RECEIPT.json").exists()
    if stage == "after-staging":
        states = operation_status(root)["operations"]
        assert len(states) == 1
        recovery = recover_operation(root, states[0]["operation_id"])
        result = recover_operation(root, states[0]["operation_id"], apply=True,
                                   expected_plan=recovery["plan_digest"])
        assert result["stage"] == "complete"
    else:
        result = pull_skill(root, key, store, **args, apply=True, expected_plan=preview["plan_digest"])
        assert result["mode"] == "imported"
    assert (root / "source.py").read_text() == "print('original')\n"
