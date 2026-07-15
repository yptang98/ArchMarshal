from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

import archmarshal.promotion as promotion
import archmarshal.user_store as user_store
from archmarshal.errors import ArchMarshalError
from archmarshal.skill_validation import MAX_SKILL_MD_BYTES, validate_skill_package

PROVENANCE = [{"kind": "learning_pack", "ref": "learning-pack/test", "digest": "c" * 64}]


def _skill_dir(tmp_path: Path, name: str = "branch-skill") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory


def _skill_text(
    name: str = "branch-skill",
    description: str = "Use when security branch validation is required.",
    *,
    extra: str = "",
) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{extra}"
        "---\n\n"
        "# Branch Skill\n"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("---\nname: branch-skill\ndescription: open", "skill_frontmatter_unclosed"),
        ("---\nname: [\ndescription: bad\n---\n", "skill_frontmatter_invalid"),
        ("---\n- branch-skill\n---\n", "skill_frontmatter_invalid"),
        (_skill_text(extra="unexpected: true\n"), "skill_frontmatter_extra_fields"),
        (_skill_text(name="Bad_Name"), "skill_name_invalid"),
        (_skill_text(description=""), "skill_description_missing"),
        (_skill_text(description="x" * 2049), "skill_description_too_long"),
    ],
)
def test_skill_validation_rejects_frontmatter_boundaries(
    tmp_path: Path,
    payload: str,
    expected: str,
) -> None:
    directory = _skill_dir(tmp_path)
    (directory / "SKILL.md").write_text(payload, encoding="utf-8")
    result = validate_skill_package(directory)
    assert expected in {item["code"] for item in result["errors"]}


def test_skill_validation_rejects_missing_large_binary_and_long_entrypoints(
    tmp_path: Path,
) -> None:
    not_directory = tmp_path / "not-directory"
    not_directory.write_text("file\n", encoding="utf-8")
    assert validate_skill_package(not_directory)["errors"][0]["code"] == "skill_directory_invalid"

    missing = _skill_dir(tmp_path, "missing")
    assert validate_skill_package(missing)["errors"][0]["code"] == "skill_entrypoint_invalid"

    large = _skill_dir(tmp_path, "large")
    (large / "SKILL.md").write_bytes(b"x" * (MAX_SKILL_MD_BYTES + 1))
    assert "skill_entrypoint_too_large" in {
        item["code"] for item in validate_skill_package(large)["errors"]
    }

    binary = _skill_dir(tmp_path, "binary")
    (binary / "SKILL.md").write_bytes(b"\xff\xfe")
    with pytest.raises(ArchMarshalError) as raised:
        validate_skill_package(binary)
    assert raised.value.code == "skill_entrypoint_unreadable"

    long = _skill_dir(tmp_path, "long")
    (long / "SKILL.md").write_text(
        _skill_text(name="long") + ("line\n" * 501), encoding="utf-8"
    )
    assert "skill_entrypoint_too_long" in {
        item["code"] for item in validate_skill_package(long)["errors"]
    }


