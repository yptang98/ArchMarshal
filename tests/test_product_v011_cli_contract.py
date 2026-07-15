from __future__ import annotations

import json

import pytest

from archmarshal import __version__
from archmarshal.cli import end_main, main, start_main


@pytest.mark.parametrize(
    ("entrypoint", "arguments"),
    [
        (main, []),
        (main, ["backup-restore"]),
        (main, ["end", "--apply"]),
        (start_main, ["--backup-scope", "unknown"]),
        (end_main, ["--apply"]),
    ],
)
def test_cli_usage_failures_are_versioned_json(
    entrypoint, arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert entrypoint(arguments) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["api_version"] == "archmarshal-cli-v1"
    assert payload["mode"] == "error"
    assert payload["error"]["code"] == "cli_usage_error"
    assert payload["error"]["details"]["usage"].startswith("usage:")
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("entrypoint", "program"),
    [(main, "archmarshal"), (start_main, "archmarshal-start"), (end_main, "archmarshal-end")],
)
def test_cli_version_remains_plain_text(
    entrypoint, program: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        entrypoint(["--version"])

    assert raised.value.code == 0
    assert capsys.readouterr().out.strip() == f"{program} {__version__}"
