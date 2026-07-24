"""Vector-threshold leg for graphrag.normative.

Runs an approximate nearest-neighbour query against the normative partition
and returns results that are NOT already in the SPARQL result set
(additive-only semantics — the vector leg never removes SPARQL results).

The ``NormativeVectorClient`` protocol decouples this leg from the existing
``VectorStore`` / ``Chunk`` abstraction (which is K8s-corpus-specific).
The production implementation would be an OpenSearch adapter that applies
a ``named_graph = "urn:graph:normative"`` bool filter; the offline
substitute is ``_MemoryNormativeVectorClient`` in the test suite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from ..embed import Embedder
from ._types import NormativeResult

log = logging.getLogger(__name__)

# ADR-0012 minimum cosine similarity for vector-leg additions.
DEFAULT_THRESHOLD: float = 0.7

# Upper bound on vector candidates before threshold filtering.
# Large enough to be effectively unlimited for the normative partition.
_VECTOR_K_MAX: int = 1000

# Named-graph URI for the normative partition — always required as a filter.
NORMATIVE_GRAPH: str = "urn:graph:normative"


@dataclass
class NormativeVectorHit:
    """A single vector search hit for a normative document.

    The production adapter populates these fields from the OpenSearch
    document metadata; the offline substitute populates them from fixture
    dicts in tests.
    """

    doc_uri: str
    score: float
    title: str | None = None
    doc_type: str | None = None
    domain: str | None = None
    effective_date: str | None = None
    scope: str | None = None
    pii_flagged: bool = False
    git_commit: str | None = None
    git_path: str | None = None


class NormativeVectorClient(Protocol):
    """Minimal vector-store protocol for the normative retrieval leg.

    The implementation is expected to scope results to ``named_graph`` via a
    mandatory ``bool.filter`` (OpenSearch) or equivalent, never returning
    documents from the descriptive partition.
    """

    def knn(
        self,
        vector: list[float],
        *,
        named_graph: str,
        k_max: int,
    ) -> list[NormativeVectorHit]:
        """Return up to ``k_max`` nearest hits from ``named_graph``, unfiltered
        by threshold (threshold is applied by the caller)."""
        ...  # pragma: no cover


def vector_leg(
    client: NormativeVectorClient,
    embedder: Embedder,
    context: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    sparql_uris: frozenset[str],
) -> list[NormativeResult]:
    """Execute the vector-threshold leg; return additive-only results.

    Graceful degrade on embedding or kNN failure (log WARNING; return ``[]``).
    The SPARQL leg alone satisfies exhaustive recall from the structured
    partition.

    ``sparql_uris`` is the full set of document URIs returned by the SPARQL
    leg (pre-PII-filter); deduplication is against this full set so a
    PII-flagged policy already found by SPARQL is not re-surfaced by the
    vector leg.
    """
    # Step 1 — embed the query context.
    try:
        vectors = embedder.embed([context])
        if not vectors:
            log.warning("Vector leg: embedder returned empty result; skipping")
            return []
        query_vector = vectors[0]
    except Exception as exc:
        log.warning("Vector leg: embedding failed (%s); skipping", exc)
        return []

    # Step 2 — kNN against the normative partition.
    try:
        hits = client.knn(
            query_vector,
            named_graph=NORMATIVE_GRAPH,
            k_max=_VECTOR_K_MAX,
        )
    except Exception as exc:
        log.warning("Vector leg: kNN failed (%s); skipping", exc)
        return []

    # Step 3 — apply threshold and deduplicate against SPARQL URIs.
    added: list[NormativeResult] = []
    for hit in hits:
        if hit.score < threshold:
            continue  # below similarity threshold
        if hit.doc_uri in sparql_uris:
            continue  # already returned by SPARQL leg — do not re-add

        added.append(
            NormativeResult(
                uri=hit.doc_uri,
                title=hit.title or "",
                doc_type=hit.doc_type or "Unknown",
                domain=hit.domain,
                effective_date=hit.effective_date,
                scope=hit.scope,
                pii_flagged=hit.pii_flagged,
                relevance=hit.score,
                git_commit=hit.git_commit,
                git_path=hit.git_path,
            )
        )

    return added
