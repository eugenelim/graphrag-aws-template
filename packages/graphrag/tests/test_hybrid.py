"""T4 — seed-and-expand orchestration with a dual-seed, bounded trace (AC4).

Over the fixture corpus with in-memory stores + the offline HashEmbedder +
TemplateSynthesizer. The offline embedder is non-semantic, so the hybrid win is
asserted **structurally** (the entity-led query's owned-KEP set shows up via the
2-hop expansion), never by similarity score.

# STUB: AC4
"""

from __future__ import annotations

from pathlib import Path

from graphrag.chunk import chunk_corpus
from graphrag.embed import HashEmbedder
from graphrag.hybrid import HybridResult, hybrid_query
from graphrag.model import EntityKind, Node
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


def test_entity_led_dual_seed_and_two_hop_expansion(
    community_root: Path, enhancements_root: Path
) -> None:
    vstore, graph, embedder = _stores(community_root, enhancements_root)
    result = hybrid_query(
        "the KEPs the SIG @thockin tech-leads owns",
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases=load_aliases(),
        max_hops=2,
    )
    assert isinstance(result, HybridResult)

    # @thockin links to person:thockin (a handle), tagged source=question — NOT the SIG.
    q_seeds = {s.entity_id for s in result.seeds if s.source == "question"}
    assert "person:thockin" in q_seeds
    assert "sig:sig-network" not in q_seeds  # the SIG is not a question seed here

    # The 2-hop expansion person:thockin -> sig:sig-network -> owned KEPs surfaces the
    # owned set. sig:sig-network is part of the neighborhood (a seed or reached); the
    # owned KEPs are reached via the expansion.
    reached = set(result.hop_trace.result_ids)
    seed_ids = {s.entity_id for s in result.seeds}
    assert "sig:sig-network" in (reached | seed_ids)
    assert {"kep-1880", "kep-2086"} <= reached


def test_render_is_ordered_seeds_hops_citations_answer(
    community_root: Path, enhancements_root: Path
) -> None:
    vstore, graph, embedder = _stores(community_root, enhancements_root)
    result = hybrid_query(
        "what does SIG Network own",
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases=load_aliases(),
    )
    rendered = result.render()
    assert rendered.index("seeds") < rendered.index("hops") < rendered.index("citations")
    assert rendered.index("citations") < rendered.index("answer")
    # the seed source split is visible.
    assert "vector" in rendered and "question" in rendered


def test_unconfirmed_question_candidate_is_dropped(
    community_root: Path, enhancements_root: Path
) -> None:
    vstore, graph, embedder = _stores(community_root, enhancements_root)
    result = hybrid_query(
        "ask @nobody about KEP 99999",
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases=load_aliases(),
    )
    dropped = {c.entity_id for c in result.dropped_candidates}
    assert "person:nobody" in dropped  # linked but absent from the graph -> dropped
    assert all(s.entity_id != "person:nobody" for s in result.seeds)


def test_seed_cap_truncates_and_records() -> None:
    # A graph with many chunk owners forces the seed set past a small cap.
    graph = MemoryGraphStore()
    vstore = MemoryVectorStore()
    embedder = HashEmbedder()
    from graphrag.chunk import Chunk

    texts = []
    for i in range(12):
        sig = f"sig:s{i}"
        graph.upsert_node(Node(sig, EntityKind.SIG))
        chunk = Chunk(
            id=f"c{i}",
            text=f"chunk about topic {i}",
            source="X",
            doc_path=f"d{i}.md",
            heading="H",
            entity_ids=[sig],
        )
        texts.append(chunk)
    vectors = embedder.embed([c.text for c in texts])
    for c, v in zip(texts, vectors, strict=True):
        vstore.index_chunk(EmbeddedChunk(c, v))

    result = hybrid_query(
        "topic",
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases={},
        k=12,
        seed_cap=3,
    )
    assert len(result.seeds) == 3
    assert result.seed_cap_truncated is True
    assert result.vector_truncated is True
    assert result.question_truncated is False
    assert "[seed cap truncated]" in result.render()


def test_seed_cap_keeps_question_seed_and_blames_vector() -> None:
    # Many vector owners + one question-linked seed, with a cap smaller than the vector
    # owners alone. The question seed (the entity-led pedagogy) MUST survive; the
    # truncation is attributed to vector, never to the question source.
    graph = MemoryGraphStore()
    vstore = MemoryVectorStore()
    embedder = HashEmbedder()
    from graphrag.chunk import Chunk

    graph.upsert_node(Node("sig:sig-network", EntityKind.SIG))  # the question-linked node
    chunks = []
    for i in range(12):
        sig = f"sig:s{i}"
        graph.upsert_node(Node(sig, EntityKind.SIG))
        chunks.append(
            Chunk(
                id=f"c{i}",
                text=f"topic {i}",
                source="X",
                doc_path=f"d{i}.md",
                heading="H",
                entity_ids=[sig],
            )
        )
    for c, v in zip(chunks, embedder.embed([c.text for c in chunks]), strict=True):
        vstore.index_chunk(EmbeddedChunk(c, v))

    result = hybrid_query(
        "what does SIG Network own",  # links sig:sig-network as source=question
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases={},
        k=12,
        seed_cap=3,
    )
    q_seeds = {s.entity_id for s in result.seeds if s.source == "question"}
    assert "sig:sig-network" in q_seeds  # the question seed survived the cap
    assert result.vector_truncated is True
    assert result.question_truncated is False
    # the truncation note sits on the vector line, not the question line.
    rendered = result.render()
    vector_line = next(ln for ln in rendered.splitlines() if ln.strip().startswith("vector:"))
    question_line = next(ln for ln in rendered.splitlines() if ln.strip().startswith("question:"))
    assert "[seed cap truncated]" in vector_line
    assert "[seed cap truncated]" not in question_line


