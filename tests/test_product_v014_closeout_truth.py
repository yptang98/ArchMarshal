from __future__ import annotations

from pathlib import Path

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.session import record_closeout


def _adopt(root: Path) -> None:
    preview = plan_adoption(root)
    applied = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    assert applied["mode"] == "overlay_applied"


def test_unowned_closeout_distinguishes_evidence_from_recording_authority(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unowned"
    root.mkdir()

    preview = record_closeout(
        root,
        level="standard",
        summary="Completed the review.",
        steps=["Inspect the result."],
    )

    assert preview["mode"] == "blocked"
    assert preview["workspace_owned"] is False
    assert preview["evidence_ready"] is True
    assert preview["recording_ready"] is False
    assert preview["reproducibility_ready"] is False
    assert preview["reproduction_evidence_ready"] is False
    assert preview["execution_validated"] is False
    assert preview["recording_blockers"]
    assert preview["session_preview"]["summary"] == "Completed the review."
    assert preview["session_preview"]["reproducibility"]["evidence_complete"] is True
    assert not (root / ".agent").exists()


def test_owned_reproducible_closeout_is_recordable_but_not_execution_validated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    _adopt(root)
    script = root / "run.py"
    script.write_text("print('reference only')\n", encoding="utf-8")

    preview = record_closeout(
        root,
        level="reproducible",
        summary="Prepared exact rerun evidence.",
        steps=["Run the reference script."],
        scripts=["run.py"],
        commands=["python run.py"],
    )

    assert preview["mode"] == "propose_only"
    assert preview["workspace_owned"] is True
    assert preview["evidence_ready"] is True
    assert preview["recording_ready"] is True
    assert preview["reproducibility_ready"] is True
    assert preview["execution_validated"] is False
    assert preview["recording_blockers"] == []
    assert preview["session_preview"]["reproducibility"]["reproducible_claim"] is False
    assert preview["session_preview"]["commands"] == ["python run.py"]
