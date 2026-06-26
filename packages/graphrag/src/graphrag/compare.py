"""Three-mode comparison runner with per-mode traces (slice-3 AC5).

The demo's pedagogy made executable: ``run_modes`` runs ``vector-only``,
``graph-only``, and ``hybrid`` **independently** over one question, each rendering its
own retrieval trace so the divergence is legible side by side. Running the three as
standalone paths (not the hybrid's internal dual-seed) is the honest contrast
(ADR-0001 boundary; charter principle 2 — no strawman).

- **vector-only** — ``vector_search`` → synthesize over the chunks (no graph).
- **graph-only** — question entity-linking → confirm → ``expand_neighborhood`` →
  synthesize over the graph facts (no vector).
- **hybrid** — the seed-and-expand path (``hybrid_query``, AC4).

On the entity-led exemplar, the graph + hybrid paths expand
``person:thockin → sig:sig-network → owned KEPs`` (the 2-hop TECH_LEADS/OWNS path), so
their result sets enumerate the owned KEPs while vector-only does not — the structural
demonstration that graph augments vector.

PyYAML-free import graph (mirrors ``hybrid.py``) — CLI/test use only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .embed import Embedder
from .entity_link import link_question
from .hybrid import DEFAULT_K, DEFAULT_MAX_HOPS, DEFAULT_SEED_CAP, hybrid_query
from .model import Node
from .query import expand_neighborhood, resolve_nodes
from .store.base import GraphStore
from .store.vector_base import VectorStore
from .synthesize import Synthesizer
from .vector import vector_search
from .visibility import DEFAULT_VISIBILITY, Clearance

if TYPE_CHECKING:
    from .selfquery import MetadataFilter


def _node_visibility(node: Node) -> str:
    return str(node.props.get("visibility", DEFAULT_VISIBILITY))


@dataclass
class ModeResult:
    """One mode's retrieval trace + synthesized answer (the side-by-side unit)."""

    mode: str
    trace: str
    answer: str
    citations: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    result_ids: list[str] = field(default_factory=list)  # graph entity IDs surfaced

    def render(self) -> str:
        lines = [f"--- mode: {self.mode} ---", self.trace, "answer:", f"  {self.answer}"]
        return "\n".join(lines)


@dataclass
class ComparisonResult:
    """The three modes' results, for a side-by-side ``render()``."""

    question: str
    vector: ModeResult
    graph: ModeResult
    hybrid: ModeResult

    def render(self) -> str:
        lines = [f"== compare: {self.question} =="]
        for mode in (self.vector, self.graph, self.hybrid):
            lines.append(mode.render())
        return "\n".join(lines)


def run_modes(
    question: str,
    *,
    vector_store: VectorStore,
    graph_store: GraphStore,
    embedder: Embedder,
    synthesizer: Synthesizer,
    aliases: dict[str, str],
    k: int = DEFAULT_K,
    max_hops: int = DEFAULT_MAX_HOPS,
    seed_cap: int = DEFAULT_SEED_CAP,
    clearance: Clearance | None = None,
    metadata_filter: MetadataFilter | None = None,
) -> ComparisonResult:
    """Run the three retrieval modes independently and return their traced results.

    A ``clearance`` (slice-4 permission filter) is threaded into **every** mode — including
    vector-only, which must filter its own chunks or it would leak restricted chunks the
    other two modes drop (a per-mode divergence the demo must not have). ``None`` =
    unfiltered (slice-3 behavior unchanged).

    A ``metadata_filter`` (the self-query structured filter) is threaded into the **vector
    legs** (vector-only and hybrid's vector leg) — graph-only has no vector leg, so the
    self-query filter does not apply there. It composes with ``clearance`` and can only
    narrow. ``None``/empty = unfiltered.
    """
    return ComparisonResult(
        question=question,
        vector=_vector_only(
            question,
            vector_store,
            embedder,
            synthesizer,
            k,
            clearance=clearance,
            metadata_filter=metadata_filter,
        ),
        graph=_graph_only(
            question, graph_store, synthesizer, aliases, max_hops=max_hops, clearance=clearance
        ),
        hybrid=_hybrid(
            question,
            vector_store,
            graph_store,
            embedder,
            synthesizer,
            aliases,
            k=k,
            max_hops=max_hops,
            seed_cap=seed_cap,
            clearance=clearance,
            metadata_filter=metadata_filter,
        ),
    )


