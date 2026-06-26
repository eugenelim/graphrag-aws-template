"""The ``ParentChildStore`` seam — the nested-vector twin of ``VectorStore`` (parent-child slice).

Parent-Child Retriever: small **child** chunks carry the vectors for precise matching;
the larger **parent** document body is returned for context-complete synthesis. On
OpenSearch the two live in **one nested document** — the parent holds its children as a
``nested`` array (each child a ``knn_vector``) plus the parent's full prose in an
app-stored ``body`` field (RFC-0001 §3; **not** an Elasticsearch ``has_child`` cross-doc
join — the app stores/fetches the parent body). A nested k-NN query matches a child vector
and returns the parent document, scored by its **best** child (``score_mode: max``).

Like the flat ``VectorStore``, the in-memory implementation is the offline / test / demo
backend and the OpenSearch adapter is the deployed one; both return the same ``ParentHit``
shape (parent + matched child) so the retrieval trace is identical offline and live.

This module is **PyYAML-free** (pure dataclasses + an ABC), so it bundles in the
``Code.from_asset`` query Lambda.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ChildVector:
    """A child chunk (a small, precise unit) paired with its embedding — the nested sub-doc."""

    child_id: str
    heading: str
    text: str
    vector: list[float]


@dataclass(frozen=True)
class ParentDoc:
    """A parent document: its provenance + visibility, its full app-stored ``body``, and its
    ordered children. ``parent_id`` is the source-qualified ``{source}/{doc_path}`` key; a
    document's chunks share one composed ``visibility`` tier and the same ``entity_ids``."""

    parent_id: str
    source: str
    doc_path: str
    heading: str  # the ordinal-0 child's heading — a stable parent label (cited by doc_path)
    entity_ids: tuple[str, ...]
    visibility: str
    body: str
    children: tuple[ChildVector, ...] = ()


@dataclass(frozen=True)
class ParentHit:
    """A retrieved parent with its best-child score and the child that matched — the unit a
    parent-child search returns. ``matched_child`` is the precise match; ``parent.body`` is the
    complete context handed to synthesis."""

    parent: ParentDoc
    score: float
    matched_child: ChildVector | None = None


class ParentChildStore(ABC):
    """A backend that persists ``ParentDoc``s (parents + nested child vectors) and answers a
    nested k-NN query, scoring each parent by its best-matching child."""

    def create_index(self) -> None:  # noqa: B027  (intentional optional hook, not abstract)
        """Create any backend index/schema needed before indexing. No-op by default
        (the in-memory store has nothing to create; OpenSearch overrides this)."""

    @abstractmethod
    def index_parent(self, parent: ParentDoc) -> None: ...

    @abstractmethod
    def search(
        self,
        vector: list[float],
        k: int,
        *,
        allowed_labels: frozenset[str] | None = None,
    ) -> list[ParentHit]:
        """The ``k`` parents whose best child is nearest to ``vector`` (cosine), score-descending.

        ``allowed_labels`` is the slice-4 permission filter (a teaching stand-in for an ACL,
        never real authz): when not ``None``, only parents whose ``visibility`` is in the set are
        eligible, applied as a parent-level filter composed AND with the nested child match (it can
        only narrow). ``None`` = unrestricted; an **empty** set matches nothing (the fail-closed
        permission semantics)."""

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def clear(self) -> None:
        """Remove every parent (the ``--rebuild`` ground-truth reset)."""
