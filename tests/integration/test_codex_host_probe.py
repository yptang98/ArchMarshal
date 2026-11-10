"""Optional real Codex protocol check; no model turn or native config write."""
from __future__ import annotations

import os
import shutil

import pytest

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.codex_skill_probe import probe_codex_skills
from archmarshal.host_skills import reconcile_host_skills
from archmarshal.skill_index import load_skill_index
from archmarshal.skill_review import review_workspace_skill


@pytest.mark.skipif(not os.environ.get("ARCHMARSHAL_CODEX_BINARY"), reason="Explicit native Codex binary not selected")
def test_real_host_observation_and_reviewed_installed_copy(tmp_path):
    root = tmp_path / "project"
    source = ".agents/skills/native-host-fixture"
    authoring = root / source
    authoring.mkdir(parents=True)
    (authoring / "SKILL.md").write_text(
        "---\nname: native-host-fixture\ndescription: A local protocol verification fixture.\n---\n# Fixture\n",
        encoding="utf-8")
    (authoring / "helper.txt").write_text("reviewed version")
    preview = plan_adoption(root)
    adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])
    binary = os.environ["ARCHMARSHAL_CODEX_BINARY"]
    initial = reconcile_host_skills(root, snapshot=probe_codex_skills(root, binary))
    row = next(item for item in initial["skills"] if item.get("source") == source)
    assert row["native_enabled_at_observation"] is True
    assert row["alignment"] == "native_enabled_review_blocked"
    args = dict(decision="approve", reviewer="protocol-fixture", reason="Verify selected fixture bytes",
                expected_head=load_skill_index(root)["head"])
    review = review_workspace_skill(root, source, **args)
    review_workspace_skill(root, source, **args, reviewed_plan=review["review_plan"],
                           expected_plan=review["plan_digest"], apply=True)
    installed = root / ".agents/skills/installed/native-host-fixture"
    shutil.copytree(authoring, installed)
    (authoring / "helper.txt").write_text("new draft not yet reviewed")
    snapshot = probe_codex_skills(root, binary)
    result = reconcile_host_skills(root, snapshot=snapshot, verify_sources=[source],
                                   installed_paths={source: str(installed / "SKILL.md")})
    row = next(item for item in result["skills"] if item.get("source") == source)
    assert row["source_verification"]["state"] == "changed"
    assert row["installation_verification"]["state"] == "verified"
    assert row["alignment"] == "enabled_observation_matches_reviewed_installation"
    assert row["runtime_active"] is None
