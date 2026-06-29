"""`GraphDelta` — the graph store mutation, planned before it is applied (medallion-staging T3).

`plan_graph_delta` is **pure**: it reads the store and the freshly-resolved scratch graph and
returns a `GraphDelta` (the upserts and deletes the reconciliation implies) without mutating
anything. `apply_graph_delta` is the **only** mutating step. This carves the reconciliation that
lived inline in `ingest._reconcile_graph` into a plan/apply pair, so the mutation is inspectable
before it lands and the staged driver (Gold) can reason about the change set.

The pair reproduces `_reconcile_graph` exactly — **byte-identical store state and the same set of
mutating calls**: an unchanged row triggers no `replace_*` (the no-op optimization is preserved),
and an edge incident to a deleted node is removed by the node-delete cascade rather than a
separate `delete_edge` (so the orphan count matches). `_reconcile_graph` is retained as the thin
composition `apply_graph_delta(store, plan_graph_delta(...))`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Edge, EdgeKind, Graph, Node
from .store.base import GraphStore


def _sources_of(doc_paths: set[str]) -> set[str]:
    """The source tags a provenance set implies — the ``{source}`` prefix of each doc id, so
    reconciled ``sources`` match a full rebuild's exactly (slice 5)."""
    return {did.split("/", 1)[0] for did in doc_paths}


def _reconcile_node(
    store_node: Node | None, scratch_node: Node | None, surviving: set[str]
) -> Node:
    """The exact target node: ``doc_paths`` = surviving set, ``sources`` derived from it, props
    last-writer-wins (a changed/added document overrides the store's), id/kind from whichever
    side is present (a store-only survivor keeps its props; only ``doc_paths``/``sources`` shrink).
    Caller guarantees at least one side is present and ``surviving`` is non-empty."""
    ref = scratch_node or store_node
    if ref is None:  # pragma: no cover - caller guarantees one side
        raise ValueError("_reconcile_node needs at least one node")
    props: dict[str, object] = dict(store_node.props) if store_node else {}
    if scratch_node is not None:
        props.update(scratch_node.props)  # the changed/added document overrides
    return Node(ref.id, ref.kind, props=props, sources=_sources_of(surviving), doc_paths=surviving)


def _reconcile_edge(
    store_edge: Edge | None, scratch_edge: Edge | None, surviving: set[str]
) -> Edge:
    """The exact target edge — the edge twin of :func:`_reconcile_node`."""
    ref = scratch_edge or store_edge
    if ref is None:  # pragma: no cover - caller guarantees one side
        raise ValueError("_reconcile_edge needs at least one edge")
    props: dict[str, object] = dict(store_edge.props) if store_edge else {}
    if scratch_edge is not None:
        props.update(scratch_edge.props)
    return Edge(
        ref.src_id,
        ref.dst_id,
        ref.kind,
        props=props,
        sources=_sources_of(surviving),
        doc_paths=surviving,
    )


def _node_unchanged(store_node: Node, target: Node) -> bool:
    return (
        store_node.kind == target.kind
        and store_node.doc_paths == target.doc_paths
        and store_node.sources == target.sources
        and store_node.props == target.props
    )


def _edge_unchanged(store_edge: Edge, target: Edge) -> bool:
    return (
        store_edge.doc_paths == target.doc_paths
        and store_edge.sources == target.sources
        and store_edge.props == target.props
    )


@dataclass
class GraphDelta:
    """The planned store mutation: the exact rows to replace and to delete.

    ``upsert_nodes``/``upsert_edges`` hold **only rows that actually changed** (a store-only
    survivor whose provenance/props are unchanged is omitted — the no-op optimization).
    ``delete_*`` hold the orphans whose surviving provenance went empty; an edge incident to a
    deleted node is **not** listed (the node-delete cascade removes it)."""

    upsert_nodes: list[Node] = field(default_factory=list)
    upsert_edges: list[Edge] = field(default_factory=list)
    delete_nodes: list[str] = field(default_factory=list)
    delete_edges: list[tuple[str, EdgeKind, str]] = field(default_factory=list)


def plan_graph_delta(store: GraphStore, scratch: Graph, removed_ids: set[str]) -> GraphDelta:
    """Plan the reconciliation as a `GraphDelta` **without mutating the store** (pure).

    For every node/edge currently in the store or freshly extracted, the surviving provenance is
    ``(store.doc_paths - removed_ids) | scratch.doc_paths`` — the union is computed **before** the
    empty-check, so a changed/moved document (which both removes and re-adds) never transiently
    orphans a node a surviving document still contributes. Empty surviving set → orphan (delete);
    otherwise the exact target is an upsert **only when it actually changed**.

    The edge phase sees the store **as if the node-delete cascade had already run** — an edge
    incident to a deleted node is excluded, mirroring `_reconcile_graph` recomputing ``store_edges``
    after deleting the orphan nodes — so it is removed by the cascade, not double-deleted or
    double-counted.
    """
    delta = GraphDelta()
    store_nodes = {n.id: n for n in store.all_nodes()}
    scratch_nodes = dict(scratch.nodes)
    deleted_node_ids: set[str] = set()
    for node_id in set(store_nodes) | set(scratch_nodes):
        store_node = store_nodes.get(node_id)
        scratch_node = scratch_nodes.get(node_id)
        surviving = (store_node.doc_paths - removed_ids if store_node else set()) | (
            scratch_node.doc_paths if scratch_node else set()
        )
        if not surviving:
            delta.delete_nodes.append(node_id)
            deleted_node_ids.add(node_id)
            continue
        node_target = _reconcile_node(store_node, scratch_node, surviving)
        if store_node is None or not _node_unchanged(store_node, node_target):
            delta.upsert_nodes.append(node_target)

    # Exclude edges the node-delete cascade will remove (incident to a deleted node), so the edge
    # decisions match _reconcile_graph's post-cascade recompute of store_edges.
    store_edges = {
        e.key(): e
        for e in store.all_edges()
        if e.src_id not in deleted_node_ids and e.dst_id not in deleted_node_ids
    }
    scratch_edges = {e.key(): e for e in scratch.edges}
    for key in set(store_edges) | set(scratch_edges):
        store_edge = store_edges.get(key)
        scratch_edge = scratch_edges.get(key)
        surviving = (store_edge.doc_paths - removed_ids if store_edge else set()) | (
            scratch_edge.doc_paths if scratch_edge else set()
        )
        if not surviving:
            if store_edge is not None:
                delta.delete_edges.append((store_edge.src_id, store_edge.kind, store_edge.dst_id))
            continue
        edge_target = _reconcile_edge(store_edge, scratch_edge, surviving)
        if store_edge is None or not _edge_unchanged(store_edge, edge_target):
            delta.upsert_edges.append(edge_target)
    return delta


def apply_graph_delta(store: GraphStore, delta: GraphDelta) -> int:
    """Apply a planned `GraphDelta` to the store; return the orphan count (nodes + edges deleted).

    The **only** mutating step. Nodes are settled before edges (so an upserted edge's endpoints
    exist), and node deletes run first so their incident-edge cascade lands before any edge upsert —
    mirroring `_reconcile_graph`'s node-phase-then-edge-phase order."""
    for node_id in delta.delete_nodes:
        store.delete_node(node_id)
    for node in delta.upsert_nodes:
        store.replace_node(node)
    for src_id, kind, dst_id in delta.delete_edges:
        store.delete_edge(src_id, kind, dst_id)
    for edge in delta.upsert_edges:
        store.replace_edge(edge)
    return len(delta.delete_nodes) + len(delta.delete_edges)
