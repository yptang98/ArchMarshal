from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import archmarshal.external_records as records
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.external_records import project_status, record_location, record_project
from archmarshal.portable import canonical, digest, sha
from archmarshal.project_context import resolve_project_context
from archmarshal.workspace_lock import workspace_mutation_lock


@pytest.fixture
def external(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "report.txt").write_text("verified result\n", encoding="utf-8")
    (project / "source.py").write_text("print('original')\n", encoding="utf-8")
    return project, tmp_path / "records"


def apply(project, store, **kwargs):
    preview = record_project(project, store, **kwargs)
    return record_project(project, store, **kwargs, apply=True,
                          reviewed_plan=preview["plan"], expected_plan=preview["plan_digest"])


def test_external_deliverables_are_real_verified_files_without_project_writes(external):
    project, store = external
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    args = dict(kind="close", summary="Report verified", artifacts=["report.txt"],
                project_id="project-1", thread_id="chat-1", evidence=["Read the result"])
    preview = record_project(project, store, **args)
    assert not store.exists()
    assert project_status(project, store)["registered"] is False
    saved = record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                           expected_plan=preview["plan_digest"])
    assert saved["mode"] == "recorded" and saved["native_archmarshal_state"] is False
    artifact = saved["artifacts"][0]
    assert Path(artifact["absolute_path"]).read_bytes() == (project / "report.txt").read_bytes()
    assert Path(artifact["absolute_path"]).is_relative_to(store)
    assert artifact["state"] == "verified"
    status = project_status(project, store, verify_artifacts=True)
    assert status["latest"][0]["record"]["context"]["thread_id"] == "chat-1"
    assert status["uncommitted"] == []
    assert status["latest"][0]["integrity"] == "content_address_verified"
    assert before == {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert not (project / ".agent").exists()
    assert not (project / ".agents").exists()
    repeated = record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                              expected_plan=preview["plan_digest"])
    assert repeated["mode"] == "unchanged"


def test_two_chats_can_publish_previews_created_before_binding(external):
    project, store = external
    args = dict(kind="start", summary="Start")
    previews = [record_project(project, store, **args, thread_id=thread) for thread in ("one", "two")]
    for thread, preview in zip(("one", "two"), previews):
        record_project(project, store, **args, thread_id=thread, apply=True,
                       reviewed_plan=preview["plan"], expected_plan=preview["plan_digest"])
    latest = project_status(project, store)["latest"]
    assert [item["record"]["context"]["thread_id"] for item in latest] == ["two", "one"]


def test_same_name_projects_and_changed_locations_do_not_share_records(tmp_path):
    roots = [tmp_path / name / "same" for name in ("a", "b")]
    store = tmp_path / "records"
    for root in roots:
        root.mkdir(parents=True)
        apply(root, store, kind="start", summary=root.parent.name)
    assert record_location(roots[0], store)[1] != record_location(roots[1], store)[1]
    original_records = project_status(roots[0], store)["record_root"]
    moved = tmp_path / "renamed"
    assert roots[0].resolve().is_relative_to(tmp_path.resolve())
    assert moved.resolve().is_relative_to(tmp_path.resolve())
    roots[0].rename(moved)
    assert project_status(roots[0], store)["project_available"] is False
    assert project_status(moved, store)["registered"] is False
    apply(moved, store, kind="note", summary="Explicit new location")
    assert project_status(moved, store)["record_root"] != original_records
    assert Path(original_records).is_dir()


def test_existing_local_adapter_records_are_read_without_rewriting(external):
    project, store = external
    _, location = record_location(project, store)
    location.mkdir(parents=True)
    binding = location / "project.json"
    old = json.dumps(records._binding(project), indent=2).encode()
    binding.write_bytes(old)
    history = location / "history/2026/01/01/120000-legacy.json"
    history.parent.mkdir(parents=True)
    history.write_bytes(canonical({"format": records.LEGACY_FORMAT, "project_root": str(project),
                                  "native_archmarshal_state": False, "summary": "Existing record"}))
    assert project_status(project, store)["latest"][0]["integrity"] == "legacy_unsealed"
    apply(project, store, kind="note", summary="New record")
    assert binding.read_bytes() == old
    assert len(project_status(project, store)["latest"]) == 2


