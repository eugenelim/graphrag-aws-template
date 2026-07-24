"""Shared dataclasses for the text2sparql module.

Three types cover the full audit trail:
- ``ValidationResult`` — the verdict on one model-authored SPARQL query.
- ``GeneratedQuery`` — one generate→validate cycle.
- ``Text2SparqlResult`` — the complete audit trace returned to the caller.

The ``question`` text is intentionally absent from ``Text2SparqlResult``:
ADR-0014 content-capture policy forbids question text from any trace field
or log line at INFO or above.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Verdict on one model-authored SPARQL query.

    ``rule`` is ``None`` when ``valid=True``; a rule-name string when ``valid=False``.
    The rule name is safe to surface in feedback (our own text, not attacker-controlled).
    """

    valid: bool
    rule: str | None = None  # e.g. "mutation_keyword", "missing_from_named"


@dataclass
class GeneratedQuery:
    """One generate→validate cycle's provenance, recorded in the audit trace."""

    query_text: str
    validation_verdict: ValidationResult


@dataclass
class Text2SparqlResult:
    """The audit artifact of one text2sparql query.

    On success: the schema shown to the model, every generation attempt with its
    verdict, the ``executed_query``, and the rows.  On refusal: ``refusal_reason``
    is set and ``executed_query`` is ``None`` — no query ran.

    ``question`` is deliberately absent: ADR-0014 content-capture policy forbids
    question text from appearing in any trace field or log line at INFO or above.
    """

    schema_context: str
    generated_queries: list[GeneratedQuery] = field(default_factory=list)
    executed_query: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    refusal_reason: str | None = None
