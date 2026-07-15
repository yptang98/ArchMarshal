from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

import archmarshal

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "archmarshal"
WRAPPER = PLUGIN_ROOT / "scripts" / "invoke_archmarshal.py"


def test_codex_plugin_manifest_marketplace_and_skill_are_discoverable() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads(
        (REPOSITORY_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    skill = PLUGIN_ROOT / "skills/manage-agent-workspace/SKILL.md"
    skill_text = skill.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(skill_text.split("---", 2)[1])
    openai = yaml.safe_load(
        (skill.parent / "agents/openai.yaml").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "archmarshal"
    assert manifest["version"] == archmarshal.__version__
    assert manifest["skills"] == "./skills/"
    assert manifest["interface"]["displayName"] == "ArchMarshal"
    assert manifest["interface"]["defaultPrompt"]
    assert marketplace["plugins"] == [
        {
            "name": "archmarshal",
            "source": {"source": "local", "path": "./plugins/archmarshal"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }
    ]
    assert frontmatter.keys() == {"name", "description"}
    assert frontmatter["name"] == "manage-agent-workspace"
    assert "ArchMarshal" in frontmatter["description"]
    assert "$manage-agent-workspace" in openai["interface"]["default_prompt"]
    assert "[TODO:" not in skill_text


def test_codex_plugin_wrapper_uses_checkout_engine_and_keeps_doctor_read_only(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    completed = subprocess.run(
        [sys.executable, str(WRAPPER), "doctor", str(missing)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(completed.stdout)

    assert payload["state"] == "absent"
    assert payload["source_mutation"] is False
    assert payload["filesystem_safety"]["anchored_components"] is False
    assert not missing.exists()


def test_codex_plugin_wrapper_fails_closed_on_engine_version_mismatch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    spec = importlib.util.spec_from_file_location("archmarshal_plugin_wrapper", WRAPPER)
    assert spec is not None and spec.loader is not None
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    monkeypatch.setattr(archmarshal, "__version__", "0.0.0")
    target = tmp_path / "must-not-exist"

    assert wrapper.main(["doctor", str(target)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "archmarshal_engine_version_mismatch"
    assert not target.exists()


def test_codex_plugin_wrapper_finds_engine_in_configured_marketplace(
    tmp_path: Path, monkeypatch
) -> None:
    spec = importlib.util.spec_from_file_location("archmarshal_plugin_marketplace", WRAPPER)
    assert spec is not None and spec.loader is not None
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    marketplace = tmp_path / "marketplace"
    source = marketplace / "src"
    (source / "archmarshal").mkdir(parents=True)
    (source / "archmarshal/__init__.py").write_text("", encoding="utf-8")
    listing = json.dumps(
        {"marketplaces": [{"name": "personal", "root": str(marketplace)}]}
    )
    monkeypatch.setattr(wrapper.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        wrapper.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, listing, ""),
    )

    assert wrapper._configured_marketplace_source() == source
