"""The ``VectorStore`` seam — the vector-half twin of ``GraphStore`` (slice-2 AC3/AC4).

A backend persists embedded chunks and answers a k-NN query. Like the graph store,
the in-memory implementation is the offline / test / demo backend and the OpenSearch
adapter is the deployed one; both return the same ``VectorHit`` shape so the
retrieval trace is identical offline and live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..chunk import Chunk


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
        self, vector: list[float], k: int, *, allowed_labels: frozenset[str] | None = None
    ) -> list[VectorHit]:
        """The ``k`` nearest chunks to ``vector`` by cosine similarity, score-descending.

        ``allowed_labels`` is the slice-4 permission filter (a teaching stand-in for an
        ACL, never real authz): when not ``None``, only chunks whose ``visibility`` is in
        the set are eligible, applied **during** the k-NN search (an OpenSearch metadata
        ``filter`` on the ANN query; the in-memory equivalent). ``None`` = unfiltered.
        """

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def delete(self, ids: list[str]) -> None: ...