def test_merge_dedupes_chunks_and_nodes(community_root: Path, enhancements_root: Path) -> None:
    vstore, graph, embedder = _stores(community_root, enhancements_root)
    result = hybrid_query(
        "service networking",
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases=load_aliases(),
    )
    chunk_ids = [c.chunk.id for c in result.chunks]
    assert len(chunk_ids) == len(set(chunk_ids))  # deduped chunks
    node_ids = [n.id for n in result.graph_nodes]
    assert len(node_ids) == len(set(node_ids))  # deduped nodes
    assert result.answer  # synthesized, non-empty


# --- slice-4: permission-filtered hybrid + filtered-out trace (AC5) -------------------

from graphrag.labels import label_chunks, label_graph, load_labels  # noqa: E402
from graphrag.visibility import resolve_clearance  # noqa: E402


def _labeled_stores(
    community_root: Path, enhancements_root: Path
) -> tuple[MemoryVectorStore, MemoryGraphStore, HashEmbedder]:
    docs = load_corpus(community_root, enhancements_root)
    graph_obj = resolve(docs)
    label_graph(graph_obj, load_labels())  # kep-1287=restricted, kep-1880=internal
    graph = MemoryGraphStore.from_graph(graph_obj)
    embedder = HashEmbedder()
    vstore = MemoryVectorStore()
    chunks = chunk_corpus(docs)
    label_chunks(chunks, load_labels())
    vectors = embedder.embed([c.text for c in chunks])
    for c, v in zip(chunks, vectors, strict=True):
        vstore.index_chunk(EmbeddedChunk(c, v))
    return vstore, graph, embedder


def _hybrid(vstore, graph, embedder, q, persona):  # type: ignore[no-untyped-def]
    return hybrid_query(
        q,
        vector_store=vstore,
        graph_store=graph,
        embedder=embedder,
        synthesizer=TemplateSynthesizer(),
        aliases=load_aliases(),
        max_hops=2,
        clearance=resolve_clearance(persona) if persona else None,
    )


def test_hybrid_public_reader_excludes_restricted_kep(
    community_root: Path, enhancements_root: Path
) -> None:
    vstore, graph, embedder = _labeled_stores(community_root, enhancements_root)
    q = "What KEPs does SIG Node own?"  # sig-node OWNS kep-9 (public) + kep-1287 (restricted)

    reader = _hybrid(vstore, graph, embedder, q, "public-reader")
    maint = _hybrid(vstore, graph, embedder, q, "maintainer")

    reader_nodes = {n.id for n in reader.graph_nodes}
    maint_nodes = {n.id for n in maint.graph_nodes}
    # the restricted KEP is absent for the reader, present for the maintainer — divergence.
    assert "kep-1287" not in reader_nodes
    assert "kep-1287" in maint_nodes
    # the public sibling KEP is still reachable for the reader (sig-node OWNS kep-9).
    assert "kep-9" in reader_nodes
    # the final merged-node guard: NO node above the reader's clearance leaks through.
    assert all(n.props.get("visibility", "public") == "public" for n in reader.graph_nodes)


def test_hybrid_filtered_question_seed_recorded_and_traced(
    community_root: Path, enhancements_root: Path
) -> None:
    vstore, graph, embedder = _labeled_stores(community_root, enhancements_root)
    # The question names the restricted KEP directly; for a public-reader it is filtered
    # (recorded), never seeded — distinct from an unconfirmed-candidate drop.
    reader = _hybrid(vstore, graph, embedder, "summarize KEP-1287", "public-reader")
    filtered_ids = {c.entity_id for c in reader.filtered_seeds}
    assert "kep-1287" in filtered_ids
    assert "kep-1287" not in {s.entity_id for s in reader.seeds}
    rendered = reader.render()
    assert "clearance: persona=public-reader" in rendered
    assert "filtered (visibility" in rendered
    assert "not real authz" in rendered  # synthetic stand-in label present


def test_hybrid_no_clearance_render_unchanged(
    community_root: Path, enhancements_root: Path
) -> None:
    vstore, graph, embedder = _labeled_stores(community_root, enhancements_root)
    result = _hybrid(vstore, graph, embedder, "What KEPs does SIG Node own?", None)
    rendered = result.render()
    # No persona => no clearance/filtered lines (slice-3 render shape unchanged).
    assert "clearance:" not in rendered
    assert "filtered (visibility" not in rendered
