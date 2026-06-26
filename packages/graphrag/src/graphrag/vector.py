"""Semantic retrieval with a legible trace — the narratable vector query (AC5/AC10).

``vector_search`` embeds the question, runs k-NN over a ``VectorStore``, and returns a
``VectorQueryResult`` whose ``render()`` names the query, the embedding model + dims,
and every hit with its score and source provenance (repo, doc path, heading, owning
entities) — charter principle 1 (no black-box hop) as a data structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .embed import Embedder
from .store.vector_base import VectorHit, VectorStore
from .visibility import Clearance

if TYPE_CHECKING:
    from .selfquery import MetadataFilter

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
    store: VectorStore,
    embedder: Embedder,
    query: str,
    k: int = DEFAULT_K,
    *,
    clearance: Clearance | None = None,
    metadata_filter: MetadataFilter | None = None,
) -> VectorQueryResult:
    """Embed ``query``, retrieve the top-``k`` chunks, and return the traced result.

    When ``clearance`` is set (slice-4 permission filter), the allowed visibility tiers are
    threaded into ``knn`` so a chunk above clearance is never a candidate (the filter rides
    the ANN search, not a post-filter). ``None`` = unfiltered (slice-2/3 behavior).

    When ``metadata_filter`` is set (the self-query structured filter), it is threaded into
    ``knn`` as an independent clause composed with ``clearance`` — both applied during ANN, so
    a self-query filter can only narrow, never widen past clearance. ``None``/empty =
    unfiltered.
    """
    vector = embedder.embed([query])[0]
    allowed = clearance.allowed if clearance is not None else None
    hits = store.knn(vector, k, allowed_labels=allowed, metadata_filter=metadata_filter)
    return VectorQueryResult(
        query=query, model_id=embedder.model_id, dimensions=embedder.dimensions, hits=hits
    )
