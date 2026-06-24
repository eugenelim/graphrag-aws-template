"""The graph data model: entity/edge kinds, ``Node``, ``Edge``, and ``Graph``.

``Graph.upsert_node``/``upsert_edge`` *union* sources and props on an ID
collision — that union **is** the resolution merge (see ``resolve.py``). Because a
node's ID is its normalized key (``normalize.py``), two source rows that produce
the same ID land on the same node, and the merge is visible as a growing
``sources`` set rather than a hidden model decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class EntityKind(StrEnum):
    """The four organizational entity kinds this slice extracts."""

    SIG = "SIG"
    PERSON = "Person"
    KEP = "KEP"
    SUBPROJECT = "Subproject"


class EdgeKind(StrEnum):
    """The relationships between entities, directed src → dst."""

    CHAIRS = "CHAIRS"  # Person → SIG
    TECH_LEADS = "TECH_LEADS"  # Person → SIG
    OWNS = "OWNS"  # SIG → KEP
    AUTHORS = "AUTHORS"  # Person → KEP
    APPROVES = "APPROVES"  # Person → KEP
    HAS_SUBPROJECT = "HAS_SUBPROJECT"  # SIG → Subproject


class Direction(StrEnum):
    """Traversal direction relative to a node, for ``neighbors()``."""

    OUT = "OUT"  # follow edges where the node is the src
    IN = "IN"  # follow edges where the node is the dst


@dataclass
class Node:
    """A resolved graph entity. ``id`` is the normalized key (the merge key)."""

    id: str
    kind: EntityKind
    props: dict[str, object] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)


@dataclass
class Edge:
    """A directed relationship between two node IDs."""

    src_id: str
    dst_id: str
    kind: EdgeKind
    props: dict[str, object] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)

    def key(self) -> tuple[str, str, str]:
        """Identity of an edge for de-duplication."""
        return (self.src_id, self.kind.value, self.dst_id)


class Graph:
    """An in-memory entity graph with merge-on-upsert semantics."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges.values())

    def upsert_node(self, node: Node) -> Node:
        """Insert ``node``, or merge into the existing node with the same ID.

        On collision, ``sources`` is unioned and ``props`` is updated (new keys
        win, existing keys kept unless explicitly overwritten). The returned node
        is the canonical one held by the graph — this is the resolution merge.
        """
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return node
        if existing.kind != node.kind:
            raise ValueError(
                f"node id {node.id!r} reused across kinds "
                f"{existing.kind.value} vs {node.kind.value}"
            )
        existing.sources |= node.sources
        for k, v in node.props.items():
            existing.props.setdefault(k, v)
        return existing

    def upsert_edge(self, edge: Edge) -> Edge:
        """Insert ``edge``, or merge sources/props into the matching edge."""
        existing = self._edges.get(edge.key())
        if existing is None:
            self._edges[edge.key()] = edge
            return edge
        existing.sources |= edge.sources
        for k, v in edge.props.items():
            existing.props.setdefault(k, v)
        return existing

    def get_node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)
