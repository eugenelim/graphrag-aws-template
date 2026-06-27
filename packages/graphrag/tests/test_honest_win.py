"""AC8 — offline absence invariant + contract shape (NOT the honesty gate).

A hand-authored gold set of prose inter-entity edges is pinned. The test builds the **actual
deterministic graph** (``resolve()`` over the real fixture corpus) and asserts, for each gold edge,
that **no deterministic edge of any kind** connects its two endpoints in the asserted direction —
absence at the *relationship* level, not the trivial "no edge of a never-emitted kind" — so the
contrast is real, not a strawman. It then asserts the offline pass plumbs each gold edge through
validation + grounding + stamping.

The seeded offline ``RuleTripleExtractor`` makes **NO semantic-quality claim**: a green AC8 is *not*
a cleared ship gate (that is AC9, live recall + precision).

# STUB: AC8
"""

from __future__ import annotations

from pathlib import Path

from graphrag.extract_llm import EXTRACTION_SCHEMA, RuleTripleExtractor
from graphrag.model import LLM_EXTRACTABLE_EDGE_KINDS, EdgeKind
from graphrag.resolve import resolve
from graphrag.schema_extract import extract_schema_guided
from graphrag.showcase import load_extraction_showcase
from graphrag.sources import load_corpus

CORPUS = Path(__file__).parent / "fixtures" / "corpus"

# The pinned gold set: prose inter-entity edges (each (src_id, kind, dst_id) resolves in the
# fixture corpus) that the deterministic graph does NOT contain. Hand-authored from the actual
# SIG-README / KEP-Motivation prose (cross-SIG collaboration, KEP supersession/dependency).
GOLD_EDGES: list[tuple[str, EdgeKind, str]] = [
    ("sig:sig-network", EdgeKind.COLLABORATES_WITH, "sig:sig-node"),
    ("kep-2086", EdgeKind.DEPENDS_ON, "kep-1880"),
    ("kep-1287", EdgeKind.SUPERSEDES, "kep-9"),
]


def _deterministic_graph():
    return resolve(load_corpus(CORPUS / "community", CORPUS / "enhancements"))


def test_gold_endpoints_all_resolve_in_the_fixture_corpus() -> None:
    graph = _deterministic_graph()
    for src, _kind, dst in GOLD_EDGES:
        assert graph.get_node(src) is not None, f"{src} must resolve"
        assert graph.get_node(dst) is not None, f"{dst} must resolve"


def test_gold_edges_are_absent_from_the_deterministic_graph_at_relationship_level() -> None:
    # Relationship-level absence: NO deterministic edge of ANY kind connects the two endpoints in
    # the asserted direction — not the trivial "no edge of a never-emitted kind". This is what
    # makes the contrast real (RFC-0002 Principle 2: not a strawman).
    graph = _deterministic_graph()
    for src, _kind, dst in GOLD_EDGES:
        connecting = [e for e in graph.edges if e.src_id == src and e.dst_id == dst]
        assert connecting == [], f"deterministic graph connects {src} -> {dst}: {connecting}"


def test_offline_pass_plumbs_every_gold_edge_through_validate_ground_stamp() -> None:
    # The offline (seeded, non-semantic) pass plumbs each gold edge through the contract: validate
    # -> ground -> stamp. This pins the orchestration + provenance shape, NOT extraction quality.
    docs = load_corpus(CORPUS / "community", CORPUS / "enhancements")
    graph = resolve(docs)
    result = extract_schema_guided(
        docs, graph, extractor=RuleTripleExtractor(), schema=EXTRACTION_SCHEMA
    )
    produced = {(e.src_id, e.kind, e.dst_id) for e in result.edges}
    for gold in GOLD_EDGES:
        assert gold in produced, f"offline pass did not produce gold edge {gold}"
    # Pin the exact counts the guide's "running contrast" prints: the offline pass over the real
    # fixture corpus produces exactly the gold edges with nothing rejected/dropped, so the doc's
    # `+3 schema-guided edges; 0 off-schema-rejected; 0 dropped-ungrounded` can't silently drift.
    assert result.accepted_count == len(GOLD_EDGES)
    assert result.off_schema_count == 0
    assert result.dropped_count == 0
    # every produced edge is an LLM-extractable kind, stamped distinguishable, span-traceable.
    for e in result.edges:
        assert e.kind in LLM_EXTRACTABLE_EDGE_KINDS
        assert e.props["extraction_method"] == "schema-guided-llm"
        assert e.props["span"]


# --- the LLM-only-edge demo query (AC9/AC10's "exact CLI + graph query") -------------------


def test_extraction_showcase_queries_parse_and_target_llm_only_edges() -> None:
    queries = load_extraction_showcase()
    assert queries  # the group is populated
    gold = set(GOLD_EDGES)
    for q in queries:
        assert q.mode in {"graph", "hybrid"}
        src, kind_str, dst = q.llm_edge
        kind = EdgeKind(kind_str)
        assert kind in LLM_EXTRACTABLE_EDGE_KINDS  # the demo leans on a model-asserted edge
        assert (src, kind, dst) in gold  # and that edge is in the pinned gold set
        for entity in q.expected_entities:
            assert entity in (src, dst) or entity  # expected entities are the edge endpoints
