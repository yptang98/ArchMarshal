from __future__ import annotations

import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
INSTALL_PROMPT = ROOT / "INSTALL_PROMPT.md"
UPDATE_PROMPT = ROOT / "UPDATE_PROMPT.md"
GETTING_STARTED = ROOT / "docs" / "getting-started.md"
BEGIN = "<!-- BEGIN INSTALL PROMPT -->"
END = "<!-- END INSTALL PROMPT -->"
UPDATE_BEGIN = "<!-- BEGIN UPDATE PROMPT -->"
UPDATE_END = "<!-- END UPDATE PROMPT -->"


def _prompt(text: str) -> str:
    assert text.count(BEGIN) == 1
    assert text.count(END) == 1
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip()


def _update_prompt(text: str) -> str:
    assert text.count(UPDATE_BEGIN) == 1
    assert text.count(UPDATE_END) == 1
    return text.split(UPDATE_BEGIN, 1)[1].split(UPDATE_END, 1)[0].strip()


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
        "full 40-character commit SHA",
        "GitHub Actions CI",
        "CODEX_HOME",
        "backups/archmarshal/",
        "Never delete, move, or rewrite a user-owned checkout",
        "Do not back up the complete Codex configuration or any credentials",
        "scripts/run_archmarshal.py",
        "--bootstrap-status",
        "dependency_imported=false",
        "Run read-only `doctor`",
        "do not modify the system Python environment",
        "Install only their wheel dependency closure",
        "pip check",
        "archmarshal-runtime-v1",
        "current.json",
        "Prepare the candidate before changing the active installation",
        "installed old version must remain installed and enabled",
        "lightweight fast path",
        "last-known-good capsule",
        "update_support.py create",
        "update_support.py materialize",
        "CAPSULE.json",
        "-B -I",
        "Never register or execute project commands directly against the sealed capsule",
        "Do not remove the active old plugin or marketplace until both",
        "Perform the shortest possible cutover only after preparation succeeds",
        "Do not adopt or reorganize the current project during installation",
    )
    for phrase in required:
        assert phrase in prompt

    assert "<reviewed-full-commit-sha>" not in prompt
    assert "pip install -e" not in prompt


def test_update_prompt_is_dedicated_safe_and_install_compatible() -> None:
    install = _prompt(INSTALL_PROMPT.read_text(encoding="utf-8"))
    update = _update_prompt(UPDATE_PROMPT.read_text(encoding="utf-8"))

    assert "Install or safely update" in install
    for phrase in (
        "Update the installed ArchMarshal management plugin",
        "If ArchMarshal is not installed, safely perform a first installation instead",
        "full 40-character commit SHA",
        "GitHub Actions CI",
        "user-owned local checkout",
        "CODEX_HOME/backups/archmarshal/",
        "restore the last known-good pinned plugin/marketplace",
        "codex plugin",
        "--bootstrap-status",
        "Run read-only `doctor`",
        "Keep the installed version enabled while preparing the replacement",
        "lightweight fast path",
        "old version untouched and usable",
        "last-known-good capsule",
        "shortest possible cutover",
        "A stale or malformed runtime pointer must not block",
        "Do not run ArchMarshal against the current project",
    ):
        assert phrase in update
    assert "<reviewed-full-commit-sha>" not in update
    assert "pip install -e" not in update


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


def test_primary_public_docs_are_english_only() -> None:
    han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

    for path in (README, INSTALL_PROMPT, UPDATE_PROMPT, GETTING_STARTED):
        assert han.search(path.read_text(encoding="utf-8")) is None, path
