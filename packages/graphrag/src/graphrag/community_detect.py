"""Community detection over the resolved entity graph (global-community-summary slice).

The **ingest-side** half of the Global Community Summary pattern (Microsoft GraphRAG
*global*): partition the entity graph into communities, then summarize each one (T3,
``summarize_communities`` below) for corpus-wide map-reduce retrieval at query time
(``globalsearch.py``).

**Where this runs (ADR-0005):** the on-demand Fargate ingest task — *not* a standing
Neptune Analytics service. The clustering algorithm is **Louvain** (via ``networkx``),
**seeded** for reproducibility (charter principle 3). Louvain is chosen over Leiden so the
algorithm matches the managed alternative (Neptune Analytics ships Louvain) for an
apples-to-apples self-compute-vs-managed comparison; the divergence from Microsoft
GraphRAG's Leiden is stated, not hidden (charter honesty note).

**``networkx`` is imported lazily**, inside ``detect_communities`` only, so this module —
and anything that imports it at load time — pulls in no ``networkx``; the query Lambda's
import graph stays ``networkx``-free (the PyYAML-free discipline, extended). A
``sys.modules`` guard test pins this.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Edge, Node
from .visibility import DEFAULT_VISIBILITY, compose

# Pinned Louvain random seed — Louvain is randomized, so a fixed seed makes the partition
# reproducible across runs on the locked corpus (charter principle 3; ADR-0005 Confirmation).
DEFAULT_SEED = 42


def _node_visibility(node: Node) -> str:
    """The visibility tier of a node (default ``public`` if unlabeled) — the same expression
    the graph path uses (``hybrid._node_visibility``), read here from the **pure** visibility
    module so this ingest-side module never imports the query/vector surface. An unlabeled or
    unknown tier composes as ``public``: the deliberate teaching default (``visibility.rank``),
    named because for a corpus-wide summary it is a down-classification default, not silent."""
    return str(node.props.get("visibility", DEFAULT_VISIBILITY))


@dataclass(frozen=True)
class CommunitySpec:
    """A detected community before summarization: its member entity ids, size, and composed
    (most-restrictive) member visibility tier. ``id`` is ``community-{n}``, stable by the
    sort below so a re-run with the same seed produces identical ids."""

    id: str
    entity_ids: tuple[str, ...]
    size: int
    tier: str


def detect_communities(
    nodes: list[Node], edges: list[Edge], *, seed: int = DEFAULT_SEED
) -> list[CommunitySpec]:
    """Partition the entity graph into communities with **Louvain** (seeded, reproducible).

    Builds an **undirected** ``networkx`` graph whose vertices are the entity node ids and
    whose edges are the entity edges (relationship direction is irrelevant to community
    structure), runs ``louvain_communities(G, seed=seed)``, and returns one ``CommunitySpec``
    per community. An **isolated** node (no edges) is its own singleton community. The
    community ``tier`` is ``compose`` over its members' visibilities (most-restrictive wins);
    an unlabeled member composes as ``public`` (``_node_visibility``).

    Communities are sorted **largest-first, then by their first (sorted) member id** so the
    ``community-{n}`` ids are stable across runs — the reproducibility the seed buys at the
    partition level is carried through to the ids.

    ``networkx`` is imported here, lazily, so the module load stays ``networkx``-free.
    """
    import networkx as nx
    from networkx.algorithms.community import louvain_communities

    by_id = {node.id: node for node in nodes}

    graph = nx.Graph()
    graph.add_nodes_from(by_id)
    for edge in edges:
        # Only edges between known entity nodes contribute to community structure; a dangling
        # edge endpoint (should not happen on a resolved graph) is ignored rather than
        # silently inventing a vertex.
        if edge.src_id in by_id and edge.dst_id in by_id:
            graph.add_edge(edge.src_id, edge.dst_id)

    partition: list[set[str]] = louvain_communities(graph, seed=seed)

    # Sort members within a community, then communities by (-size, first member id) for stable
    # `community-{n}` ids — identical input + seed ⇒ identical ids.
    members_per_community = sorted(
        (tuple(sorted(community)) for community in partition),
        key=lambda members: (-len(members), members[0]),
    )

    specs: list[CommunitySpec] = []
    for index, members in enumerate(members_per_community):
        tier = compose(*(_node_visibility(by_id[member_id]) for member_id in members))
        specs.append(
            CommunitySpec(id=f"community-{index}", entity_ids=members, size=len(members), tier=tier)
        )
    return specs
