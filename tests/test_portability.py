from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from archmarshal.adoption import adopt_workspace, plan_adoption
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.handoff import resume_context, save_context
from archmarshal.lifecycle import start_workspace
from archmarshal.portable import canonical, decode, device, digest, init_device, relative
from archmarshal.skill_sync import (
    install_skill,
    pull_skill,
    push_skill,
    snapshot_skill,
    sync_status,
    validate_snapshot,
)
from archmarshal.sync_transport import FolderTransport, GitHubTransport, register_backend, transport


def adopt(root, **kwargs):
    preview = plan_adoption(root, **kwargs)
    result = adopt_workspace(root, apply=True, expected_plan=preview["plan_digest"], **kwargs)
    assert result["mode"] in {"overlay_applied", "overlay_synced", "already_configured"}, result


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    identities = []
    for name in ("laptop", "desktop"):
        path = tmp_path / (name + ".json")
        preview = init_device(name, path=path)
        assert not path.exists()
        init_device(name, path=path, apply=True, expected_plan=preview["plan_digest"])
        identities.append(path)
    remote = tmp_path / "cloud"
    remote.mkdir()
    roots = []
    for name in ("project-a", "project-b"):
        root = tmp_path / name
        root.mkdir()
        (root / "source.py").write_text("print('original')\n", encoding="utf-8")
        roots.append(root)
    skill = roots[0] / ".agents/skills/demo"
    (skill / "scripts").mkdir(parents=True)
    (skill / "assets/empty").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: A demo skill\n---\nRun checks.\n")
    (skill / "scripts/check.py").write_text("print('check')\n")
    (skill / "assets/icon.bin").write_bytes(bytes(range(256)))
    if os.name != "nt":
        (skill / "scripts/check.py").chmod(0o755)
    for root in roots:
        adopt(root)
    return roots, identities, FolderTransport(str(remote))


def upload(env):
    roots, identities, store = env
    args = dict(device_path=identities[0])
    preview = push_skill(roots[0], ".agents/skills/demo", store, **args)
    assert not store.keys()
    result = push_skill(roots[0], ".agents/skills/demo", store, apply=True,
                        expected_plan=preview["plan_digest"], **args)
    assert result["mode"] == "uploaded"
    return result["plan"]["object_id"]


def pull(env, key):
    roots, identities, store = env
    args = dict(device_path=identities[1])
    preview = pull_skill(roots[1], key, store, **args)
    assert not Path(preview["package"]).exists()
    return pull_skill(roots[1], key, store, apply=True,
                      expected_plan=preview["plan_digest"], **args)


def save(root, profile, stream, summary="Ready to continue", **kwargs):
    args = dict(stream=stream, summary=summary, device_path=profile, **kwargs)
    preview = save_context(root, **args)
    return save_context(root, apply=True, expected_plan=preview["plan_digest"], **args)


def test_two_machine_roundtrip_install_and_origin_preservation(environment):
    roots, identities, store = environment
    key = upload(environment)
    catalog = sync_status(store)
    assert catalog["objects"][0]["origin"] == device(identities[0])
    result = pull(environment, key)
    package = Path(result["package"])
    assert result["activation"] == "quarantined"
    assert (package / "assets/icon.bin").read_bytes() == bytes(range(256))
    assert (package / "assets/empty").is_dir()
    assert not (roots[1] / ".agents/skills/demo").exists()
    destination = ".agents/skills/imported/demo"
    plan = install_skill(roots[1], key, destination)
    install_skill(roots[1], key, destination, apply=True, expected_plan=plan["plan_digest"])
    assert (roots[1] / destination / "SKILL.md").read_bytes() == (package / "SKILL.md").read_bytes()
    if os.name != "nt":
        assert (roots[1] / destination / "scripts/check.py").stat().st_mode & 0o111
    adopt(roots[1])
    from archmarshal.inventory import collect_inventory
    from archmarshal.resolver import skill_activation_block_reason
    from archmarshal.skill_index import load_skill_index
    from archmarshal.skill_review import review_workspace_skill
    before = next(s for s in collect_inventory(roots[1]).skills if s["_skill_dir"] == destination)
    assert skill_activation_block_reason(before) is not None
    head = load_skill_index(roots[1])["head"]
    review = review_workspace_skill(roots[1], destination, decision="approve", expected_head=head)
    review_workspace_skill(roots[1], destination, decision="approve", expected_head=head,
                           expected_plan=review["plan_digest"], reviewed_plan=review["review_plan"],
                           apply=True)
    after = next(s for s in collect_inventory(roots[1]).skills if s["_skill_dir"] == destination)
    assert skill_activation_block_reason(after) is None
    copied = snapshot_skill(roots[1], destination, device_path=identities[1])
    assert copied["origin"] == device(identities[0])
    assert copied["exporter"] == device(identities[1])
    assert copied["parents"] == [key]
    (roots[1] / destination / "scripts/check.py").write_text("print('modified')\n")
    modified = snapshot_skill(roots[1], destination, device_path=identities[1])
    assert modified["origin"] == copied["origin"]
    assert digest(modified) != digest(copied)


