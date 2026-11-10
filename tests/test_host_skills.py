from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

import archmarshal.host_skills as host
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.cli import main
from archmarshal.codex_skill_probe import _query, probe_codex_skills
from archmarshal.errors import ArchMarshalError
from archmarshal.host_skills import OBSERVATION_FORMAT, reconcile_host_skills
from archmarshal.safety import fingerprint_directory
from archmarshal.skill_index import load_skill_index
from archmarshal.skill_review import review_workspace_skill

SOURCE = ".agents/skills/demo"


def managed(tmp_path, *, exclude=None):
    root = tmp_path / "project"
    skill = root / SOURCE
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Fixture task.\n---\n# Demo\n")
    (skill / "helper.py").write_text("print('fixture')\n")
    preview = plan_adoption(root, exclude_skills=exclude)
    adopt_workspace(root, exclude_skills=exclude, apply=True, expected_plan=preview["plan_digest"])
    return root


def approve(root):
    args = dict(decision="approve", reviewer="fixture", reason="Reviewed exact package",
                expected_head=load_skill_index(root)["head"])
    preview = review_workspace_skill(root, SOURCE, **args)
    review_workspace_skill(root, SOURCE, **args, apply=True, reviewed_plan=preview["review_plan"],
                           expected_plan=preview["plan_digest"])


def observation(root, enabled=True):
    return {"format": OBSERVATION_FORMAT, "host_id": "local", "invalidated": False,
            "observed_at": datetime.now(timezone.utc).isoformat(), "origin": "fixture",
            "response": {"data": [{"cwd": str(root), "errors": [], "skills": [
                {"path": str(root / SOURCE / "SKILL.md"), "name": "demo", "scope": "repo", "enabled": enabled}
            ]}]}}


def test_native_enabled_does_not_imply_review_and_approval_does_not_enable(tmp_path):
    root = managed(tmp_path)
    row = reconcile_host_skills(root, snapshot=observation(root))["skills"][0]
    assert row["review"] == "needs_review"
    assert row["alignment"] == "native_enabled_review_blocked"
    approve(root)
    row = reconcile_host_skills(root, snapshot=observation(root, False))["skills"][0]
    assert row["review"] == "approved" and row["alignment"] == "native_disabled"
    assert row["source_verification"]["state"] == "unverified"


