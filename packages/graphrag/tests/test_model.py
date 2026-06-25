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


def test_upsert_node_unions_doc_paths_on_collision() -> None:
    # doc_paths is the slice-5 provenance set (the orphan-removal reference count); it unions
    # on collision exactly like sources, so a node contributed by several docs records them all.
    g = Graph()
    g.upsert_node(Node("sig:sig-node", EntityKind.SIG, doc_paths={"community/sigs.yaml"}))
    g.upsert_node(
        Node("sig:sig-node", EntityKind.SIG, doc_paths={"enhancements/keps/x/kep.yaml"})
    )
    node = g.get_node("sig:sig-node")
    assert node is not None
    assert node.doc_paths == {"community/sigs.yaml", "enhancements/keps/x/kep.yaml"}


def test_upsert_edge_unions_doc_paths_on_collision() -> None:
    g = Graph()
    g.upsert_edge(Edge("sig:s", "kep-1", EdgeKind.OWNS, doc_paths={"enhancements/keps/x/kep.yaml"}))
    g.upsert_edge(
        Edge("sig:s", "kep-1", EdgeKind.OWNS, doc_paths={"enhancements/keps/x/README.md"})
    )
    assert len(g.edges) == 1
    assert g.edges[0].doc_paths == {
        "enhancements/keps/x/kep.yaml",
        "enhancements/keps/x/README.md",
    }