def test_repeat_sync_is_idempotent_and_conflicts_keep_both(environment):
    roots, identities, store = environment
    first = upload(environment)
    preview = push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[0])
    repeated = push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[0],
                          apply=True, expected_plan=preview["plan_digest"])
    assert repeated["mode"] == "unchanged"
    pull(environment, first)
    preview = pull_skill(roots[1], first, store, device_path=identities[1])
    assert pull_skill(roots[1], first, store, device_path=identities[1], apply=True,
                      expected_plan=preview["plan_digest"])["mode"] == "unchanged"
    (roots[0] / ".agents/skills/demo/scripts/check.py").write_text("print('new revision')\n")
    second = push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[1])
    push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[1], apply=True,
               expected_plan=second["plan_digest"])
    assert len(store.keys()) == 2
    assert len(next(iter(sync_status(store)["variants"].values()))) == 2


def test_stale_upload_plan_and_remote_tampering(environment):
    roots, identities, store = environment
    preview = push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[0])
    (roots[0] / ".agents/skills/demo/scripts/check.py").write_text("changed\n")
    with pytest.raises(ArchMarshalError, match="Preview changed"):
        push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[0],
                   apply=True, expected_plan=preview["plan_digest"])
    assert not store.keys()
    key = upload(environment)
    path = store.root / "archmarshal-skills/v1" / (key + ".json")
    snapshot = json.loads(path.read_bytes())
    snapshot["origin"]["name"] = "forged"
    path.write_bytes(canonical(snapshot))
    with pytest.raises(ArchMarshalError, match="digest"):
        pull_skill(roots[1], key, store, device_path=identities[1])


def test_exclusions_caches_nested_packages_and_secrets(environment):
    roots, identities, store = environment
    skill = roots[0] / ".agents/skills/demo"
    child = skill / "child"
    child.mkdir()
    (child / "SKILL.md").write_text("---\nname: child\ndescription: child\n---\n")
    (child / "private.txt").write_text("must not export")
    (skill / "node_modules").mkdir()
    (skill / "node_modules/secret.txt").write_text("must not export")
    adopt(roots[0], exclude_skills=[".agents/skills/demo/child"])
    snapshot = snapshot_skill(roots[0], ".agents/skills/demo", device_path=identities[0])
    assert not any(f["path"].startswith(("child/", "node_modules/")) for f in snapshot["files"])
    with pytest.raises(ArchMarshalError, match="excluded"):
        snapshot_skill(roots[0], ".agents/skills/demo/child", device_path=identities[0])
    (skill / ".env").write_text("HELLO=world")
    with pytest.raises(ArchMarshalError, match="credential"):
        push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[0])
    assert not store.keys()


@pytest.mark.parametrize("path", ["../outside", "C:/outside", "a\\b", "/abs", "a//b", "CON",
                                  "a. ", "a\x00b", "foo/../bar"])
def test_portable_paths_reject_unsafe_names(path):
    with pytest.raises(ArchMarshalError):
        relative(path)


