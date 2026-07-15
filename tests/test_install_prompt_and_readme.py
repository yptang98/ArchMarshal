from __future__ import annotations

import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
INSTALL_PROMPT = ROOT / "INSTALL_PROMPT.md"
GETTING_STARTED = ROOT / "docs" / "getting-started.md"
BEGIN = "<!-- BEGIN INSTALL PROMPT -->"
END = "<!-- END INSTALL PROMPT -->"


def _prompt(text: str) -> str:
    assert text.count(BEGIN) == 1
    assert text.count(END) == 1
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip()


def test_readme_and_standalone_install_prompt_stay_identical() -> None:
    readme = README.read_text(encoding="utf-8")
    standalone = INSTALL_PROMPT.read_text(encoding="utf-8")

    assert _prompt(readme) == _prompt(standalone)


def test_install_prompt_is_copyable_safe_and_codex_native() -> None:
    prompt = _prompt(INSTALL_PROMPT.read_text(encoding="utf-8"))

    required = (
        "https://github.com/yptang98/ArchMarshal",
        "codex plugin marketplace add yptang98/ArchMarshal --ref",
        "codex plugin add archmarshal@archmarshal",
        "完整 40 位 SHA",
        "GitHub Actions CI",
        "CODEX_HOME",
        "backups/archmarshal/",
        "不要删除、移动或改写用户本地 checkout",
        "不要备份整个 Codex 配置或任何凭据",
        "scripts/run_archmarshal.py",
        "--bootstrap-status",
        "dependency_imported=false",
        "只读 `doctor`",
        "不要污染系统 Python",
        "只安装它们的 wheel 依赖闭包",
        "pip check",
        "archmarshal-runtime-v1",
        "current.json",
        "不要在安装任务中接管或整理当前项目",
    )
    for phrase in required:
        assert phrase in prompt

    assert "<reviewed-full-commit-sha>" not in prompt
    assert "pip install -e" not in prompt


def test_user_docs_do_not_present_a_literal_sha_placeholder_as_install() -> None:
    readme = README.read_text(encoding="utf-8")
    getting_started = GETTING_STARTED.read_text(encoding="utf-8")

    assert "<reviewed-full-commit-sha>" not in readme
    assert "<reviewed-full-commit-sha>" not in getting_started
    assert "INSTALL_PROMPT.md" in readme
    assert "INSTALL_PROMPT.md" in getting_started
    assert "does not publish a fake SHA placeholder" in getting_started


def test_readme_has_no_hidden_format_controls_or_broken_fences() -> None:
    readme = README.read_text(encoding="utf-8")
    unexpected_controls = [
        character
        for character in readme
        if unicodedata.category(character) == "Cf" and character not in {"\u200c", "\u200d"}
    ]

    assert "\ufffd" not in readme
    assert "\x00" not in readme
    assert unexpected_controls == []
    assert len(re.findall(r"^```", readme, flags=re.MULTILINE)) % 2 == 0


def test_readme_primary_flow_is_plugin_first_not_cli_dump() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "ArchMarshal is not a separate agent application" in readme
    assert "It uses Codex's interface" in readme
    assert "No GUI." not in readme
    assert "No automatic global configuration mutation." not in readme
    assert "skill-review.json" not in readme
    assert "learning-plan.json" not in readme
    assert readme.count("## Install with one Codex prompt") == 1
