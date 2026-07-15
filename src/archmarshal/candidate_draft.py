from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import ArchMarshalError
from .io import MAX_YAML_BYTES, read_bytes_safe
from .ownership import require_owned_workspace
from .promotion import verified_learning_candidate_context
from .safety import (
    create_bytes_exclusive,
    ensure_unlinked_path,
    fsync_directory,
    is_link_or_reparse,
)
from .schema_validation import validate_schema
from .user_store import _load_store, _require_owned_store

CANDIDATE_DRAFT_PLAN_FORMAT = "archmarshal-candidate-draft-plan-v1"
CANDIDATE_DRAFT_PREVIEW_FORMAT = "archmarshal-candidate-draft-preview-v1"
CANDIDATE_DRAFT_RECEIPT_FORMAT = "archmarshal-candidate-draft-commit-v1"
MAX_CANDIDATE_DRAFT_PREVIEW_BYTES = 16 * 1024 * 1024
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SKILL_ID = re.compile(r"^skill\.[a-z0-9_.-]+$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_RECEIPT_PATH = "COMMITTED.json"


def candidate_to_skill_draft(
    root: Path | str,
    pack: Path | str,
    candidate_id: str,
    user_store: Path | str,
    destination: Path | str,
    *,
    reviewed_preview: dict[str, Any] | None = None,
    expected_plan: str | None = None,
    expected_head_token: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or create a non-executable Skill draft envelope from an accepted candidate."""
    if apply:
        reviewed_plan, expected_head = _require_reviewed_preview(
            reviewed_preview,
            expected_plan=expected_plan,
            expected_head_token=expected_head_token,
        )
    elif any(value is not None for value in (reviewed_preview, expected_plan, expected_head_token)):
        raise ArchMarshalError(
            "candidate_draft_plan_unexpected",
            "Saved preview and exact apply preconditions are accepted only with --apply.",
        )
    else:
        reviewed_plan = None
        expected_head = None

    current = _build_candidate_draft_plan(
        root,
        pack,
        candidate_id,
        user_store,
        destination,
    )
    if reviewed_plan is None:
        return _preview_envelope(current)
    if expected_head != current["user_store"]["expected_head"]:
        raise ArchMarshalError(
            "candidate_draft_stale_head",
            "The user-store HEAD changed after the saved draft preview; nothing was created.",
            details={
                "expected_head": expected_head,
                "actual_head": current["user_store"]["expected_head"],
            },
        )
    if reviewed_plan != current:
        raise ArchMarshalError(
            "candidate_draft_plan_stale",
            "Candidate, pack, acceptance, destination, or proposed bytes changed after preview; nothing was created.",
        )
    result = _publish_candidate_draft(current)
    return {
        "tool": "archmarshal",
        "stage": "candidate_draft",
        "mode": "candidate_draft_created",
        "candidate_id": current["candidate"]["candidate_id"],
        "candidate_digest": current["candidate"]["candidate_digest"],
        "plan_digest": current["plan_digest"],
        "expected_head": current["user_store"]["expected_head"],
        "created": current["destination"]["real_path"],
        "promotion_path": current["destination"]["promotion_path"],
        "receipt": result,
        "next_actions": _next_actions(current),
        "source_mutation": False,
        "user_store_mutation": False,
        "auto_promoted": False,
    }


def load_candidate_draft_preview(path: Path | str) -> dict[str, Any]:
    plan_path = Path(path)
    loaded = read_bytes_safe(
        plan_path,
        max_bytes=MAX_CANDIDATE_DRAFT_PREVIEW_BYTES,
        label="Candidate draft preview",
    )
    if loaded.error:
        raise ArchMarshalError(
            "candidate_draft_preview_invalid",
            "Candidate draft preview is linked, unreadable, or exceeds the safe size limit.",
            details={"path": str(plan_path), "error": loaded.error},
        )
    try:
        raw = loaded.data
        text = (
            raw.decode("utf-16")
            if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
            else raw.decode("utf-8-sig")
        )
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchMarshalError(
            "candidate_draft_preview_invalid",
            "Candidate draft preview must be UTF-8 or BOM-marked UTF-16 JSON.",
            details={"path": str(plan_path)},
        ) from exc
    if not isinstance(payload, dict):
        raise ArchMarshalError(
            "candidate_draft_preview_invalid",
            "Candidate draft preview must be a complete JSON object.",
        )
    return payload


def _build_candidate_draft_plan(
    root: Path | str,
    pack: Path | str,
    candidate_id: str,
    user_store: Path | str,
    destination: Path | str,
) -> dict[str, Any]:
    root_path = require_owned_workspace(root, operation="Candidate Skill draft")
    candidate, candidate_digest, provenance, verified_pack = verified_learning_candidate_context(
        root_path, pack, candidate_id
    )
    if candidate.get("candidate_type") != "common_skill":
        raise ArchMarshalError(
            "candidate_draft_type_invalid",
            "Only an accepted common-Skill candidate can be scaffolded as a Skill draft.",
            details={"candidate_type": candidate.get("candidate_type")},
        )
    source_skill_id = candidate.get("skill_id")
    source_implementation = candidate.get("implementation_sha256")
    if (
        not isinstance(source_skill_id, str)
        or not _SKILL_ID.fullmatch(source_skill_id)
        or not isinstance(source_implementation, str)
        or not _SHA256.fullmatch(source_implementation)
    ):
        raise ArchMarshalError(
            "candidate_draft_lineage_invalid",
            "The committed candidate lacks valid source Skill lineage.",
        )

    pack_binding = _verified_pack_binding(verified_pack)
    canonical_candidate = _canonical_bytes(candidate)
    if hashlib.sha256(canonical_candidate).hexdigest() != candidate_digest:
        raise ArchMarshalError(
            "candidate_draft_candidate_invalid",
            "The verified candidate digest is internally inconsistent.",
        )
    store_root, store_head, acceptance = _accepted_candidate_decision(
        user_store,
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
        provenance=provenance,
    )
    destination_path = _absent_destination(
        destination,
        source_root=root_path,
        user_store_root=store_root,
    )
    skill_name = _candidate_skill_name(candidate)
    promotion_path = destination_path / skill_name
    files = _draft_files(
        skill_name,
        candidate=candidate,
        candidate_digest=candidate_digest,
        source_skill_id=source_skill_id,
        source_implementation_sha256=source_implementation,
        provenance=provenance,
        pack_binding=pack_binding,
        store_root=store_root,
        store_head=store_head,
        acceptance=acceptance,
        destination=destination_path,
    )
    plan: dict[str, Any] = {
        "format": CANDIDATE_DRAFT_PLAN_FORMAT,
        "operation": "candidate_to_skill_draft",
        "source_workspace": str(root_path),
        "pack": pack_binding,
        "candidate": {
            "candidate_id": candidate_id,
            "candidate_type": "common_skill",
            "candidate_digest": candidate_digest,
            "canonical_bytes": len(canonical_candidate),
            "canonical_json_utf8": canonical_candidate.decode("utf-8"),
            "snapshot": candidate,
            "provenance": provenance,
        },
        "user_store": {
            "root": str(store_root),
            "expected_head": store_head,
            "expected_head_token": store_head,
            "accepted_decision": acceptance,
        },
        "destination": {
            "real_path": str(destination_path),
            "promotion_path": str(promotion_path),
            "policy": "absent_create_only",
            "receipt": _RECEIPT_PATH,
        },
        "publication_order": [record["path"] for record in files],
        "files": files,
        "mutation_scope": [str(destination_path)],
        "source_mutation": False,
        "user_store_mutation": False,
        "executable_code_inferred": False,
        "auto_promoted": False,
    }
    plan["plan_digest"] = _canonical_digest(plan)
    return plan


def _verified_pack_binding(verified: dict[str, Any]) -> dict[str, Any]:
    pack_root = Path(str(verified["path"]))
    candidate_path = pack_root / "candidates.yaml"
    commit_path = pack_root / "COMMITTED.json"
    candidate_file = read_bytes_safe(
        candidate_path,
        max_bytes=MAX_YAML_BYTES,
        label="Learning candidate payload",
    )
    commit_file = read_bytes_safe(
        commit_path,
        max_bytes=MAX_YAML_BYTES,
        label="Learning candidate commit",
    )
    commit = verified.get("commit")
    if (
        candidate_file.error
        or commit_file.error
        or not isinstance(commit, dict)
        or candidate_file.sha256 != verified.get("sha256")
        or candidate_file.byte_count != commit.get("bytes")
        or commit_file.sha256 != verified.get("commit_sha256")
    ):
        raise ArchMarshalError(
            "candidate_draft_pack_changed",
            "The committed learning pack changed while the draft was being planned.",
        )
    return {
        "real_path": str(pack_root),
        "format": commit.get("format"),
        "candidate_format": commit.get("candidate_format"),
        "candidate_file": {
            "path": "candidates.yaml",
            "bytes": candidate_file.byte_count,
            "sha256": candidate_file.sha256,
        },
        "commit_file": {
            "path": "COMMITTED.json",
            "bytes": commit_file.byte_count,
            "sha256": commit_file.sha256,
        },
    }


def _accepted_candidate_decision(
    user_store: Path | str,
    *,
    candidate_id: str,
    candidate_digest: str,
    provenance: list[dict[str, str]],
) -> tuple[Path, str, dict[str, Any]]:
    store_root = _require_owned_store(user_store)
    loaded = _load_store(store_root, allow_locked=False)
    head = loaded.get("head")
    generation = loaded.get("generation") or {}
    decisions = generation.get("candidate_decisions") or []
    matches = [
        item
        for item in decisions
        if isinstance(item, dict)
        and item.get("candidate_id") == candidate_id
        and item.get("candidate_digest") == candidate_digest
        and item.get("provenance") == provenance
    ]
    latest = matches[-1] if matches else None
    if latest is None or latest.get("decision") != "accepted":
        raise ArchMarshalError(
            "candidate_draft_candidate_not_accepted",
            "Draft scaffolding requires the latest review decision for this exact candidate and provenance to be accepted.",
            details={
                "candidate_id": candidate_id,
                "candidate_digest": candidate_digest,
                "latest_decision": latest.get("decision") if latest else None,
            },
        )
    decision_digest = latest.get("digest")
    if (
        not isinstance(head, str)
        or not _SHA256.fullmatch(head)
        or not isinstance(decision_digest, str)
        or not _SHA256.fullmatch(decision_digest)
    ):
        raise ArchMarshalError(
            "candidate_draft_acceptance_invalid",
            "The accepted user-store decision is not bound to a valid immutable HEAD.",
        )
    return (
        store_root,
        head,
        {
            "candidate_id": candidate_id,
            "candidate_digest": candidate_digest,
            "decision": "accepted",
            "decision_digest": decision_digest,
            "decided_at": latest.get("decided_at"),
            "provenance": provenance,
        },
    )


def _absent_destination(
    destination: Path | str,
    *,
    source_root: Path,
    user_store_root: Path,
) -> Path:
    lexical = ensure_unlinked_path(destination, purpose="Candidate draft destination")
    parent = lexical.parent
    if not parent.exists() or not parent.is_dir() or is_link_or_reparse(parent):
        raise ArchMarshalError(
            "candidate_draft_parent_invalid",
            "Candidate draft destination requires an existing real parent directory.",
            details={"parent": str(parent)},
        )
    if lexical.exists() or is_link_or_reparse(lexical):
        raise ArchMarshalError(
            "candidate_draft_destination_exists",
            "Candidate draft destination must be absent; existing or partial output is never overwritten or deleted.",
            details={"destination": str(lexical)},
        )
    real = lexical.resolve(strict=False)
    if _paths_overlap(real, source_root) or _paths_overlap(real, user_store_root):
        raise ArchMarshalError(
            "candidate_draft_destination_overlap",
            "Candidate draft destination must be disjoint from the source project and user store.",
            details={
                "destination": str(real),
                "source_workspace": str(source_root),
                "user_store": str(user_store_root),
            },
        )
    return real


def _candidate_skill_name(candidate: dict[str, Any]) -> str:
    name = candidate.get("name")
    if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name) or len(name) > 64:
        raise ArchMarshalError(
            "candidate_draft_skill_name_invalid",
            "The committed candidate must retain a valid lowercase hyphenated Skill name.",
            details={"name": name},
        )
    return name


def _draft_files(
    skill_name: str,
    *,
    candidate: dict[str, Any],
    candidate_digest: str,
    source_skill_id: str,
    source_implementation_sha256: str,
    provenance: list[dict[str, str]],
    pack_binding: dict[str, Any],
    store_root: Path,
    store_head: str,
    acceptance: dict[str, Any],
    destination: Path,
) -> list[dict[str, Any]]:
    description = (
        f"Prepare a human-reviewed reusable {skill_name} workflow. Use when {skill_name} work "
        "recurs across projects and the accepted candidate evidence applies."
    )
    frontmatter = yaml.safe_dump(
        {"name": skill_name, "description": description},
        sort_keys=False,
        allow_unicode=True,
    )
    skill_md = (
        f"---\n{frontmatter}---\n\n"
        f"# {skill_name}\n\n"
        "- Define the reusable workflow from reviewed evidence.\n"
        "- State required inputs, outputs, and safety boundaries.\n"
        "- Remove project-specific paths, secrets, and assumptions.\n"
        "- Do not rename, execute, or promote this draft until REVIEW.md is complete.\n"
    ).encode("utf-8")
    manifest = {
        "id": f"skill.common-project.{skill_name}",
        "name": skill_name,
        "kind": "common_project_skill",
        "version": "0.1.0",
        "status": "disabled",
        "priority": "normal",
        "scope": "common_project",
        "summary": f"Human-review scaffold for reusable {skill_name} work.",
        "tags": [skill_name, "candidate-draft"],
        "triggers": [f"Use when {skill_name} work recurs across projects after review."],
        "negative_triggers": ["Do not use before review or for project-specific variants."],
        "permissions": {
            "reads": [],
            "writes": [],
            "proposes": [],
            "forbidden": ["execution before human completion and explicit promotion"],
        },
        "promotion": {
            "candidate_id": candidate["candidate_id"],
            "candidate_digest": candidate_digest,
            "source_skill_id": source_skill_id,
            "source_implementation_sha256": source_implementation_sha256,
        },
        "draft_state": "human_completion_required",
    }
    issues = validate_schema(manifest, "skill-manifest")
    if issues:
        raise ArchMarshalError(
            "candidate_draft_manifest_invalid",
            "Generated candidate draft manifest failed its schema before publication.",
            details={
                "issues": [
                    {"location": item.location, "message": item.message} for item in issues[:20]
                ]
            },
        )
    manifest_bytes = yaml.safe_dump(
        manifest,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    review_md = _review_markdown(
        skill_name,
        candidate_id=str(candidate["candidate_id"]),
        candidate_digest=candidate_digest,
        pack_sha256=str(pack_binding["candidate_file"]["sha256"]),
        store_head=store_head,
        decision_digest=str(acceptance["decision_digest"]),
    ).encode("utf-8")
    payloads = {
        f"{skill_name}/SKILL.md.draft": skill_md,
        f"{skill_name}/manifest.yaml": manifest_bytes,
        "REVIEW.md": review_md,
    }
    payload_records = [_file_record(path, payloads[path]) for path in payloads]
    binding = {
        "format": "archmarshal-candidate-draft-binding-v1",
        "candidate_id": candidate["candidate_id"],
        "candidate_digest": candidate_digest,
        "candidate_provenance": provenance,
        "learning_pack": pack_binding,
        "user_store": str(store_root),
        "expected_head": store_head,
        "decision_digest": acceptance["decision_digest"],
        "destination": str(destination),
        "promotion_path": str(destination / skill_name),
        "files": [
            {key: record[key] for key in ("path", "bytes", "sha256")} for record in payload_records
        ],
    }
    receipt = {
        "format": CANDIDATE_DRAFT_RECEIPT_FORMAT,
        "binding_digest": _canonical_digest(binding),
        "binding": binding,
        "receipt_published_last": True,
        "baseline_only": True,
        "human_review_required": True,
        "auto_promoted": False,
        "source_mutation": False,
        "user_store_mutation": False,
    }
    receipt_bytes = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    return [*payload_records, _file_record(_RECEIPT_PATH, receipt_bytes)]


def _review_markdown(
    skill_name: str,
    *,
    candidate_id: str,
    candidate_digest: str,
    pack_sha256: str,
    store_head: str,
    decision_digest: str,
) -> str:
    return (
        "# Candidate Skill draft review\n\n"
        f"Promotion package: `{skill_name}/`\n\n"
        "This envelope is a create-only baseline, not an approved or executable Skill. "
        "ArchMarshal inferred no scripts, commands, resources, permissions, or operational steps.\n\n"
        "## Bound evidence\n\n"
        f"- Candidate: `{candidate_id}`\n"
        f"- Candidate digest: `{candidate_digest}`\n"
        f"- Learning pack payload: `{pack_sha256}`\n"
        f"- Accepted user-store HEAD: `{store_head}`\n"
        f"- Acceptance decision: `{decision_digest}`\n\n"
        "## Human completion checklist\n\n"
        "- [ ] Complete `SKILL.md.draft` with concise imperative workflow instructions.\n"
        "- [ ] Confirm the description includes exact trigger context.\n"
        "- [ ] Review summary, tags, positive triggers, and negative triggers.\n"
        "- [ ] Declare only necessary permissions, dependencies, and resources.\n"
        "- [ ] Remove secrets, absolute paths, and project-specific assumptions.\n"
        "- [ ] Add and test executable code only when a human determines it is necessary.\n"
        "- [ ] Keep the promotion lineage fields unchanged.\n"
        "- [ ] Set manifest status to `active` only after every review item is complete.\n"
        "- [ ] Rename `SKILL.md.draft` to `SKILL.md` only after every review item is complete.\n"
        "- [ ] Run candidate-promote as a separate preview and review that plan before apply.\n\n"
        "`COMMITTED.json` attests only to the original scaffold bytes. Human edits intentionally "
        "make that baseline receipt historical; candidate-promote re-hashes the final nested package.\n"
    )


def _file_record(relative: str, content: bytes) -> dict[str, Any]:
    return {
        "path": relative,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_utf8": content.decode("utf-8"),
    }


def _preview_envelope(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": CANDIDATE_DRAFT_PREVIEW_FORMAT,
        "tool": "archmarshal",
        "stage": "candidate_draft",
        "mode": "propose_only",
        "candidate_id": plan["candidate"]["candidate_id"],
        "candidate_digest": plan["candidate"]["candidate_digest"],
        "expected_head": plan["user_store"]["expected_head"],
        "expected_head_token": plan["user_store"]["expected_head_token"],
        "plan_digest": plan["plan_digest"],
        "destination": plan["destination"]["real_path"],
        "promotion_path": plan["destination"]["promotion_path"],
        "apply_precondition": (
            "--plan-file <saved-preview.json> --expect-head <exact-head> "
            "--expect-plan <plan-digest> --apply"
        ),
        "draft_plan": plan,
        "next_actions": _next_actions(plan),
        "source_mutation": False,
        "user_store_mutation": False,
        "auto_promoted": False,
        "notes": [
            "Save this complete preview; apply rejects a plan fragment or changed source bytes.",
            "Only the absent destination envelope may be created.",
            "COMMITTED.json is published last; partial output remains inspectable and is never overwritten or deleted.",
            "The nested package stays disabled until a human completes REVIEW.md.",
        ],
    }


def _next_actions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    review_path = str(Path(plan["destination"]["real_path"]) / "REVIEW.md")
    promote_args = [
        "archmarshal",
        "candidate-promote",
        plan["source_workspace"],
        "--pack",
        plan["pack"]["real_path"],
        "--candidate",
        plan["candidate"]["candidate_id"],
        "--user-store",
        plan["user_store"]["root"],
        "--draft",
        plan["destination"]["promotion_path"],
    ]
    return [
        {
            "action": "complete_human_review",
            "available_after_apply": True,
            "review_path": review_path,
            "promotion_path": plan["destination"]["promotion_path"],
        },
        {
            "action": "preview_candidate_promotion",
            "available_after_human_review": True,
            "requires_manifest_status": "active",
            "requires_rename": "SKILL.md.draft -> SKILL.md",
            "command_args": promote_args,
            "command": subprocess.list2cmdline(promote_args),
        },
    ]


def _require_reviewed_preview(
    preview: dict[str, Any] | None,
    *,
    expected_plan: str | None,
    expected_head_token: str | None,
) -> tuple[dict[str, Any], str]:
    if preview is None or expected_plan is None or expected_head_token is None:
        raise ArchMarshalError(
            "candidate_draft_review_required",
            "Apply requires the complete saved preview, exact plan digest, and exact user-store HEAD.",
        )
    if (
        preview.get("format") != CANDIDATE_DRAFT_PREVIEW_FORMAT
        or preview.get("stage") != "candidate_draft"
        or preview.get("mode") != "propose_only"
        or not isinstance(preview.get("draft_plan"), dict)
    ):
        raise ArchMarshalError(
            "candidate_draft_preview_invalid",
            "Apply requires the complete candidate-draft preview envelope, not a plan fragment.",
        )
    plan = preview["draft_plan"]
    _validate_plan(plan)
    if preview.get("plan_digest") != plan["plan_digest"] or expected_plan != plan["plan_digest"]:
        raise ArchMarshalError(
            "candidate_draft_plan_digest_mismatch",
            "Saved preview and --expect-plan do not bind the same candidate-draft plan.",
        )
    expected_head = plan.get("user_store", {}).get("expected_head")
    if (
        not isinstance(expected_head, str)
        or not _SHA256.fullmatch(expected_head)
        or expected_head_token != expected_head
    ):
        raise ArchMarshalError(
            "candidate_draft_expected_head_mismatch",
            "--expect-head must exactly match the accepted user-store HEAD in the saved preview.",
        )
    return plan, expected_head


def _validate_plan(plan: dict[str, Any]) -> None:
    if (
        plan.get("format") != CANDIDATE_DRAFT_PLAN_FORMAT
        or plan.get("operation") != "candidate_to_skill_draft"
        or plan.get("source_mutation") is not False
        or plan.get("user_store_mutation") is not False
        or plan.get("auto_promoted") is not False
        or plan.get("executable_code_inferred") is not False
    ):
        raise ArchMarshalError(
            "candidate_draft_plan_invalid",
            "Saved candidate-draft plan has an invalid format or mutation policy.",
        )
    plan_digest = plan.get("plan_digest")
    if not isinstance(plan_digest, str) or not _SHA256.fullmatch(plan_digest):
        raise ArchMarshalError(
            "candidate_draft_plan_invalid",
            "Saved candidate-draft plan has no valid digest.",
        )
    unsigned = {key: value for key, value in plan.items() if key != "plan_digest"}
    if _canonical_digest(unsigned) != plan_digest:
        raise ArchMarshalError(
            "candidate_draft_plan_invalid",
            "Saved candidate-draft plan digest does not match its complete contents.",
        )
    files = plan.get("files")
    order = plan.get("publication_order")
    if not isinstance(files, list) or not files or order != [item.get("path") for item in files]:
        raise ArchMarshalError(
            "candidate_draft_plan_invalid",
            "Saved candidate-draft file publication order is invalid.",
        )
    if order[-1] != _RECEIPT_PATH or len(order) != len(set(order)):
        raise ArchMarshalError(
            "candidate_draft_plan_invalid",
            "Candidate draft receipt must be the unique final publication.",
        )
    for record in files:
        relative = record.get("path") if isinstance(record, dict) else None
        content = record.get("content_utf8") if isinstance(record, dict) else None
        if (
            not isinstance(relative, str)
            or not _safe_relative(relative)
            or not isinstance(content, str)
        ):
            raise ArchMarshalError(
                "candidate_draft_plan_invalid",
                "Candidate draft plan contains an unsafe file record.",
            )
        content_bytes = content.encode("utf-8")
        if (
            record.get("bytes") != len(content_bytes)
            or record.get("sha256") != hashlib.sha256(content_bytes).hexdigest()
        ):
            raise ArchMarshalError(
                "candidate_draft_plan_invalid",
                "Candidate draft file bytes do not match their reviewed hashes.",
                details={"path": relative},
            )


def _publish_candidate_draft(plan: dict[str, Any]) -> dict[str, Any]:
    destination = Path(plan["destination"]["real_path"])
    _validate_plan(plan)
    try:
        destination.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise ArchMarshalError(
            "candidate_draft_destination_exists",
            "Candidate draft destination appeared after review; nothing was overwritten.",
            details={"destination": str(destination)},
        ) from exc
    fsync_directory(destination.parent)
    identity = _directory_identity(destination)
    written: list[dict[str, Any]] = []
    for record in plan["files"]:
        _verify_partial_output(destination, identity, written)
        target = destination / PurePosixPath(record["path"])
        create_bytes_exclusive(target, record["content_utf8"].encode("utf-8"), mode=0o600)
        written.append(record)
        _verify_partial_output(destination, identity, written)
    return {
        "path": str(destination / _RECEIPT_PATH),
        "sha256": plan["files"][-1]["sha256"],
        "published_last": True,
        "created_files": [record["path"] for record in written],
        "partial_output_policy": "preserve_never_overwrite_or_delete",
    }


def _verify_partial_output(
    destination: Path,
    identity: tuple[int, int],
    expected_records: list[dict[str, Any]],
) -> None:
    if _directory_identity(destination) != identity:
        raise ArchMarshalError(
            "candidate_draft_destination_changed",
            "Candidate draft destination changed during create-only publication; partial output was preserved.",
        )
    expected_paths = [record["path"] for record in expected_records]
    expected_directories = {
        PurePosixPath(relative).parts[0]
        for relative in expected_paths
        if len(PurePosixPath(relative).parts) > 1
    }
    actual_files: list[str] = []
    actual_directories: set[str] = set()
    for item in _bounded_children(destination):
        if is_link_or_reparse(item):
            raise ArchMarshalError(
                "candidate_draft_destination_changed",
                "Linked content appeared during create-only publication; partial output was preserved.",
            )
        if item.is_file():
            actual_files.append(item.relative_to(destination).as_posix())
        elif item.is_dir():
            relative_directory = item.relative_to(destination).as_posix()
            actual_directories.add(relative_directory)
            for nested in _bounded_children(item):
                if is_link_or_reparse(nested) or not nested.is_file():
                    raise ArchMarshalError(
                        "candidate_draft_destination_changed",
                        "Unsupported or linked nested content appeared during publication; partial output was preserved.",
                    )
                actual_files.append(nested.relative_to(destination).as_posix())
        else:
            raise ArchMarshalError(
                "candidate_draft_destination_changed",
                "Unsupported content appeared during publication; partial output was preserved.",
            )
    if sorted(actual_files) != sorted(expected_paths):
        raise ArchMarshalError(
            "candidate_draft_destination_changed",
            "Candidate draft destination contents changed during publication; partial output was preserved.",
            details={"expected": sorted(expected_paths), "actual": sorted(actual_files)},
        )
    if not actual_directories.issubset(expected_directories):
        raise ArchMarshalError(
            "candidate_draft_destination_changed",
            "Unexpected directories appeared during publication; partial output was preserved.",
        )
    for record in expected_records:
        relative = record["path"]
        loaded = read_bytes_safe(
            destination / PurePosixPath(relative),
            max_bytes=record["bytes"],
            label="Candidate draft output",
        )
        if (
            loaded.error
            or loaded.byte_count != record["bytes"]
            or loaded.sha256 != record["sha256"]
        ):
            raise ArchMarshalError(
                "candidate_draft_destination_changed",
                "Candidate draft output bytes changed during publication; partial output was preserved.",
                details={"path": relative},
            )


def _bounded_children(directory: Path) -> list[Path]:
    children: list[Path] = []
    try:
        for child in directory.iterdir():
            children.append(child)
            if len(children) > 16:
                raise ArchMarshalError(
                    "candidate_draft_destination_changed",
                    "Candidate draft destination exceeded its bounded entry budget; partial output was preserved.",
                )
    except OSError as exc:
        raise ArchMarshalError(
            "candidate_draft_destination_changed",
            "Candidate draft destination could not be inspected safely; partial output was preserved.",
        ) from exc
    return children


def _directory_identity(path: Path) -> tuple[int, int]:
    if not path.is_dir() or is_link_or_reparse(path):
        raise ArchMarshalError(
            "candidate_draft_destination_changed",
            "Candidate draft destination is missing, linked, or not a directory.",
            details={"destination": str(path)},
        )
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def _safe_relative(value: str) -> bool:
    pure = PurePosixPath(value)
    return bool(value) and not pure.is_absolute() and ".." not in pure.parts and "\\" not in value


def _paths_overlap(first: Path, second: Path) -> bool:
    first = first.resolve(strict=False)
    second = second.resolve(strict=False)
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


__all__ = [
    "CANDIDATE_DRAFT_PLAN_FORMAT",
    "CANDIDATE_DRAFT_PREVIEW_FORMAT",
    "candidate_to_skill_draft",
    "load_candidate_draft_preview",
]
