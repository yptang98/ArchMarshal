from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from test_portability import environment as environment
from test_portability import save

import archmarshal.context_store as context_store
import archmarshal.handoff as handoff
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.handoff import archive_context, read_context, resume_context, save_context


def archive(root):
    preview = archive_context(root)
    return archive_context(root, apply=True, expected_plan=preview["plan_digest"])


def test_archive_releases_capacity_preserving_bytes_and_old_ids(environment, monkeypatch):
    roots, profiles, _ = environment
    root = roots[0]
    monkeypatch.setattr(handoff, "MAX_RECORDS", 2)
    first = save(root, profiles[0], "window", summary="Original decision", decisions=["Keep API stable"])
    second = save(root, profiles[0], "window", summary="Current state", next_steps=["Verify recovery"])
    originals = {p: p.read_bytes() for p in (root / handoff.INDEX).iterdir()}
    originals.update({Path(item["path"]): Path(item["path"]).read_bytes() for item in (first, second)})
    with pytest.raises(ArchMarshalError, match="budget|limit|capacity"):
        save(root, profiles[0], "window")
    result = archive(root)
    assert result["mode"] == "archived"
    assert result["active_records_before"] == 2 and result["active_records_after"] == 1
    assert all(p.read_bytes() == content for p, content in originals.items())
    third = save(root, profiles[0], "window", summary="Resumed after archive")
    assert (root / ".agent/context-index-g000001" / (third["record_id"] + ".json")).is_file()
    assert read_context(root, first["record_id"])["record"]["decisions"] == ["Keep API stable"]
    contexts = resume_context(root)["contexts"]
    assert len(contexts) == 1 and contexts[0]["record_id"] == third["record_id"]
    assert contexts[0]["record"]["parent"] == second["record_id"]


def test_multiple_generations_keep_exact_reads_and_merge_evidence(environment, monkeypatch):
    roots, profiles, _ = environment
    root = roots[0]
    monkeypatch.setattr(handoff, "MAX_RECORDS", 3)
    original = save(root, profiles[0], "a", summary="Prior analysis", constraints=["Preserve history"])
    save(root, profiles[0], "a", summary="New analysis")
    archive(root)
    save(root, profiles[0], "a", summary="Further analysis")
    archive(root)
    other = save(root, profiles[0], "b", summary="Separate window")
    merged = save(root, profiles[0], "synthesis", integrates=[original["record_id"], other["record_id"]])
    result = read_context(root, merged["record_id"])["record"]
    assert result["integrated_contexts"][original["record_id"]]["constraints"] == ["Preserve history"]
    assert len(context_store.archive_catalog(root)) == 2


def test_resume_does_not_read_cold_record_bodies(environment, monkeypatch):
    roots, profiles, _ = environment
    root = roots[0]
    old = save(root, profiles[0], "a", summary="Cold record")
    current = save(root, profiles[0], "a", summary="Live head")
    archive(root)
    read_paths = []
    real_read = context_store.read
    def tracked(path, *args):
        read_paths.append(path)
        return real_read(path, *args)
    monkeypatch.setattr(context_store, "read", tracked)
    assert resume_context(root)["contexts"][0]["record_id"] == current["record_id"]
    assert Path(old["path"]) not in read_paths
    assert Path(current["path"]) in read_paths
    read_context(root, old["record_id"])
    assert Path(old["path"]) in read_paths


def test_tampered_cold_record_fails_exact_read_without_hiding_live_head(environment):
    roots, profiles, _ = environment
    old = save(roots[0], profiles[0], "a", summary="Archived body")
    current = save(roots[0], profiles[0], "a", summary="Live body")
    archive(roots[0])
    Path(old["path"]).write_bytes(b"changed historical evidence")
    assert resume_context(roots[0])["contexts"][0]["record_id"] == current["record_id"]
    with pytest.raises(ArchMarshalError):
        read_context(roots[0], old["record_id"])


