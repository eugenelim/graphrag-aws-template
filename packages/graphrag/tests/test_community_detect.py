"""T1/T3 — community detection (Louvain, seeded) + per-community summarization.

# STUB: AC1 (detect_communities — Louvain in-process, seeded, pure, networkx-isolated)
# STUB: AC3 (summarize_communities — per-community summaries via the Synthesizer seam)
"""

from __future__ import annotations

import sys

from graphrag.model import Edge, EdgeKind, EntityKind, Node
from graphrag.visibility import Visibility


def _two_cluster_graph() -> tuple[list[Node], list[Edge]]:
    """Two clearly separable clusters joined by no edge — a network SIG triangle and a node
    SIG triangle — plus one isolated node. Louvain should find two (or three with the
    isolate) communities regardless of seed."""
    nodes = [
        Node("sig:sig-network", EntityKind.SIG),
        Node("person:thockin", EntityKind.PERSON),
        Node("kep-2086", EntityKind.KEP),
        Node("sig:sig-node", EntityKind.SIG),
        Node("person:dchen", EntityKind.PERSON),
        Node("kep-1287", EntityKind.KEP),
        Node("sig:sig-lonely", EntityKind.SIG),  # isolated — its own singleton community
    ]
    edges = [
        # network cluster
        Edge("person:thockin", "sig:sig-network", EdgeKind.TECH_LEADS),
        Edge("sig:sig-network", "kep-2086", EdgeKind.OWNS),
        Edge("person:thockin", "kep-2086", EdgeKind.AUTHORS),
        # node cluster
        Edge("person:dchen", "sig:sig-node", EdgeKind.CHAIRS),
        Edge("sig:sig-node", "kep-1287", EdgeKind.OWNS),
        Edge("person:dchen", "kep-1287", EdgeKind.AUTHORS),
    ]
    return nodes, edges


# --- AC1: detection ---------------------------------------------------------------------


def test_detect_partitions_every_node() -> None:
    from graphrag.community_detect import detect_communities

    nodes, edges = _two_cluster_graph()
    specs = detect_communities(nodes, edges)
    covered = {eid for spec in specs for eid in spec.entity_ids}
    assert covered == {n.id for n in nodes}
    # each node lands in exactly one community
    assert sum(spec.size for spec in specs) == len(nodes)


def test_detect_is_reproducible_under_the_seed() -> None:
    from graphrag.community_detect import detect_communities

    nodes, edges = _two_cluster_graph()
    first = detect_communities(nodes, edges, seed=42)
    second = detect_communities(nodes, edges, seed=42)
    # identical ids AND identical membership — the seed makes the partition reproducible
    assert [(s.id, s.entity_ids) for s in first] == [(s.id, s.entity_ids) for s in second]


def test_isolated_node_is_a_singleton_community() -> None:
    from graphrag.community_detect import detect_communities

    nodes, edges = _two_cluster_graph()
    specs = detect_communities(nodes, edges)
    singletons = [s for s in specs if s.entity_ids == ("sig:sig-lonely",)]
    assert len(singletons) == 1
    assert singletons[0].size == 1


def test_two_clusters_separate() -> None:
    from graphrag.community_detect import detect_communities

    nodes, edges = _two_cluster_graph()
    specs = detect_communities(nodes, edges)
    # the network triad and the node triad never share a community
    for spec in specs:
        members = set(spec.entity_ids)
        net = {"sig:sig-network", "person:thockin", "kep-2086"}
        nod = {"sig:sig-node", "person:dchen", "kep-1287"}
        assert not (members & net and members & nod)


def test_community_ids_are_stable_and_ordered_largest_first() -> None:
    from graphrag.community_detect import detect_communities

    nodes, edges = _two_cluster_graph()
    specs = detect_communities(nodes, edges)
    assert [s.id for s in specs] == [f"community-{i}" for i in range(len(specs))]
    # largest first
    sizes = [s.size for s in specs]
    assert sizes == sorted(sizes, reverse=True)


def test_tier_is_composed_most_restrictive_member() -> None:
    from graphrag.community_detect import detect_communities

    # one cluster blends a restricted member; the community tier must rise to restricted
    nodes = [
        Node("sig:a", EntityKind.SIG, {"visibility": Visibility.PUBLIC.value}),
        Node("kep-secret", EntityKind.KEP, {"visibility": Visibility.RESTRICTED.value}),
    ]
    edges = [Edge("sig:a", "kep-secret", EdgeKind.OWNS)]
    specs = detect_communities(nodes, edges)
    assert len(specs) == 1
    assert specs[0].tier == Visibility.RESTRICTED.value


