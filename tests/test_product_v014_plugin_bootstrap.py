from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "archmarshal"
WRAPPER = PLUGIN_ROOT / "scripts" / "invoke_archmarshal.py"
LOCK = PLUGIN_ROOT / "engine.lock.json"


def _wrapper(name: str = "archmarshal_plugin_bootstrap"):
    spec = importlib.util.spec_from_file_location(name, WRAPPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_lock_is_current_and_binds_plugin_identity() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/build_plugin_engine_lock.py", "--check"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    assert completed.returncode == 0, completed.stderr
    assert lock["format"] == "archmarshal-plugin-engine-lock-v1"
    assert lock["engine_version"] == "0.15.0"
    assert lock["engine_api"] == "archmarshal-engine-api-v1"
    assert lock["file_count"] == len(lock["files"])
    assert lock["content_bytes"] == sum(item["bytes"] for item in lock["files"])


def test_bootstrap_status_verifies_checkout_without_importing_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, str(WRAPPER), "--bootstrap-status"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert payload["mode"] == "ready"
    assert payload["marketplace"] == "archmarshal"
    assert payload["source_kind"] == "checkout"
    assert payload["verified"] is True
    assert payload["dependency_imported"] is False
    assert payload["engine_version"] == "0.15.0"
    assert len(payload["source_tree_sha256"]) == 64


def test_same_version_tampered_engine_is_rejected_before_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = _wrapper("archmarshal_plugin_tamper")
    source = tmp_path / "src"
    shutil.copytree(REPOSITORY_ROOT / "src" / "archmarshal", source / "archmarshal")
    init = source / "archmarshal" / "__init__.py"
    assert '0.15.0' in init.read_text(encoding="utf-8")
    (source / "archmarshal" / "cli.py").write_text(
        (source / "archmarshal" / "cli.py").read_text(encoding="utf-8") + "\n# tampered\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(wrapper, "_locate_engine_source", lambda plugin_root: (source, "test"))

    with pytest.raises(wrapper.BootstrapError) as raised:
        wrapper._bootstrap()

    assert raised.value.code == "archmarshal_engine_lock_verification_failed"


def test_wrapper_never_falls_back_to_an_ambient_installed_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = _wrapper("archmarshal_plugin_no_ambient")
    plugin_root = tmp_path / "cache" / "archmarshal"
    plugin_root.mkdir(parents=True)
    monkeypatch.setattr(wrapper, "_configured_marketplace_source", lambda: None)

    with pytest.raises(wrapper.BootstrapError) as raised:
        wrapper._locate_engine_source(plugin_root)

    assert raised.value.code == "archmarshal_engine_unavailable"


def test_duplicate_archmarshal_marketplace_identity_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = _wrapper("archmarshal_plugin_ambiguous")
    monkeypatch.setattr(
        wrapper,
        "_marketplace_roots",
        lambda: [tmp_path / "one", tmp_path / "two"],
    )

    with pytest.raises(wrapper.BootstrapError) as raised:
        wrapper._configured_marketplace_source()

    assert raised.value.code == "archmarshal_marketplace_ambiguous"
