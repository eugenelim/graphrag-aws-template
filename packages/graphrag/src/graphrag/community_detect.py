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
from .store.community_base import Community
from .synthesize import Synthesizer
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


def _entity_label(node: Node) -> str:
    """A short human label for an entity (its title/name prop, else its id) — member-derived,
    so a community ``title`` built from it inherits the whole-community clearance gate."""
    title = node.props.get("title") or node.props.get("name")
    return str(title) if title else node.id


def _community_title(member_nodes: list[Node]) -> str:
    """A stable, member-derived community label: the first member's label, '+N more' for the
    rest. Deterministic because ``member_nodes`` is in the spec's sorted-member order."""
    if not member_nodes:
        return "(empty community)"
    primary = _entity_label(member_nodes[0])
    extra = len(member_nodes) - 1
    return f"{primary} +{extra} more" if extra else primary


def summarize_communities(
    specs: list[CommunitySpec], nodes: list[Node], edges: list[Edge], synthesizer: Synthesizer
) -> list[Community]:
    """Generate one summary per community via the ``Synthesizer`` seam — the **member subgraph**
    (member entities + the relationships among them) is the synthesis context.

    For each ``CommunitySpec`` this builds the member ``Node`` list and the **intra-community**
    edges (relationships where both endpoints are members), passes the members as the
    synthesizer's ``graph_facts`` and the relationships as untrusted data in the summarization
    question, and calls ``synthesizer.synthesize`` once. The resulting ``Community`` carries the
    synthesized text as its ``summary``, a stable member-derived ``title``, and the spec's
    composed ``tier``/``size``.

    One Converse call per community: at the locked demo corpus scale the community count and
    each member subgraph are small (bounded ingest fan-out + bounded prompt); an unbounded
    large-corpus fan-out is the named LLM10 scale-out residual (ADR-0005). Offline,
    ``TemplateSynthesizer`` makes each summary deterministic and non-semantic.
    """
    by_id = {node.id: node for node in nodes}
    communities: list[Community] = []
    for spec in specs:
        member_set = set(spec.entity_ids)
        member_nodes = [by_id[eid] for eid in spec.entity_ids if eid in by_id]
        intra_edges = [
            edge for edge in edges if edge.src_id in member_set and edge.dst_id in member_set
        ]
        rels = (
            "; ".join(
                f"{edge.src_id} -{edge.kind.value}-> {edge.dst_id}" for edge in intra_edges
            )
            or "(no internal relationships)"
        )
        question = (
            "Summarize this community of related Kubernetes organizational entities for a "
            "corpus-wide overview: describe what the group is about and how its members "
            f"relate. Relationships within the community: {rels}"
        )
        # Member subgraph is the context: members as graph_facts, relationships as data in the
        # question (which the synthesizer places in `messages`, never `system`).
        result = synthesizer.synthesize(question, [], member_nodes)
        # Member documents (union of member entities' provenance) — carried on the community so
        # the read-only query path cites real source docs without an Entity lookup.
        doc_paths: set[str] = set()
        for node in member_nodes:
            doc_paths |= node.doc_paths
        communities.append(
            Community(
                id=spec.id,
                title=_community_title(member_nodes),
                summary=result.answer,
                entity_ids=spec.entity_ids,
                tier=spec.tier,
                size=spec.size,
                doc_paths=tuple(sorted(doc_paths)),
            )
        )
    return communities
