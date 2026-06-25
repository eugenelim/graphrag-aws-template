"""T3 — in-memory vector store cosine k-NN (AC3).

# STUB: AC3
"""

from __future__ import annotations

from graphrag.chunk import Chunk
from graphrag.store.vector_base import EmbeddedChunk
from graphrag.store.vector_memory import MemoryVectorStore


def _ec(cid: str, vector: list[float]) -> EmbeddedChunk:
    return EmbeddedChunk(Chunk(cid, cid, "src", f"{cid}.md", "H", []), vector)


def _store() -> MemoryVectorStore:
    store = MemoryVectorStore()
    store.index_chunk(_ec("a", [1.0, 0.0, 0.0]))
    store.index_chunk(_ec("b", [0.9, 0.1, 0.0]))
    store.index_chunk(_ec("c", [0.0, 1.0, 0.0]))
    return store


def test_knn_returns_top_k_cosine_ordered() -> None:
    hits = _store().knn([1.0, 0.0, 0.0], k=2)
    assert [h.chunk.id for h in hits] == ["a", "b"]  # a is identical, b is closest
    assert hits[0].score >= hits[1].score
    assert hits[0].score > 0.99


def test_empty_store_returns_empty() -> None:
    assert MemoryVectorStore().knn([1.0, 0.0, 0.0], k=5) == []


def test_k_larger_than_corpus_returns_all() -> None:
    store = _store()
    assert len(store.knn([1.0, 0.0, 0.0], k=99)) == 3
    assert store.count() == 3


def test_delete_removes_by_id() -> None:
    store = _store()
    store.delete(["a", "missing"])
    assert store.count() == 2
    assert "a" not in [h.chunk.id for h in store.knn([1.0, 0.0, 0.0], k=99)]


def test_delete_by_doc_disambiguates_source() -> None:
    # Two chunks share the bare path "README.md" but differ by source — delete_by_doc keyed by
    # the source-qualified doc id must remove only the named one (slice-5 orphan removal).
    store = MemoryVectorStore()
    store.index_chunk(
        EmbeddedChunk(Chunk("community/README.md#0", "t", "community", "README.md", "H", []), [1.0])
    )
    store.index_chunk(
        EmbeddedChunk(
            Chunk("enhancements/README.md#0", "t", "enhancements", "README.md", "H", []), [1.0]
        )
    )
    store.delete_by_doc(["enhancements/README.md"])
    remaining = {h.chunk.id for h in store.knn([1.0], k=99)}
    assert remaining == {"community/README.md#0"}


def test_delete_by_doc_removes_all_chunks_of_a_doc() -> None:
    store = MemoryVectorStore()
    for ordinal in range(3):
        store.index_chunk(
            EmbeddedChunk(
                Chunk(f"community/a.md#{ordinal}", "t", "community", "a.md", "H", []), [1.0]
            )
        )
    store.delete_by_doc(["community/a.md"])
    assert store.count() == 0


def test_clear_empties_vector_store() -> None:
    store = _store()
    store.clear()
    assert store.count() == 0


def test_zero_vector_scores_zero_not_nan() -> None:
    store = MemoryVectorStore()
    store.index_chunk(_ec("z", [0.0, 0.0, 0.0]))
    hits = store.knn([1.0, 0.0, 0.0], k=1)
    assert hits[0].score == 0.0


# --- slice-4: in-memory permission filter during k-NN (AC4) ---------------------------


def _ec_vis(cid: str, vector: list[float], visibility: str) -> EmbeddedChunk:
    return EmbeddedChunk(
        Chunk(cid, cid, "src", f"{cid}.md", "H", [], visibility=visibility), vector
    )


def test_knn_filters_chunks_above_clearance() -> None:
    store = MemoryVectorStore()
    store.index_chunk(_ec_vis("pub", [1.0, 0.0, 0.0], "public"))
    store.index_chunk(_ec_vis("res", [1.0, 0.0, 0.0], "restricted"))
    # public-reader sees only the public chunk (the restricted one is not a candidate).
    hits = store.knn([1.0, 0.0, 0.0], k=10, allowed_labels=frozenset({"public"}))
    assert {h.chunk.id for h in hits} == {"pub"}
    # no clearance -> unfiltered (both).
    assert {h.chunk.id for h in store.knn([1.0, 0.0, 0.0], k=10)} == {"pub", "res"}
