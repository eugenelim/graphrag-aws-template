"""Shared data types for graphrag.ingestion.

CleansingReport — produced by CleansingPipeline for every processed document.
ProcessResult   — returned by process_document(); consumed by MedallionOrchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CleansingReport:
    """Quality-gate audit record produced for every processed document.

    Written to S3 at ``silver/<doc_uri_enc>/<sha>.report.json`` regardless of
    outcome.  ``quarantined=True`` means the document was stopped at a gate.
    """

    doc_uri: str
    sha: str
    extractor: str
    char_count_raw: int
    char_count_clean: int
    gates_passed: list[str] = field(default_factory=list)
    gates_failed: list[str] = field(default_factory=list)
    pii_flagged: bool = False
    pii_entities_detected: int = 0
    quarantined: bool = False
    headers_stripped: int = 0
    binary_blocks_stripped: int = 0


@dataclass
class ProcessResult:
    """Outcome of ``process_document()``.

    ``outcome`` is one of:
    - ``"loaded"``      — all gates passed; Silver + Gold artifacts ready.
    - ``"quarantined"`` — a Silver or SHACL gate failed; quarantine INSERT emitted.
    - ``"error"``       — an unrecoverable exception (e.g. extractor crash, OOM).
    """

    doc_uri: str
    sha: str
    outcome: str  # "loaded" | "quarantined" | "error"
    quarantine_reason: str | None = None
    # SPARQL INSERT string for the caller (MedallionOrchestrator) to execute against Neptune.
    # Populated for all quarantined/error outcomes; None for "loaded".
    quarantine_insert: str | None = None
    silver_artifact_uri: str | None = None
    gold_artifact_uri: str | None = None
    vectors_artifact_uri: str | None = None
    cleansing_report: CleansingReport | None = None
    turtle: str | None = None
    named_graph: str | None = None
