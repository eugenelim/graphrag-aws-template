"""The ``GraphStore`` interface — the seam between traversal and a backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..model import Direction, Edge, EdgeKind, Node


@dataclass
class NeighborEdge:
    """One edge from a frontier node to a neighbor — the unit ``neighbors_batch``
    returns. Because the batch fetches edges for a *set* of frontier nodes in one
    call, each edge records its source (``src_id``) to stay attributable, and
    ``direction``/``edge_kind`` are the edge's own identity (which relationship,
    which way) — the same data ``neighbors()`` conveys positionally."""

    src_id: str
    edge_kind: EdgeKind
    direction: Direction
    neighbor: Node


class GraphStore(ABC):
    """A backend that persists nodes/edges and answers single-hop neighbor queries.

    Traversal is built in the application layer on ``neighbors()`` so every backend
    produces an identical trace; a backend only has to upsert and answer one hop.
    """

    @abstractmethod
    def upsert_node(self, node: Node) -> None: ...

    @abstractmethod
    def upsert_edge(self, edge: Edge) -> None: ...

    @abstractmethod
    def get_node(self, node_id: str) -> Node | None: ...

    @abstractmethod
    def neighbors(
        self,
        node_id: str,
        edge_kind: EdgeKind,
        direction: Direction,
        *,
        allowed_labels: frozenset[str] | None = None,
    ) -> list[Node]:
        """Nodes one hop from ``node_id`` along ``edge_kind`` in ``direction``.

        ``OUT`` follows edges where ``node_id`` is the source; ``IN`` follows edges
        where it is the destination.

        ``allowed_labels`` is the slice-4 permission filter (a teaching stand-in for an
        ACL, never real authz): when not ``None``, the hop excludes any edge whose
        ``visibility`` — or whose neighbor node's ``visibility`` — is outside the set, so a
        forbidden node never enters the frontier. Because an edge's visibility is
        ``compose(src, dst) = max`` and a clearance is downward-closed, the edge check
        subsumes the node check; the neighbor check is kept as a defensive guard against a
        stale edge label. ``None`` = unfiltered (the slice-1–3 path).
        """

    @abstractmethod
    def all_nodes(self) -> list[Node]: ...

    @abstractmethod
    def all_edges(self) -> list[Edge]: ...

    @abstractmethod
    def delete_node(self, node_id: str) -> None:
        """Delete a node and its incident edges (slice-5 orphan removal)."""

    @abstractmethod
    def delete_edge(self, src_id: str, kind: EdgeKind, dst_id: str) -> None:
        """Delete one edge by its ``(src, kind, dst)`` identity (slice-5 orphan removal)."""

    @abstractmethod
    def clear(self) -> None:
        """Remove every node and edge — the ``--rebuild`` ground-truth reset (slice 5)."""

    @abstractmethod
    def replace_node(self, node: Node) -> None:
        """Set a node's full state exactly (slice-5 delta reconciliation).

        Unlike ``upsert_node`` — which *unions* ``doc_paths``/``sources`` and *setdefaults*
        props (the resolve-merge) — this **replaces** them, so a surviving node that lost a
        contributing document has its ``doc_paths`` shrunk and a changed document's props
        applied. Does **not** cascade to edges (edges are reconciled separately)."""

    @abstractmethod
    def replace_edge(self, edge: Edge) -> None:
        """Set an edge's full state exactly (slice-5 delta reconciliation) — the edge twin of
        ``replace_node``; replaces ``doc_paths``/``sources``/props rather than unioning."""

    def neighbors_batch(
        self, node_ids: list[str], *, allowed_labels: frozenset[str] | None = None
    ) -> list[NeighborEdge]:
        """All neighbor edges of ``node_ids`` across **every** edge kind and **both**
        directions — the batched primitive seed-and-expand expands over.

        Default: an application-layer fan-out over ``neighbors()``, so a backend that
        only implements single-hop ``neighbors()`` works unchanged and stays
        trace-consistent with it (the in-memory store keeps this — it is instant).
        A backend with a cheap set-based query overrides this to issue O(hops)
        round-trips instead of O(nodes x kinds x directions) — the live-perf path
        (Neptune). The order of the returned edges is not significant: callers that
        need a stable trace sort the reached set themselves.

        ``allowed_labels`` (slice-4 permission filter) is threaded into each ``neighbors``
        call, so the during-traversal edge filter applies identically whether a backend
        uses this fan-out or overrides the batch.
        """
        out: list[NeighborEdge] = []
        for node_id in node_ids:
            for edge_kind in EdgeKind:
                for direction in (Direction.OUT, Direction.IN):
                    for neighbor in self.neighbors(
                        node_id, edge_kind, direction, allowed_labels=allowed_labels
                    ):
                        out.append(NeighborEdge(node_id, edge_kind, direction, neighbor))
        return out
