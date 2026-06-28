"""medallion-staging T3 — `plan_graph_delta` / `apply_graph_delta` reproduce `_reconcile_graph`.

The characterization oracle `_reconcile_graph_v0` is a frozen copy of the pre-refactor inline
reconciliation (`ingest.py` before T3). For a representative mixed delta, `apply(plan(...))` must
yield a byte-identical store state **and the same multiset of mutating calls** — an unchanged row
triggers no `replace_*`, and an orphan edge incident to a deleted node is removed by the cascade,
not a separate `delete_edge`. `plan_graph_delta` performs no store mutation.
"""

from __future__ import annotations

from collections import Counter

from graphrag.graphdelta import (
    _reconcile_edge,
    _reconcile_node,
    apply_graph_delta,
    plan_graph_delta,
)
from graphrag.model import Edge, EdgeKind, EntityKind, Graph, Node
from graphrag.store.base import GraphStore
from graphrag.store.memory import MemoryGraphStore

# --- the frozen pre-refactor oracle ---------------------------------------------------


def _reconcile_graph_v0(store: GraphStore, scratch: Graph, removed_ids: set[str]) -> int:
    """Frozen copy of the pre-T3 inline `_reconcile_graph` — the characterization oracle."""
    orphans = 0
    store_nodes = {n.id: n for n in store.all_nodes()}
    scratch_nodes = dict(scratch.nodes)
    for node_id in set(store_nodes) | set(scratch_nodes):
        store_node = store_nodes.get(node_id)
        scratch_node = scratch_nodes.get(node_id)
        surviving = (store_node.doc_paths - removed_ids if store_node else set()) | (
            scratch_node.doc_paths if scratch_node else set()
        )
        if not surviving:
            store.delete_node(node_id)
            orphans += 1
            continue
        node_target = _reconcile_node(store_node, scratch_node, surviving)
        if store_node is None or not _node_unchanged_v0(store_node, node_target):
            store.replace_node(node_target)

    store_edges = {e.key(): e for e in store.all_edges()}
    scratch_edges = {e.key(): e for e in scratch.edges}
    for key in set(store_edges) | set(scratch_edges):
        store_edge = store_edges.get(key)
        scratch_edge = scratch_edges.get(key)
        surviving = (store_edge.doc_paths - removed_ids if store_edge else set()) | (
            scratch_edge.doc_paths if scratch_edge else set()
        )
        if not surviving:
            if store_edge is not None:
                store.delete_edge(store_edge.src_id, store_edge.kind, store_edge.dst_id)
                orphans += 1
            continue
        edge_target = _reconcile_edge(store_edge, scratch_edge, surviving)
        if store_edge is None or not _edge_unchanged_v0(store_edge, edge_target):
            store.replace_edge(edge_target)
    return orphans


def _node_unchanged_v0(store_node: Node, target: Node) -> bool:
    return (
        store_node.kind == target.kind
        and store_node.doc_paths == target.doc_paths
        and store_node.sources == target.sources
        and store_node.props == target.props
    )


def _edge_unchanged_v0(store_edge: Edge, target: Edge) -> bool:
    return (
        store_edge.doc_paths == target.doc_paths
        and store_edge.sources == target.sources
        and store_edge.props == target.props
    )


# --- a call-recording store -----------------------------------------------------------


class _SpyStore(MemoryGraphStore):
    """A MemoryGraphStore that records every mutating call (for call-multiset parity)."""

    def __init__(self, graph: Graph | None = None) -> None:
        super().__init__(graph)
        self.calls: list[tuple[str, object]] = []

    def replace_node(self, node: Node) -> None:
        self.calls.append(("replace_node", node.id))
        super().replace_node(node)

    def replace_edge(self, edge: Edge) -> None:
        self.calls.append(("replace_edge", edge.key()))
        super().replace_edge(edge)

    def delete_node(self, node_id: str) -> None:
        self.calls.append(("delete_node", node_id))
        super().delete_node(node_id)

    def delete_edge(self, src_id: str, kind: EdgeKind, dst_id: str) -> None:
        self.calls.append(("delete_edge", (src_id, kind.value, dst_id)))
        super().delete_edge(src_id, kind, dst_id)


def _node(nid: str, kind: EntityKind, doc: str, **props: object) -> Node:
    return Node(nid, kind, props=dict(props), sources={"src"}, doc_paths={doc})


