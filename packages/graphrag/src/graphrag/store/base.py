"""The ``GraphStore`` interface — the seam between traversal and a backend."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..model import Direction, Edge, EdgeKind, Node


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
