"""Explain and compare pinned candidate evidence without merging review decisions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .learning import verify_learning_pack
from .ownership import require_owned_workspace
from .portable import digest, fail, is_digest, relative
from .safety import ensure_managed_path, ensure_unlinked_path


def explain_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = candidate.get("evidence_refs") or []
    if not isinstance(evidence, list) or len(evidence) > 5_000:
        fail("Candidate evidence exceeds its structure budget.")
    pinned = {}
    for item in evidence:
        if (not isinstance(item, dict) or not isinstance(item.get("workspace_id"), str)
                or not isinstance(item.get("session"), str)
                or not is_digest(item.get("commit_sha256"))):
            fail("Candidate contains an invalid pinned evidence reference.")
        relative(item["session"])
        pinned[digest(item)] = item
    return {"reason": candidate.get("reason") or "Repeated observations produced this review candidate.",
            "claimed_sessions": candidate.get("observed_sessions", 0),
            "pinned_evidence_count": len(pinned),
            "pinned_workspace_count": len({item["workspace_id"] for item in pinned.values()}),
            "evidence_refs": [pinned[key] for key in sorted(pinned)],
            "promotion_requirements": [
                "Accept this exact candidate digest and provenance in the user store.",
                "Review a completed Skill draft for common-Skill candidates.",
                "Review the exact promotion plan and current store HEAD before apply.",
                "Confirm replacement separately when an active record differs."],
            "trust": "Pack integrity pins these references; repetition does not prove successful execution."}


def compare_candidates(root: Path | str, packs: list[str]) -> dict[str, Any]:
    root = require_owned_workspace(ensure_unlinked_path(Path(root), purpose="Candidate workspace"),
                                   operation="Candidate comparison")
    if not isinstance(packs, list) or not 2 <= len(packs) <= 20:
        fail("Compare between 2 and 20 explicit committed learning packs.")
    groups: dict[str, dict[str, Any]] = {}
    subjects: dict[str, set[str]] = {}
    total = 0
    seen_packs = set()
    candidate_count = 0
    for value in packs:
        path = Path(value)
        path = path if path.is_absolute() else root / path
        path = ensure_unlinked_path(path, purpose="Candidate comparison pack")
        path = ensure_managed_path(root, path, purpose="Candidate comparison pack")
        if not path.relative_to(root).as_posix().startswith(".agent/inbox/learning/"):
            fail("Candidate comparison accepts only project learning packs.")
        if path in seen_packs:
            fail("Select distinct learning pack paths.")
        seen_packs.add(path)
        verified = verify_learning_pack(path)
        total += verified["commit"]["bytes"]
        if total > 32 * 1024 * 1024:
            fail("Candidate comparison exceeds its byte budget.")
        profile = verified["profile"]
        for candidate in [*profile.get("common_skill_candidates", []), *profile.get("preference_candidates", [])]:
            candidate_count += 1
            if candidate_count > 5_000:
                fail("Candidate comparison exceeds its record budget.")
            kind = candidate.get("candidate_type")
            if kind == "common_skill":
                if not is_digest(candidate.get("implementation_sha256")):
                    fail("Candidate has no valid implementation identity.")
                subject = {"type": kind, "skill_id": candidate.get("skill_id")}
                identity = {**subject, "implementation_sha256": candidate["implementation_sha256"]}
            elif kind == "preference":
                subject = {"type": kind, "key": candidate.get("key")}
                identity = {**subject, "value": candidate.get("value")}
            else:
                fail("Unsupported candidate comparison type.")
            explanation = explain_candidate(candidate)
            key = digest(identity)
            subjects.setdefault(digest(subject), set()).add(key)
            group = groups.setdefault(key, {"identity": identity, "members": [], "evidence": {}})
            refs = {digest(item): item for item in explanation["evidence_refs"]}
            group["evidence"].update(refs)
            group["members"].append({"pack": path.relative_to(root).as_posix(),
                                     "pack_sha256": verified["sha256"],
                                     "candidate_id": candidate["candidate_id"],
                                     "candidate_digest": digest(candidate), "evidence_ids": sorted(refs)})
    results = []
    for key, group in sorted(groups.items()):
        sets = [set(item["evidence_ids"]) for item in group["members"]]
        relation = "single"
        if len(sets) > 1:
            relation = ("duplicate_evidence" if all(item == sets[0] for item in sets)
                        else "extended_evidence" if any(all(other <= item for other in sets) for item in sets)
                        else "complementary_evidence")
        results.append({"group_id": key, "identity": group["identity"], "relation": relation,
                        "members": group["members"], "combined_evidence_refs": [
                            group["evidence"][ref] for ref in sorted(group["evidence"])]})
    # This comparison is advisory. No merged candidate or acceptance is synthesized.
    return {"mode": "read_only", "groups": results, "packs": len(seen_packs),
            "conflicts": [{"subject_id": key, "variant_groups": sorted(ids)}
                          for key, ids in sorted(subjects.items()) if len(ids) > 1],
            "preserved_all_evidence": True, "automatic_acceptance": False,
            "next_action": "Review one exact candidate; evidence from another pack never transfers acceptance.",
            "comparison_sha256": digest(results)}