def test_reviewed_version_requires_explicit_byte_verification_and_detects_drift(tmp_path):
    root = managed(tmp_path)
    approve(root)
    snapshot = observation(root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    row = reconcile_host_skills(root, snapshot=snapshot)["skills"][0]
    assert row["alignment"] == "native_enabled_source_unverified"
    result = reconcile_host_skills(root, snapshot=snapshot, verify_sources=[SOURCE])
    assert result["skills"][0]["alignment"] == "enabled_observation_matches_reviewed_source"
    assert result["skills"][0]["runtime_active"] is None
    assert result["observation"]["live_runtime_verified"] is False
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    (root / SOURCE / "helper.py").write_text("changed behavior")
    row = reconcile_host_skills(root, snapshot=snapshot, verify_sources=[SOURCE])["skills"][0]
    assert row["source_verification"]["state"] == "changed"
    assert row["alignment"] == "native_enabled_source_changed"


@pytest.mark.parametrize("change,state", [("stale", "stale"), ("future", "future"), ("invalidated", "invalidated")])
def test_expired_or_invalidated_observations_never_claim_current_alignment(tmp_path, change, state):
    root = managed(tmp_path)
    snapshot = observation(root)
    if change == "invalidated":
        snapshot["invalidated"] = True
    else:
        delta = timedelta(hours=-1 if change == "stale" else 1)
        snapshot["observed_at"] = (datetime.now(timezone.utc) + delta).isoformat()
    report = reconcile_host_skills(root, snapshot=snapshot)
    assert report["observation"]["state"] == state
    assert report["skills"][0]["alignment"] == "native_observation_" + state


def test_exact_paths_keep_same_name_skills_separate_and_ambiguous_paths_unknown(tmp_path):
    root = managed(tmp_path)
    snapshot = observation(root)
    skills = snapshot["response"]["data"][0]["skills"]
    skills.append({**skills[0], "path": str(tmp_path / "elsewhere/SKILL.md"), "enabled": False})
    report = reconcile_host_skills(root, snapshot=snapshot)
    assert len(report["skills"]) == 2
    assert report["skills"][0]["native_enabled_at_observation"] is True
    assert report["skills"][1]["review"] == "unmanaged"
    skills.append({**skills[0], "enabled": False})
    row = reconcile_host_skills(root, snapshot=snapshot)["skills"][0]
    assert row["alignment"] == "ambiguous_native_path"
    assert row["native_enabled_at_observation"] is None


def test_excluded_skill_bytes_are_never_read_or_promoted_to_unmanaged(tmp_path, monkeypatch):
    root = managed(tmp_path, exclude=[SOURCE])
    def never(*args, **kwargs):
        raise AssertionError("Excluded content must remain unread")
    monkeypatch.setattr(host, "fingerprint_directory", never)
    report = reconcile_host_skills(root, snapshot=observation(root))
    assert report["skills"][0]["alignment"] == "excluded"
    with pytest.raises(ArchMarshalError, match="non-excluded"):
        reconcile_host_skills(root, snapshot=observation(root), verify_sources=[SOURCE])


@pytest.mark.parametrize("change", ["cwd", "host", "duplicate_cwd", "timestamp", "enabled", "format"])
def test_malformed_or_misdirected_snapshots_fail_closed(tmp_path, change):
    root = managed(tmp_path)
    snapshot = observation(root)
    entry = snapshot["response"]["data"][0]
    if change == "cwd":
        entry["cwd"] = str(tmp_path)
    elif change == "host":
        snapshot["host_id"] = "another-machine"
    elif change == "duplicate_cwd":
        snapshot["response"]["data"].append(copy.deepcopy(entry))
    elif change == "timestamp":
        snapshot["observed_at"] = "2026-01-01T12:00:00"
    elif change == "enabled":
        entry["skills"][0]["enabled"] = "true"
    else:
        snapshot["format"] = "unknown"
    with pytest.raises(ArchMarshalError):
        reconcile_host_skills(root, snapshot=snapshot)


def test_missing_snapshot_missing_paths_and_discovery_errors_are_not_disabled(tmp_path):
    root = managed(tmp_path)
    report = reconcile_host_skills(root)
    assert report["observation"]["state"] == "unavailable"
    assert report["skills"][0]["native_enabled_at_observation"] is None
    snapshot = observation(root)
    snapshot["response"]["data"][0]["skills"][0].pop("path")
    snapshot["response"]["data"][0]["errors"] = [{"path": str(root), "message": "load error"}]
    report = reconcile_host_skills(root, snapshot=snapshot)
    assert report["skills"][0]["alignment"] == "native_discovery_incomplete"
    assert report["skills"][1]["alignment"] == "native_path_unavailable"


def test_selected_verification_has_a_real_read_budget_and_index_race_check(tmp_path, monkeypatch):
    root = managed(tmp_path)
    monkeypatch.setattr(host, "MAX_VERIFY_BYTES", 4)
    result = reconcile_host_skills(root, snapshot=observation(root), verify_sources=[SOURCE])
    assert result["skills"][0]["source_verification"]["state"] == "unsafe_or_unreadable"
    original = host.load_skill_index
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        result = original(path)
        if calls == 2:
            result["head"] = "f" * 64
        return result
    monkeypatch.setattr(host, "load_skill_index", changed)
    with pytest.raises(ArchMarshalError, match="index changed"):
        reconcile_host_skills(root)


def test_bounded_fingerprint_preserves_existing_digest_and_rejects_invalid_limits(tmp_path):
    (tmp_path / "report.txt").write_bytes(b"12345")
    assert fingerprint_directory(tmp_path) == fingerprint_directory(tmp_path, max_files=1, max_content_bytes=5)
    for kwargs in ({"max_files": 0}, {"max_content_bytes": 4}, {"max_files": True}, {"max_content_bytes": -1}):
        with pytest.raises(ArchMarshalError):
            fingerprint_directory(tmp_path, **kwargs)


def test_host_skill_cli_snapshot_route(tmp_path, capsys):
    root = managed(tmp_path)
    snapshot_file = tmp_path / "observed.json"
    snapshot_file.write_text(json.dumps(observation(root)))
    assert main(["skill-host-status", str(root), "--host-snapshot", str(snapshot_file)]) == 0
    assert json.loads(capsys.readouterr().out)["skills"][0]["native_enabled_at_observation"] is True


def fake_host(tmp_path, behavior):
    script = tmp_path / "fake_host.py"
    script.write_text('''import json, sys, time
for line in sys.stdin:
    message = json.loads(line)
    if message['method'] == 'initialize':
        print(json.dumps({'id': 0, 'result': {'userAgent': 'fixture/1', 'platformOs': 'fixture'}}), flush=True)
    elif message['method'] == 'skills/list':
        assert message['params']['forceReload'] is True
        assert len(message['params']['cwds']) == 1
''' + behavior + '''
    elif message['method'] != 'initialized':
        raise RuntimeError('Unexpected mutating method')
''')
    return [sys.executable, str(script)]


def test_stdio_probe_observes_enabled_state_without_starting_threads(tmp_path):
    command = fake_host(tmp_path, """        print(json.dumps({'method': 'skills/changed', 'params': {}}), flush=True)
        print(json.dumps({'id': 1, 'result': {'data': [{'cwd': message['params']['cwds'][0], 'skills': [], 'errors': []}]}}), flush=True)""")
    snapshot = _query(command, tmp_path, "local", 3)
    assert snapshot["origin"] == "codex-cli-app-server" and snapshot["invalidated"] is True
    assert snapshot["server"]["userAgent"] == "fixture/1"
    assert reconcile_host_skills(tmp_path, snapshot=snapshot)["observation"]["state"] == "invalidated"


@pytest.mark.parametrize("behavior", [
    "        time.sleep(5)",
    "        print(json.dumps({'id': 1, 'error': {'code': -32601}}), flush=True)",
    "        print(json.dumps({'id': 9, 'method': 'item/permissions/requestApproval'}), flush=True)",
    "        print('invalid json', flush=True)",
    "        sys.exit(0)",
])
def test_stdio_probe_rejects_timeout_error_host_action_and_malformed_data(tmp_path, behavior):
    with pytest.raises(ArchMarshalError):
        _query(fake_host(tmp_path, behavior), tmp_path, "local", 1)


def test_probe_rejects_shell_wrappers(tmp_path):
    wrapper = tmp_path / "codex.cmd"
    wrapper.write_text("do not run")
    with pytest.raises(ArchMarshalError, match="native Codex executable"):
        probe_codex_skills(tmp_path, wrapper)


def test_explicit_installation_mapping_verifies_native_copy_independently_of_draft(tmp_path):
    root = managed(tmp_path)
    approve(root)
    installed = tmp_path / "native-cache/demo"
    shutil.copytree(root / SOURCE, installed)
    snapshot = observation(root)
    snapshot["response"]["data"][0]["skills"][0]["path"] = str(installed / "SKILL.md")
    mappings = {SOURCE: str(installed / "SKILL.md")}
    (root / SOURCE / "helper.py").write_text("new unreviewed draft")
    report = reconcile_host_skills(root, snapshot=snapshot, installed_paths=mappings, verify_sources=[SOURCE])
    row = report["skills"][0]
    assert row["source_verification"]["state"] == "changed"
    assert row["installation_verification"]["state"] == "verified"
    assert row["alignment"] == "enabled_observation_matches_reviewed_installation"
    (installed / "helper.py").write_text("changed installed version")
    report = reconcile_host_skills(root, snapshot=snapshot, installed_paths=mappings, verify_sources=[SOURCE])
    assert report["skills"][0]["alignment"] == "native_enabled_installation_changed"


def test_unobserved_installed_binding_does_not_read_external_bytes(tmp_path, monkeypatch):
    root = managed(tmp_path)
    installed = tmp_path / "not-in-host/SKILL.md"
    original = host._verify_source
    calls = []
    def checked(*args, **kwargs):
        calls.append(kwargs.get("installed_path"))
        return original(*args, **kwargs)
    monkeypatch.setattr(host, "_verify_source", checked)
    report = reconcile_host_skills(root, snapshot=observation(root),
                                  installed_paths={SOURCE: str(installed)}, verify_sources=[SOURCE])
    assert calls == [None]
    assert report["skills"][0]["alignment"] == "not_observed"
    assert not installed.exists()


def test_installed_bindings_require_explicit_safe_unambiguous_source_paths(tmp_path):
    root = managed(tmp_path)
    for mapping in ({SOURCE: "relative/SKILL.md"}, {SOURCE: str(tmp_path/'config.json')},
                    {"unknown": str(tmp_path/'SKILL.md')}):
        with pytest.raises(ArchMarshalError):
            reconcile_host_skills(root, snapshot=observation(root), installed_paths=mapping)


def test_probe_timeout_terminates_its_process(tmp_path, monkeypatch):
    import archmarshal.codex_skill_probe as probe
    actual = probe.subprocess.Popen
    children = []
    def capture(*args, **kwargs):
        child = actual(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(probe.subprocess, "Popen", capture)
    with pytest.raises(ArchMarshalError):
        _query(fake_host(tmp_path, "        time.sleep(10)"), tmp_path, "local", 1)
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("behavior", [
    "        print('x' * (4 * 1024 * 1024 + 1), flush=True)",
    "        [print(json.dumps({'method': 'noise', 'params': {}}), flush=True) for _ in range(120)]",
])
def test_probe_response_and_notification_budgets(tmp_path, behavior):
    with pytest.raises(ArchMarshalError):
        _query(fake_host(tmp_path, behavior), tmp_path, "local", 3)


def test_source_and_installed_verification_share_one_byte_budget(tmp_path, monkeypatch):
    root = managed(tmp_path)
    approve(root)
    installed = tmp_path / "native-cache"
    shutil.copytree(root / SOURCE, installed)
    snapshot = observation(root)
    snapshot["response"]["data"][0]["skills"][0]["path"] = str(installed / "SKILL.md")
    size = sum(path.stat().st_size for path in (root / SOURCE).iterdir() if path.is_file())
    monkeypatch.setattr(host, "MAX_VERIFY_BYTES", size)
    report = reconcile_host_skills(root, snapshot=snapshot, verify_sources=[SOURCE],
                                  installed_paths={SOURCE: str(installed / "SKILL.md")})
    row = report["skills"][0]
    assert row["source_verification"]["state"] == "verified"
    assert row["installation_verification"]["state"] == "budget_exhausted"


def test_detached_worktree_does_not_inherit_another_checkout_review_index(tmp_path):
    root = managed(tmp_path)
    approve(root)
    for args in (["init", "--quiet"], ["config", "user.name", "Fixture"],
                 ["config", "user.email", "fixture@example.invalid"],
                 ["add", SOURCE], ["commit", "--quiet", "-m", "fixture"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=20)
    work = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", str(work)],
                   check=True, capture_output=True, timeout=20)
    report = reconcile_host_skills(root, work_root=work, snapshot=observation(work))
    assert report["index_head"] is None
    assert report["skills"][0]["review"] == "unmanaged"
    assert not (work / ".agent").exists()


def test_native_probe_respects_exclusions_before_starting_any_process(tmp_path, monkeypatch):
    import archmarshal.codex_skill_probe as probe
    root = managed(tmp_path, exclude=[SOURCE])
    def never(*args, **kwargs):
        raise AssertionError("Native reload must not inspect excluded packages")
    monkeypatch.setattr(probe, "_query", never)
    with pytest.raises(ArchMarshalError, match="excluded Skills"):
        probe_codex_skills(root, sys.executable)


def test_public_probe_validates_binary_and_builds_only_the_read_only_command(tmp_path, monkeypatch):
    import archmarshal.codex_skill_probe as probe
    calls = []
    def capture(command, work, host_id, timeout):
        calls.append((command, work, host_id, timeout))
        return observation(work)
    monkeypatch.setattr(probe, "_query", capture)
    result = probe_codex_skills(tmp_path, sys.executable, timeout=5)
    assert result["format"] == OBSERVATION_FORMAT
    assert calls[0][0] == [sys.executable, "app-server", "--listen", "stdio://"]
    for invalid in (0, 61, float('nan'), True):
        with pytest.raises(ArchMarshalError):
            probe_codex_skills(tmp_path, sys.executable, timeout=invalid)
    assert len(calls) == 1
