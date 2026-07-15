from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import archmarshal.skill_review as skill_review_module
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.skill_review import review_workspace_skill


def _owned_workspace(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "project"
    skill = root / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: Use when demo tasks need an exact reviewed helper.\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )
    adoption = plan_adoption(root)
    adopt_workspace(root, apply=True, expected_plan=adoption["plan_digest"])
    head = (root / ".agent/skill-overlays/.archmarshal/HEAD").read_text(
        encoding="ascii"
    ).strip()
    return root, "skills/demo", head


def _preview(root: Path, source: str, head: str) -> dict:
    return review_workspace_skill(
        root,
        source,
        decision="approve",
        reviewer="reviewer",
        reason="exact review",
        expected_head=head,
    )


def _apply(root: Path, source: str, head: str, preview: dict) -> dict:
    return review_workspace_skill(
        root,
        source,
        decision="approve",
        reviewer="reviewer",
        reason="exact review",
        expected_head=head,
        expected_plan=preview["plan_digest"],
        reviewed_plan=preview["review_plan"],
        apply=True,
    )


def test_preview_generation_is_the_exact_generation_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, source, head = _owned_workspace(tmp_path)
    clock_values = [
        datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
        datetime(2030, 8, 16, 4, 5, 6, tzinfo=timezone.utc),
    ]

    class ControlledDateTime(datetime):
        calls = 0

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            value = clock_values[cls.calls]
            cls.calls += 1
            return value

    monkeypatch.setattr(skill_review_module, "datetime", ControlledDateTime)
    preview = _preview(root, source, head)
    review_plan = preview["review_plan"]
    generation_bytes = (
        json.dumps(
            review_plan["generation"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")

    assert ControlledDateTime.calls == 1
    assert review_plan["reviewed_at"] == "2026-07-15T01:02:03+00:00"
    assert review_plan["generation_object"] == {
        "path": f".agent/skill-overlays/.archmarshal/objects/sha256/{preview['proposed_head']}.json",
        "bytes": len(generation_bytes),
        "sha256": hashlib.sha256(generation_bytes).hexdigest(),
    }
    digest_subject = {key: value for key, value in review_plan.items() if key != "plan_digest"}
    assert preview["plan_digest"] == hashlib.sha256(
        json.dumps(
            digest_subject,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    applied = _apply(root, source, head, preview)

    assert ControlledDateTime.calls == 2  # apply reads time only for the backup filename
    assert applied["proposed_head"] == preview["proposed_head"]
    assert applied["commit"]["head"] == preview["proposed_head"]
    assert (root / review_plan["generation_object"]["path"]).read_bytes() == generation_bytes


@pytest.mark.parametrize("tamper", ["reviewed_at", "generation", "digest", "object_path"])
def test_apply_rejects_every_tampered_generation_binding(tmp_path: Path, tamper: str) -> None:
    root, source, head = _owned_workspace(tmp_path)
    preview = _preview(root, source, head)
    reviewed_plan = copy.deepcopy(preview["review_plan"])
    if tamper == "reviewed_at":
        reviewed_plan["reviewed_at"] = "2040-01-01T00:00:00+00:00"
    elif tamper == "generation":
        reviewed_plan["generation"]["skills"][0]["manifest"]["review"]["reviewer"] = "other"
    elif tamper == "digest":
        reviewed_plan["generation_object"]["sha256"] = "0" * 64
    else:
        reviewed_plan["generation_object"]["path"] = ".agent/wrong.json"
    backups_before = sorted((root / ".agent/backups").glob("*.zip"))

    with pytest.raises(ArchMarshalError) as raised:
        review_workspace_skill(
            root,
            source,
            decision="approve",
            reviewer="reviewer",
            reason="exact review",
            expected_head=head,
            expected_plan=preview["plan_digest"],
            reviewed_plan=reviewed_plan,
            apply=True,
        )

    assert raised.value.code == "skill_review_stale_plan"
    assert (root / ".agent/skill-overlays/.archmarshal/HEAD").read_text(
        encoding="ascii"
    ).strip() == head
    assert sorted((root / ".agent/backups").glob("*.zip")) == backups_before


def test_apply_requires_the_complete_saved_review_plan(tmp_path: Path) -> None:
    root, source, head = _owned_workspace(tmp_path)
    preview = _preview(root, source, head)

    result = review_workspace_skill(
        root,
        source,
        decision="approve",
        reviewer="reviewer",
        reason="exact review",
        expected_head=head,
        expected_plan=preview["plan_digest"],
        apply=True,
    )

    assert result["mode"] == "review_required"
    assert (root / ".agent/skill-overlays/.archmarshal/HEAD").read_text(
        encoding="ascii"
    ).strip() == head


def test_cli_applies_the_complete_saved_preview(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, source, head = _owned_workspace(tmp_path)
    preview = _preview(root, source, head)
    saved = tmp_path / "skill-review.json"
    saved.write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")

    assert main(
        [
            "skill-review",
            str(root),
            "--source",
            source,
            "--decision",
            "approve",
            "--reviewer",
            "reviewer",
            "--reason",
            "exact review",
            "--expect-head",
            head,
            "--expect-plan",
            preview["plan_digest"],
            "--plan-file",
            str(saved),
            "--apply",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commit"]["head"] == preview["proposed_head"]
    assert payload["proposed_head"] == preview["proposed_head"]
