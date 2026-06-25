"""In-memory cosine-kNN vector store — the offline / test / demo backend (AC3)."""

from __future__ import annotations

import math

from .vector_base import EmbeddedChunk, VectorHit, VectorStore


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 if either vector is zero-length (no NaN)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryVectorStore(VectorStore):
    """A ``VectorStore`` backed by an in-memory dict, scored by cosine on query."""

    def __init__(self) -> None:
        self._items: dict[str, EmbeddedChunk] = {}

    def index_chunk(self, embedded: EmbeddedChunk) -> None:
        self._items[embedded.chunk.id] = embedded

    def knn(
        self, vector: list[float], k: int, *, allowed_labels: frozenset[str] | None = None
    ) -> list[VectorHit]:
        hits = [
            VectorHit(ec.chunk, cosine(vector, ec.vector))
            for ec in self._items.values()
            # slice-4 permission filter: a chunk above clearance is not even a candidate.
            if allowed_labels is None or ec.chunk.visibility in allowed_labels
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: max(0, k)]

    def count(self) -> int:
        return len(self._items)

    def delete(self, ids: list[str]) -> None:
        for node_id in ids:
            self._items.pop(node_id, None)

    def delete_by_doc(self, doc_ids: list[str]) -> None:
        targets = set(doc_ids)
        self._items = {
            cid: ec
            for cid, ec in self._items.items()
            if f"{ec.chunk.source}/{ec.chunk.doc_path}" not in targets
        }

    def clear(self) -> None:
        self._items = {}