def test_malformed_packages_fail_before_import(environment):
    roots, profiles, store = environment
    original = snapshot_skill(roots[0], ".agents/skills/demo", device_path=profiles[0])
    cases = []
    for field, value in (("path", "../escape"), ("sha256", "0" * 64),
                         ("bytes", 9999), ("content", "!!!"), ("executable", "yes")):
        item = copy.deepcopy(original)
        item["files"][0][field] = value
        cases.append(item)
    item = copy.deepcopy(original)
    item["files"].append(item["files"][0])
    cases.append(item)
    item = copy.deepcopy(original)
    item["directories"] = []
    cases.append(item)
    for snapshot in cases:
        with pytest.raises(ArchMarshalError):
            validate_snapshot(canonical(snapshot), digest(snapshot))
    with pytest.raises(ArchMarshalError, match="Duplicate"):
        decode(b'{"format":1,"format":2}')


def test_local_import_changes_and_install_collision_are_preserved(environment):
    roots, identities, store = environment
    key = upload(environment)
    result = pull(environment, key)
    source = Path(result["package"]) / "SKILL.md"
    source.write_text("user changed it")
    plan = pull_skill(roots[1], key, store, device_path=identities[1])
    with pytest.raises(ArchMarshalError, match="modified"):
        pull_skill(roots[1], key, store, device_path=identities[1], apply=True,
                   expected_plan=plan["plan_digest"])
    assert source.read_text() == "user changed it"
    destination = roots[1] / ".agents/skills/collision"
    destination.mkdir(parents=True)
    with pytest.raises(ArchMarshalError, match="already exists"):
        install_skill(roots[1], key, ".agents/skills/collision")


def test_public_upload_requires_explicit_choice(environment, monkeypatch):
    roots, identities, store = environment
    monkeypatch.setattr(store, "describe", lambda: {"backend": "test", "public": True})
    plan = push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[0])
    assert plan["public_upload_blocked"]
    with pytest.raises(ArchMarshalError, match="allow-public"):
        push_skill(roots[0], ".agents/skills/demo", store, device_path=identities[0],
                   apply=True, expected_plan=plan["plan_digest"])
    assert not store.keys()


def test_parallel_context_restore_overlap_staleness_and_synthesis(environment):
    roots, profiles, _ = environment
    root = roots[0]
    args = dict(files=["source.py"], task="Build frontend", decisions=["Use stable API because clients depend on it"])
    before = save_context(root, stream="frontend", summary="UI ready", device_path=profiles[0], **args)
    assert not (root / ".agent/context-index").exists()
    first = save_context(root, stream="frontend", summary="UI ready", device_path=profiles[0],
                         apply=True, expected_plan=before["plan_digest"], **args)
    second = save(root, profiles[1], "backend", "API ready", files=["source.py"],
                  next_steps=["Integrate UI"], evidence=["pytest: 12 passed; observed locally"])
    resumed = resume_context(root)
    assert {item["record"]["stream"] for item in resumed["contexts"]} == {"frontend", "backend"}
    assert resumed["overlapping_files"][0]["path"] == "source.py"
    assert not resumed["execution_verified"]
    (root / "source.py").write_text("changed by the other window")
    assert all(item["changed_files"] == ["source.py"] for item in resume_context(root)["contexts"])
    merged = save(root, profiles[0], "integration", "Join the UI and API", integrates=[
        first["record_id"], second["record_id"]], next_steps=["Run integration tests"])
    merged_record = json.loads(Path(merged["path"]).read_bytes())
    assert merged_record["integrated_contexts"][first["record_id"]]["summary"] == "UI ready"
    assert len(resume_context(root)["contexts"]) == 3
    assert start_workspace(root)["context"]["total_stream_heads"] == 3


def test_stale_same_window_plan_rejected_without_losing_other_stream(environment):
    roots, profiles, _ = environment
    root = roots[0]
    args = dict(stream="frontend", summary="Old planned summary", device_path=profiles[0])
    old = save_context(root, **args)
    save(root, profiles[1], "backend")
    # Independent stream writes do not invalidate the preview.
    save_context(root, **args, apply=True, expected_plan=old["plan_digest"])
    with pytest.raises(ArchMarshalError, match="Preview changed"):
        save_context(root, **args, apply=True, expected_plan=old["plan_digest"])
    assert len(resume_context(root)["contexts"]) == 2


