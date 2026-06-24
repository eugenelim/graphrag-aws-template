"""T1 — graph model. The upsert-union is the resolution-merge primitive.

# STUB: AC4
"""

from __future__ import annotations

import pytest

from graphrag.model import Edge, EdgeKind, EntityKind, Graph, Node


def test_upsert_node_unions_sources_on_id_collision() -> None:
    g = Graph()
    g.upsert_node(Node("person:thockin", EntityKind.PERSON, {"name": "Tim Hockin"}, {"community"}))
    g.upsert_node(Node("person:thockin", EntityKind.PERSON, {}, {"enhancements"}))

    node = g.get_node("person:thockin")
    assert node is not None
    # One node, both sources — the cross-source merge, made visible.
    assert len(g.nodes) == 1
    assert node.sources == {"community", "enhancements"}
    assert node.props["name"] == "Tim Hockin"


def test_upsert_node_keeps_existing_prop_unless_new() -> None:
    g = Graph()
    g.upsert_node(Node("sig:sig-network", EntityKind.SIG, {"label": "network"}, {"community"}))
    g.upsert_node(
        Node("sig:sig-network", EntityKind.SIG, {"label": "net", "x": 1}, {"enhancements"})
    )
    node = g.get_node("sig:sig-network")
    assert node is not None
    assert node.props["label"] == "network"  # first writer wins
    assert node.props["x"] == 1  # new key added


def test_upsert_node_rejects_kind_reuse() -> None:
    g = Graph()
    g.upsert_node(Node("x", EntityKind.SIG))
    with pytest.raises(ValueError, match="reused across kinds"):
        g.upsert_node(Node("x", EntityKind.PERSON))


def test_upsert_edge_dedupes_and_unions_sources() -> None:
    g = Graph()
    g.upsert_edge(
        Edge("person:thockin", "sig:sig-network", EdgeKind.TECH_LEADS, sources={"community"})
    )
    g.upsert_edge(
        Edge("person:thockin", "sig:sig-network", EdgeKind.TECH_LEADS, sources={"enhancements"})
    )
    assert len(g.edges) == 1
    assert g.edges[0].sources == {"community", "enhancements"}
