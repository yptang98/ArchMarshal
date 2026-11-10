from __future__ import annotations

import hashlib
import json

import pytest
import yaml
from test_portability import environment as environment

from archmarshal.candidate_evidence import compare_candidates, explain_candidate
from archmarshal.cli import main
from archmarshal.errors import ArchMarshalError
from archmarshal.learning import learn_from_projects
from archmarshal.session import record_closeout


def close(root, summary):
    args = dict(level="quick", summary=summary, tags=["testing"])
    preview = record_closeout(root, **args)
    result = record_closeout(root, **args, apply=True, expected_plan=preview["plan_digest"])
    assert result["mode"] == "append_only_applied"


def pack(root):
    before = set((root / ".agent/inbox/learning").rglob("COMMITTED.json"))
    preview = learn_from_projects([root])
    learn_from_projects([root], apply=True, reviewed_plan=preview["learning_plan"],
                        expected_plan=preview["plan_digest"])
    after = set((root / ".agent/inbox/learning").rglob("COMMITTED.json"))
    return (after - before).pop().parent


def test_compare_real_repeated_and_extended_candidate_evidence(environment, capsys):
    roots, _, _ = environment
    root = roots[0]
    close(root, "First completed task")
    close(root, "Second completed task")
    first, repeated = pack(root), pack(root)
    old_bytes = {p: p.read_bytes() for directory in (first, repeated) for p in directory.iterdir()}
    duplicate = compare_candidates(root, [str(first), str(repeated)])
    assert len(duplicate["groups"]) == 1
    assert duplicate["groups"][0]["relation"] == "duplicate_evidence"
    assert len(duplicate["groups"][0]["combined_evidence_refs"]) == 2
    close(root, "Third completed task")
    extended = pack(root)
    result = compare_candidates(root, [str(first), str(repeated), str(extended)])
    assert result["groups"][0]["relation"] == "extended_evidence"
    assert len(result["groups"][0]["combined_evidence_refs"]) == 3
    assert result["automatic_acceptance"] is False
    assert all(path.read_bytes() == content for path, content in old_bytes.items())
    assert main(["candidate-compare", str(root), "--pack", str(first), "--pack", str(extended)]) == 0
    assert json.loads(capsys.readouterr().out)["preserved_all_evidence"] is True


def test_candidate_variants_are_explicit_conflicts(environment):
    roots, _, _ = environment
    root = roots[0]
    close(root, "First")
    close(root, "Second")
    first, second = pack(root), pack(root)
    content = yaml.safe_load((second / "candidates.yaml").read_bytes())
    content["preference_candidates"][0]["value"] = "different-preference"
    raw = yaml.safe_dump(content).encode()
    (second / "candidates.yaml").write_bytes(raw)
    marker = json.loads((second / "COMMITTED.json").read_bytes())
    marker.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (second / "COMMITTED.json").write_text(json.dumps(marker))
    result = compare_candidates(root, [str(first), str(second)])
    assert len(result["groups"]) == 2 and len(result["conflicts"]) == 1
    assert all(group["relation"] == "single" for group in result["groups"])
    (second / "candidates.yaml").write_bytes(raw + b"tampered")
    with pytest.raises(ArchMarshalError, match="commit marker"):
        compare_candidates(root, [str(first), str(second)])


def test_candidate_explanation_separates_counts_from_pinned_references():
    result = explain_candidate({"observed_sessions": 5, "evidence_refs": []})
    assert result["claimed_sessions"] == 5
    assert result["pinned_evidence_count"] == 0
    assert "does not prove" in result["trust"]
    assert len(result["promotion_requirements"]) == 4
    with pytest.raises(ArchMarshalError, match="invalid pinned"):
        explain_candidate({"evidence_refs": [{"commit_sha256": "broken"}]})


def test_compare_requires_distinct_owned_bounded_packs(environment):
    roots, _, _ = environment
    with pytest.raises(ArchMarshalError, match="between 2 and 20"):
        compare_candidates(roots[0], ["one"])
    with pytest.raises(ArchMarshalError, match="project learning packs"):
        compare_candidates(roots[0], ["source.py", "source.py"])