def test_context_corruption_and_missing_parent_are_not_silently_ignored(environment):
    from archmarshal.doctor import doctor_workspace
    roots, profiles, _ = environment
    result = save(roots[0], profiles[0], "work")
    path = Path(result["path"])
    original = path.read_bytes()
    assert any(f["code"] == "context_verified" for f in doctor_workspace(roots[0])["findings"])
    path.write_bytes(original.replace(b"Ready", b"Edited"))
    with pytest.raises(ArchMarshalError, match="modified"):
        resume_context(roots[0])
    assert start_workspace(roots[0])["context"]["mode"] == "error"
    assert any(f["code"] == "context_integrity_failed"
               for f in doctor_workspace(roots[0])["findings"])


def test_context_budget_and_explicit_cross_checkout_merge(environment):
    roots, profiles, _ = environment
    first = save(roots[0], profiles[0], "frontend", "UI " + "x" * 3000)
    second = save(roots[1], profiles[1], "backend", "API " + "y" * 3000)
    small = resume_context(roots[0], include_roots=[str(roots[1])], max_chars=2000)
    assert small["truncated"] and len(small["omitted"]) == 2
    merged = save(roots[0], profiles[0], "combined", integrates=[first["record_id"], second["record_id"]],
                  include_roots=[str(roots[1])])
    record = json.loads(Path(merged["path"]).read_bytes())
    assert record["integrated_contexts"][second["record_id"]]["stream"] == "backend"
    # Restoration of the synthesis no longer needs the other checkout.
    assert resume_context(roots[0])["total_stream_heads"] == 2


def test_github_address_discovers_saved_local_checkouts(environment):
    roots, profiles, _ = environment
    for root in roots:
        subprocess.run(["git", "init", str(root)], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                        "git@github.com:example/project.git"], check=True)
    save(roots[0], profiles[0], "ui")
    save(roots[1], profiles[1], "api")
    result = resume_context("https://github.com/example/project")
    assert len(result["roots"]) == 2 and result["total_stream_heads"] == 2
    with pytest.raises(ArchMarshalError, match="No recorded"):
        resume_context("https://github.com/example/absent")


def test_context_sensitive_inputs_and_paths_are_rejected(environment):
    roots, profiles, _ = environment
    for kwargs in ({"summary": "token=" + "secret-value"}, {"files": ["../private"]},
                   {"status": "unknown"}, {"integrates": ["a" * 64]}):
        args = {"stream": "one", "summary": "Safe", "device_path": profiles[0], **kwargs}
        with pytest.raises(ArchMarshalError):
            save_context(roots[0], **args)
    assert not (roots[0] / ".agent/context-index").exists()


def test_cli_device_and_context_workflow(environment, capsys):
    roots, profiles, store = environment
    command = ["context-save", str(roots[0]), "--stream", "cli", "--summary", "CLI handoff",
               "--device-file", str(profiles[0])]
    assert main(command) == 0
    preview = json.loads(capsys.readouterr().out)
    assert main([*command, "--apply", "--expect-plan", preview["plan_digest"]]) == 0
    capsys.readouterr()
    assert main(["context-resume", str(roots[0])]) == 0
    assert json.loads(capsys.readouterr().out)["contexts"][0]["record"]["summary"] == "CLI handoff"
    assert main(["sync-status", "--backend", "folder", "--remote", str(store.root)]) == 0
    assert json.loads(capsys.readouterr().out)["objects"] == []


def test_transport_extension_and_github_default(environment):
    _, _, store = environment
    register_backend("test-portability", lambda remote, **kwargs: store)
    try:
        assert transport("unused", backend="test-portability") is store
        assert isinstance(transport("owner/repo"), GitHubTransport)
        with pytest.raises(ArchMarshalError):
            transport("https://token@github.com/owner/repo")
    finally:
        from archmarshal.sync_transport import BACKENDS
        BACKENDS.pop("test-portability")


