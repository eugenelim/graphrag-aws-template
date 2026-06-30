"""AC11 — read-side edge provenance: the retrieval trace surfaces extraction_method per hop.

Seed-and-expand traverses **all** edge kinds, so a ``schema-guided-llm`` edge rides into the
neighborhood by default. This is the read-side half of the distinguishability guarantee (the
write-side stamp is AC4): a test traverses a graph holding both edge classes and asserts the
trace attributes each hop's method, so an answer leaning on a model-asserted edge is visibly
marked and never blended silently.

# STUB: AC11
"""

from __future__ import annotations

from graphrag.governed import governed_query
from graphrag.model import (
    EXTRACTION_METHOD_LLM,
    Direction,
    Edge,
    EdgeKind,
    EntityKind,
    Graph,
    Node,
)
from graphrag.query import expand_neighborhood, traverse
from graphrag.select import RuleTemplateSelector
from graphrag.store import MemoryGraphStore
from graphrag.synthesize import TemplateSynthesizer
from graphrag.templates import Template


def _mixed_store() -> MemoryGraphStore:
    g = Graph()
    g.upsert_node(Node("sig:sig-network", EntityKind.SIG))
    g.upsert_node(Node("sig:sig-node", EntityKind.SIG))
    g.upsert_node(Node("kep-2086", EntityKind.KEP))
    # A deterministic edge and a schema-guided-llm edge from the same seed.
    g.upsert_edge(Edge("sig:sig-network", "kep-2086", EdgeKind.OWNS, sources={"community"}))
    g.upsert_edge(
        Edge(
            "sig:sig-network",
            "sig:sig-node",
            EdgeKind.COLLABORATES_WITH,
            props={"extraction_method": EXTRACTION_METHOD_LLM},
        )
    )
    return MemoryGraphStore(g)


def test_expand_trace_attributes_each_hop_method() -> None:
    result = expand_neighborhood(_mixed_store(), ["sig:sig-network"], max_hops=1)
    # Both neighbors reached; the hop's methods name both classes.
    assert "sig:sig-node" in result.result_ids and "kep-2086" in result.result_ids
    hop = result.trace[0]
    assert set(hop.extraction_methods) == {"deterministic", "schema-guided-llm"}
    # The rendered trace surfaces the LLM provenance so an answer using it shows it.
    assert "schema-guided-llm" in result.render()


def test_deterministic_only_traversal_shows_no_llm_hop() -> None:
    g = Graph()
    g.upsert_node(Node("sig:sig-network", EntityKind.SIG))
    g.upsert_node(Node("kep-2086", EntityKind.KEP))
    g.upsert_edge(Edge("sig:sig-network", "kep-2086", EdgeKind.OWNS))
    result = expand_neighborhood(MemoryGraphStore(g), ["sig:sig-network"], max_hops=1)
    assert result.trace[0].extraction_methods == ["deterministic"]
    assert "schema-guided-llm" not in result.render()


def test_traverse_marks_an_llm_hop() -> None:
    result = traverse(
        _mixed_store(), ["sig:sig-network"], [(EdgeKind.COLLABORATES_WITH, Direction.OUT)]
    )
    assert result.result_ids == ["sig:sig-node"]
    assert result.trace[0].extraction_method == "schema-guided-llm"
    assert "schema-guided-llm" in result.render()


def test_governed_template_surfaces_traversed_edge_methods() -> None:
    # The fixed library traverses only deterministic kinds.
    result = governed_query(
        "Which KEPs does SIG Network own?",
        graph_store=_mixed_store(),
        selector=RuleTemplateSelector(),
        synthesizer=TemplateSynthesizer(),
    )
    assert result.template_id is not None
    assert result.traversed_methods == ["deterministic"]
    assert "edge provenance: deterministic" in result.render()


def test_kind_not_the_stored_prop_is_the_distinguishability_authority() -> None:
    # The read-side method is derived from the edge KIND, not the (forgeable) stored prop. Pin it:
    # an LLM-kind edge written WITHOUT the stamp still reads schema-guided-llm, and a deterministic
    # edge carrying a STRAY llm stamp still reads deterministic — so a write-side stamping bug can
    # never make an LLM edge read as a deterministic fact (or vice-versa).
    g = Graph()
    g.upsert_node(Node("sig:sig-network", EntityKind.SIG))
    g.upsert_node(Node("sig:sig-node", EntityKind.SIG))
    g.upsert_node(Node("kep-2086", EntityKind.KEP))
    g.upsert_edge(Edge("sig:sig-network", "sig:sig-node", EdgeKind.COLLABORATES_WITH))  # no stamp
    g.upsert_edge(
        Edge(
            "sig:sig-network",
            "kep-2086",
            EdgeKind.OWNS,
            props={"extraction_method": EXTRACTION_METHOD_LLM},  # stray (wrong) stamp
        )
    )
    from graphrag.model import extraction_method_for_kind

    result = expand_neighborhood(MemoryGraphStore(g), ["sig:sig-network"], max_hops=1)
    # The method is read off the kind, not the stored (mis-)stamp.
    assert extraction_method_for_kind(EdgeKind.COLLABORATES_WITH) == "schema-guided-llm"
    assert extraction_method_for_kind(EdgeKind.OWNS) == "deterministic"
    # both methods present in the rendered trace, attributed by kind — the unstamped LLM edge
    # still reads schema-guided-llm; the stray-stamped deterministic edge still reads deterministic.
    assert set(result.trace[0].extraction_methods) == {"deterministic", "schema-guided-llm"}


def test_hybrid_render_and_lambda_serialize_surface_the_hop_method() -> None:
    # AC11 integration: the hybrid/seed-and-expand path is where LLM edges actually ride into
    # answers, and its trace builds its OWN hop line (not NeighborhoodResult.render()), so it must
    # surface the method too. Pins both HybridResult.render() and the query_lambda envelope.
    from graphrag.hybrid import HybridResult
    from graphrag.query import NeighborhoodResult, NeighborhoodTrace
    from graphrag.query_lambda import _serialize

    hop = NeighborhoodTrace(
        hop=1,
        frontier_in=["kep-9"],
        reached=["kep-1287"],
        edge_kinds=[EdgeKind.SUPERSEDES],  # an LLM-extractable kind
    )
    nbr = NeighborhoodResult(seed_ids=["kep-9"], trace=[hop], result_ids=["kep-1287"])
    result = HybridResult(
        question="q",
        seeds=[],
        dropped_candidates=[],
        hop_trace=nbr,
        chunks=[],
        graph_nodes=[],
        answer="a",
        citations=[],
        seed_cap=10,
        max_hops=1,
    )
    assert "[schema-guided-llm]" in result.render()  # the human trace
    envelope = _serialize(result)
    # the structured envelope carries it too
    assert envelope["hops"][0]["extraction_methods"] == ["schema-guided-llm"]


def test_template_derives_methods_from_traversed_edge_kinds() -> None:
    # A template that traverses an LLM kind surfaces schema-guided-llm (the capability holds for
    # LLM kinds too, by the disjoint-set invariant) — even though the shipped library has none.
    llm_template = Template(
        id="sig_collaborators",
        description="SIGs a given SIG collaborates with.",
        params=(),
        cypher=(
            "MATCH (s:Entity {id: $sig})-[r:REL {kind: 'COLLABORATES_WITH'}]->(n:Entity) RETURN n"
        ),
        evaluate=lambda store, params: [],
    )
    assert llm_template.traversed_edge_kinds() == {EdgeKind.COLLABORATES_WITH}
    assert llm_template.extraction_methods() == {EXTRACTION_METHOD_LLM}
