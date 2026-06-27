"""The closed-schema triple validator — the governance boundary, layer 1 (AC1, ADR-0006).

The first of the two guards that make the LLM extraction hop safe. Because the model authors
*which entities relate and how*, every candidate triple is validated against the **closed**
schema before it can become an edge: its predicate must be one of the fixed LLM-extractable
edge kinds and the schema's endpoint kinds must be real ``EntityKind``s. The validator is
**conservative** — ambiguous or malformed ⇒ reject, with the violated rule named — and an
off-schema triple is **never written** (``schema_extract`` records it in the trace).

The entity-grounding guard (layer 2, ``ground.py``) resolves the raw mentions to real graph
ids; this layer is purely about the predicate + well-formedness. Pure-Python, ingest-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .extract_llm import CandidateTriple, ExtractionSchema
from .model import EntityKind


@dataclass(frozen=True)
class TripleValidation:
    """The closed-schema verdict for one candidate triple.

    ``ok`` is True iff the triple passed every rule; otherwise ``violated_rule`` names the first
    rule it failed (the trace records this as ``off-schema-rejected``)."""

    ok: bool
    triple: CandidateTriple
    violated_rule: str | None = None


def validate_triple(triple: CandidateTriple, *, schema: ExtractionSchema) -> TripleValidation:
    """Validate a candidate against the closed schema (AC1).

    Rules, in order (conservative — the first failure wins, ambiguous ⇒ reject):

    - ``empty-subject`` / ``empty-object`` — a blank endpoint mention.
    - ``malformed-predicate`` — a blank predicate or one that is not a single token.
    - ``off-schema-predicate`` — a predicate not in the closed LLM-extractable set (an unknown
      predicate, or a *deterministic-only* predicate like ``AUTHORS``).
    - ``unknown-endpoint-kind`` — the schema entry's endpoint kinds are not real ``EntityKind``s
      (a malformed schema; fail-closed).
    """
    if not triple.subject.strip():
        return TripleValidation(False, triple, "empty-subject")
    if not triple.object.strip():
        return TripleValidation(False, triple, "empty-object")
    predicate = triple.predicate.strip()
    if not predicate or len(predicate.split()) != 1:
        return TripleValidation(False, triple, "malformed-predicate")

    schema_edge = schema.by_predicate(predicate)
    if schema_edge is None:
        return TripleValidation(False, triple, "off-schema-predicate")
    if not (
        isinstance(schema_edge.src_kind, EntityKind)
        and isinstance(schema_edge.dst_kind, EntityKind)
    ):
        return TripleValidation(False, triple, "unknown-endpoint-kind")

    return TripleValidation(True, triple, None)
