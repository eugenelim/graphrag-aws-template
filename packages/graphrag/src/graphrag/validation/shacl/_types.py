"""GateResult dataclass for graphrag.validation.shacl."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GateResult:
    """Result returned by ShaclGate.validate().

    outcome:
        "passed"                 — SHACL validation passed; no Neptune call issued.
        "quarantined"            — Violations found; quarantine INSERT succeeded.
        "quarantine_insert_failed" — Violations found but Neptune INSERT failed;
                                   error carries str(exception).
    """

    outcome: str  # "passed" | "quarantined" | "quarantine_insert_failed"
    error: str | None = None
