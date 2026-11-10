"""Regression cases found by the 0.18 stability and boundary audit."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from test_portability import environment as environment
from test_portability import pull, save, upload

from archmarshal import handoff, skill_sync
from archmarshal.errors import ArchMarshalError
from archmarshal.layout_policy import build_layout_plan
from archmarshal.ownership import require_owned_workspace, valid_ownership_marker
from archmarshal.portable import canonical, decode, digest, strict_files
from archmarshal.skill_index import load_skill_index
from archmarshal.skill_sync import install_skill, pull_skill, snapshot_skill, validate_snapshot
from archmarshal.skill_validation import validate_skill_package
from archmarshal.sync_transport import GitHubTransport
from archmarshal.workspace_layout import load_workspace_layout


def test_context_capacity_rejected_before_append(environment, monkeypatch):
    roots, profiles, _ = environment
    monkeypatch.setattr(handoff, "MAX_RECORDS", 1)
    first = save(roots[0], profiles[0], "one")
    before = set(roots[0].rglob("*.json"))
    with pytest.raises(ArchMarshalError, match="budget|limit|capacity"):
        save(roots[0], profiles[0], "two")
    assert set(roots[0].rglob("*.json")) == before
    assert handoff.resume_context(roots[0])["contexts"][0]["record_id"] == first["record_id"]


def test_context_byte_capacity_rejected_before_append(environment, monkeypatch):
    roots, profiles, _ = environment
    first = save(roots[0], profiles[0], "one")
    monkeypatch.setattr(handoff, "MAX_HISTORY_BYTES", Path(first["path"]).stat().st_size + 10)
    with pytest.raises(ArchMarshalError, match="budget|limit|capacity"):
        save(roots[0], profiles[0], "two")
    assert handoff.resume_context(roots[0])["total_stream_heads"] == 1


@pytest.mark.parametrize("field,value", [("status", []), ("status", {}),
    ("decisions", {}), ("decisions", 0), ("decisions", None)])
def test_malformed_handoff_rejected_as_domain_error(environment, field, value):
    roots, profiles, _ = environment
    first = save(roots[0], profiles[0], "one")
    path = Path(first["path"])
    record = decode(path.read_bytes())
    record[field] = value
    key = digest(record)
    path.write_bytes(canonical(record))
    index = roots[0] / handoff.INDEX
    old = index / (first["record_id"] + ".json")
    pointer = decode(old.read_bytes())
    pointer["id"] = key
    old.unlink()
    (index / (key + ".json")).write_bytes(canonical(pointer))
    with pytest.raises(ArchMarshalError):
        handoff.resume_context(roots[0])


@pytest.mark.parametrize("entry", [{"format": "archmarshal-project-location-v1"},
    {"format": "archmarshal-project-location-v1", "root": [], "address": "https://github.com/a/b"}])
def test_malformed_registry_rejected_as_domain_error(environment, entry):
    directory = handoff._registry()
    directory.mkdir(parents=True)
    (directory / "bad.json").write_bytes(canonical(entry))
    with pytest.raises(ArchMarshalError):
        handoff.resume_context("https://github.com/a/b")


@pytest.mark.parametrize("extra", ["node_modules/extra.js", "nested/SKILL.md"])
def test_quarantine_verification_rejects_ignored_extra_files(environment, extra):
    roots, profiles, store = environment
    key = upload(environment)
    imported = pull(environment, key)
    added = Path(imported["package"]) / extra
    added.parent.mkdir(parents=True)
    added.write_text("unexpected code")
    preview = pull_skill(roots[1], key, store, device_path=profiles[1])
    with pytest.raises(ArchMarshalError):
        pull_skill(roots[1], key, store, device_path=profiles[1], apply=True,
                   expected_plan=preview["plan_digest"])
    with pytest.raises(ArchMarshalError):
        install_skill(roots[1], key, ".agents/skills/demo")
    assert added.read_text() == "unexpected code"


@pytest.mark.parametrize("inbox", [".git", ".GIT", ".agents/skills", ".claude/skills",
    ".agent/context-index", "custom-skills/incoming"])
def test_quarantine_cannot_enter_reserved_or_discovered_roots(environment, inbox):
    roots, profiles, store = environment
    key = upload(environment)
    config = roots[1] / ".agent/workspace.yaml"
    data = yaml.safe_load(config.read_text())
    data["paths"]["inbox"] = [inbox]
    data["paths"]["source_skill_roots"] = ["custom-skills"]
    config.write_text(yaml.safe_dump(data))
    with pytest.raises(ArchMarshalError):
        pull_skill(roots[1], key, store, device_path=profiles[1])


@pytest.mark.parametrize("destination", [".GIT/demo", ".AGENT/demo"])
def test_install_rejects_reserved_path_aliases(environment, destination):
    roots, _, _ = environment
    key = upload(environment)
    pull(environment, key)
    with pytest.raises(ArchMarshalError):
        install_skill(roots[1], key, destination)


def test_snapshot_parent_spelling_must_match_exactly(environment):
    roots, profiles, _ = environment
    snapshot = snapshot_skill(roots[0], ".agents/skills/demo", device_path=profiles[0])
    next(f for f in snapshot["files"] if f["path"] == "scripts/check.py")["path"] = "Scripts/check.py"
    with pytest.raises(ArchMarshalError, match="topology"):
        validate_snapshot(canonical(snapshot), digest(snapshot))


@pytest.mark.parametrize("raw", [b'{"number":NaN}', b'{"number":Infinity}',
    b'{"number":1e1000}', ('{"nested":' + '[' * 120 + '0' + ']' * 120 + '}').encode()])
def test_portable_json_rejects_nonfinite_and_deep_values(raw):
    with pytest.raises(ArchMarshalError):
        decode(raw)


@pytest.mark.parametrize("field", ["strategy", "timezone", "date_partition"])
def test_invalid_layout_options_are_diagnostics_not_type_errors(tmp_path, field):
    plan = build_layout_plan(tmp_path, configured=False, naming_overrides={field: []})
    assert plan["quality"] == "unsafe"
    assert plan["issues"]


def test_github_visibility_is_refreshed(monkeypatch):
    store = GitHubTransport("owner/repo")
    state = {"id": 123, "private": True, "default_branch": "main"}
    monkeypatch.setattr(store, "_request", lambda *a, **k: dict(state))
    assert store.describe()["public"] is False
    state["private"] = False
    assert store.describe()["public"] is True


def test_context_evidence_is_hashed_once_per_checkout(environment, monkeypatch):
    roots, profiles, _ = environment
    for stream in ("one", "two", "three"):
        save(roots[0], profiles[0], stream, files=["source.py"])
    original = handoff.read
    reads = []
    def tracked(path, *args, **kwargs):
        if path.name == "source.py":
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(handoff, "read", tracked)
    assert handoff.resume_context(roots[0])["total_stream_heads"] == 3
    assert len(reads) == 1


def test_context_rechecks_capacity_under_lock(environment, monkeypatch):
    roots, profiles, _ = environment
    save(roots[0], profiles[0], "one")
    args = dict(stream="two", summary="later", device_path=profiles[0])
    preview = handoff.save_context(roots[0], **args)
    original = handoff.workspace_mutation_lock
    @contextmanager
    def changed_capacity(*args, **kwargs):
        with original(*args, **kwargs) as held:
            monkeypatch.setattr(handoff, "MAX_RECORDS", 1)
            yield held
    monkeypatch.setattr(handoff, "workspace_mutation_lock", changed_capacity)
    with pytest.raises(ArchMarshalError, match="budget"):
        handoff.save_context(roots[0], apply=True, expected_plan=preview["plan_digest"], **args)
    assert handoff.resume_context(roots[0])["total_stream_heads"] == 1


def test_registry_failure_reports_committed_context(environment, monkeypatch):
    roots, profiles, _ = environment
    monkeypatch.setattr(handoff, "_git_state", lambda _: {
        "head": "", "branch": "", "remote": "https://github.com/owner/repo"})
    directory = handoff._registry()
    directory.parent.mkdir(parents=True)
    directory.write_text("occupied")
    result = save(roots[0], profiles[0], "one")
    assert result["mode"] == "recorded"
    assert result["warnings"][0]["kind"] == "project_registry_unavailable"
    assert handoff.resume_context(roots[0])["contexts"][0]["record_id"] == result["record_id"]


def test_exhausted_evidence_budget_never_verifies_a_later_stream(environment, monkeypatch):
    roots, profiles, _ = environment
    for stream in ("one", "two"):
        save(roots[0], profiles[0], stream, files=["source.py"])
    monkeypatch.setattr(handoff, "MAX_EVIDENCE_BYTES", 1)
    result = handoff.resume_context(roots[0])
    assert all(item["changed_files"] == ["source.py"] for item in result["contexts"])
    assert len(result["warnings"]) == 2


def test_sync_rechecks_inbox_after_lock(environment, monkeypatch):
    roots, profiles, store = environment
    key = upload(environment)
    args = dict(device_path=profiles[1])
    preview = pull_skill(roots[1], key, store, **args)
    original = skill_sync.workspace_mutation_lock
    @contextmanager
    def changed_mapping(*args, **kwargs):
        with original(*args, **kwargs) as held:
            config = roots[1] / ".agent/workspace.yaml"
            data = yaml.safe_load(config.read_text())
            data["paths"]["inbox"] = ["new-inbox"]
            config.write_text(yaml.safe_dump(data))
            yield held
    monkeypatch.setattr(skill_sync, "workspace_mutation_lock", changed_mapping)
    with pytest.raises(ArchMarshalError, match="mapping changed"):
        pull_skill(roots[1], key, store, apply=True, expected_plan=preview["plan_digest"], **args)
    assert not Path(preview["package"]).exists()


def test_regular_file_cannot_masquerade_as_empty_store(tmp_path):
    path = tmp_path / "index"
    path.write_text("corruption")
    with pytest.raises(ArchMarshalError, match="directory"):
        strict_files(path)


def test_missing_registry_is_read_only(environment):
    with pytest.raises(ArchMarshalError, match="No recorded local checkout"):
        handoff.resume_context("https://github.com/owner/repo")
    assert not handoff._registry().exists()


@pytest.mark.parametrize("field,value", [("format", "other"), ("imported_by", []),
    ("activation", "active")])
def test_install_requires_complete_valid_receipt(environment, field, value):
    roots, _, _ = environment
    key = upload(environment)
    result = pull(environment, key)
    path = Path(result["package"]).parent / "RECEIPT.json"
    receipt = decode(path.read_bytes())
    receipt[field] = value
    path.write_bytes(canonical(receipt))
    with pytest.raises(ArchMarshalError):
        install_skill(roots[1], key, ".agents/skills/demo")


def test_interrupted_import_is_preserved_and_never_installed(environment, monkeypatch):
    roots, profiles, store = environment
    key = upload(environment)
    args = dict(device_path=profiles[1])
    preview = pull_skill(roots[1], key, store, **args)
    original = skill_sync.publish
    def interrupted(path, content):
        if path.name == "RECEIPT.json":
            raise OSError("simulated disk failure")
        return original(path, content)
    monkeypatch.setattr(skill_sync, "publish", interrupted)
    with pytest.raises(OSError, match="disk failure"):
        pull_skill(roots[1], key, store, apply=True, expected_plan=preview["plan_digest"], **args)
    assert (Path(preview["package"]) / "SKILL.md").exists()
    with pytest.raises(ArchMarshalError, match="Partial import"):
        pull_skill(roots[1], key, store, apply=True, expected_plan=preview["plan_digest"], **args)
    with pytest.raises(ArchMarshalError):
        install_skill(roots[1], key, ".agents/skills/demo")


@pytest.mark.parametrize("value", [[], {}])
def test_corrupt_ownership_fails_closed(environment, value):
    roots, _, _ = environment
    marker = roots[0] / ".agent/ownership.json"
    data = decode(marker.read_bytes())
    data["skill_index"] = value
    marker.write_bytes(canonical(data))
    assert valid_ownership_marker(marker) is False
    with pytest.raises(ArchMarshalError, match="ownership"):
        require_owned_workspace(roots[0], operation="audit")


def test_layout_runtime_rejects_container_partition(tmp_path):
    with pytest.raises(ArchMarshalError):
        load_workspace_layout(tmp_path, naming={"project_files": {"date_partition": []}})


@pytest.mark.parametrize("target", ["changes", "skills"])
def test_malformed_index_enum_has_domain_error(environment, target):
    from archmarshal.portable import sha
    from archmarshal.skill_index import _object_bytes, _validate_generation
    roots, _, _ = environment
    generation = load_skill_index(roots[0])["generation"]
    generation[target][0]["kind" if target == "changes" else "state"] = []
    with pytest.raises(ArchMarshalError):
        _validate_generation(generation, sha(_object_bytes(generation)))


@pytest.mark.parametrize("target", ["SKILL.md", "agents/openai.yaml"])
def test_skill_yaml_depth_is_bounded(tmp_path, target):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n")
    path = skill / target
    path.parent.mkdir(exist_ok=True)
    content = "nested: " + "[" * 1100 + "0" + "]" * 1100
    if target == "SKILL.md":
        content = "---\nname: demo\ndescription: Demo\n" + content + "\n---\n"
    path.write_text(content)
    assert validate_skill_package(skill)["valid"] is False


def test_yaml_byte_and_alias_limits_apply_to_in_memory_input(monkeypatch):
    from archmarshal import io
    monkeypatch.setattr(io, "MAX_YAML_BYTES", 32)
    assert "byte" in io.parse_yaml_safe(b"x: " + b"a" * 32).error
    monkeypatch.setattr(io, "MAX_YAML_ALIASES", 1)
    assert "alias" in io.parse_yaml_safe(b"x: &x []\ny: [*x, *x]").error


def test_skill_metadata_keeps_existing_scalar_contract(tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo\nmetadata:\n  date: 2026-11-10\n---\n")
    result = validate_skill_package(skill)
    assert not result["valid"]
    assert any(e["code"] == "skill_frontmatter_metadata_invalid" for e in result["errors"])


def test_malformed_user_store_operation_is_a_domain_error():
    from archmarshal.user_store import _validate_operation
    with pytest.raises(ArchMarshalError):
        _validate_operation({"kind": []})
