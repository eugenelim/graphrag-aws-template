"""The schema-guided extraction orchestrator + the per-triple replayable trace (AC4).

Wires the seam together: for each prose doc, ``extractor.extract`` proposes candidate triples;
each is validated against the closed schema (``validate_triple``, guard layer 1), grounded to
known graph entities (``ground_triple``, guard layer 2), and — if it passes both — written as an
``Edge`` stamped ``extraction_method: "schema-guided-llm"`` carrying its source-span provenance.

The returned ``ExtractionResult`` is the **audit artifact**: it records the schema shown to the
model, **every** candidate (accepted / off-schema-rejected / dropped-ungrounded) with its source
span and verdict, and the accepted edges. ``.render()`` narrates, in order,
**doc/span → candidate triple → verdict → resulting edge** — no black-box hop (charter principle
1). Persisted by the ingest phase as a replayable artifact (ADR-0005 affordance, extended to
per-triple provenance). Pure-Python, ingest-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .extract_llm import EXTRACTION_SCHEMA, CandidateTriple, ExtractionSchema, TripleExtractor
from .ground import ground_triple
from .model import EXTRACTION_METHOD_LLM, Edge, Graph
from .sources import ParsedDoc
from .validate_triple import validate_triple

# Verdict vocabulary recorded per candidate (the trace's audit verdicts).
VERDICT_ACCEPTED = "accepted"
VERDICT_OFF_SCHEMA = "off-schema-rejected"
VERDICT_DROPPED = "dropped-ungrounded"


@dataclass
class TraceEntry:
    """One candidate's provenance: the candidate, the verdict, the resulting edge (accepted
    only), and the reason it was rejected/dropped (off-schema / ungrounded only)."""

    candidate: CandidateTriple
    verdict: str
    edge: Edge | None = None
    reason: str | None = None


@dataclass
class ExtractionResult:
    """The replayable audit artifact of one schema-guided extraction pass (AC4).

    ``edges`` are the accepted edges (each ``props["extraction_method"] == "schema-guided-llm"``
    plus ``source_doc`` / ``span`` provenance props). ``entries`` is the full per-candidate trace
    (accepted *and* rejected/dropped). ``prompt`` is the schema block shown to the model, echoed
    so a presenter can replay exactly what constrained the extraction."""

    schema: ExtractionSchema
    prompt: str
    extractor_model_id: str
    entries: list[TraceEntry] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return sum(1 for e in self.entries if e.verdict == VERDICT_ACCEPTED)

    @property
    def off_schema_count(self) -> int:
        return sum(1 for e in self.entries if e.verdict == VERDICT_OFF_SCHEMA)

    @property
    def dropped_count(self) -> int:
        return sum(1 for e in self.entries if e.verdict == VERDICT_DROPPED)

    def render(self) -> str:
        """Narrate the audit trace in order: schema → per-candidate (doc/span → triple →
        verdict → resulting edge) → summary counts (charter principle 1)."""
        lines = [
            f"extractor: {self.extractor_model_id}",
            self.prompt,
            "candidates:",
        ]
        for entry in self.entries:
            cand = entry.candidate
            lines.append(f"  - doc: {cand.source_doc}")
            lines.append(f"    span: {cand.span}")
            lines.append(
                f"    triple: ({cand.subject}) -[{cand.predicate}]-> ({cand.object})"
            )
            verdict_line = f"    verdict: {entry.verdict}"
            if entry.reason:
                verdict_line += f" ({entry.reason})"
            lines.append(verdict_line)
            if entry.edge is not None:
                e = entry.edge
                lines.append(f"    edge: {e.src_id} -[{e.kind.value}]-> {e.dst_id}")
        lines.append(
            f"summary: +{self.accepted_count} schema-guided edges; "
            f"{self.off_schema_count} off-schema-rejected; "
            f"{self.dropped_count} dropped-ungrounded"
        )
        return "\n".join(lines)


def extract_schema_guided(
    docs: list[ParsedDoc],
    graph: Graph,
    *,
    extractor: TripleExtractor,
    schema: ExtractionSchema = EXTRACTION_SCHEMA,
    aliases: Mapping[str, str] | None = None,
) -> ExtractionResult:
    """Extract → validate → ground → stamp, returning the full per-triple trace (AC4).

    The model relates known entities; off-schema or ungrounded candidates are **recorded but
    never written**. Accepted edges carry the ``schema-guided-llm`` stamp + source-span
    provenance, and ``doc_paths`` from the source doc (so a delta that removes the source doc
    removes the edge like any other). Deterministic edges in ``graph`` are never touched —
    their kinds are disjoint from the LLM-extractable kinds, so no ``(src, kind, dst)`` key can
    collide (the stamp is set authoritatively, not ``setdefault``-merged)."""
    result = ExtractionResult(
        schema=schema, prompt=schema.render(), extractor_model_id=extractor.model_id
    )
    for doc in docs:
        for candidate in extractor.extract(doc, schema):
            validation = validate_triple(candidate, schema=schema)
            if not validation.ok:
                result.entries.append(
                    TraceEntry(candidate, VERDICT_OFF_SCHEMA, reason=validation.violated_rule)
                )
                continue
            grounded = ground_triple(candidate, graph, schema=schema, aliases=aliases)
            if grounded is None:
                result.entries.append(
                    TraceEntry(
                        candidate,
                        VERDICT_DROPPED,
                        reason="endpoint(s) not grounded to a known entity",
                    )
                )
                continue
            edge = Edge(
                grounded.src_id,
                grounded.dst_id,
                grounded.kind,
                props={
                    "extraction_method": EXTRACTION_METHOD_LLM,
                    "source_doc": grounded.source_doc,
                    "span": grounded.span,
                },
                doc_paths={grounded.source_doc},
            )
            result.edges.append(edge)
            result.entries.append(TraceEntry(candidate, VERDICT_ACCEPTED, edge=edge))
    return result
