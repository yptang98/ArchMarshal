from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from test_portability import environment as environment


def test_fresh_process_resumes_saved_state_decisions_and_next_action(environment, tmp_path):
    roots, profiles, _ = environment
    root = roots[0]
    process_env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "archmarshal", *map(str, args)],
                                cwd=tmp_path, env=process_env, check=True,
                                capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)

    save = ["context-save", root, "--stream", "recovery-window", "--summary", "Recovery ready",
            "--decision", "Keep original bytes because recovery must preserve evidence",
            "--next-step", "Run the filesystem failure checks", "--file", "source.py",
            "--device-file", profiles[0]]
    preview = cli(*save)
    saved = cli(*save, "--apply", "--expect-plan", preview["plan_digest"])
    resumed = cli("context-start", root, "--stream", "recovery-window")
    context = resumed["contexts"][0]
    assert context["record_id"] == saved["record_id"]
    assert context["record"]["summary"] == "Recovery ready"
    assert context["record"]["decisions"] == [
        "Keep original bytes because recovery must preserve evidence"]
    assert context["unfinished_next_steps"] == ["Run the filesystem failure checks"]
    assert resumed["workstreams"][0]["evidence_needs_review"] is False
    (root / "source.py").write_text("print('changed by a separate window')\n", encoding="utf-8")
    assert cli("context-start", root, "--stream", "recovery-window")[
        "workstreams"][0]["evidence_needs_review"] is True
