"""AC4 — extraction orchestration with a full per-triple replayable trace.

Offline (``RuleTripleExtractor`` + a fixture graph) on the exemplar: validate → ground → stamp,
with every candidate's source span + verdict recorded and ``.render()`` narrating in order. An
off-schema candidate is recorded ``off-schema-rejected``; an ungrounded one ``dropped-ungrounded``;
deterministic edges in the same graph are untouched.

# STUB: AC4
"""

from __future__ import annotations

from graphrag.extract_llm import (
    EXTRACTION_SCHEMA,
    CandidateTriple,
    ExtractionSchema,
    RuleTripleExtractor,
)
from graphrag.model import EXTRACTION_METHOD_LLM, Edge, EdgeKind, EntityKind, Graph, Node
from graphrag.parse import ParsedMarkdown
from graphrag.schema_extract import ExtractionResult, extract_schema_guided, ground_candidates
from graphrag.sources import COMMUNITY, ParsedDoc


def _graph_with_deterministic_edge() -> Graph:
    g = Graph()
    g.upsert_node(Node("sig:sig-network", EntityKind.SIG))
    g.upsert_node(Node("sig:sig-node", EntityKind.SIG))
    g.upsert_node(Node("kep-2086", EntityKind.KEP))
    # A pre-existing deterministic edge in the same graph (untouched by the pass).
    g.upsert_edge(Edge("sig:sig-network", "kep-2086", EdgeKind.OWNS, sources={COMMUNITY}))
    return g


def _sig_doc(body: str) -> ParsedDoc:
    return ParsedDoc(
        COMMUNITY,
        "sig-network/README.md",
        "sig_readme",
        payload={"slug": "sig-network"},
        markdown=ParsedMarkdown(front_matter={}, headings=[], body=body),
    )


class _ScriptedExtractor:
    """Returns a fixed candidate list (so off-schema / ungrounded paths are exercisable)."""

    def __init__(self, candidates: list[CandidateTriple]) -> None:
        self._candidates = candidates

    @property
    def model_id(self) -> str:
        return "scripted (test)"

    def extract(self, doc: ParsedDoc, schema: ExtractionSchema) -> list[CandidateTriple]:
        return [c for c in self._candidates if c.source_doc == doc.doc_id]


def test_accepted_candidate_is_validated_grounded_and_stamped() -> None:
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("SIG Network collaborates closely with SIG Node on routing.")
    result = extract_schema_guided(
        [doc], graph, extractor=RuleTripleExtractor(), schema=EXTRACTION_SCHEMA
    )

    assert isinstance(result, ExtractionResult)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert (edge.src_id, edge.kind, edge.dst_id) == (
        "sig:sig-network",
        EdgeKind.COLLABORATES_WITH,
        "sig:sig-node",
    )
    assert edge.props["extraction_method"] == EXTRACTION_METHOD_LLM
    assert edge.props["source_doc"] == doc.doc_id
    assert edge.props["span"]
    assert doc.doc_id in edge.doc_paths

    assert [e.verdict for e in result.entries] == ["accepted"]
    assert result.entries[0].edge is edge


def test_off_schema_and_ungrounded_candidates_are_recorded_without_edges() -> None:
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("prose")
    candidates = [
        CandidateTriple("person:thockin", "AUTHORS", "kep-2086", doc.doc_id, "off-schema span"),
        CandidateTriple(
            "SIG Network", "COLLABORATES_WITH", "SIG Storage", doc.doc_id, "ungrounded span"
        ),
    ]
    result = extract_schema_guided(
        [doc], graph, extractor=_ScriptedExtractor(candidates), schema=EXTRACTION_SCHEMA
    )
    verdicts = {(e.candidate.span, e.verdict) for e in result.entries}
    assert ("off-schema span", "off-schema-rejected") in verdicts
    assert ("ungrounded span", "dropped-ungrounded") in verdicts
    assert result.edges == []  # nothing written
    assert all(e.edge is None for e in result.entries)


def test_deterministic_edges_are_untouched() -> None:
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("SIG Network collaborates closely with SIG Node.")
    extract_schema_guided([doc], graph, extractor=RuleTripleExtractor(), schema=EXTRACTION_SCHEMA)
    det = next(e for e in graph.edges if e.kind is EdgeKind.OWNS)
    assert "extraction_method" not in det.props  # deterministic edge unstamped (read-derived)


def test_render_narrates_doc_span_triple_verdict_edge_in_order() -> None:
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("SIG Network collaborates closely with SIG Node on routing.")
    result = extract_schema_guided(
        [doc], graph, extractor=RuleTripleExtractor(), schema=EXTRACTION_SCHEMA
    )
    text = result.render()
    assert "EXTRACTION SCHEMA" in text  # the schema shown is echoed
    assert "non-semantic" in text  # the extractor label
    assert "sig-network/README.md" in text  # the source doc/span
    assert "COLLABORATES_WITH" in text
    assert "accepted" in text
    # Ordering: doc/span appears before its verdict appears before the resulting edge.
    assert text.index("collaborates") < text.index("accepted")


