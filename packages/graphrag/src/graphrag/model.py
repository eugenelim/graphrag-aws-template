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
    """The relationships between entities, directed src → dst.

    The first six are emitted by the *deterministic* extractor (``extract.py``); the last
    three are the closed set of *LLM-extractable* relationships the schema-guided extraction
    pass emits (free-narrative inter-entity edges the labeled-field regex structurally cannot
    reach — ``schema_extract.py`` / ADR-0006). The two sets are **disjoint** by invariant
    (``DETERMINISTIC_EDGE_KINDS`` / ``LLM_EXTRACTABLE_EDGE_KINDS`` below)."""

    CHAIRS = "CHAIRS"  # Person → SIG
    TECH_LEADS = "TECH_LEADS"  # Person → SIG
    OWNS = "OWNS"  # SIG → KEP
    AUTHORS = "AUTHORS"  # Person → KEP
    APPROVES = "APPROVES"  # Person → KEP
    HAS_SUBPROJECT = "HAS_SUBPROJECT"  # SIG → Subproject
    # LLM-extractable (schema-guided extraction) — never emitted by the deterministic path.
    COLLABORATES_WITH = "COLLABORATES_WITH"  # SIG → SIG
    SUPERSEDES = "SUPERSEDES"  # KEP → KEP
    DEPENDS_ON = "DEPENDS_ON"  # KEP → KEP


# The ``extraction_method`` edge-prop values — the distinguishability stamp that keeps a
# model-asserted edge from being confused with a deterministic fact (schema-guided-extraction
# AC4 at write, AC11 at read).
EXTRACTION_METHOD_DETERMINISTIC = "deterministic"
EXTRACTION_METHOD_LLM = "schema-guided-llm"

# The closed LLM-extractable edge-kind set and the deterministic set, pinned as named
# frozensets. Their **disjointness is load-bearing** (schema-guided-extraction AC1): because an
# LLM edge's kind can never equal a deterministic edge's kind, an LLM edge can never share a
# ``(src, kind, dst)`` key with a deterministic one — so the merge-on-upsert ``setdefault`` in
# ``Graph.upsert_edge`` can never mislabel a deterministic edge or strip an LLM stamp, and the
# read-side method (``extraction_method_for_kind``) is a pure function of the kind. Defined
# explicitly (not as a complement) so a future kind added to *both* sets, or to *neither*, is
# caught by the disjoint + exhaustive assertions in ``test_validate_triple.py``.
DETERMINISTIC_EDGE_KINDS: frozenset[EdgeKind] = frozenset(
    {
        EdgeKind.CHAIRS,
        EdgeKind.TECH_LEADS,
        EdgeKind.OWNS,
        EdgeKind.AUTHORS,
        EdgeKind.APPROVES,
        EdgeKind.HAS_SUBPROJECT,
    }
)
LLM_EXTRACTABLE_EDGE_KINDS: frozenset[EdgeKind] = frozenset(
    {EdgeKind.COLLABORATES_WITH, EdgeKind.SUPERSEDES, EdgeKind.DEPENDS_ON}
)


def extraction_method_for_kind(kind: EdgeKind) -> str:
    """The provenance method an edge of ``kind`` carries — derived from the kind alone.

    Sound because the LLM-extractable and deterministic kind sets are disjoint (the AC1
    invariant): a kind is in exactly one set, so its method is unambiguous. The read path
    (``query.expand_neighborhood`` / the graph templates) uses this to mark each traversed
    hop's method in the retrieval trace, so a model-asserted edge is never blended silently
    into an answer (AC11). Deterministic edges therefore need no write-side stamp — their
    method is read off the kind — while LLM edges *also* carry the authoritative
    ``extraction_method`` prop at write (AC4).

    Deriving the read-side method from the **kind** (not the stored prop) is deliberate and
    tamper-resistant: an edge whose ``extraction_method`` prop was altered post-write cannot make
    an LLM edge read as deterministic (or vice-versa), because the kind — not the forgeable prop —
    is the distinguishability authority at read. The write-side prop is provenance/audit only."""
    if kind in LLM_EXTRACTABLE_EDGE_KINDS:
        return EXTRACTION_METHOD_LLM
    return EXTRACTION_METHOD_DETERMINISTIC


class Direction(StrEnum):
    """Traversal direction relative to a node, for ``neighbors()``."""

    OUT = "OUT"  # follow edges where the node is the src
    IN = "IN"  # follow edges where the node is the dst


@dataclass
class Node:
    """A resolved graph entity. ``id`` is the normalized key (the merge key).

    ``doc_paths`` (slice 5) is the set of ``{source}/{path}`` document ids that contribute
    this node — the provenance/reference-count that the incremental delta's orphan-removal
    pass reads: a node survives a delta iff at least one surviving document remains in this
    set (``delta.py`` / ``ingest.ingest_delta``)."""

    id: str
    kind: EntityKind
    props: dict[str, object] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)
    doc_paths: set[str] = field(default_factory=set)


@dataclass
class Edge:
    """A directed relationship between two node IDs.

    ``doc_paths`` (slice 5) is the contributing-document provenance set, same role as on
    ``Node`` — the reference count the delta's orphan pass uses to decide edge removal."""

    src_id: str
    dst_id: str
    kind: EdgeKind
    props: dict[str, object] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)
    doc_paths: set[str] = field(default_factory=set)

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
        existing.doc_paths |= node.doc_paths
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
        existing.doc_paths |= edge.doc_paths
        for k, v in edge.props.items():
            existing.props.setdefault(k, v)
        return existing

    def remove_node(self, node_id: str) -> None:
        """Delete a node and every edge incident to it (slice-5 orphan removal).

        A dangling edge to a removed node would be a stale-reference orphan, so node removal
        cascades to its incident edges in both directions."""
        self.nodes.pop(node_id, None)
        self._edges = {
            key: edge
            for key, edge in self._edges.items()
            if edge.src_id != node_id and edge.dst_id != node_id
        }

    def remove_edge(self, src_id: str, kind: EdgeKind, dst_id: str) -> None:
        """Delete one edge by its ``(src, kind, dst)`` identity (slice-5 orphan removal)."""
        self._edges.pop((src_id, kind.value, dst_id), None)

    def set_node(self, node: Node) -> None:
        """Set a node's full state exactly (slice-5 reconciliation) — *replaces*, not unions."""
        self.nodes[node.id] = node

    def set_edge(self, edge: Edge) -> None:
        """Set an edge's full state exactly (slice-5 reconciliation) — *replaces*, not unions."""
        self._edges[edge.key()] = edge

    def get_node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)