def test_github_wire_protocol_pinned_reads_create_only_and_retry(environment, monkeypatch):
    roots, profiles, _ = environment
    data = canonical(snapshot_skill(roots[0], ".agents/skills/demo", device_path=profiles[0]))
    key = digest(json.loads(data))
    store = GitHubTransport("owner/repo")
    objects = {}
    requests = []
    def request(path, *, method="GET", payload=None, **kwargs):
        requests.append((path, method, payload))
        if path == "":
            return {"id": 123, "private": True, "default_branch": "main"}
        if path.startswith("/git/trees/"):
            return {"truncated": False, "tree": [
                {"path": "archmarshal-skills/v1/" + k + ".json", "type": "blob", "mode": "100644",
                 "sha": "a" * 40, "size": len(v)} for k, v in objects.items()]}
        if path.startswith("/contents/"):
            assert method == "PUT" and "sha" not in payload
            assert payload["branch"] == "main"
            objects[key] = base64.b64decode(payload["content"])
            return {"commit": {"sha": "b" * 40}}
        if path == "/git/blobs/" + "a" * 40:
            return {"encoding": "base64", "content": base64.b64encode(objects[key]).decode()}
        raise AssertionError(path)
    monkeypatch.setattr(store, "_request", request)
    assert store.put(key, data)
    assert not store.put(key, data)
    assert store.get(key) == data
    assert len([r for r in requests if r[1] == "PUT"]) == 1
    monkeypatch.setattr(store, "_request", lambda *a, **k: {"tree": [], "truncated": True})
    with pytest.raises(ArchMarshalError, match="incomplete"):
        store.keys()


def test_device_is_persistent_and_invalid_profiles_fail_closed(tmp_path):
    path = tmp_path / "identity.json"
    preview = init_device("machine", path=path)
    with pytest.raises(ArchMarshalError):
        init_device("machine", path=path, apply=True, expected_plan="stale")
    first = init_device("machine", path=path, apply=True, expected_plan=preview["plan_digest"])
    assert init_device("machine", path=path)["device"] == first["device"]
    with pytest.raises(ArchMarshalError, match="another name"):
        init_device("another", path=path)
    path.write_text('{"format":"archmarshal-device-v1","id":"bad","name":"machine"}')
    with pytest.raises(ArchMarshalError, match="UUID"):
        device(path)


def test_github_http_contract_keeps_authentication_out_of_errors(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from archmarshal.sync_transport import _github_token, _NoRedirect

    monkeypatch.setenv("GH_TOKEN", "private-token-for-test")
    captured = []
    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            return io.BytesIO(b'{"id":123,"private":true,"default_branch":"main"}')
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    store = GitHubTransport("owner/repo")
    assert store.describe()["public"] is False
    assert captured[0].headers["Authorization"] == "Bearer private-token-for-test"
    assert captured[0].full_url == "https://api.github.com/repos/owner/repo"
    assert _NoRedirect().redirect_request(None, None, 302, "", None, "https://elsewhere") is None
    class FailingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "private-token-for-test", {}, None)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: FailingOpener())
    with pytest.raises(ArchMarshalError) as error:
        GitHubTransport("owner/repo").describe()
    assert "private-token-for-test" not in str(error.value)
    assert error.value.code == "sync_http_401"
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, "username=test\npassword=from-helper\n"))
    assert _github_token() == "from-helper"
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 1, "", "sensitive helper message"))
    with pytest.raises(ArchMarshalError, match="Authenticate Git"):
        _github_token()


def test_symlinked_journal_and_folder_are_rejected(environment, tmp_path):
    roots, profiles, store = environment
    saved = save(roots[0], profiles[0], "one")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = roots[0] / ".agent/context-index/linked"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArchMarshalError, match="link|junction"):
        resume_context(roots[0])
    assert Path(saved["path"]).exists()


def test_mapped_checkpoint_directory_and_historical_read(environment):
    import yaml

    from archmarshal.handoff import read_context
    roots, profiles, _ = environment
    config = roots[0] / ".agent/workspace.yaml"
    data = yaml.safe_load(config.read_text())
    data["save_paths"]["project_files"]["checkpoints"] = "project-notes"
    config.write_text(yaml.safe_dump(data))
    first = save(roots[0], profiles[0], "history", "original rationale",
                 constraints=["Keep old clients supported"], rejected_options=["Breaking API rejected"])
    second = save(roots[0], profiles[0], "history", "work complete", status="completed")
    assert Path(first["path"]).is_relative_to(roots[0] / "project-notes")
    assert read_context(roots[0], first["record_id"])["record"]["summary"] == "original rationale"
    restored = resume_context(roots[0])
    assert len(restored["contexts"]) == 1
    assert restored["contexts"][0]["record_id"] == second["record_id"]
    assert read_context(roots[0], first["record_id"])["record"]["constraints"]