def _vector_only(
    question: str,
    vector_store: VectorStore,
    embedder: Embedder,
    synthesizer: Synthesizer,
    k: int,
    *,
    clearance: Clearance | None = None,
    metadata_filter: MetadataFilter | None = None,
) -> ModeResult:
    vresult = vector_search(
        vector_store, embedder, question, k=k, clearance=clearance, metadata_filter=metadata_filter
    )
    synth = synthesizer.synthesize(question, vresult.hits, [])
    # the entity IDs vector-only surfaces are the chunk owners (no graph expansion).
    owner_ids: list[str] = []
    for hit in vresult.hits:
        for entity_id in hit.chunk.entity_ids:
            if entity_id not in owner_ids:
                owner_ids.append(entity_id)
    return ModeResult(
        mode="vector-only",
        trace=vresult.render(),
        answer=synth.answer,
        citations=synth.citations,
        chunk_ids=[hit.chunk.id for hit in vresult.hits],
        result_ids=owner_ids,
    )


def _graph_only(
    question: str,
    graph_store: GraphStore,
    synthesizer: Synthesizer,
    aliases: dict[str, str],
    *,
    max_hops: int,
    clearance: Clearance | None = None,
) -> ModeResult:
    # confirm question-linked candidates against the graph, then expand. Unconfirmed
    # candidates are recorded and surfaced in the trace — a misseed must be visible in
    # graph-only too, never silently dropped (ADR-0001; charter principle 1). A confirmed
    # candidate above the persona's clearance is filtered (slice-4), recorded separately.
    seed_ids: list[str] = []
    dropped: list[str] = []
    filtered: list[str] = []
    for cand in link_question(question, aliases):
        node = graph_store.get_node(cand.entity_id)
        if node is None:
            dropped.append(f"{cand.surface}->{cand.entity_id}")
        elif clearance is not None and not clearance.allows(_node_visibility(node)):
            filtered.append(f"{cand.surface}->{cand.entity_id}")
        elif cand.entity_id not in seed_ids:
            seed_ids.append(cand.entity_id)
    hop = expand_neighborhood(graph_store, seed_ids, max_hops=max_hops, clearance=clearance)
    node_ids: list[str] = []
    for node_id in seed_ids + hop.result_ids:
        if node_id not in node_ids:
            node_ids.append(node_id)
    nodes = resolve_nodes(graph_store, node_ids)
    # Independent final guard, mirroring hybrid: drop any merged node above clearance.
    if clearance is not None:
        nodes = [n for n in nodes if clearance.allows(_node_visibility(n))]
        node_ids = [n.id for n in nodes]
    synth = synthesizer.synthesize(question, [], nodes)
    trace = hop.render()
    if dropped:
        trace += f"\ndropped (unconfirmed): {', '.join(dropped)}"
    if filtered:
        trace += (
            f"\nfiltered (visibility; teaching aid, a real ACL would not reveal this): "
            f"{', '.join(filtered)}"
        )
    return ModeResult(
        mode="graph-only",
        trace=trace,
        answer=synth.answer,
        citations=synth.citations,
        result_ids=node_ids,
    )


def _hybrid(
    question: str,
    vector_store: VectorStore,
    graph_store: GraphStore,
    embedder: Embedder,
    synthesizer: Synthesizer,
    aliases: dict[str, str],
    *,
    k: int,
    max_hops: int,
    seed_cap: int,
    clearance: Clearance | None = None,
    metadata_filter: MetadataFilter | None = None,
) -> ModeResult:
    result = hybrid_query(
        question,
        vector_store=vector_store,
        graph_store=graph_store,
        embedder=embedder,
        synthesizer=synthesizer,
        aliases=aliases,
        k=k,
        max_hops=max_hops,
        seed_cap=seed_cap,
        clearance=clearance,
        metadata_filter=metadata_filter,
    )
    surfaced: list[str] = [n.id for n in result.graph_nodes]
    return ModeResult(
        mode="hybrid",
        trace=result.render(),
        answer=result.answer,
        citations=result.citations,
        chunk_ids=[hit.chunk.id for hit in result.chunks],
        result_ids=surfaced,
    )