def test_partial_artifact_delivery_is_visible_and_exact_retry_completes(external, monkeypatch):
    project, store = external
    args = dict(kind="close", summary="Delivery", artifacts=["report.txt"])
    preview = record_project(project, store, **args)
    original = records._seal
    def interrupt(path, content):
        if "history" in path.parts:
            raise OSError("interrupted before commit")
        original(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(records, "_seal", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                           expected_plan=preview["plan_digest"])
    status = project_status(project, store)
    assert status["latest"] == [] and status["uncommitted"][0]["stage"] == "uncommitted"
    saved = record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                           expected_plan=preview["plan_digest"])
    assert saved["mode"] == "recorded"


@pytest.mark.parametrize("change", ["source", "binding", "summary", "path"])
def test_changed_plans_and_inputs_block_without_overwriting(external, change):
    project, store = external
    args = dict(kind="close", summary="Delivery", artifacts=["report.txt"])
    preview = record_project(project, store, **args)
    if change == "source":
        (project / "report.txt").write_text("changed")
    elif change == "binding":
        _, location = record_location(project, store)
        location.mkdir(parents=True)
        (location / "project.json").write_text("{}")
    elif change == "summary":
        args["summary"] = "Changed decision"
    else:
        preview["plan"]["record_path"] = "../escaped"
        preview["plan_digest"] = digest(preview["plan"])
    with pytest.raises(ArchMarshalError):
        record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                       expected_plan=preview["plan_digest"])
    assert not (project / ".agent").exists()
    assert not (store / "escaped").exists()


def test_changed_delivered_bytes_are_reported_and_never_repaired_in_place(external):
    project, store = external
    args = dict(kind="close", summary="Delivery", artifacts=["report.txt"])
    preview = record_project(project, store, **args)
    result = record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                            expected_plan=preview["plan_digest"])
    delivered = Path(result["artifacts"][0]["absolute_path"])
    delivered.write_text("User-edited delivered file")
    assert project_status(project, store, verify_artifacts=True)["latest"][0]["artifacts"][0][
        "state"] == "unreadable_or_changed"
    with pytest.raises(ArchMarshalError, match="missing or changed"):
        record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                       expected_plan=preview["plan_digest"])
    assert delivered.read_text() == "User-edited delivered file"


def test_store_failures_and_overlap_do_not_mutate_the_project(external):
    project, store = external
    store.write_text("existing user file")
    with pytest.raises(ArchMarshalError, match="not a directory"):
        record_project(project, store, kind="start", summary="Start")
    with pytest.raises(ArchMarshalError, match="disjoint"):
        record_project(project, project / "records", kind="start", summary="Start")
    assert store.read_text() == "existing user file"
    assert sorted(p.name for p in project.iterdir()) == ["report.txt", "source.py"]


def test_record_requires_exact_preview_and_rejects_secret_summary(external):
    project, store = external
    with pytest.raises(ArchMarshalError, match="complete reviewed"):
        record_project(project, store, kind="start", summary="Start", apply=True)
    with pytest.raises(ArchMarshalError, match="secret"):
        record_project(project, store, kind="note", summary="token=actual-credential")
    assert not store.exists()


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, timeout=20).stdout.strip()


