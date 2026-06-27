"""The entity-grounding check — the governance boundary, layer 2 (AC2, ADR-0006).

The second guard: the model may relate entities the deterministic graph **already resolved**;
it may never invent one. ``ground_triple`` resolves each endpoint mention through the **existing**
``normalize`` functions (no new resolver) — keyed by the predicate's single permitted
``(src EntityKind, dst EntityKind)`` pair — and accepts the triple iff both endpoints resolve to a
node id **already present in the graph, of the expected kind**. A mention that grounds to no known
id (or to a node of the wrong kind) is dropped; an ambiguous predicate→endpoint mapping is dropped,
never guessed (fail closed).

Reuses ``normalize.sig_id`` / ``kep_id`` / ``person_id`` + the alias table verbatim, so an
LLM-asserted endpoint resolves to the *same* id the deterministic pass produced (else a real
relationship between known entities would be falsely dropped). Pure-Python, ingest-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .extract_llm import EXTRACTION_SCHEMA, CandidateTriple, ExtractionSchema
from .model import EdgeKind, EntityKind, Graph
from .normalize import kep_id, person_id, sig_id

# The normalizer registry — keyed by endpoint kind, holding the EXISTING ``normalize``
# functions verbatim (a unit test pins the identity, so no new resolution path can sneak in).
# Endpoint kinds without an entry (e.g. SUBPROJECT) are unsupported and fail closed.
_NORMALIZERS = {
    EntityKind.SIG: sig_id,
    EntityKind.KEP: kep_id,
    EntityKind.PERSON: person_id,
}


@dataclass(frozen=True)
class GroundedTriple:
    """A validated + grounded triple ready to become an edge (AC2).

    ``src_id`` / ``dst_id`` are canonical graph node ids (both present in the graph, of the
    schema-declared kinds); ``kind`` is the LLM-extractable edge kind; ``source_doc`` / ``span``
    carry the per-triple provenance forward to the written edge."""

    src_id: str
    dst_id: str
    kind: EdgeKind
    source_doc: str
    span: str


def _normalize(raw: str, kind: EntityKind, aliases: Mapping[str, str]) -> str | None:
    """Normalize ``raw`` to a canonical id via the endpoint kind's normalizer, or ``None`` for
    an unsupported endpoint kind (fail closed)."""
    fn = _NORMALIZERS.get(kind)
    if fn is None:
        return None
    if kind is EntityKind.PERSON:
        return person_id(raw, dict(aliases))
    return fn(raw)


def _ground_endpoint(
    raw: str, kind: EntityKind, graph: Graph, aliases: Mapping[str, str]
) -> str | None:
    """The canonical id for ``raw`` that is a node of ``kind`` in ``graph``, or ``None``.

    Tries the raw mention **as-is first** (so an already-canonical id from the model — e.g.
    ``sig:sig-network`` — is not double-normalized into ``sig:sig-sig-network``), then the
    normalized form (so a prose mention — ``SIG Network`` — resolves the same way the
    deterministic pass resolved it). Either way the chosen id must be present *and* of the
    expected kind — the no-invented-entity / no-wrong-kind guarantee."""
    seen: set[str] = set()
    for cid in (raw.strip(), _normalize(raw, kind, aliases)):
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        node = graph.get_node(cid)
        if node is not None and node.kind is kind:
            return cid
    return None


def ground_triple(
    triple: CandidateTriple,
    graph: Graph,
    *,
    schema: ExtractionSchema = EXTRACTION_SCHEMA,
    aliases: Mapping[str, str] | None = None,
) -> GroundedTriple | None:
    """Ground a validated candidate against ``graph`` (AC2), or ``None`` if it can't.

    The predicate selects **exactly one** ``(src, dst)`` endpoint-kind pair from the schema; if
    the schema maps the predicate to zero or more than one pair (off-schema or ambiguous), the
    triple is dropped (fail closed — never guess). Each endpoint is normalized by its kind's
    function and must resolve to a node **present in the graph and of the expected kind**."""
    predicate = triple.predicate.strip()
    matches = [e for e in schema.edges if e.kind.value == predicate]
    if len(matches) != 1:
        return None
    schema_edge = matches[0]
    alias_map = aliases or {}

    src_id = _ground_endpoint(triple.subject, schema_edge.src_kind, graph, alias_map)
    dst_id = _ground_endpoint(triple.object, schema_edge.dst_kind, graph, alias_map)
    if src_id is None or dst_id is None:
        return None

    return GroundedTriple(src_id, dst_id, schema_edge.kind, triple.source_doc, triple.span)
