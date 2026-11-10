"""Behavioral regressions from the two 0.19 development reviews."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from test_host_skills import SOURCE, approve, managed, observation

import archmarshal.host_skills as host
from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.doctor import doctor_workspace
from archmarshal.errors import ArchMarshalError
from archmarshal.git_environment import inspection_environment
from archmarshal.handoff import read_context, save_context
from archmarshal.portable import canonical, init_device, sha
from archmarshal.project_context import resolve_project_context
from archmarshal.session import record_closeout
from archmarshal.skill_index import load_skill_index
from archmarshal.skill_package import scan_package
from archmarshal.skill_review import review_workspace_skill
from archmarshal.skill_sync import install_skill, pull_skill, push_skill, snapshot_skill
from archmarshal.sync_transport import PREFIX, FolderTransport, GitHubTransport

BODY = "---\nname: demo\ndescription: Regression fixture.\n---\n# Demo\n"


def adopt(root):
    preview = plan_adoption(root)
    adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"])


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "device.json"
    preview = init_device("regression", path=path)
    init_device("regression", path=path, apply=True, expected_plan=preview["plan_digest"])
    return path


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", env=inspection_environment(), timeout=20).stdout.strip()


def test_git_handoff_and_closeout_stay_in_selected_checkout(tmp_path, monkeypatch, profile):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
        (root / "source.txt").write_text(root.name)
        adopt(root)
        git(root, "init", "--quiet")
        git(root, "config", "user.name", "Fixture")
        git(root, "config", "user.email", "fixture@example.invalid")
        git(root, "add", "source.txt")
        git(root, "commit", "--quiet", "-m", root.name)
    a, b = roots
    git(a, "branch", "-m", "中文分支")
    expected = git(a, "rev-parse", "HEAD")
    (a / "source.txt").write_text("local edit")
    monkeypatch.setenv("GIT_DIR", str(b / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(b))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(b))
    args = dict(stream="fixture", summary="Selected project", device_path=profile)
    preview = save_context(a, **args)
    saved = save_context(a, **args, apply=True, expected_plan=preview["plan_digest"])
    handoff = read_context(a, saved["record_id"])["record"]["git"]
    closeout = record_closeout(a, level="quick", summary="Selected project")["session_preview"]["git"]
    assert handoff["head"] == closeout["commit"] == resolve_project_context(a)["git"]["head"] == expected
    assert handoff["branch"] == closeout["branch"] == "中文分支"
    assert closeout["dirty"] is True
    assert "source.txt" in closeout["changed_paths"]
    assert os.environ["GIT_WORK_TREE"] == str(b)  # Parent process is unchanged.


@pytest.mark.parametrize("corrupt", [False, True])
def test_doctor_checks_configured_pre_journal_session(tmp_path, corrupt):
    root = tmp_path / "project"
    root.mkdir()
    adopt(root)
    config = root / ".agent/workspace.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    value["save_paths"]["project_files"]["history"] = "records/custom-history"
    config.write_text(yaml.safe_dump(value), encoding="utf-8")
    preview = record_closeout(root, level="quick", summary="Legacy evidence")
    session = yaml.safe_dump(preview["session_preview"]).encode("utf-8")
    directory = root / "records/custom-history/2026/11/10/legacy"
    directory.mkdir(parents=True)
    (directory / "session.yaml").write_bytes(session)
    (directory / "COMMITTED.json").write_bytes(canonical({
        "format": "archmarshal-session-commit-v1", "session_format": "archmarshal-session-v2",
        "source_mutation": False, "file_count": 1,
        "files": [{"path": "session.yaml", "bytes": len(session), "sha256": sha(session)}]}))
    assert not (root / ".agent/operations").exists()
    if corrupt:
        (directory / "session.yaml").write_bytes(session + b"# tampered\n")
    report = doctor_workspace(root)
    assert report["state"] == ("error" if corrupt else "healthy")
    assert any(f["code"] == "session_file_digest_mismatch" for f in report["findings"]) == corrupt
    assert not any(f["code"] == "sessions_empty" for f in report["findings"])


@pytest.mark.parametrize("path", ["../outside", ".git/history"])
def test_doctor_does_not_scan_invalid_history_mapping(tmp_path, path):
    root = tmp_path / "project"
    root.mkdir()
    adopt(root)
    config = root / ".agent/workspace.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    value["save_paths"]["project_files"]["history"] = path
    config.write_text(yaml.safe_dump(value), encoding="utf-8")
    report = doctor_workspace(root)
    assert report["state"] == "error"
    assert any(f["code"] == "session_history_layout_invalid" for f in report["findings"])


def test_doctor_empty_history_is_healthy_but_unmanaged_workspace_is_absent(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    assert doctor_workspace(root)["state"] == "absent"
    adopt(root)
    report = doctor_workspace(root)
    assert report["state"] == "healthy"
    assert any(f["code"] == "sessions_empty" for f in report["findings"])
    assert doctor_workspace(tmp_path / "missing")["state"] == "absent"


@pytest.mark.parametrize("nested", [False, True])
def test_installed_alias_cannot_read_excluded_package(tmp_path, monkeypatch, nested):
    excluded = ".agents/skills/private"
    target = tmp_path / "project" / excluded
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(BODY)
    if nested:
        target = target / "nested"
        target.mkdir()
        (target / "SKILL.md").write_text(BODY)
    root = managed(tmp_path, exclude=[excluded])
    approve(root)
    snapshot = observation(root)
    snapshot["response"]["data"][0]["skills"][0]["path"] = str(target / "SKILL.md")
    def never(*args, **kwargs):
        raise AssertionError("Reject excluded installation before fingerprinting any package")
    monkeypatch.setattr(host, "fingerprint_directory", never)
    with pytest.raises(ArchMarshalError, match="non-excluded"):
        host.reconcile_host_skills(root, snapshot=snapshot, verify_sources=[SOURCE],
                                   installed_paths={SOURCE: str(target / "SKILL.md")})


def test_mapped_installation_preserves_actual_excluded_children(tmp_path, monkeypatch):
    excluded = "copied/private"
    private = tmp_path / "project" / excluded
    private.mkdir(parents=True)
    (private / "SKILL.md").write_text(BODY)
    root = managed(tmp_path, exclude=[excluded])
    approve(root)
    target = root / "copied"
    shutil.copytree(root / SOURCE, target, dirs_exist_ok=True)
    snapshot = observation(root)
    snapshot["response"]["data"][0]["skills"][0]["path"] = str(target / "SKILL.md")
    original = os.open
    def guarded(path, *args, **kwargs):
        assert private not in Path(path).parents, "Excluded installation child must remain unread"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", guarded)
    report = host.reconcile_host_skills(root, snapshot=snapshot, verify_sources=[SOURCE],
                                       installed_paths={SOURCE: str(target / "SKILL.md")})
    assert report["skills"][0]["alignment"] == "enabled_observation_matches_reviewed_installation"


@pytest.mark.parametrize("directory_name", ["root-project", "package"])
def test_root_skill_roundtrip_is_entrypoint_only(tmp_path, profile, monkeypatch, directory_name):
    root = tmp_path / directory_name
    root.mkdir()
    (root / "SKILL.md").write_text(BODY)
    (root / ".env").write_text("PRIVATE PROJECT CONTENT")
    (root / "unrelated").mkdir()
    (root / "unrelated/secret.txt").write_text("PRIVATE PROJECT CONTENT")
    adopt(root)
    assert "." in [r["source"] for r in load_skill_index(root)["generation"]["skills"]]
    args = dict(decision="approve", reviewer="fixture", reason="Reviewed root entrypoint",
                expected_head=load_skill_index(root)["head"])
    preview = review_workspace_skill(root, ".", **args)
    review_workspace_skill(root, ".", **args, apply=True, reviewed_plan=preview["review_plan"],
                           expected_plan=preview["plan_digest"])
    (tmp_path / "RECEIPT.json").write_text("unrelated invalid data must remain unread")
    (tmp_path / "snapshot.json").write_text("unrelated invalid data must remain unread")
    walk = os.walk
    def bounded(directory, *args, **kwargs):
        assert Path(directory) != root, "Root Skill export must never walk the project tree"
        return walk(directory, *args, **kwargs)
    monkeypatch.setattr(os, "walk", bounded)
    snapshot = snapshot_skill(root, ".", device_path=profile)
    assert [f["path"] for f in snapshot["files"]] == ["SKILL.md"]
    assert snapshot["directories"] == []
    remote = tmp_path / "remote"
    remote.mkdir()
    store = FolderTransport(str(remote))
    preview = push_skill(root, ".", store, device_path=profile)
    push_skill(root, ".", store, device_path=profile, apply=True, expected_plan=preview["plan_digest"])
    target = tmp_path / "target"
    target.mkdir()
    adopt(target)
    key = preview["plan"]["object_id"]
    preview = pull_skill(target, key, store, device_path=profile)
    pull_skill(target, key, store, device_path=profile, apply=True, expected_plan=preview["plan_digest"])
    preview = install_skill(target, key, SOURCE)
    install_skill(target, key, SOURCE, apply=True, expected_plan=preview["plan_digest"])
    assert (target / SOURCE / "SKILL.md").read_text() == BODY
    assert [f["path"] for f in scan_package(target / SOURCE)[0]] == ["SKILL.md"]


@pytest.mark.parametrize("change,at_read", [("public", 3), ("public", 5), ("identity", 5), ("branch", 5)])
def test_upload_revalidates_reviewed_policy_before_put(tmp_path, profile, monkeypatch, change, at_read):
    root = managed(tmp_path)
    store = GitHubTransport("fixture/repository")
    reads, writes = [], []
    def request(path, *, method="GET", payload=None, **kwargs):
        if path == "":
            value = {"id": 123, "private": True, "default_branch": "main"}
            if len(reads) + 1 >= at_read:
                if change == "public":
                    value["private"] = False
                elif change == "identity":
                    value["id"] = 456
                else:
                    store.branch = "unexpected-branch"
            reads.append(value)
            return value
        if path.startswith("/git/trees/"):
            return {"tree": [], "truncated": False}
        writes.append((path, method))
        raise AssertionError("No write is authorized after destination drift")
    monkeypatch.setattr(store, "_request", request)
    preview = push_skill(root, SOURCE, store, device_path=profile)
    with pytest.raises(ArchMarshalError, match="allow-public|destination changed"):
        push_skill(root, SOURCE, store, device_path=profile, apply=True,
                   expected_plan=preview["plan_digest"])
    assert len(reads) >= at_read and writes == []


def test_explicit_public_upload_still_succeeds_and_checks_bytes(monkeypatch):
    store = GitHubTransport("fixture/public")
    key, data = "a" * 64, b"explicitly approved fictional content"
    objects, writes = {}, []
    def request(path, *, method="GET", payload=None, **kwargs):
        if path == "":
            return {"id": 123, "private": False, "default_branch": "main"}
        if path.startswith("/git/trees/"):
            return {"truncated": False, "tree": [
                {"path": PREFIX + k + ".json", "type": "blob", "mode": "100644",
                 "size": len(v), "sha": "b" * 40} for k, v in objects.items()]}
        if method == "PUT":
            writes.append(path)
            objects[key] = base64.b64decode(payload["content"], validate=True)
            return {}
        return {"encoding": "base64", "content": base64.b64encode(objects[key]).decode()}
    monkeypatch.setattr(store, "_request", request)
    expected = store.describe()
    with pytest.raises(ArchMarshalError, match="allow-public"):
        store.put(key, data, expected_remote=expected)
    assert not writes
    assert store.put(key, data, expected_remote=expected, allow_public=True)
    assert store.get(key) == data
    assert store.put(key, data, expected_remote=expected, allow_public=True) is False
    assert len(writes) == 1
