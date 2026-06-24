"""Semantic retrieval with a legible trace — the narratable vector query (AC5/AC10).

``vector_search`` embeds the question, runs k-NN over a ``VectorStore``, and returns a
``VectorQueryResult`` whose ``render()`` names the query, the embedding model + dims,
and every hit with its score and source provenance (repo, doc path, heading, owning
entities) — charter principle 1 (no black-box hop) as a data structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .embed import Embedder
from .store.vector_base import VectorHit, VectorStore

DEFAULT_K = 5


@dataclass
class VectorQueryResult:
    query: str
    model_id: str
    dimensions: int
    hits: list[VectorHit] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"query: {self.query}",
            f"embedding: {self.model_id} (dim={self.dimensions})",
            f"top-{len(self.hits)} hits:",
        ]
        for rank, hit in enumerate(self.hits, start=1):
            chunk = hit.chunk
            entities = ", ".join(chunk.entity_ids) or "(none)"
            lines.append(
                f"  {rank}. score={hit.score:.4f}  [{chunk.source}] "
                f"{chunk.doc_path} # {chunk.heading or '(intro)'}"
            )
            lines.append(f"       entities: {entities}")
        if not self.hits:
            lines.append("  (no hits)")
        return "\n".join(lines)


def vector_search(
    store: VectorStore, embedder: Embedder, query: str, k: int = DEFAULT_K
) -> VectorQueryResult:
    """Embed ``query``, retrieve the top-``k`` chunks, and return the traced result."""
    vector = embedder.embed([query])[0]
    hits = store.knn(vector, k)
    return VectorQueryResult(
        query=query, model_id=embedder.model_id, dimensions=embedder.dimensions, hits=hits
    )