def test_summary_counts_are_reported() -> None:
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("prose")
    candidates = [
        CandidateTriple("sig:sig-network", "COLLABORATES_WITH", "sig:sig-node", doc.doc_id, "ok"),
        CandidateTriple("x", "AUTHORS", "y", doc.doc_id, "bad"),
    ]
    result = extract_schema_guided(
        [doc], graph, extractor=_ScriptedExtractor(candidates), schema=EXTRACTION_SCHEMA
    )
    assert result.accepted_count == 1
    assert result.off_schema_count == 1
    assert result.dropped_count == 0


def test_orchestrator_calls_extractor_exactly_once_per_doc() -> None:
    # The denial-of-wallet bound (LLM10) is "one call per doc": the orchestrator loops docs once
    # with no per-candidate re-call / retry, so call count scales with corpus size only.
    graph = _graph_with_deterministic_edge()
    docs = [_sig_doc("prose a"), _sig_doc("prose b"), _sig_doc("prose c")]
    calls: list[str] = []

    class _CountingExtractor:
        @property
        def model_id(self) -> str:
            return "counting (test)"

        def extract(self, doc: ParsedDoc, schema: ExtractionSchema) -> list[CandidateTriple]:
            calls.append(doc.doc_id)
            return []

    extract_schema_guided(docs, graph, extractor=_CountingExtractor(), schema=EXTRACTION_SCHEMA)
    # all three docs share a doc_id (same _sig_doc path) but extract is still invoked per element.
    assert len(calls) == len(docs)


# --- medallion-staging T2b: the carved ground_candidates seam --------------------------


def test_ground_candidates_matches_extract_schema_guided_with_zero_extractor_calls() -> None:
    # Characterization: grounding the candidates the extractor WOULD produce yields edges
    # byte-identical to the full extract_schema_guided, and ground_candidates calls no extractor.
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("SIG Network collaborates closely with SIG Node on routing.")
    extractor = RuleTripleExtractor()
    full = extract_schema_guided([doc], graph, extractor=extractor, schema=EXTRACTION_SCHEMA)

    # Reproduce exactly what extract_schema_guided gathers (one extract() call per doc).
    candidates = list(extractor.extract(doc, EXTRACTION_SCHEMA))
    entries, edges = ground_candidates(candidates, graph, schema=EXTRACTION_SCHEMA)

    assert [(e.src_id, e.kind, e.dst_id) for e in edges] == [
        (e.src_id, e.kind, e.dst_id) for e in full.edges
    ]
    assert [(e.props["source_doc"], e.props["span"]) for e in edges] == [
        (e.props["source_doc"], e.props["span"]) for e in full.edges
    ]
    assert [e.verdict for e in entries] == [e.verdict for e in full.entries]


def test_ground_candidates_edge_set_is_order_independent() -> None:
    # The staged path reloads candidates per-doc, not in one extract pass; the edge SET must be
    # stable across candidate orderings (the store reconciles by (src, kind, dst) key).
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("x")
    # Two candidates that BOTH ground to distinct accepted edges (opposite-direction SIG↔SIG
    # collaboration), so the set comparison has real content on both sides of the ordering.
    candidates = [
        CandidateTriple("sig:sig-network", "COLLABORATES_WITH", "sig:sig-node", doc.doc_id, "s1"),
        CandidateTriple("sig:sig-node", "COLLABORATES_WITH", "sig:sig-network", doc.doc_id, "s2"),
    ]
    _, edges_a = ground_candidates(candidates, graph, schema=EXTRACTION_SCHEMA)
    _, edges_b = ground_candidates(list(reversed(candidates)), graph, schema=EXTRACTION_SCHEMA)
    keys_a = {(e.src_id, e.kind, e.dst_id) for e in edges_a}
    assert len(keys_a) == 2  # both candidates grounded to distinct edges
    assert keys_a == {(e.src_id, e.kind, e.dst_id) for e in edges_b}


def test_extract_schema_guided_trace_unchanged_for_multi_candidate_doc() -> None:
    # The refactor must not reorder the trace: ground_candidates preserves input order, so the
    # doc-then-extractor entry order (and render() bytes) is identical to the pre-carve behavior.
    graph = _graph_with_deterministic_edge()
    doc = _sig_doc("x")
    candidates = [
        CandidateTriple("sig:sig-network", "COLLABORATES_WITH", "sig:sig-node", doc.doc_id, "ok"),
        CandidateTriple("x", "AUTHORS", "y", doc.doc_id, "off"),  # off-schema, recorded after
    ]
    result = extract_schema_guided(
        [doc], graph, extractor=_ScriptedExtractor(candidates), schema=EXTRACTION_SCHEMA
    )
    # Entries follow input order: accepted (the COLLABORATES_WITH) then off-schema-rejected.
    assert [e.verdict for e in result.entries] == ["accepted", "off-schema-rejected"]
    assert result.render().index("ok") < result.render().index("off")