def _edge(src: str, dst: str, kind: EdgeKind, doc: str) -> Edge:
    return Edge(src, dst, kind, sources={"src"}, doc_paths={doc})


def _node_snap(store: GraphStore) -> dict[str, object]:
    return {
        n.id: (n.kind, frozenset(n.doc_paths), frozenset(n.sources), tuple(sorted(n.props.items())))
        for n in store.all_nodes()
    }


def _edge_snap(store: GraphStore) -> dict[object, object]:
    return {e.key(): (frozenset(e.doc_paths), frozenset(e.sources)) for e in store.all_edges()}


def _seed_store(store: GraphStore) -> None:
    """A small seeded store: two SIGs, one KEP, an OWNS edge and a COLLABORATES_WITH edge."""
    store.replace_node(_node("sig:a", EntityKind.SIG, "src/a", x=1))
    store.replace_node(_node("sig:b", EntityKind.SIG, "src/b"))
    store.replace_node(_node("kep-1", EntityKind.KEP, "src/k"))
    store.replace_edge(_edge("sig:a", "kep-1", EdgeKind.OWNS, "src/a"))
    store.replace_edge(_edge("sig:a", "sig:b", EdgeKind.COLLABORATES_WITH, "src/c"))


def _mixed_scratch() -> Graph:
    """A scratch graph: changes sig:a's props, adds a new node+edge, leaves sig:b untouched."""
    g = Graph()
    g.upsert_node(_node("sig:a", EntityKind.SIG, "src/a", x=2))
    g.upsert_node(_node("sig:c", EntityKind.SIG, "src/cc"))
    g.upsert_edge(_edge("sig:a", "sig:c", EdgeKind.COLLABORATES_WITH, "src/cc"))
    return g


def test_apply_plan_matches_v0_final_state_and_call_multiset() -> None:
    # Delta: src/k removed (orphans kep-1 + its OWNS edge by cascade); src/c removed (orphans the
    # COLLABORATES_WITH a↔b edge whose endpoints both survive); sig:a props changed; sig:c added.
    removed_ids = {"src/k", "src/c"}

    new_store = _SpyStore()
    _seed_store(new_store)
    new_store.calls.clear()  # ignore the seeding writes
    delta = plan_graph_delta(new_store, _mixed_scratch(), removed_ids)
    n_orphans = apply_graph_delta(new_store, delta)

    v0_store = _SpyStore()
    _seed_store(v0_store)
    v0_store.calls.clear()
    v0_orphans = _reconcile_graph_v0(v0_store, _mixed_scratch(), removed_ids)

    assert n_orphans == v0_orphans
    # Same multiset of mutating calls (order may differ: v0 interleaves, the new path batches).
    assert Counter(new_store.calls) == Counter(v0_store.calls)
    # Byte-identical final state.
    assert _node_snap(new_store) == _node_snap(v0_store)
    assert _edge_snap(new_store) == _edge_snap(v0_store)


def test_unchanged_rows_trigger_no_replace() -> None:
    # The no-op optimization: an empty delta (scratch == store, nothing removed) writes nothing.
    store = _SpyStore()
    _seed_store(store)
    store.calls.clear()
    scratch = Graph()  # nothing re-extracted; nothing removed
    orphans = apply_graph_delta(store, plan_graph_delta(store, scratch, set()))
    assert orphans == 0
    assert store.calls == []  # zero mutating calls on a no-op delta


def test_only_the_changed_row_is_replaced() -> None:
    store = _SpyStore()
    _seed_store(store)
    store.calls.clear()
    # Scratch re-asserts sig:a with a changed prop and re-asserts sig:b UNCHANGED.
    scratch = Graph()
    scratch.upsert_node(_node("sig:a", EntityKind.SIG, "src/a", x=9))
    scratch.upsert_node(_node("sig:b", EntityKind.SIG, "src/b"))
    apply_graph_delta(store, plan_graph_delta(store, scratch, {"src/a", "src/b"}))
    replaced = [c for c in store.calls if c[0] == "replace_node"]
    assert replaced == [("replace_node", "sig:a")]  # sig:b unchanged → not rewritten


def test_plan_graph_delta_performs_no_store_mutation() -> None:
    store = _SpyStore()
    _seed_store(store)
    store.calls.clear()
    plan_graph_delta(store, _mixed_scratch(), {"src/k", "src/c"})
    assert store.calls == []  # planning is pure — zero writes
