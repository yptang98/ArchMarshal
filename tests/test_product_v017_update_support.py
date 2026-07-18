from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "plugins" / "archmarshal" / "scripts" / "update_support.py"
OFFICIAL = "https://github.com/yptang98/ArchMarshal.git"


def _support():
    spec = importlib.util.spec_from_file_location("archmarshal_update_support", SUPPORT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_installation(tmp_path: Path) -> tuple[Path, Path, Path]:
    marketplace = tmp_path / "marketplace"
    plugin = tmp_path / "plugin-cache" / "archmarshal"
    codex_home = tmp_path / "codex-home"
    for path in (
        marketplace / ".agents" / "plugins",
        marketplace / "src" / "archmarshal",
        plugin / ".codex-plugin",
        plugin / "scripts",
        codex_home,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (marketplace / ".agents" / "plugins" / "marketplace.json").write_text(
        '{"name":"archmarshal","plugins":[]}', encoding="utf-8"
    )
    (marketplace / "src" / "archmarshal" / "__init__.py").write_text(
        '__version__ = "0.16.1"\n', encoding="utf-8"
    )
    (marketplace / "src" / "archmarshal" / "engine.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (marketplace / "pyproject.toml").write_text(
        '[project]\nname = "archmarshal"\nversion = "0.16.1"\n', encoding="utf-8"
    )
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        '{"name":"archmarshal","version":"0.16.1"}', encoding="utf-8"
    )
    (plugin / "engine.lock.json").write_text('{"format":"lock"}', encoding="utf-8")
    (plugin / "scripts" / "run_archmarshal.py").write_text(
        "print('ready')\n", encoding="utf-8"
    )
    return codex_home, marketplace, plugin


def test_create_and_verify_minimal_last_known_good_capsule(tmp_path: Path) -> None:
    support = _support()
    codex_home, marketplace, plugin = _old_installation(tmp_path)
    pointer = codex_home / "runtimes" / "archmarshal" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text('{"format":"archmarshal-runtime-v1"}', encoding="utf-8")

    created = support.create_capsule(
        codex_home=codex_home,
        marketplace_root=marketplace,
        plugin_root=plugin,
        old_repository=OFFICIAL,
        old_commit="a" * 40,
        old_version="0.16.1",
        output=Path("capsule"),
    )

    capsule = Path(created["capsule"])
    assert created["verified"] is True
    assert (capsule / "CAPSULE.json").is_file()
    assert (capsule / "runtime" / "current.json").is_file()
    assert (capsule / "plugins" / "archmarshal" / "engine.lock.json").is_file()
    verified = support.verify_capsule(capsule)
    assert verified["verified"] is True
    manifest = json.loads((capsule / "CAPSULE.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "archmarshal-update-capsule-v1"
    assert all("codex-home" not in item["path"] for item in manifest["files"])
    assert manifest["rollback"]["runtime_pointer"] == "runtime/current.json"
    assert manifest["rollback"]["pinned_marketplace"][2].endswith("a" * 40)
    assert "update_support.py materialize" in manifest["rollback"][
        "temporary_capsule_marketplace"
    ][0]


def test_capsule_verification_detects_tampering(tmp_path: Path) -> None:
    support = _support()
    codex_home, marketplace, plugin = _old_installation(tmp_path)
    created = support.create_capsule(
        codex_home=codex_home,
        marketplace_root=marketplace,
        plugin_root=plugin,
        old_repository=OFFICIAL,
        old_commit="b" * 40,
        old_version="0.16.1",
        output=Path("capsule"),
    )
    capsule = Path(created["capsule"])
    (capsule / "pyproject.toml").write_text("tampered", encoding="utf-8")

    with pytest.raises(support.CapsuleError, match="hash mismatch"):
        support.verify_capsule(capsule)


def test_capsule_verification_rejects_uncommitted_extra_file(tmp_path: Path) -> None:
    support = _support()
    codex_home, marketplace, plugin = _old_installation(tmp_path)
    created = support.create_capsule(
        codex_home=codex_home,
        marketplace_root=marketplace,
        plugin_root=plugin,
        old_repository=OFFICIAL,
        old_commit="c" * 40,
        old_version="0.16.1",
        output=Path("capsule"),
    )
    capsule = Path(created["capsule"])
    (capsule / "extra.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(support.CapsuleError, match="uncommitted extra"):
        support.verify_capsule(capsule)


def test_capsule_must_be_new_and_below_codex_backup_root(tmp_path: Path) -> None:
    support = _support()
    codex_home, marketplace, plugin = _old_installation(tmp_path)
    outside = tmp_path / "outside"

    with pytest.raises(support.CapsuleError, match="direct child"):
        support.create_capsule(
            codex_home=codex_home,
            marketplace_root=marketplace,
            plugin_root=plugin,
            old_repository=OFFICIAL,
            old_commit="d" * 40,
            old_version="0.16.1",
            output=outside,
        )

    first = support.create_capsule(
        codex_home=codex_home,
        marketplace_root=marketplace,
        plugin_root=plugin,
        old_repository=OFFICIAL,
        old_commit="d" * 40,
        old_version="0.16.1",
        output=Path("capsule"),
    )
    with pytest.raises(support.CapsuleError, match="already exists"):
        support.create_capsule(
            codex_home=codex_home,
            marketplace_root=marketplace,
            plugin_root=plugin,
            old_repository=OFFICIAL,
            old_commit="d" * 40,
            old_version="0.16.1",
            output=Path(first["capsule"]),
        )


def test_capsule_rejects_unofficial_repository(tmp_path: Path) -> None:
    support = _support()
    codex_home, marketplace, plugin = _old_installation(tmp_path)

    with pytest.raises(support.CapsuleError, match="official ArchMarshal origin"):
        support.create_capsule(
            codex_home=codex_home,
            marketplace_root=marketplace,
            plugin_root=plugin,
            old_repository="https://example.com/ArchMarshal.git",
            old_commit="e" * 40,
            old_version="0.16.1",
            output=Path("capsule"),
        )


def test_real_repository_capsule_is_independently_bootstrappable(tmp_path: Path) -> None:
    support = _support()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    plugin_root = ROOT / "plugins" / "archmarshal"
    plugin_manifest = json.loads(
        (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    created = support.create_capsule(
        codex_home=codex_home,
        marketplace_root=ROOT,
        plugin_root=plugin_root,
        old_repository=OFFICIAL,
        old_commit="f" * 40,
        old_version=plugin_manifest["version"],
        output=Path("capsule"),
    )
    capsule = Path(created["capsule"])
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)

    completed = subprocess.run(
        [
            sys.executable,
            str(capsule / "plugins" / "archmarshal" / "scripts" / "run_archmarshal.py"),
            "--bootstrap-status",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "ready"
    assert payload["source_kind"] == "checkout"
    assert payload["verified"] is True
    doctor_root = tmp_path / "doctor-root-does-not-exist"
    doctor = subprocess.run(
        [
            sys.executable,
            str(capsule / "plugins" / "archmarshal" / "scripts" / "run_archmarshal.py"),
            "doctor",
            str(doctor_root),
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert doctor.returncode == 0, doctor.stderr
    assert not doctor_root.exists()
    assert not list(capsule.rglob("__pycache__"))
    assert support.verify_capsule(capsule)["verified"] is True


def test_materialized_recovery_is_disposable_and_capsule_stays_sealed(
    tmp_path: Path,
) -> None:
    support = _support()
    codex_home, marketplace, plugin = _old_installation(tmp_path)
    created = support.create_capsule(
        codex_home=codex_home,
        marketplace_root=marketplace,
        plugin_root=plugin,
        old_repository=OFFICIAL,
        old_commit="9" * 40,
        old_version="0.16.1",
        output=Path("capsule"),
    )
    capsule = Path(created["capsule"])

    result = support.materialize_recovery(
        capsule=capsule,
        codex_home=codex_home,
        output=Path("recovery"),
    )

    recovery = Path(result["recovery"])
    assert result["capsule_verified"] is True
    assert (recovery / ".agents" / "plugins" / "marketplace.json").is_file()
    assert (recovery / "plugins" / "archmarshal" / "engine.lock.json").is_file()
    assert (recovery / "RECOVERY.json").is_file()
    assert not (recovery / "runtime" / "current.json").exists()
    (recovery / "generated-cache.txt").write_text("disposable", encoding="utf-8")
    assert support.verify_capsule(capsule)["verified"] is True
