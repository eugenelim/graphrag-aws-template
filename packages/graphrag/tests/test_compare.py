"""T5 — three-mode comparison runner with per-mode traces (AC5).

The three modes run **independently** (honest side-by-side, charter principle 2).
On the entity-led exemplar, graph + hybrid enumerate sig:sig-network's owned KEPs via
the 2-hop TECH_LEADS/OWNS path while vector-only does not — the structural graph-win
asserted offline (the offline embedder is non-semantic).

# STUB: AC5
"""

from __future__ import annotations

from pathlib import Path

from graphrag.chunk import chunk_corpus
from graphrag.compare import ComparisonResult, run_modes
from graphrag.embed import HashEmbedder
from graphrag.resolve import load_aliases, resolve
from graphrag.sources import load_corpus
from graphrag.store import EmbeddedChunk, MemoryGraphStore, MemoryVectorStore
from graphrag.synthesize import TemplateSynthesizer


def _stores(
    community_root: Path, enhancements_root: Path
) -> tuple[MemoryVectorStore, MemoryGraphStore, HashEmbedder]:
    docs = load_corpus(community_root, enhancements_root)
    graph = MemoryGraphStore.from_graph(resolve(docs))
    embedder = HashEmbedder()
    vstore = MemoryVectorStore()
    chunks = chunk_corpus(docs)
    vectors = embedder.embed([c.text for c in chunks])
    for c, v in zip(chunks, vectors, strict=True):
        vstore.index_chunk(EmbeddedChunk(c, v))
    return vstore, graph, embedder


def _run(community_root: Path, enhancements_root: Path, q: str) -> ComparisonResult:
    vstore, graph, embedder = _stores(community_root, enhancements_root)
    return run_modes(
        q,
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases=load_aliases(),
        max_hops=2,
    )


def test_graph_and_hybrid_enumerate_owned_keps_vector_does_not(
    community_root: Path, enhancements_root: Path
) -> None:
    result = _run(community_root, enhancements_root, "the KEPs the SIG @thockin tech-leads owns")

    graph_ids = set(result.graph.result_ids)
    hybrid_ids = set(result.hybrid.result_ids)
    vector_ids = set(result.vector.result_ids)

    # graph + hybrid expand person:thockin -> sig:sig-network -> owned KEPs (2 hops).
    assert {"kep-1880", "kep-2086"} <= graph_ids
    assert {"kep-1880", "kep-2086"} <= hybrid_ids
    # The structural win, asserted directly (not via an escape-hatch disjunct): the graph
    # path surfaces owned KEPs that vector-only does NOT — vector returns chunk owners, it
    # cannot enumerate "all KEPs owned by sig-network". This fails iff vector-only already
    # enumerates the whole owned set, which would negate the graph-win claim.
    assert {"kep-1880", "kep-2086"} - vector_ids


def test_three_modes_render_side_by_side(community_root: Path, enhancements_root: Path) -> None:
    result = _run(community_root, enhancements_root, "what does SIG Network own")
    rendered = result.render()
    assert "vector" in rendered.lower()
    assert "graph" in rendered.lower()
    assert "hybrid" in rendered.lower()
    # each mode carries its own answer.
    assert result.vector.answer
    assert result.graph.answer
    assert result.hybrid.answer


def test_semantic_led_query_has_vector_chunks(
    community_root: Path, enhancements_root: Path
) -> None:
    result = _run(community_root, enhancements_root, "service internal traffic policy")
    assert result.vector.chunk_ids  # vector mode retrieved chunks