def test_detached_worktree_binds_to_project_but_unrelated_checkout_does_not(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    git(project, "init", "--quiet")
    git(project, "config", "user.name", "Fixture")
    git(project, "config", "user.email", "fixture@example.invalid")
    (project / "file.txt").write_text("original")
    git(project, "add", "file.txt")
    git(project, "commit", "--quiet", "-m", "fixture")
    work = tmp_path / "worktree"
    git(project, "worktree", "add", "--detach", str(work))
    context = resolve_project_context(project, work, project_id="native-project", thread_id="native-chat")
    assert context["relationship"] == "git_worktree" and context["git"]["branch"] is None
    result = apply(project, tmp_path / "records", work_root=work, kind="close",
                   summary="Detached work verified", artifacts=["file.txt"])
    assert Path(result["artifacts"][0]["absolute_path"]).read_text() == "original"
    assert not (work / ".agent").exists() and not (project / ".agent").exists()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    with pytest.raises(ArchMarshalError, match="cannot be associated"):
        resolve_project_context(project, unrelated)


def test_external_commands_preview_apply_and_verify(external, tmp_path, capsys):
    project, store = external
    args = ["project-record", str(project), "--store-root", str(store), "--kind", "close",
            "--summary", "CLI deliverable", "--artifact", "report.txt"]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    plan_file = tmp_path / "reviewed.json"
    plan_file.write_text(json.dumps(preview), encoding="utf-8")
    assert main([*args, "--apply", "--plan-file", str(plan_file), "--expect-plan", preview["plan_digest"]]) == 0
    assert json.loads(capsys.readouterr().out)["artifacts"][0]["state"] == "verified"
    assert main(["project-status", str(project), "--store-root", str(store), "--verify-artifacts"]) == 0
    assert json.loads(capsys.readouterr().out)["latest"][0]["record"]["summary"] == "CLI deliverable"


@pytest.mark.parametrize("plan", [[], ["invalid"], "invalid", {"plan": []}])
def test_malformed_plan_fails_before_creating_storage(external, plan):
    project, store = external
    with pytest.raises(ArchMarshalError, match="complete reviewed"):
        record_project(project, store, kind="note", summary="Result", apply=True,
                       reviewed_plan=plan, expected_plan=digest(plan))
    assert not store.exists()


@pytest.mark.parametrize("field,value", [("kind", []), ("recorded_at", "invalid"), ("evidence", None)])
def test_corrupt_record_metadata_is_rejected_before_writes(external, field, value):
    project, store = external
    preview = record_project(project, store, kind="note", summary="Result")
    preview["plan"]["record"][field] = value
    with pytest.raises(ArchMarshalError):
        record_project(project, store, kind="note", summary="Result", apply=True,
                       reviewed_plan=preview["plan"], expected_plan=digest(preview["plan"]))
    assert not store.exists()


@pytest.mark.parametrize("paths", [[".env"], [".git/config"], ["report.txt", "REPORT.txt"], ["report.txt", 42]])
def test_unsafe_or_ambiguous_artifact_selection_is_rejected(external, paths):
    project, store = external
    with pytest.raises(ArchMarshalError):
        record_project(project, store, kind="close", summary="Report", artifacts=paths)
    assert not store.exists()


def test_limits_bound_reads_and_make_partial_verification_explicit(external, monkeypatch):
    project, store = external
    with monkeypatch.context() as patch:
        patch.setattr(records, "MAX_ARTIFACT_BYTES", 4)
        with pytest.raises(ArchMarshalError):
            record_project(project, store, kind="close", summary="Too large", artifacts=["report.txt"])
    result = apply(project, store, kind="close", summary="Report", artifacts=["report.txt"])
    monkeypatch.setattr(records, "MAX_STATUS_BYTES", 1)
    status = project_status(project, store, verify_artifacts=True)
    assert status["latest"][0]["artifacts"][0]["state"] == "unverified"
    Path(result["record_root"], "history", "extra-entry").touch()
    monkeypatch.setattr(records, "MAX_LIST_ENTRIES", 1)
    with pytest.raises(ArchMarshalError, match="listing budget"):
        project_status(project, store)


def test_active_project_lock_blocks_publication_without_partial_output(external):
    project, store = external
    preview = record_project(project, store, kind="note", summary="Result")
    with workspace_mutation_lock(project, operation="fixture"):
        with pytest.raises(ArchMarshalError, match="Another ArchMarshal mutation"):
            record_project(project, store, kind="note", summary="Result", apply=True,
                           reviewed_plan=preview["plan"], expected_plan=preview["plan_digest"])
    assert not store.exists()


def test_unexpected_partial_bundle_content_is_preserved(external):
    project, store = external
    args = dict(kind="close", summary="Report", artifacts=["report.txt"])
    preview = record_project(project, store, **args)
    location = Path(preview["record_root"])
    location.mkdir(parents=True)
    (location / "project.json").write_bytes(canonical(records._binding(project)))
    bundle = location / "artifacts" / preview["plan"]["record"]["event_id"]
    bundle.mkdir(parents=True)
    unexpected = bundle / "user-owned.txt"
    unexpected.write_text("preserve")
    with pytest.raises(ArchMarshalError, match="Unexpected artifact bundle"):
        record_project(project, store, **args, apply=True, reviewed_plan=preview["plan"],
                       expected_plan=preview["plan_digest"])
    assert unexpected.read_text() == "preserve"
    assert project_status(project, store)["latest"] == []


def test_source_symlink_is_rejected_without_following_it(external, tmp_path):
    project, store = external
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    try:
        (project / "linked.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Platform cannot create fixture symlink: {exc}")
    with pytest.raises(ArchMarshalError):
        record_project(project, store, kind="close", summary="Report", artifacts=["linked.txt"])
    assert not store.exists()
    assert outside.read_text() == "private"


def test_git_environment_cannot_override_explicit_project(external, tmp_path, monkeypatch):
    project, _ = external
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "--quiet")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert resolve_project_context(project)["git"] is None
    with pytest.raises(ArchMarshalError, match="cannot be associated"):
        resolve_project_context(project, other)