def test_skill_validation_rejects_name_resource_and_agents_boundaries(tmp_path: Path) -> None:
    long_name = _skill_dir(tmp_path, "long-name")
    (long_name / "SKILL.md").write_text(
        _skill_text(name="a" * 65), encoding="utf-8"
    )
    result = validate_skill_package(long_name, enforce_folder_name=False)
    assert "skill_name_too_long" in {item["code"] for item in result["errors"]}

    mismatch = _skill_dir(tmp_path, "different-folder")
    (mismatch / "SKILL.md").write_text(_skill_text(), encoding="utf-8")
    result = validate_skill_package(mismatch)
    assert "skill_folder_name_mismatch" in {item["code"] for item in result["errors"]}

    resource = _skill_dir(tmp_path, "resource")
    (resource / "SKILL.md").write_text(_skill_text(name="resource"), encoding="utf-8")
    (resource / "scripts").write_text("not a directory\n", encoding="utf-8")
    result = validate_skill_package(resource)
    assert "skill_scripts_invalid" in {item["code"] for item in result["errors"]}
    assert "skill_scripts_not_referenced" in {item["code"] for item in result["warnings"]}

    agents_file = _skill_dir(tmp_path, "agents-file")
    (agents_file / "SKILL.md").write_text(
        _skill_text(name="agents-file"), encoding="utf-8"
    )
    (agents_file / "agents").write_text("not a directory\n", encoding="utf-8")
    assert validate_skill_package(agents_file)["agents_metadata"]["valid"] is False

    invalid_agents = _skill_dir(tmp_path, "invalid-agents")
    (invalid_agents / "SKILL.md").write_text(
        _skill_text(name="invalid-agents"), encoding="utf-8"
    )
    (invalid_agents / "agents").mkdir()
    (invalid_agents / "agents" / "openai.yaml").write_text("interface: nope\n", encoding="utf-8")
    assert validate_skill_package(invalid_agents)["agents_metadata"]["valid"] is False

    valid_agents = _skill_dir(tmp_path, "valid-agents")
    (valid_agents / "SKILL.md").write_text(
        _skill_text(name="valid-agents"), encoding="utf-8"
    )
    (valid_agents / "agents").mkdir()
    (valid_agents / "agents" / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")
    assert validate_skill_package(valid_agents)["agents_metadata"]["valid"] is True


def test_reviewed_plan_and_candidate_helpers_fail_closed(tmp_path: Path) -> None:
    plan = {"kind": "promotion", "plan_digest": "plan", "expected_head": None}
    assert promotion._reviewed_plan(
        plan,
        expected_plan="plan",
        expected_head_token="none",
        kind="promotion",
    ) == (plan, None)
    for kwargs in [
        {"plan": None, "expected_plan": "plan", "expected_head_token": "none"},
        {"plan": plan, "expected_plan": "wrong", "expected_head_token": "none"},
        {"plan": plan, "expected_plan": "plan", "expected_head_token": "f" * 64},
    ]:
        with pytest.raises(ArchMarshalError):
            promotion._reviewed_plan(kind="promotion", **kwargs)

    decision = {
        "candidate_id": "candidate.one",
        "candidate_digest": "a" * 64,
        "decision": "accepted",
        "provenance": PROVENANCE,
        "reason": "reviewed",
        "digest": "decision-digest",
    }
    candidate_plan = {
        "generation": {
            "candidate_decisions": [decision],
            "operation": {"decision_digest": "decision-digest"},
        }
    }
    promotion._verify_plan_candidate(
        candidate_plan,
        candidate_id="candidate.one",
        candidate_digest="a" * 64,
        provenance=PROVENANCE,
        decision="accepted",
        reason="reviewed",
    )
    for changed in [
        {},
        {
            "generation": {
                "candidate_decisions": [decision],
                "operation": {"decision_digest": "wrong"},
            }
        },
    ]:
        with pytest.raises(ArchMarshalError):
            promotion._verify_plan_candidate(
                changed,
                candidate_id="candidate.one",
                candidate_digest="a" * 64,
                provenance=[],
                decision="accepted",
                reason="reviewed",
            )

    draft = _skill_dir(tmp_path, "draft")
    common = {"candidate_type": "common_skill"}
    with pytest.raises(ArchMarshalError):
        promotion._verify_promotion_payload(
            {}, candidate=common, candidate_digest="a" * 64, draft=draft
        )
    with pytest.raises(ArchMarshalError):
        promotion._verify_promotion_payload(
            {"generation": {"operation": {"kind": "promotion_preference"}}},
            candidate=common,
            candidate_digest="a" * 64,
            draft=draft,
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "key", "value", "package", "kind"],
)
def test_preference_promotion_plan_is_exactly_bound(mutation: str) -> None:
    candidate = {"candidate_type": "preference", "key": "preferred.shell", "value": "bash"}
    record = {"digest": "record", "key": "preferred.shell", "value": "bash"}
    plan = {
        "generation": {
            "operation": {"kind": "promotion_preference", "record_digest": "record"},
            "preferences": [record],
        },
        "package": None,
    }
    changed = copy.deepcopy(plan)
    if mutation == "missing":
        changed["generation"]["preferences"] = []
    elif mutation == "key":
        changed["generation"]["preferences"][0]["key"] = "other"
    elif mutation == "value":
        changed["generation"]["preferences"][0]["value"] = "powershell"
    elif mutation == "package":
        changed["package"] = {}
    else:
        changed["generation"]["operation"]["kind"] = "promotion_skill"
    with pytest.raises(ArchMarshalError):
        promotion._verify_promotion_payload(
            changed,
            candidate=candidate,
            candidate_digest="a" * 64,
            draft=None,
        )


def test_reviewed_plan_loader_rejects_invalid_json_and_non_object(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ArchMarshalError):
        promotion.load_reviewed_plan(invalid)
    non_object = tmp_path / "list.json"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(ArchMarshalError):
        promotion.load_reviewed_plan(non_object)


@pytest.mark.parametrize(
    ("value", "function"),
    [
        (None, lambda value: user_store._normalize_label(value, "field")),
        ("", lambda value: user_store._normalize_label(value, "field")),
        (None, user_store._normalize_reason),
        ("x" * 1001, user_store._normalize_reason),
        (None, user_store._safe_relative),
        ("../escape", user_store._safe_relative),
    ],
)
def test_user_store_scalar_validators_fail_closed(value: object, function) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ArchMarshalError):
        function(value)


