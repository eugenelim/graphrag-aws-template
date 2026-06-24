"""Seed-and-expand hybrid orchestration (slice-3 AC4 / ADR-0001).

The keystone of the demo: one question runs the dual-seed seed-and-expand path —

1. **vector search** (top-k) → the owning entity IDs of the hits (``source=vector``);
2. **question entity-linking** (``entity_link.link_question``) → candidates confirmed
   to exist in the graph (``get_node``) become ``source=question`` seeds; unconfirmed
   candidates are recorded as dropped (a misseed is visible, never silently expanded);
3. the union is **capped** to ``seed_cap`` (truncation recorded);
4. **expanded** 1–2 hops over ``neighbors()`` (``query.expand_neighborhood``);
5. the vector chunks are **merged** with the reached graph facts (deduped);
6. an answer is **synthesized** (injected ``Synthesizer``);

returning a ``HybridResult`` whose ``.render()`` narrates, in order,
**seeds-by-source → hops → citations → answer** — charter principle 1 (no black-box
hop). Over-expansion is bounded by the hop limit + seed cap, both surfaced in the
trace so truncation is visible, never silent (ADR-0001 consequence).

Imports only ``vector``/``query``/``entity_link``/``synthesize``/``store``/``model``
— none pull PyYAML into the runtime import graph, so this stays out of the
pure-Python Lambda's PyYAML-free bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .embed import Embedder
from .entity_link import Candidate, link_question
from .model import Node
from .query import NeighborhoodResult, expand_neighborhood, resolve_nodes
from .store.base import GraphStore
from .store.vector_base import VectorHit, VectorStore
from .synthesize import Synthesizer
from .vector import vector_search

DEFAULT_K = 5
DEFAULT_MAX_HOPS = 2
DEFAULT_SEED_CAP = 8

SeedSource = Literal["vector", "question"]


@dataclass
class Seed:
    """A graph seed carrying its source — the dual-seed visibility the demo turns on."""

    entity_id: str
    source: SeedSource
    surface: str | None = None


@dataclass
class HybridResult:
    """The traced output of ``hybrid_query`` — answer + the full seed/hop narration."""

    question: str
    seeds: list[Seed]
    dropped_candidates: list[Candidate]
    hop_trace: NeighborhoodResult
    chunks: list[VectorHit]
    graph_nodes: list[Node]
    answer: str
    citations: list[str]
    seed_cap: int
    max_hops: int
    # Truncation is attributed to the source actually dropped (never blamed on the
    # wrong source) — the dual-seed split is the demo's pedagogy (ADR-0001).
    vector_truncated: bool = False
    question_truncated: bool = False

    @property
    def seed_cap_truncated(self) -> bool:
        """True when the seed cap dropped seeds from *either* source."""
        return self.vector_truncated or self.question_truncated

    def render(self) -> str:
        """Narrate seeds-by-source → hops → citations → answer (AC4 ordering)."""
        lines = ["== hybrid-query =="]
        # seeds, grouped by source so the dual-seed split is legible.
        lines.append("seeds:")
        truncated_by_source = {"vector": self.vector_truncated, "question": self.question_truncated}
        for source in ("vector", "question"):
            ids = [s.entity_id for s in self.seeds if s.source == source]
            note = "  [seed cap truncated]" if truncated_by_source[source] else ""
            lines.append(f"  {source}: {', '.join(ids) or '(none)'}{note}")
        if self.dropped_candidates:
            dropped = ", ".join(f"{c.surface}->{c.entity_id}" for c in self.dropped_candidates)
            lines.append(f"  dropped (unconfirmed): {dropped}")
        # hops.
        lines.append("hops:")
        for entry in self.hop_trace.trace:
            kinds = ", ".join(ek.value for ek in entry.edge_kinds) or "(none)"
            reached = ", ".join(entry.reached) or "(none)"
            trunc = "  [frontier truncated]" if entry.truncated else ""
            lines.append(f"  hop {entry.hop}: via {kinds} -> {reached}{trunc}")
        lines.append(f"  reached: {', '.join(self.hop_trace.result_ids) or '(none)'}")
        # citations.
        lines.append("citations:")
        for cite in self.citations:
            lines.append(f"  - {cite}")
        if not self.citations:
            lines.append("  (none)")
        # answer.
        lines.append("answer:")
        lines.append(f"  {self.answer}")
        return "\n".join(lines)


def _dedupe_chunks(hits: list[VectorHit]) -> list[VectorHit]:
    seen: set[str] = set()
    out: list[VectorHit] = []
    for hit in hits:
        if hit.chunk.id not in seen:
            seen.add(hit.chunk.id)
            out.append(hit)
    return out


def hybrid_query(
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
) -> HybridResult:
    """Run the seed-and-expand hybrid path end-to-end and return the traced result."""
    # 1. Vector search → owning entity IDs (source=vector).
    vresult = vector_search(vector_store, embedder, question, k=k)
    chunks = _dedupe_chunks(vresult.hits)

    vector_seeds: list[Seed] = []
    seen_ids: set[str] = set()
    for hit in chunks:
        for entity_id in hit.chunk.entity_ids:
            if entity_id not in seen_ids:
                seen_ids.add(entity_id)
                vector_seeds.append(Seed(entity_id=entity_id, source="vector"))

    # 2. Question entity-linking → confirmed (source=question) ∪ dropped (unconfirmed).
    question_seeds: list[Seed] = []
    dropped: list[Candidate] = []
    for cand in link_question(question, aliases):
        if graph_store.get_node(cand.entity_id) is None:
            dropped.append(cand)
            continue
        if cand.entity_id not in seen_ids:
            seen_ids.add(cand.entity_id)
            question_seeds.append(
                Seed(entity_id=cand.entity_id, source="question", surface=cand.surface)
            )

    # 3. Cap the seed set. Question seeds take priority — the entity-led pedagogy must
    #    survive the cap — and vector seeds fill the remaining budget; each source's
    #    truncation is recorded separately so the trace never blames the wrong source.
    question_truncated = len(question_seeds) > seed_cap
    kept_question = question_seeds[:seed_cap]
    vector_budget = max(0, seed_cap - len(kept_question))
    vector_truncated = len(vector_seeds) > vector_budget
    kept_vector = vector_seeds[:vector_budget]
    seeds = kept_vector + kept_question

    # 4. Expand 1–2 hops over the (capped) seed set.
    seed_ids = [s.entity_id for s in seeds]
    hop_trace = expand_neighborhood(graph_store, seed_ids, max_hops=max_hops)

    # 5. Merge: the vector chunks + the seed nodes + the reached graph facts (deduped).
    node_ids: list[str] = []
    seen_nodes: set[str] = set()
    for node_id in seed_ids + hop_trace.result_ids:
        if node_id not in seen_nodes:
            seen_nodes.add(node_id)
            node_ids.append(node_id)
    graph_nodes = resolve_nodes(graph_store, node_ids)

    # 6. Synthesize (injected; display-only output).
    synth = synthesizer.synthesize(question, chunks, graph_nodes)

    return HybridResult(
        question=question,
        seeds=seeds,
        dropped_candidates=dropped,
        hop_trace=hop_trace,
        chunks=chunks,
        graph_nodes=graph_nodes,
        answer=synth.answer,
        citations=synth.citations,
        seed_cap=seed_cap,
        max_hops=max_hops,
        vector_truncated=vector_truncated,
        question_truncated=question_truncated,
    )