def test_worktree_project_subdirectory_and_changed_head(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    git(project, "init", "--quiet")
    git(project, "config", "user.name", "Fixture")
    git(project, "config", "user.email", "fixture@example.invalid")
    (project / "app").mkdir()
    (project / "app/result.txt").write_text("report")
    git(project, "add", "app/result.txt")
    git(project, "commit", "--quiet", "-m", "fixture")
    work = tmp_path / "worktree"
    git(project, "worktree", "add", "--detach", str(work))
    with pytest.raises(ArchMarshalError, match="outside the selected"):
        resolve_project_context(project / "app", work)
    assert resolve_project_context(project / "app", work / "app")["relationship"] == "git_worktree"
    store = tmp_path / "records"
    args = dict(work_root=work / "app", kind="close", summary="Result", artifacts=["result.txt"])
    preview = record_project(project / "app", store, **args)
    git(work, "commit", "--quiet", "--allow-empty", "-m", "changed HEAD")
    with pytest.raises(ArchMarshalError, match="Git state changed"):
        record_project(project / "app", store, **args, apply=True,
                       reviewed_plan=preview["plan"], expected_plan=preview["plan_digest"])
    assert not store.exists()


def local_delivery(project, store):
    apply(project, store, kind="note", summary="Local files archived")
    location = record_location(project, store)[1]
    directory = location / "artifacts/20261110-report-v1"
    directory.mkdir(parents=True)
    raw = b"formal deliverable"
    (directory / "report.txt").write_bytes(raw)
    manifest = {"format": "codex-external-artifact-delivery-v1", "project_root": str(project),
                "record_root": str(location), "native_archmarshal_state": False,
                "artifacts": [{"path": "report.txt", "bytes": len(raw), "sha256": sha(raw)}]}
    (directory / "MANIFEST.json").write_bytes(canonical(manifest))
    return directory, manifest


def test_existing_local_deliverables_are_verified_without_rewriting_or_native_claim(external):
    project, store = external
    directory, _ = local_delivery(project, store)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = project_status(project, store, verify_artifacts=True)
    assert result["uncommitted"] == []
    delivery = result["local_deliveries"][0]
    assert delivery["integrity"] == "legacy_unsealed_manifest"
    assert delivery["state"] == "listed_files_verified" and delivery["native_archmarshal_state"] is False
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    (directory / "report.txt").write_text("changed")
    assert project_status(project, store, verify_artifacts=True)["local_deliveries"][0]["state"] == "changed_or_missing"


@pytest.mark.parametrize("change", ["path", "binding", "duplicate", "size", "self"])
def test_local_delivery_manifest_tampering_is_reported_without_reading_other_files(external, change):
    project, store = external
    directory, manifest = local_delivery(project, store)
    entry = manifest["artifacts"][0]
    if change == "path":
        entry["path"] = "../../private"
    elif change == "binding":
        manifest["project_root"] = str(project.parent)
    elif change == "duplicate":
        manifest["artifacts"].append(dict(entry))
    elif change == "self":
        entry["path"] = "MANIFEST.json"
    else:
        entry["bytes"] = -1
    (directory / "MANIFEST.json").write_bytes(canonical(manifest))
    result = project_status(project, store, verify_artifacts=True)
    assert result["local_deliveries"][0]["state"] == "invalid_manifest"
    assert result["uncommitted"] == []


def test_unknown_external_folders_are_preserved_and_budget_stays_explicit(external, monkeypatch):
    project, store = external
    directory, manifest = local_delivery(project, store)
    (directory.parent / "unrelated-export").mkdir()
    monkeypatch.setattr(records, "MAX_STATUS_BYTES", 1)
    result = project_status(project, store, verify_artifacts=True)
    assert result["local_deliveries"][0]["state"] == "unverified"
    assert result["unmanaged_bundles"][0]["state"] == "unmanaged"
    assert result["uncommitted"] == []
    manifest["format"] = "some-other-tool"
    (directory / "MANIFEST.json").write_bytes(canonical(manifest))
    assert project_status(project, store)["local_deliveries"][0]["state"] == "unrecognized_manifest"
