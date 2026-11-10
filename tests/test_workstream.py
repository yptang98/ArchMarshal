from __future__ import annotations

import json

from test_portability import environment as environment
from test_portability import save

from archmarshal.cli import main
from archmarshal.handoff import resume_context
from archmarshal.lifecycle import end_workspace, start_workspace
from archmarshal.workstream import start_workstream


def test_start_selects_one_active_stream_and_reports_pending_work(environment):
    roots, profiles, _ = environment
    saved = save(roots[0], profiles[0], "review-window", decisions=["Keep previous API"],
                 next_steps=["Run integration checks"], files=["source.py"])
    result = start_workstream(roots[0])
    assert result["workflow"]["selected_stream"] == "review-window"
    assert result["workflow"]["selection_required"] is False
    card = result["workstreams"][0]
    assert card["record_id"] == saved["record_id"]
    assert card["last_saved_at"] and card["save_age_seconds"] >= 0
    assert card["unfinished_next_steps"] == ["Run integration checks"]
    assert card["evidence_needs_review"] is False
    (roots[0] / "source.py").write_text("changed\n")
    assert start_workstream(roots[0])["workstreams"][0]["evidence_needs_review"] is True
    assert start_workspace(roots[0])["context"]["workflow"]["selected_stream"] == "review-window"


def test_multiple_windows_require_selection_and_support_resume_filter(environment):
    roots, profiles, _ = environment
    save(roots[0], profiles[0], "code", next_steps=["Implement"])
    save(roots[0], profiles[1], "docs", next_steps=["Document"])
    overview = start_workstream(roots[0])
    assert overview["workflow"]["selection_required"] is True
    selected = start_workstream(roots[0], stream="docs")
    assert selected["workflow"]["selected_stream"] == "docs"
    assert [item["stream"] for item in selected["workstreams"]] == ["docs"]
    assert selected["available_streams"] == ["code", "docs"]
    resumed = resume_context(roots[0], stream="code")
    assert len(resumed["contexts"]) == 1
    assert resumed["contexts"][0]["unfinished_next_steps"] == ["Implement"]


def test_new_stream_suggestion_is_stable_and_does_not_write(environment):
    roots, profiles, _ = environment
    before = set(roots[1].rglob("*"))
    first = start_workstream(roots[1], task="Implement recovery", device_path=profiles[1])
    second = start_workstream(roots[1], task="Implement recovery", device_path=profiles[1])
    assert first["workflow"]["suggested_stream"] == second["workflow"]["suggested_stream"]
    assert first["workflow"]["automatic_chat_capture"] is False
    assert first["workflow"]["automatic_saves"] is False
    assert set(roots[1].rglob("*")) == before


def test_finish_cli_closes_selected_stream_and_end_warns_about_remaining_work(environment, capsys):
    roots, profiles, _ = environment
    root = roots[0]
    save(root, profiles[0], "done-soon", next_steps=["Verify result"])
    save(root, profiles[0], "still-working", next_steps=["Write docs"])
    args = ["context-finish", str(root), "--stream", "done-soon", "--summary", "Result verified",
            "--device-file", str(profiles[0])]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert main([*args, "--apply", "--expect-plan", preview["plan_digest"]]) == 0
    capsys.readouterr()
    finished = resume_context(root, stream="done-soon")["contexts"][0]
    assert finished["record"]["status"] == "completed"
    assert finished["unfinished_next_steps"] == []
    closeout = end_workspace(root)["workstream_closeout"]
    assert [item["stream"] for item in closeout["active_streams"]] == ["still-working"]
    assert closeout["active_streams"][0]["unfinished_next_steps"] == ["Write docs"]
    assert main(["context-start", str(root), "--stream", "still-working"]) == 0
    assert json.loads(capsys.readouterr().out)["workflow"]["selected_stream"] == "still-working"
