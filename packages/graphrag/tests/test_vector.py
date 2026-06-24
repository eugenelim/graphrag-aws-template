"""T5 — semantic retrieval + the legible trace (AC5, AC10 for query).

The trace-shape assertions use hand-built vectors; the semantic-led exemplar
("risks of in-place pod resize" -> KEP-1287) runs through real frozen Titan v2
vectors so the win is genuine, not lexical.

# STUB: AC5
# STUB: AC10
"""

from __future__ import annotations

from pathlib import Path

from graphrag.chunk import Chunk, chunk_corpus
from graphrag.embed import Embedder
from graphrag.sources import load_corpus
from graphrag.store.vector_base import EmbeddedChunk
from graphrag.store.vector_memory import MemoryVectorStore
from graphrag.vector import vector_search
from graphrag.vector_eval import load_frozen, load_query_set

FIXT = Path(__file__).parent / "fixtures" / "vector"


class _FixedEmbedder:
    """Returns a preset vector for any text — for trace-shape assertions."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    @property
    def model_id(self) -> str:
        return "fixed-test"

    @property
    def dimensions(self) -> int:
        return len(self._vector)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


class _FrozenEmbedder:
    """Maps a known query string to its committed real Titan v2 vector."""

    def __init__(self, by_text: dict[str, list[float]]) -> None:
        self._by_text = by_text

    @property
    def model_id(self) -> str:
        return "amazon.titan-embed-text-v2:0"

    @property
    def dimensions(self) -> int:
        return 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._by_text[t] for t in texts]


def _trace_store() -> MemoryVectorStore:
    store = MemoryVectorStore()
    store.index_chunk(
        EmbeddedChunk(
            Chunk(
                "kep-1287#0", "resize", "enhancements", "keps/n/README.md", "Summary", ["kep-1287"]
            ),
            [1.0, 0.0],
        )
    )
    store.index_chunk(
        EmbeddedChunk(
            Chunk(
                "sig#0", "charter", "community", "sig-node/README.md", "SIG Node", ["sig:sig-node"]
            ),
            [0.0, 1.0],
        )
    )
    return store


def test_render_names_query_model_and_each_hit_with_provenance() -> None:
    result = vector_search(_trace_store(), _FixedEmbedder([1.0, 0.0]), "anything", k=2)
    out = result.render()
    # Ordered: query line, embedding line, then ranked hits with provenance + entities.
    lines = out.splitlines()
    assert lines[0].startswith("query: anything")
    assert lines[1].startswith("embedding: fixed-test (dim=2)")
    assert "1. score=" in out and "keps/n/README.md # Summary" in out
    assert "entities: kep-1287" in out
    # Top hit is the one aligned with the query vector.
    assert result.hits[0].chunk.id == "kep-1287#0"
    assert out.index("1. score=") < out.index("2. score=")  # ranked order


def test_no_hits_renders_cleanly() -> None:
    result = vector_search(MemoryVectorStore(), _FixedEmbedder([1.0, 0.0]), "q", k=3)
    assert "(no hits)" in result.render()


def test_semantic_exemplar_in_place_pod_resize_returns_kep_1287() -> None:
    # Real frozen Titan v2 vectors: a natural question retrieves the KEP-1287 README.
    corpus = {
        c.id: c
        for c in chunk_corpus(load_corpus(FIXT / "corpus/community", FIXT / "corpus/enhancements"))
    }
    frozen = load_frozen(FIXT / "frozen_embeddings.json")
    cases = {c.id: c for c in load_query_set(FIXT / "query_set.yaml")}

    store = MemoryVectorStore()
    for cid, vec in frozen["chunks"].items():
        store.index_chunk(EmbeddedChunk(corpus[cid], vec))

    q1 = cases["q1"]
    embedder: Embedder = _FrozenEmbedder({q1.query: frozen["queries"]["q1"]})
    result = vector_search(store, embedder, q1.query, k=5)
    assert "1287-in-place-update-pod-resources" in result.hits[0].chunk.doc_path
