from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Diagnostic:
    rule: str
    severity: Severity
    message: str
    path: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path:
            data["path"] = self.path
        if self.suggestion:
            data["suggestion"] = self.suggestion
        return data


def severity_counts(diagnostics: list[Diagnostic]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for diagnostic in diagnostics:
        counts[diagnostic.severity] += 1
    return counts

