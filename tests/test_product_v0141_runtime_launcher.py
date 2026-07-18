from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "plugins" / "archmarshal" / "scripts" / "run_archmarshal.py"


def _launcher():
    spec = importlib.util.spec_from_file_location("archmarshal_runtime_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_defaults_to_active_python_without_creating_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _launcher()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, check: calls.append(command)
        or subprocess.CompletedProcess(command, 0),
    )

    assert launcher.main(["--bootstrap-status"]) == 0
    assert calls[0][0] == sys.executable
    assert calls[0][1] == "-I"
    assert calls[0][2].endswith("invoke_archmarshal.py")
    assert not (tmp_path / "runtimes").exists()


def test_launcher_accepts_only_commit_scoped_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _launcher()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    commit = "a" * 40
    runtime = tmp_path / "runtimes" / "archmarshal" / commit
    runtime.mkdir(parents=True)
    interpreter = runtime / ("python.exe" if sys.platform == "win32" else "python")
    shutil.copy2(sys.executable, interpreter)
    pointer = runtime.parent / "current.json"
    pointer.write_text(
        json.dumps(
            {
                "format": "archmarshal-runtime-v1",
                "commit": commit,
                "engine_version": launcher.REQUIRED_ENGINE_VERSION,
                "python": str(interpreter),
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, check: calls.append(command)
        or subprocess.CompletedProcess(command, 0),
    )

    assert launcher.main(["doctor", "missing"]) == 0
    assert Path(calls[0][0]).resolve() == interpreter.resolve()


def test_bootstrap_status_bypasses_a_broken_runtime_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _launcher()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    pointer = tmp_path / "runtimes" / "archmarshal" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("not-json", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, check: calls.append(command)
        or subprocess.CompletedProcess(command, 0),
    )

    assert launcher.main(["--bootstrap-status"]) == 0
    assert calls[0][0] == sys.executable


def test_real_bootstrap_status_survives_a_broken_runtime_pointer(tmp_path: Path) -> None:
    pointer = tmp_path / "runtimes" / "archmarshal" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("not-json", encoding="utf-8")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(tmp_path)

    completed = subprocess.run(
        [sys.executable, str(LAUNCHER), "--bootstrap-status"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "ready"
    assert payload["verified"] is True
    assert payload["dependency_imported"] is False


def test_launcher_skips_a_valid_runtime_for_an_older_engine_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _launcher()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    commit = "c" * 40
    runtime = tmp_path / "runtimes" / "archmarshal" / commit
    runtime.mkdir(parents=True)
    interpreter = runtime / ("python.exe" if sys.platform == "win32" else "python")
    shutil.copy2(sys.executable, interpreter)
    pointer = runtime.parent / "current.json"
    pointer.write_text(
        json.dumps(
            {
                "format": "archmarshal-runtime-v1",
                "commit": commit,
                "engine_version": "0.15.0",
                "python": str(interpreter),
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, check: calls.append(command)
        or subprocess.CompletedProcess(command, 0),
    )

    assert launcher.main(["doctor", "missing"]) == 0
    assert calls[0][0] == sys.executable


@pytest.mark.parametrize(
    "payload",
    [
        {"format": "wrong", "commit": "a" * 40, "engine_version": "0.17.0"},
        {
            "format": "archmarshal-runtime-v1",
            "commit": "not-a-commit",
            "engine_version": "0.17.0",
            "python": "outside",
        },
        {
            "format": "archmarshal-runtime-v1",
            "commit": "a" * 40,
            "engine_version": "not-a-version",
            "python": "outside",
        },
        {
            "format": "archmarshal-runtime-v1",
            "commit": "a" * 40,
            "engine_version": "0.17.0",
            "python": "outside",
            "unexpected": "field",
        },
    ],
)
def test_launcher_rejects_structurally_invalid_pointer_without_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: dict[str, str],
) -> None:
    launcher = _launcher()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    pointer = tmp_path / "runtimes" / "archmarshal" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("invalid pointer must not run a process"),
    )

    assert launcher.main(["doctor", "missing"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "archmarshal_runtime_invalid"


def test_launcher_rejects_interpreter_outside_commit_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = _launcher()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    commit = "b" * 40
    runtime = tmp_path / "runtimes" / "archmarshal" / commit
    runtime.mkdir(parents=True)
    pointer = runtime.parent / "current.json"
    pointer.write_text(
        json.dumps(
            {
                "format": "archmarshal-runtime-v1",
                "commit": commit,
                "engine_version": launcher.REQUIRED_ENGINE_VERSION,
                "python": sys.executable,
            }
        ),
        encoding="utf-8",
    )

    assert launcher.main(["doctor", "missing"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "archmarshal_runtime_invalid"
