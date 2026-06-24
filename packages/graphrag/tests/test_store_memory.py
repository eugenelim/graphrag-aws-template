"""T6 — in-memory graph store.

# STUB: AC7
"""

from __future__ import annotations

from graphrag.model import Direction, Edge, EdgeKind, EntityKind, Node
from graphrag.store import MemoryGraphStore


def _store() -> MemoryGraphStore:
    s = MemoryGraphStore()
    s.upsert_node(Node("person:thockin", EntityKind.PERSON, {"name": "Tim Hockin"}))
    s.upsert_node(Node("sig:sig-network", EntityKind.SIG))
    s.upsert_node(Node("kep-2086", EntityKind.KEP))
    s.upsert_edge(Edge("person:thockin", "sig:sig-network", EdgeKind.TECH_LEADS))
    s.upsert_edge(Edge("sig:sig-network", "kep-2086", EdgeKind.OWNS))
    return s


def test_neighbors_out() -> None:
    s = _store()
    sigs = s.neighbors("person:thockin", EdgeKind.TECH_LEADS, Direction.OUT)
    assert [n.id for n in sigs] == ["sig:sig-network"]


def test_neighbors_in() -> None:
    s = _store()
    leads = s.neighbors("sig:sig-network", EdgeKind.TECH_LEADS, Direction.IN)
    assert [n.id for n in leads] == ["person:thockin"]


def test_neighbors_filters_by_edge_kind() -> None:
    s = _store()
    # No CHAIRS edge exists, so this is empty even though a TECH_LEADS edge does.
    assert s.neighbors("person:thockin", EdgeKind.CHAIRS, Direction.OUT) == []


def test_get_node_and_all() -> None:
    s = _store()
    assert s.get_node("kep-2086") is not None
    assert s.get_node("missing") is None
    assert len(s.all_nodes()) == 3
    assert len(s.all_edges()) == 2
