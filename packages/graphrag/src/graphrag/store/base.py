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
    def neighbors(self, node_id: str, edge_kind: EdgeKind, direction: Direction) -> list[Node]:
        """Nodes one hop from ``node_id`` along ``edge_kind`` in ``direction``.

        ``OUT`` follows edges where ``node_id`` is the source; ``IN`` follows edges
        where it is the destination.
        """

    @abstractmethod
    def all_nodes(self) -> list[Node]: ...

    @abstractmethod
    def all_edges(self) -> list[Edge]: ...

    def neighbors_batch(self, node_ids: list[str]) -> list[NeighborEdge]:
        """All neighbor edges of ``node_ids`` across **every** edge kind and **both**
        directions — the batched primitive seed-and-expand expands over.

        Default: an application-layer fan-out over ``neighbors()``, so a backend that
        only implements single-hop ``neighbors()`` works unchanged and stays
        trace-consistent with it (the in-memory store keeps this — it is instant).
        A backend with a cheap set-based query overrides this to issue O(hops)
        round-trips instead of O(nodes x kinds x directions) — the live-perf path
        (Neptune). The order of the returned edges is not significant: callers that
        need a stable trace sort the reached set themselves.
        """
        out: list[NeighborEdge] = []
        for node_id in node_ids:
            for edge_kind in EdgeKind:
                for direction in (Direction.OUT, Direction.IN):
                    for neighbor in self.neighbors(node_id, edge_kind, direction):
                        out.append(NeighborEdge(node_id, edge_kind, direction, neighbor))
        return out
