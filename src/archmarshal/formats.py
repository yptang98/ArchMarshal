from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

FORMAT_REGISTRY_VERSION = "archmarshal-format-registry-v1"


@dataclass(frozen=True)
class FormatSpec:
    family: str
    owner: str
    kind: str
    readable_versions: tuple[str, ...]
    writable_versions: tuple[str, ...]
    recognized_legacy_versions: tuple[str, ...] = ()
    migration_status: str = "not-required"
    boundedness: str = "bounded"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: list(value) if isinstance(value, tuple) else value for key, value in payload.items()}


# This registry contains document/schema identifiers that ArchMarshal can persist or
# consume from persisted state. API envelope versions and hash-domain separators are
# deliberately not durable formats and therefore do not belong here.
DURABLE_FORMATS: tuple[FormatSpec, ...] = (
    FormatSpec(
        "adoption_plan",
        "archmarshal.adoption",
        "review_plan",
        ("archmarshal-adoption-plan-v1",),
        ("archmarshal-adoption-plan-v1",),
        boundedness="bounded-by-adoption-plan-limits",
    ),
    FormatSpec(
        "workspace_ownership",
        "archmarshal.ownership",
        "control_marker",
        ("archmarshal-workspace-ownership-v1",),
        ("archmarshal-workspace-ownership-v1",),
        boundedness="64-KiB-file",
    ),
    FormatSpec(
        "adoption_transaction",
        "archmarshal.adoption_tx",
        "transaction_journal",
        ("archmarshal-adoption-transaction-v1",),
        ("archmarshal-adoption-transaction-v1",),
        boundedness="10000-files-and-64-MiB-content",
    ),
    FormatSpec(
        "adoption_receipt",
        "archmarshal.adoption_tx",
        "transaction_receipt",
        ("archmarshal-adoption-receipt-v1",),
        ("archmarshal-adoption-receipt-v1",),
        boundedness="64-MiB-transaction-journal-budget",
    ),
    FormatSpec(
        "learning_candidates",
        "archmarshal.learning",
        "candidate_profile",
        ("archmarshal-learning-candidates-v2", "archmarshal-learning-candidates-v3"),
        ("archmarshal-learning-candidates-v3",),
        migration_status="read-compatible-no-auto-migration",
        boundedness="8-MiB-YAML-and-candidate-count-limits",
    ),
    FormatSpec(
        "learning_plan",
        "archmarshal.learning",
        "review_plan",
        ("archmarshal-learning-plan-v1",),
        ("archmarshal-learning-plan-v1",),
        boundedness="bounded-by-learning-candidate-limits",
    ),
    FormatSpec(
        "learning_candidate_commit",
        "archmarshal.learning",
        "commit_marker",
        ("archmarshal-learning-candidate-commit-v1",),
        ("archmarshal-learning-candidate-commit-v1",),
        boundedness="8-MiB-file",
    ),
    FormatSpec(
        "backup",
        "archmarshal.safety",
        "backup_manifest",
        ("archmarshal-backup-v1",),
        ("archmarshal-backup-v1",),
        boundedness="100000-files-20-GiB-content-32-MiB-manifest",
    ),
    FormatSpec(
        "backup_restore_plan",
        "archmarshal.safety",
        "review_plan",
        ("archmarshal-backup-restore-plan-v1",),
        ("archmarshal-backup-restore-plan-v1",),
        boundedness="bounded-by-backup-manifest-limits",
    ),
    FormatSpec(
        "restored_workspace_rebind",
        "archmarshal.safety",
        "review_plan",
        ("archmarshal-restored-workspace-rebind-v1",),
        ("archmarshal-restored-workspace-rebind-v1",),
        boundedness="bounded-control-plane-record",
    ),
    FormatSpec(
        "skill_index",
        "archmarshal.skill_index",
        "immutable_generation",
        ("archmarshal-skill-index-v1",),
        ("archmarshal-skill-index-v1",),
        boundedness="10000-generations-256-MiB-history-64-MiB-object",
    ),
    FormatSpec(
        "skill_index_lock",
        "archmarshal.skill_index",
        "lock_metadata",
        ("archmarshal-skill-index-lock-v2",),
        ("archmarshal-skill-index-lock-v2",),
        boundedness="16-KiB-file",
    ),
    FormatSpec(
        "skill_index_rollback_plan",
        "archmarshal.skill_index",
        "review_plan",
        ("archmarshal-skill-index-rollback-plan-v1",),
        ("archmarshal-skill-index-rollback-plan-v1",),
        boundedness="bounded-by-skill-index-limits",
    ),
    FormatSpec(
        "skill_index_recovery",
        "archmarshal.skill_index",
        "recovery_record",
        ("archmarshal-skill-index-recovery-v1",),
        ("archmarshal-skill-index-recovery-v1",),
        boundedness="one-bounded-record-per-recovery",
    ),
    FormatSpec(
        "session",
        "archmarshal.session",
        "session_record",
        ("archmarshal-session-v2",),
        ("archmarshal-session-v2",),
        ("archmarshal-session-v1",),
        migration_status="legacy-recognized-recapture-required",
        boundedness="1000-files-20-MiB-script-snapshots-8-MiB-YAML",
    ),
    FormatSpec(
        "session_commit",
        "archmarshal.session",
        "commit_marker",
        ("archmarshal-session-commit-v1",),
        ("archmarshal-session-commit-v1",),
        boundedness="4-MiB-file-and-1000-records",
    ),
    FormatSpec(
        "closeout_intent",
        "archmarshal.session",
        "digest_envelope",
        ("archmarshal-closeout-intent-v1",),
        ("archmarshal-closeout-intent-v1",),
        boundedness="bounded-by-session-limits",
    ),
    FormatSpec(
        "closeout_plan",
        "archmarshal.session",
        "review_plan",
        ("archmarshal-closeout-plan-v2",),
        ("archmarshal-closeout-plan-v2",),
        boundedness="bounded-by-session-limits",
    ),
    FormatSpec(
        "skill_validation",
        "archmarshal.skill_validation",
        "validation_record",
        ("archmarshal-skill-validation-v1",),
        ("archmarshal-skill-validation-v1",),
        boundedness="bounded-by-skill-package-limits",
    ),
    FormatSpec(
        "skill_review_plan",
        "archmarshal.skill_review",
        "review_plan",
        ("archmarshal-skill-review-plan-v2",),
        ("archmarshal-skill-review-plan-v2",),
        boundedness="72-MiB-file",
    ),
    FormatSpec(
        "skill_review",
        "archmarshal.skill_review",
        "manifest_annotation",
        ("archmarshal-skill-review-v1",),
        ("archmarshal-skill-review-v1",),
        boundedness="500-character-review-fields",
    ),
    FormatSpec(
        "candidate_draft_plan",
        "archmarshal.candidate_draft",
        "review_plan",
        ("archmarshal-candidate-draft-plan-v1",),
        ("archmarshal-candidate-draft-plan-v1",),
        boundedness="16-MiB-saved-preview-envelope",
    ),
    FormatSpec(
        "candidate_draft_preview",
        "archmarshal.candidate_draft",
        "saved_preview",
        ("archmarshal-candidate-draft-preview-v1",),
        ("archmarshal-candidate-draft-preview-v1",),
        boundedness="16-MiB-file",
    ),
    FormatSpec(
        "candidate_draft_commit",
        "archmarshal.candidate_draft",
        "commit_marker",
        ("archmarshal-candidate-draft-commit-v1",),
        ("archmarshal-candidate-draft-commit-v1",),
        boundedness="bounded-by-candidate-draft-scaffold",
    ),
    FormatSpec(
        "candidate_draft_binding",
        "archmarshal.candidate_draft",
        "provenance_binding",
        ("archmarshal-candidate-draft-binding-v1",),
        ("archmarshal-candidate-draft-binding-v1",),
        boundedness="bounded-by-learning-pack-and-draft-plan-limits",
    ),
    FormatSpec(
        "user_store_ownership",
        "archmarshal.user_store",
        "control_marker",
        ("archmarshal-user-store-ownership-v1",),
        ("archmarshal-user-store-ownership-v1",),
        boundedness="64-KiB-file",
    ),
    FormatSpec(
        "user_store_generation",
        "archmarshal.user_store",
        "immutable_generation",
        ("archmarshal-user-store-generation-v1",),
        ("archmarshal-user-store-generation-v1",),
        boundedness="10000-generations-256-MiB-history-16-MiB-object",
    ),
    FormatSpec(
        "user_skill_package",
        "archmarshal.user_store",
        "immutable_package_commit",
        ("archmarshal-user-skill-package-v1", "archmarshal-user-skill-package-v2"),
        ("archmarshal-user-skill-package-v2",),
        migration_status="read-compatible-no-auto-migration",
        boundedness="1000-entries-64-MiB-content-16-MiB-commit",
    ),
    FormatSpec(
        "user_skill_snapshot",
        "archmarshal.user_store",
        "package_snapshot",
        ("archmarshal-user-skill-snapshot-v2",),
        ("archmarshal-user-skill-snapshot-v2",),
        boundedness="1000-entries-and-64-MiB-content",
    ),
    FormatSpec(
        "user_store_plan",
        "archmarshal.user_store",
        "review_plan",
        ("archmarshal-user-store-plan-v1",),
        ("archmarshal-user-store-plan-v1",),
        boundedness="bounded-by-user-store-record-limits",
    ),
    FormatSpec(
        "user_store_lock",
        "archmarshal.user_store",
        "lock_metadata",
        ("archmarshal-user-store-lock-v1",),
        ("archmarshal-user-store-lock-v1",),
        boundedness="16-KiB-file",
    ),
    FormatSpec(
        "workspace_config_schema",
        "archmarshal.schema_validation",
        "schema",
        ("https://archmarshal.local/schemas/workspace.schema.yaml",),
        ("https://archmarshal.local/schemas/workspace.schema.yaml",),
        boundedness="8-MiB-YAML",
    ),
    FormatSpec(
        "artifact_registry_schema",
        "archmarshal.schema_validation",
        "schema",
        ("https://archmarshal.local/schemas/artifact-registry.schema.yaml",),
        ("https://archmarshal.local/schemas/artifact-registry.schema.yaml",),
        boundedness="8-MiB-YAML",
    ),
    FormatSpec(
        "memory_stores_schema",
        "archmarshal.schema_validation",
        "schema",
        ("https://archmarshal.local/schemas/memory-stores.schema.yaml",),
        ("https://archmarshal.local/schemas/memory-stores.schema.yaml",),
        boundedness="8-MiB-YAML",
    ),
    FormatSpec(
        "memory_records_schema",
        "archmarshal.schema_validation",
        "schema",
        ("https://archmarshal.local/schemas/memory-records.schema.yaml",),
        ("https://archmarshal.local/schemas/memory-records.schema.yaml",),
        boundedness="8-MiB-YAML",
    ),
    FormatSpec(
        "skill_manifest_schema",
        "archmarshal.schema_validation",
        "schema",
        ("https://archmarshal.local/schemas/skill-manifest.schema.yaml",),
        ("https://archmarshal.local/schemas/skill-manifest.schema.yaml",),
        boundedness="8-MiB-YAML-and-skill-package-limits",
    ),
)


def format_registry() -> dict[str, Any]:
    return {
        "api_version": FORMAT_REGISTRY_VERSION,
        "formats": [item.to_dict() for item in DURABLE_FORMATS],
    }


def find_format(value: object) -> tuple[FormatSpec | None, str]:
    if not isinstance(value, str):
        return None, "missing"
    for spec in DURABLE_FORMATS:
        if value in spec.writable_versions:
            return spec, "current"
        if value in spec.readable_versions or value in spec.recognized_legacy_versions:
            return spec, "legacy"
    prefix = value.rsplit("-v", 1)[0] if "-v" in value else None
    if prefix:
        for spec in DURABLE_FORMATS:
            versions = (
                *spec.readable_versions,
                *spec.writable_versions,
                *spec.recognized_legacy_versions,
            )
            if any(item.rsplit("-v", 1)[0] == prefix for item in versions if "-v" in item):
                return spec, "unsupported"
    return None, "unknown"


__all__ = [
    "DURABLE_FORMATS",
    "FORMAT_REGISTRY_VERSION",
    "FormatSpec",
    "find_format",
    "format_registry",
]
