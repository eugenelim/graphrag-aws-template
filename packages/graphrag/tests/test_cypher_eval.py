"""T4 — the bounded read-subset evaluator for offline text2cypher execution (AC4).

Node-by-id, nodes-by-kind, and one-hop REL (out/in) return the sorted fixture nodes (the
exemplar: sig:sig-network -OWNS-> KEPs); LIMIT is honored; anything outside the subset raises
UnsupportedOfflineQuery (the orchestrator surfaces it as "runs live, not offline" — never
false rows). It is a labeled SUBSET; live Neptune is the fidelity oracle.

# STUB: AC4
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag.cypher_eval import UnsupportedOfflineQuery, eval_read_query
from graphrag.resolve import resolve
from graphrag.sources import load_corpus
from graphrag.store import MemoryGraphStore


def _store(community_root: Path, enhancements_root: Path) -> MemoryGraphStore:
    return MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))


def test_out_hop_returns_sorted_owned_keps(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    rows = eval_read_query(
        "MATCH (a:Entity {id: 'sig:sig-network'})-[r:REL {kind: 'OWNS'}]->(n:Entity) "
        "RETURN n LIMIT 25",
        store,
    )
    assert [n.id for n in rows] == ["kep-1880", "kep-2086"]


def test_in_hop_returns_owning_sig(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    rows = eval_read_query(
        "MATCH (a:Entity {id: 'kep-2086'})<-[r:REL {kind: 'OWNS'}]-(n:Entity) RETURN n LIMIT 25",
        store,
    )
    assert [n.id for n in rows] == ["sig:sig-network"]


def test_node_by_id(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    rows = eval_read_query("MATCH (n:Entity {id: 'sig:sig-network'}) RETURN n", store)
    assert [n.id for n in rows] == ["sig:sig-network"]


def test_nodes_by_kind_are_sorted(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    rows = eval_read_query("MATCH (n:Entity) WHERE n.kind = 'KEP' RETURN n", store)
    ids = [n.id for n in rows]
    assert ids == sorted(ids)
    assert all(n.kind.value == "KEP" for n in rows)
    assert "kep-1880" in ids and "kep-2086" in ids


def test_limit_is_honored(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    rows = eval_read_query(
        "MATCH (a:Entity {id: 'sig:sig-network'})-[r:REL {kind: 'OWNS'}]->(n:Entity) "
        "RETURN n LIMIT 1",
        store,
    )
    assert [n.id for n in rows] == ["kep-1880"]  # the sorted-first of the two


def test_node_by_id_missing_returns_empty(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    assert eval_read_query("MATCH (n:Entity {id: 'sig:nope'}) RETURN n", store) == []


@pytest.mark.parametrize(
    "query",
    [
        # a two-hop pattern is outside the subset.
        "MATCH (a:Entity {id: 'x'})-[:REL]->(b:Entity)-[:REL]->(n:Entity) RETURN n LIMIT 5",
        # an aggregation is outside the subset.
        "MATCH (n:Entity) RETURN count(n) LIMIT 5",
        # an unknown edge kind in an otherwise-shaped hop.
        "MATCH (a:Entity {id: 'x'})-[r:REL {kind: 'BOGUS'}]->(n:Entity) RETURN n LIMIT 5",
    ],
)
def test_outside_subset_raises(query: str, community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    with pytest.raises(UnsupportedOfflineQuery):
        eval_read_query(query, store)