def test_interrupted_archive_keeps_original_active_index(environment, monkeypatch):
    roots, profiles, _ = environment
    save(roots[0], profiles[0], "a", summary="First")
    latest = save(roots[0], profiles[0], "a", summary="Second")
    preview = archive_context(roots[0])
    def interrupted(*args, **kwargs):
        raise OSError("archive interruption")
    with monkeypatch.context() as patch:
        patch.setattr(handoff, "create_bytes_exclusive", interrupted)
        with pytest.raises(OSError, match="archive interruption"):
            archive_context(roots[0], apply=True, expected_plan=preview["plan_digest"])
    assert resume_context(roots[0])["contexts"][0]["record_id"] == latest["record_id"]
    assert not (roots[0] / ".agent/context-archives").exists()
    assert archive_context(roots[0], apply=True, expected_plan=preview["plan_digest"])["mode"] == "archived"


def test_archive_blocks_concurrent_new_handoff(environment, monkeypatch):
    roots, profiles, _ = environment
    root = roots[0]
    save(root, profiles[0], "a", summary="First")
    save(root, profiles[0], "a", summary="Second")
    preview = archive_context(root)
    original_lock = handoff.workspace_mutation_lock
    @contextmanager
    def changed_before_lock(*args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(handoff, "workspace_mutation_lock", original_lock)
            save(root, profiles[0], "a", summary="Concurrent third")
        with original_lock(*args, **kwargs) as held:
            yield held
    monkeypatch.setattr(handoff, "workspace_mutation_lock", changed_before_lock)
    with pytest.raises(ArchMarshalError, match="changed before archive"):
        archive_context(root, apply=True, expected_plan=preview["plan_digest"])
    assert not (root / ".agent/context-archives").exists()


def test_save_plan_is_invalidated_by_archive(environment):
    roots, profiles, _ = environment
    root = roots[0]
    save(root, profiles[0], "a", summary="First")
    save(root, profiles[0], "a", summary="Second")
    args = dict(stream="a", summary="Third", device_path=profiles[0])
    preview = save_context(root, **args)
    archive(root)
    with pytest.raises(ArchMarshalError, match="Preview changed"):
        save_context(root, **args, apply=True, expected_plan=preview["plan_digest"])
    assert not (root / ".agent/context-index-g000001").exists()


def test_archive_never_discards_heads_to_claim_reclaimed_capacity(environment, monkeypatch):
    roots, profiles, _ = environment
    monkeypatch.setattr(handoff, "MAX_RECORDS", 2)
    save(roots[0], profiles[0], "a")
    save(roots[0], profiles[0], "b", status="completed")
    result = archive_context(roots[0])
    assert result["mode"] == "unchanged"
    assert result["active_records"] == 2
    assert resume_context(roots[0])["total_stream_heads"] == 2


def test_archive_rejects_catalog_gap_and_preexisting_future_index(environment):
    roots, profiles, _ = environment
    root = roots[0]
    save(root, profiles[0], "a", summary="First")
    save(root, profiles[0], "a", summary="Second")
    future = root / ".agent/context-index-g000001"
    future.mkdir()
    (future / "user.txt").write_text("preserve")
    preview = archive_context(root)
    with pytest.raises(ArchMarshalError, match="already exists"):
        archive_context(root, apply=True, expected_plan=preview["plan_digest"])
    assert (future / "user.txt").read_text() == "preserve"
    catalog = root / ".agent/context-archives"
    catalog.mkdir()
    (catalog / "000002.json").write_text("{}")
    with pytest.raises(ArchMarshalError, match="gap"):
        resume_context(root)


def test_context_archive_cli_preview_is_read_only(environment, capsys):
    roots, profiles, _ = environment
    save(roots[0], profiles[0], "a", summary="First")
    save(roots[0], profiles[0], "a", summary="Second")
    assert main(["context-archive", str(roots[0])]) == 0
    assert '"active_records_after": 1' in capsys.readouterr().out
    assert not (roots[0] / ".agent/context-archives").exists()
