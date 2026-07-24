"""Types for graphrag.normative — exception, result, and response envelope."""

from __future__ import annotations

from dataclasses import dataclass, field


class NormativeUnavailable(Exception):
    """Raised when Neptune SPARQL normative partition is unreachable.

    Hard-fail semantics (ADR-0012): a partial normative result is worse than
    no result — a missing policy is a compliance gap that cannot be detected.
    The original cause is chained as ``__cause__`` for logging.
    """


@dataclass
class NormativeResult:
    """A single normative document returned by :class:`NormativeRetriever`."""

    uri: str
    title: str
    doc_type: str  # "Policy", "Standard", or "Guideline"
    domain: str | None  # local name extracted from biz:<Domain> URI, e.g. "Finance"
    effective_date: str | None  # ISO date string (xsd:date lexical form) or None
    scope: str | None
    pii_flagged: bool
    relevance: float | None  # cosine similarity from vector leg; None for SPARQL-only hits
    git_commit: str | None  # SHA from biz:gitCommitSHA
    git_path: str | None  # repo-relative path from biz:gitPath


@dataclass
class NormativeResponse:
    """Response envelope from :meth:`NormativeRetriever.retrieve`.

    ``pii_withheld_count`` tells the caller how many PII-flagged documents
    were excluded by the default PII filter — a non-zero value means the
    result may be incomplete for a caller who holds a role that should see
    PII-flagged policies.
    """

    results: list[NormativeResult] = field(default_factory=list)
    pii_withheld_count: int = 0
