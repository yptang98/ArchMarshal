from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest
import yaml

import archmarshal.user_store as user_store
from archmarshal.errors import ArchMarshalError
from archmarshal.safety import fingerprint_directory

PROVENANCE = [
    {
        "kind": "committed_session",
        "ref": "project-v12:session-001",
        "digest": "a" * 64,
    }
]


def _initialize(store: Path) -> None:
    plan = user_store.plan_user_store_initialization(
        store,
        created_at="2026-07-15T00:00:00+00:00",
    )
    user_store.apply_user_store_initialization(
        store,
        plan,
        expected_plan=plan["plan_digest"],
    )


def _draft(root: Path, name: str) -> Path:
    draft = root / name
    draft.mkdir(parents=True)
    manifest = {
        "id": f"skill.common-project.{name}",
        "name": name,
        "kind": "common_project_skill",
        "version": "1.0.0",
        "status": "active",
        "priority": "normal",
        "scope": "common_project",
        "summary": f"Reusable {name} workflow.",
        "tags": [name, "reusable"],
        "triggers": [f"run {name}"],
        "negative_triggers": [f"skip {name}"],
        "paths": {"skill_root": ".", "scripts": "scripts"},
    }
    (draft / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    (draft / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Use this reviewed {name} workflow and its local scripts.\n"
        "---\n\n"
        f"# {name}\n\nRun scripts/run.sh for the reviewed workflow.\n",
        encoding="utf-8",
    )
    scripts = draft / "scripts"
    scripts.mkdir()
    runner = scripts / "run.sh"
    runner.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    empty = draft / "assets" / "empty"
    empty.mkdir(parents=True)
    if os.name != "nt":
        runner.chmod(0o755)
        scripts.chmod(0o751)
        (draft / "assets").chmod(0o750)
        empty.chmod(0o710)
    return draft


def _accept(store: Path, candidate: str, candidate_digest: str) -> None:
    plan = user_store._plan_user_store_decision(
        store,
        candidate_id=candidate,
        candidate_digest=candidate_digest,
        decision="accepted",
        provenance=PROVENANCE,
        reason="Reviewed before promotion.",
        created_at="2026-07-15T00:30:00+00:00",
    )
    user_store._apply_user_store_decision(
        store,
        plan,
        expected_head=plan["expected_head"],
        expected_plan=plan["plan_digest"],
    )


def _promotion_plan(
    store: Path,
    draft: Path,
    candidate: str,
    candidate_digest: str,
) -> dict[str, object]:
    return user_store._plan_user_store_promotion(
        store,
        candidate_id=candidate,
        candidate_digest=candidate_digest,
        provenance=PROVENANCE,
        skill_draft=draft,
        created_at="2026-07-15T01:00:00+00:00",
    )


def _apply(store: Path, plan: dict[str, object]) -> dict[str, object]:
    return user_store._apply_user_store_promotion(
        store,
        plan,
        expected_head=plan["expected_head"],
        expected_plan=plan["plan_digest"],
    )


def _tree_state(root: Path) -> list[tuple[str, str, int, str | None]]:
    state: list[tuple[str, str, int, str | None]] = []
    for current, directories, filenames in os.walk(root):
        current_path = Path(current)
        for name in sorted(directories):
            path = current_path / name
            state.append(
                (
                    path.relative_to(root).as_posix(),
                    "directory",
                    stat.S_IMODE(path.lstat().st_mode) & 0o777,
                    None,
                )
            )
        for name in sorted(filenames):
            path = current_path / name
            state.append(
                (
                    path.relative_to(root).as_posix(),
                    "file",
                    stat.S_IMODE(path.lstat().st_mode) & 0o777,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return sorted(state)


def _promote(tmp_path: Path, name: str) -> tuple[Path, Path, dict[str, object]]:
    store = tmp_path / f"store-{name}"
    _initialize(store)
    draft = _draft(tmp_path, name)
    candidate = f"candidate.{name}"
    candidate_digest = hashlib.sha256(candidate.encode()).hexdigest()
    _accept(store, candidate, candidate_digest)
    plan = _promotion_plan(store, draft, candidate, candidate_digest)
    _apply(store, plan)
    return store, draft, plan


def test_v2_promotion_preserves_executable_mode_and_empty_directories(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    _initialize(store)
    draft = _draft(tmp_path, "portable-runner")
    candidate = "candidate.portable-runner"
    digest = "b" * 64
    _accept(store, candidate, digest)
    before = _tree_state(draft)

    plan = _promotion_plan(store, draft, candidate, digest)
    snapshot = plan["package"]["fingerprint"]
    assert snapshot["format"] == user_store.PACKAGE_SNAPSHOT_FORMAT_V2
    assert plan["package"]["commit"]["format"] == user_store.PACKAGE_COMMIT_FORMAT_V2
    assert "assets/empty" in {item["path"] for item in snapshot["directories"]}
    runner_record = next(item for item in snapshot["files"] if item["path"] == "scripts/run.sh")
    assert runner_record["executable"] is (os.name != "nt")

    _apply(store, plan)
    package = store / str(plan["package"]["package_path"])
    assert (package / "assets" / "empty").is_dir()
    assert (package / "COMMITTED.json").is_file()
    if os.name != "nt":
        assert stat.S_IMODE((package / "scripts" / "run.sh").stat().st_mode) == 0o755
        assert stat.S_IMODE((package / "scripts").stat().st_mode) == 0o751
        assert stat.S_IMODE((package / "assets" / "empty").stat().st_mode) == 0o710
    assert _tree_state(draft) == before
    assert user_store._verify_package(
        store,
        snapshot["sha256"],
        plan["package"]["manifest_digest"],
    ) == plan["package"]["manifest"]


@pytest.mark.parametrize("mutation", ["mode", "empty_directory"])
def test_mode_or_empty_directory_change_makes_reviewed_preview_stale(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = tmp_path / "store"
    _initialize(store)
    draft = _draft(tmp_path, "stale-package")
    candidate = "candidate.stale-package"
    digest = "c" * 64
    _accept(store, candidate, digest)
    plan = _promotion_plan(store, draft, candidate, digest)
    head_before = user_store.user_store_status(store)["head"]

    if mutation == "mode":
        runner = draft / "scripts" / "run.sh"
        runner.chmod(0o644 if os.name != "nt" else stat.S_IREAD)
    else:
        (draft / "templates" / "new-empty").mkdir(parents=True)

    with pytest.raises(ArchMarshalError) as raised:
        _apply(store, plan)

    assert raised.value.code == "user_store_source_changed"
    assert user_store.user_store_status(store)["head"] == head_before
    assert not (store / str(plan["package"]["package_path"]) / "COMMITTED.json").exists()


def test_v2_verifier_detects_missing_directory_and_posix_mode_loss(tmp_path: Path) -> None:
    store, _draft_path, plan = _promote(tmp_path, "verify-v2")
    package = store / str(plan["package"]["package_path"])
    snapshot = plan["package"]["fingerprint"]
    manifest_digest = plan["package"]["manifest_digest"]

    (package / "assets" / "empty").rmdir()
    with pytest.raises(ArchMarshalError) as missing:
        user_store._verify_package(store, snapshot["sha256"], manifest_digest)
    assert missing.value.code == "user_store_package_integrity_failed"

    (package / "assets" / "empty").mkdir()
    if os.name != "nt":
        (package / "assets" / "empty").chmod(0o710)
        (package / "scripts" / "run.sh").chmod(0o644)
        with pytest.raises(ArchMarshalError) as mode_loss:
            user_store._verify_package(store, snapshot["sha256"], manifest_digest)
        assert mode_loss.value.code == "user_store_package_integrity_failed"


def test_committed_v1_package_fixture_remains_readable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    draft = _draft(tmp_path, "legacy-package")
    manifest = user_store._load_draft_manifest(draft)
    manifest_digest = hashlib.sha256(user_store._canonical_bytes(manifest)).hexdigest()
    fingerprint = fingerprint_directory(draft, purpose="v1 fixture")
    package_digest = user_store._records_digest(fingerprint["files"])
    destination = store / user_store._package_relative(package_digest)
    shutil.copytree(draft, destination)
    commit = {
        "format": user_store.PACKAGE_COMMIT_FORMAT_V1,
        "package_sha256": package_digest,
        "file_count": fingerprint["file_count"],
        "content_bytes": fingerprint["content_bytes"],
        "files": fingerprint["files"],
        "manifest_digest": manifest_digest,
        "source_mutation": False,
    }
    (destination / user_store.PACKAGE_COMMIT_NAME).write_text(
        json.dumps(commit, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert user_store._verify_package(store, package_digest, manifest_digest) == manifest

    real_verify_v1 = user_store._verify_package_v1
    replacement = tmp_path / "replacement-commit.json"
    replacement.write_text("{}\n", encoding="utf-8")

    def verify_then_replace(*args, **kwargs):
        verified_manifest = real_verify_v1(*args, **kwargs)
        os.replace(replacement, destination / user_store.PACKAGE_COMMIT_NAME)
        return verified_manifest

    monkeypatch.setattr(user_store, "_verify_package_v1", verify_then_replace)

    with pytest.raises(ArchMarshalError) as changed:
        user_store._verify_package(store, package_digest, manifest_digest)

    assert changed.value.code == "user_store_package_integrity_failed"


@pytest.mark.parametrize(
    "path",
    [
        "CON",
        "aux.txt",
        "COM1 .txt",
        "name.",
        "name ",
        "a:b",
        "a<b",
        "a>b",
        'a"b',
        "a|b",
        "a?b",
        "a*b",
        "a\x1fb",
        "folder\\file",
        "Cafe\u0301",
    ],
)
def test_v2_rejects_windows_nonportable_path_components(path: str) -> None:
    with pytest.raises(ArchMarshalError):
        user_store._safe_relative(path)


def test_v2_rejects_reserved_commit_marker_as_directory(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _initialize(store)
    draft = _draft(tmp_path, "reserved-directory")
    (draft / user_store.PACKAGE_COMMIT_NAME).mkdir()
    candidate = "candidate.reserved-directory"
    digest = "e" * 64
    _accept(store, candidate, digest)

    with pytest.raises(ArchMarshalError) as raised:
        _promotion_plan(store, draft, candidate, digest)

    assert raised.value.code == "user_store_skill_draft_invalid"


def test_v2_rejects_unicode_casefold_portable_collisions(tmp_path: Path) -> None:
    draft = _draft(tmp_path, "collision-contract")
    snapshot = user_store._snapshot_package_v2(draft, purpose="collision fixture")
    forged = copy.deepcopy(snapshot)
    duplicate = copy.deepcopy(forged["files"][0])
    duplicate["path"] = duplicate["path"].swapcase()
    forged["files"].append(duplicate)
    forged["file_count"] += 1

    with pytest.raises(ArchMarshalError) as raised:
        user_store._validate_package_snapshot_v2(
            forged,
            code="user_store_package_integrity_failed",
        )

    assert "collide" in str(raised.value).lower()


@pytest.mark.parametrize(
    ("record_kind", "mode"),
    [("file", 0o200), ("file", 0o010), ("directory", 0o400)],
)
def test_v2_rejects_modes_that_would_be_unusable_after_copy(
    tmp_path: Path,
    record_kind: str,
    mode: int,
) -> None:
    draft = _draft(tmp_path, "mode-contract")
    snapshot = user_store._snapshot_package_v2(draft, purpose="mode fixture")
    forged = copy.deepcopy(snapshot)
    records = forged["files"] if record_kind == "file" else forged["directories"]
    records[0]["mode"] = mode
    if record_kind == "file":
        records[0]["executable"] = bool(mode & 0o111)

    with pytest.raises(ArchMarshalError):
        user_store._validate_package_snapshot_v2(
            forged,
            code="user_store_package_integrity_failed",
        )


def test_mismatching_partial_package_fails_closed_without_source_or_head_change(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    _initialize(store)
    draft = _draft(tmp_path, "partial-collision")
    candidate = "candidate.partial-collision"
    digest = "d" * 64
    _accept(store, candidate, digest)
    plan = _promotion_plan(store, draft, candidate, digest)
    draft_before = _tree_state(draft)
    head_before = user_store.user_store_status(store)["head"]
    package = store / str(plan["package"]["package_path"])
    package.mkdir(parents=True)
    collision = package / "SKILL.md"
    collision.write_text("different partial bytes\n", encoding="utf-8")

    with pytest.raises(ArchMarshalError) as raised:
        _apply(store, plan)

    assert raised.value.code == "user_store_package_collision"
    assert collision.read_text(encoding="utf-8") == "different partial bytes\n"
    assert not (package / user_store.PACKAGE_COMMIT_NAME).exists()
    assert user_store.user_store_status(store)["head"] == head_before
    assert _tree_state(draft) == draft_before


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits replacing an open path entry")
def test_v2_verifier_rejects_package_path_replacement_after_descriptor_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, _draft_path, plan = _promote(tmp_path, "descriptor-package")
    package = store / str(plan["package"]["package_path"])
    target = package / "scripts" / "run.sh"
    replacement = tmp_path / "replacement.sh"
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(target.stat().st_mode)
    real_open = user_store.os.open
    replaced = False

    def open_then_replace(path, flags, *args, **kwargs):
        nonlocal replaced
        descriptor = real_open(path, flags, *args, **kwargs)
        if not replaced and Path(path).absolute() == target.absolute():
            os.replace(replacement, target)
            replaced = True
        return descriptor

    monkeypatch.setattr(user_store.os, "open", open_then_replace)

    with pytest.raises(ArchMarshalError) as raised:
        user_store._verify_package(
            store,
            plan["package"]["fingerprint"]["sha256"],
            plan["package"]["manifest_digest"],
        )

    assert replaced is True
    assert raised.value.code == "user_store_package_integrity_failed"
