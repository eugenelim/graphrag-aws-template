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

from .embed import Embedder
from .entity_link import link_question
from .hybrid import DEFAULT_K, DEFAULT_MAX_HOPS, DEFAULT_SEED_CAP, hybrid_query
from .query import expand_neighborhood, resolve_nodes
from .store.base import GraphStore
from .store.vector_base import VectorStore
from .synthesize import Synthesizer
from .vector import vector_search


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
) -> ComparisonResult:
    """Run the three retrieval modes independently and return their traced results."""
    return ComparisonResult(
        question=question,
        vector=_vector_only(question, vector_store, embedder, synthesizer, k),
        graph=_graph_only(question, graph_store, synthesizer, aliases, max_hops=max_hops),
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
        ),
    )


def _vector_only(
    question: str,
    vector_store: VectorStore,
    embedder: Embedder,
    synthesizer: Synthesizer,
    k: int,
) -> ModeResult:
    vresult = vector_search(vector_store, embedder, question, k=k)
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
) -> ModeResult:
    # confirm question-linked candidates against the graph, then expand. Unconfirmed
    # candidates are recorded and surfaced in the trace — a misseed must be visible in
    # graph-only too, never silently dropped (ADR-0001; charter principle 1).
    seed_ids: list[str] = []
    dropped: list[str] = []
    for cand in link_question(question, aliases):
        if graph_store.get_node(cand.entity_id) is None:
            dropped.append(f"{cand.surface}->{cand.entity_id}")
        elif cand.entity_id not in seed_ids:
            seed_ids.append(cand.entity_id)
    hop = expand_neighborhood(graph_store, seed_ids, max_hops=max_hops)
    node_ids: list[str] = []
    for node_id in seed_ids + hop.result_ids:
        if node_id not in node_ids:
            node_ids.append(node_id)
    nodes = resolve_nodes(graph_store, node_ids)
    synth = synthesizer.synthesize(question, [], nodes)
    trace = hop.render()
    if dropped:
        trace += f"\ndropped (unconfirmed): {', '.join(dropped)}"
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