def test_unlabeled_member_composes_as_public() -> None:
    from graphrag.community_detect import detect_communities

    # a member with NO visibility prop must compose as public (does not raise the tier) —
    # the deliberate teaching default, named so the down-classification is reviewed
    nodes = [
        Node("sig:a", EntityKind.SIG),  # no visibility prop
        Node("kep-b", EntityKind.KEP, {"visibility": Visibility.PUBLIC.value}),
    ]
    edges = [Edge("sig:a", "kep-b", EdgeKind.OWNS)]
    specs = detect_communities(nodes, edges)
    assert specs[0].tier == Visibility.PUBLIC.value


# --- AC3: summarization -----------------------------------------------------------------


class SpySynthesizer:
    """Records each synthesize call and returns a deterministic answer naming the facts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list, list]] = []

    @property
    def model_id(self) -> str:
        return "spy"

    def synthesize(self, question, context_chunks, graph_facts):
        from graphrag.synthesize import SynthesisResult

        self.calls.append((question, list(context_chunks), list(graph_facts)))
        ids = ",".join(n.id for n in graph_facts)
        return SynthesisResult(answer=f"summary[{ids}]", citations=[])


def test_summarize_one_call_per_community_over_member_subgraph() -> None:
    from graphrag.community_detect import detect_communities, summarize_communities

    nodes, edges = _two_cluster_graph()
    specs = detect_communities(nodes, edges)
    spy = SpySynthesizer()
    communities = summarize_communities(specs, nodes, edges, spy)

    # one synthesize call per community
    assert len(spy.calls) == len(specs)
    assert len(communities) == len(specs)
    # the context is the community's OWN members, never unrelated entities
    for spec, (_question, ctx, graph_facts) in zip(specs, spy.calls, strict=True):
        assert ctx == []  # community summaries use graph_facts, not chunk context
        assert {n.id for n in graph_facts} == set(spec.entity_ids)
    # the summary is the synthesized text; tier/size carried from the spec
    by_id = {c.id: c for c in communities}
    for spec in specs:
        c = by_id[spec.id]
        assert c.summary == f"summary[{','.join(spec.entity_ids)}]"
        assert c.tier == spec.tier and c.size == spec.size
        assert c.entity_ids == spec.entity_ids


def test_summarize_passes_relationships_as_data_in_the_question() -> None:
    from graphrag.community_detect import detect_communities, summarize_communities

    nodes, edges = _two_cluster_graph()
    specs = detect_communities(nodes, edges)
    spy = SpySynthesizer()
    summarize_communities(specs, nodes, edges, spy)
    # at least one community's question names an intra-community relationship (the subgraph)
    questions = " ".join(q for q, _c, _g in spy.calls)
    assert "-OWNS->" in questions


def test_summarize_offline_template_is_deterministic() -> None:
    from graphrag.community_detect import detect_communities, summarize_communities
    from graphrag.synthesize import TemplateSynthesizer

    nodes, edges = _two_cluster_graph()
    specs = detect_communities(nodes, edges)
    first = summarize_communities(specs, nodes, edges, TemplateSynthesizer())
    second = summarize_communities(specs, nodes, edges, TemplateSynthesizer())
    assert [(c.id, c.summary, c.title) for c in first] == [
        (c.id, c.summary, c.title) for c in second
    ]
    # title is member-derived (carries the whole-community gate)
    assert all(c.title for c in first)


def test_module_import_is_networkx_free() -> None:
    # importing community_detect must NOT pull in networkx (lazy import inside the function);
    # this is the ingest-side analogue of the PyYAML-free Lambda discipline.
    for mod in [m for m in sys.modules if m == "networkx" or m.startswith("networkx.")]:
        del sys.modules[mod]
    import importlib

    import graphrag.community_detect as cd

    importlib.reload(cd)
    assert "networkx" not in sys.modules
    # calling detection DOES import it
    cd.detect_communities([Node("sig:a", EntityKind.SIG)], [])
    assert "networkx" in sys.modules
