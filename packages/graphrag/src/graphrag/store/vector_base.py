"""The ``VectorStore`` seam — the vector-half twin of ``GraphStore`` (slice-2 AC3/AC4).

A backend persists embedded chunks and answers a k-NN query. Like the graph store,
the in-memory implementation is the offline / test / demo backend and the OpenSearch
adapter is the deployed one; both return the same ``VectorHit`` shape so the
retrieval trace is identical offline and live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..chunk import Chunk

if TYPE_CHECKING:
    # Type-only import — avoids a runtime cycle (selfquery imports vector → vector_base).
    from ..selfquery import MetadataFilter


@dataclass
class EmbeddedChunk:
    """A chunk paired with its embedding vector — the unit written to the index."""

    chunk: Chunk
    vector: list[float]


@dataclass
class VectorHit:
    """A retrieved chunk with its similarity score — the unit returned by a query."""

    chunk: Chunk
    score: float


class VectorStore(ABC):
    def create_index(self) -> None:  # noqa: B027  (intentional optional hook, not abstract)
        """Create any backend index/schema needed before indexing. No-op by default
        (the in-memory store has nothing to create; OpenSearch overrides this)."""

    @abstractmethod
    def index_chunk(self, embedded: EmbeddedChunk) -> None: ...

    @abstractmethod
    def knn(
        self,
        vector: list[float],
        k: int,
        *,
        allowed_labels: frozenset[str] | None = None,
        metadata_filter: MetadataFilter | None = None,
    ) -> list[VectorHit]:
        """The ``k`` nearest chunks to ``vector`` by cosine similarity, score-descending.

        ``allowed_labels`` is the slice-4 permission filter (a teaching stand-in for an
        ACL, never real authz): when not ``None``, only chunks whose ``visibility`` is in
        the set are eligible, applied **during** the k-NN search (an OpenSearch metadata
        ``filter`` on the ANN query; the in-memory equivalent). ``None`` = unfiltered.

        ``metadata_filter`` is the self-query structured filter (``source``/``entity_ids``):
        when not ``None``/empty, only chunks the filter ``matches`` are eligible. It composes
        with ``allowed_labels`` as an **independent** clause (both applied during ANN), so the
        self-query filter can only *narrow*, never widen past clearance. ``None``/empty =
        unfiltered. The two have deliberately opposite empty-semantics: an empty
        ``metadata_filter`` is unfiltered, whereas an **empty** ``allowed_labels`` matches
        nothing (the fail-closed permission semantics) — neither affects the other.
        """

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def delete(self, ids: list[str]) -> None: ...

    @abstractmethod
    def delete_by_doc(self, doc_ids: list[str]) -> None:
        """Delete every chunk belonging to the named documents (slice-5 orphan removal).

        Each ``doc_id`` is the source-qualified ``{source}/{path}`` key; because a chunk's
        ``doc_path`` is source-less, the match is on **source AND doc_path together** — never
        ``doc_path`` alone, which would cross-delete a same-named doc in the other source."""

    @abstractmethod
    def clear(self) -> None:
        """Remove every chunk — the ``--rebuild`` ground-truth reset (slice 5)."""