def test_user_store_candidate_preference_and_budget_validators() -> None:
    provenance: list[dict[str, str]] = []
    with pytest.raises(ArchMarshalError):
        user_store._candidate_decision(
            "candidate.one", "bad", "accepted", provenance, reason="", decided_at="now"
        )
    with pytest.raises(ArchMarshalError):
        user_store._candidate_decision(
            "candidate.one", "a" * 64, "unknown", provenance, reason="", decided_at="now"
        )
    for preference in [None, {"key": "bad key", "value": "x"}]:
        with pytest.raises(ArchMarshalError):
            user_store._preference_record(preference, provenance)
    with pytest.raises(ArchMarshalError):
        user_store._preference_record(
            {"key": "preferred.large", "value": "x" * 5000}, provenance
        )
    with pytest.raises(ArchMarshalError):
        user_store._validate_preference_budget([{}] * 101)
    with pytest.raises(ArchMarshalError):
        user_store._validate_preference_budget(
            [{"key": f"key.{index}", "value": "x" * 1000} for index in range(100)]
        )
    with pytest.raises(ArchMarshalError):
        user_store._validate_decision_budget([{}] * 10001)


def test_user_store_plan_validators_detect_independent_tampering(tmp_path: Path) -> None:
    store = tmp_path / "store"
    initialization = user_store.plan_user_store_initialization(
        store, created_at="2026-07-15T00:00:00+00:00"
    )
    malformed = copy.deepcopy(initialization)
    malformed["format"] = "wrong"
    with pytest.raises(ArchMarshalError):
        user_store._validate_initialization_plan(store, malformed)
    for field in ["operations", "plan_digest"]:
        changed = copy.deepcopy(initialization)
        changed[field] = [] if field == "operations" else "0" * 64
        with pytest.raises(ArchMarshalError):
            user_store._validate_initialization_plan(store, changed)

    user_store.apply_user_store_initialization(
        store, initialization, expected_plan=initialization["plan_digest"]
    )
    plan = user_store._plan_user_store_decision(
        store,
        candidate_id="candidate.plan",
        candidate_digest="b" * 64,
        decision="deferred",
        provenance=PROVENANCE,
        created_at="2026-07-15T01:00:00+00:00",
    )
    wrong_operation_kind = copy.deepcopy(plan)
    wrong_operation_kind["kind"] = "promotion"
    with pytest.raises(ArchMarshalError):
        user_store._validate_plan(store, wrong_operation_kind, expected_kind="promotion")
    mutations = [
        ("proposed_head", "0" * 64),
        ("generation_object_path", "wrong"),
        ("operations", []),
        ("plan_digest", "0" * 64),
    ]
    for field, value in mutations:
        changed = copy.deepcopy(plan)
        changed[field] = value
        with pytest.raises(ArchMarshalError):
            user_store._validate_plan(store, changed, expected_kind="decision")


def test_record_digest_helpers_reject_invalid_records() -> None:
    assert user_store._records_digest(None) == ""
    assert user_store._records_digest([{"path": "x"}]) == ""
    assert (
        user_store._records_digest(
            [{"path": "../escape", "bytes": 0, "sha256": "a" * 64}]
        )
        == ""
    )
    assert (
        user_store._records_digest(
            [
                {"path": "a", "bytes": 0, "sha256": "a" * 64},
                {"path": "a", "bytes": 0, "sha256": "a" * 64},
            ]
        )
        == ""
    )
    assert user_store._strings_below({"key": ["value", 1]}) == ["key", "value"]


def test_user_store_draft_and_file_preconditions_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ArchMarshalError):
        user_store._plan_package(tmp_path / "missing")

    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    with pytest.raises(ArchMarshalError):
        user_store._plan_package(incomplete)

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "manifest.yaml").write_text("{}\n", encoding="utf-8")
    (empty / "SKILL.md").write_text("", encoding="utf-8")
    with pytest.raises(ArchMarshalError):
        user_store._plan_package(empty)

    invalid_manifest = tmp_path / "invalid-manifest"
    invalid_manifest.mkdir()
    (invalid_manifest / "SKILL.md").write_text(
        _skill_text(name="invalid-manifest"), encoding="utf-8"
    )
    (invalid_manifest / "manifest.yaml").write_text(
        yaml.safe_dump({"name": "invalid-manifest"}), encoding="utf-8"
    )
    with pytest.raises(ArchMarshalError):
        user_store._plan_package(invalid_manifest)

    missing_file = tmp_path / "missing-file"
    with pytest.raises(ArchMarshalError):
        user_store._read_exact_source(
            missing_file, {"path": "missing", "bytes": 0, "sha256": "a" * 64}
        )
    existing = tmp_path / "existing.txt"
    existing.write_text("changed\n", encoding="utf-8")
    record = {"path": "existing.txt", "bytes": 0, "sha256": "a" * 64}
    with pytest.raises(ArchMarshalError):
        user_store._read_exact_source(existing, record)
    with pytest.raises(ArchMarshalError):
        user_store._verify_file(existing, record, collision_code="collision")


def test_json_plan_file_envelope_still_loads(tmp_path: Path) -> None:
    plan = {"kind": "decision", "plan_digest": "digest"}
    for encoding in ("utf-8", "utf-8-sig", "utf-16"):
        path = tmp_path / f"plan-{encoding}.json"
        path.write_text(json.dumps({"user_store_plan": plan}), encoding=encoding)
        assert promotion.load_reviewed_plan(path) == plan
