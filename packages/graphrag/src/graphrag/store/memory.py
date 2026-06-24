"""In-memory graph store — the offline, test, and reproducible-demo backend."""

from __future__ import annotations

from ..model import Direction, Edge, EdgeKind, Graph, Node
from .base import GraphStore


class MemoryGraphStore(GraphStore):
    """A ``GraphStore`` backed by an in-memory ``Graph``."""

    def __init__(self, graph: Graph | None = None) -> None:
        self._graph = graph or Graph()

    @classmethod
    def from_graph(cls, graph: Graph) -> MemoryGraphStore:
        return cls(graph)

    def upsert_node(self, node: Node) -> None:
        self._graph.upsert_node(node)

    def upsert_edge(self, edge: Edge) -> None:
        self._graph.upsert_edge(edge)

    def get_node(self, node_id: str) -> Node | None:
        return self._graph.get_node(node_id)

    def neighbors(self, node_id: str, edge_kind: EdgeKind, direction: Direction) -> list[Node]:
        out: list[Node] = []
        for edge in self._graph.edges:
            if edge.kind != edge_kind:
                continue
            if direction is Direction.OUT and edge.src_id == node_id:
                target = self._graph.get_node(edge.dst_id)
            elif direction is Direction.IN and edge.dst_id == node_id:
                target = self._graph.get_node(edge.src_id)
            else:
                continue
            if target is not None:
                out.append(target)
        return out

    def all_nodes(self) -> list[Node]:
        return list(self._graph.nodes.values())

    def all_edges(self) -> list[Edge]:
        return self._graph.edges