def test_concurrent_workspace_writer_blocks_sync_and_context(environment):
    from archmarshal.workspace_lock import workspace_mutation_lock
    roots, profiles, store = environment
    with workspace_mutation_lock(roots[0], operation="other-window"):
        with pytest.raises(ArchMarshalError, match="Another ArchMarshal"):
            snapshot_skill(roots[0], ".agents/skills/demo", device_path=profiles[0])
        preview = save_context(roots[0], stream="one", summary="state", device_path=profiles[0])
        with pytest.raises(ArchMarshalError, match="Another ArchMarshal"):
            save_context(roots[0], stream="one", summary="state", device_path=profiles[0],
                         apply=True, expected_plan=preview["plan_digest"])
    assert not (roots[0] / ".agent/context-index").exists()


def test_documented_cli_sync_and_merge_end_to_end(environment, capsys):
    roots, profiles, store = environment
    def command(tokens):
        assert main(list(map(str, tokens))) == 0, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)
    def applied(tokens):
        preview = command(tokens)
        return command([*tokens, "--apply", "--expect-plan", preview["plan_digest"]])
    uploaded = applied(["sync-push", roots[0], "--skill", ".agents/skills/demo", "--backend", "folder",
                        "--remote", store.root, "--device-file", profiles[0]])
    key = uploaded["plan"]["object_id"]
    assert applied(["sync-pull", roots[1], "--object", key, "--backend", "folder",
                    "--remote", store.root, "--device-file", profiles[1]])["mode"] == "imported"
    assert applied(["sync-install", roots[1], "--object", key, "--destination",
                    ".agents/skills/from-cli/demo"])["mode"] == "installed"
    records = []
    for stream in ("ui", "api"):
        result = applied(["context-save", roots[0], "--stream", stream, "--summary", stream + " ready",
                          "--device-file", profiles[0]])
        records.append(result["record_id"])
    merged = applied(["context-merge", roots[0], "--stream", "join", "--summary", "Integrate",
                      "--integrate", records[0], "--integrate", records[1],
                      "--device-file", profiles[0]])
    loaded = command(["context-read", roots[0], "--record", merged["record_id"]])
    assert set(loaded["record"]["integrated_contexts"]) == set(records)


@pytest.mark.parametrize("entry", [None, {}, {"path": None},
    {"path": "archmarshal-skills/v1/" + "a" * 64 + ".json", "type": "blob", "mode": "120000"},
    {"path": "archmarshal-skills/v1/" + "a" * 64 + ".json", "type": "blob", "mode": "100644", "sha": "bad"},
    {"path": "archmarshal-skills/v1/" + "a" * 64 + ".json", "type": "blob", "mode": "100644",
     "sha": "b" * 40, "size": "invalid"}])
def test_remote_malformed_tree_fails_with_structured_error(entry, monkeypatch):
    store = GitHubTransport("owner/repo")
    store._repository = {"id": 123, "private": True, "default_branch": "main"}
    monkeypatch.setattr(store, "_request", lambda *a, **k: {"truncated": False, "tree": [entry]})
    with pytest.raises(ArchMarshalError):
        store.keys()


def test_github_concurrent_create_is_verified_before_noop(monkeypatch):
    store = GitHubTransport("owner/repo")
    key = "a" * 64
    payload = b"test object"
    monkeypatch.setattr(store, "describe", lambda: {"public": False})
    calls = 0
    def keys():
        nonlocal calls
        calls += 1
        return [] if calls == 1 else [key]
    def conflict(*args, **kwargs):
        raise ArchMarshalError("sync_http_409", "Concurrent create")
    monkeypatch.setattr(store, "keys", keys)
    monkeypatch.setattr(store, "get", lambda key: payload)
    monkeypatch.setattr(store, "_request", conflict)
    assert store.put(key, payload) is False
    calls = 0
    monkeypatch.setattr(store, "get", lambda key: b"different object")
    with pytest.raises(ArchMarshalError, match="Concurrent create"):
        store.put(key, payload)
