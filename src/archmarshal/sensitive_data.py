"""Shared conservative inline-secret screening (not a secret scanner)."""
import re


def sensitive_text_errors(**fields: object) -> list[str]:
    token_patterns = (
        r"https?://[^\s/:]+:[^\s/@]+@",
        r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
        r"\bglpat-[A-Za-z0-9_-]{20,}\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bsk-[A-Za-z0-9_-]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    )
    assignments = re.compile(
        r"(?i)(?:(?:--?)(?:password|passwd|token|secret|api[-_]?key)\s*(?:=|\s)|"
        r"(?:password|passwd|token|secret|api[-_]?key)\s*=)\s*([^\s]+)"
    )
    errors: list[str] = []
    for field, raw_values in fields.items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        for index, raw in enumerate(values, start=1):
            value = str(raw or "")
            has_token = any(re.search(pattern, value) for pattern in token_patterns)
            has_assignment = any(
                not _is_environment_reference(match.group(1))
                for match in assignments.finditer(value)
            )
            if has_token or has_assignment:
                errors.append(
                    f"{field} item {index} appears to contain an inline secret; use an environment-variable reference instead."
                )
    return errors


def _is_environment_reference(value: str) -> bool:
    normalized = value.strip("\"'")
    return (
        normalized.startswith(("$", "%", "${", "$env:"))
        or normalized.endswith("%")
        and normalized.startswith("%")
    )
