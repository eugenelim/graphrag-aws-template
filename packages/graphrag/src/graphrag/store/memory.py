"""In-memory graph store — the offline, test, and reproducible-demo backend."""

from __future__ import annotations

from ..model import Direction, Edge, EdgeKind, Graph, Node
from ..visibility import DEFAULT_VISIBILITY
from .base import GraphStore


def _vis(props: dict[str, object]) -> str:
    """The visibility tier of a node/edge props bag (default ``public`` if unlabeled)."""
    return str(props.get("visibility", DEFAULT_VISIBILITY))


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

    def neighbors(
        self,
        node_id: str,
        edge_kind: EdgeKind,
        direction: Direction,
        *,
        allowed_labels: frozenset[str] | None = None,
    ) -> list[Node]:
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
            if target is None:
                continue
            # Slice-4 during-traversal permission filter: exclude the edge unless both its
            # own visibility and the neighbor's are within clearance. edge.visibility =
            # compose(src,dst), so the edge check is the node guarantee; the neighbor check
            # is a defensive guard against a stale edge label (same predicate as Neptune).
            if allowed_labels is not None and (
                _vis(edge.props) not in allowed_labels or _vis(target.props) not in allowed_labels
            ):
                continue
            out.append(target)
        return out

    def all_nodes(self) -> list[Node]:
        return list(self._graph.nodes.values())

    def all_edges(self) -> list[Edge]:
        return self._graph.edges

    def delete_node(self, node_id: str) -> None:
        self._graph.remove_node(node_id)

    def delete_edge(self, src_id: str, kind: EdgeKind, dst_id: str) -> None:
        self._graph.remove_edge(src_id, kind, dst_id)

    def clear(self) -> None:
        self._graph = Graph()
