"""NormativeRetriever — orchestrates the SPARQL + vector-threshold legs.

Two-leg exhaustive retrieval for the ``get_policies`` MCP tool:

1. SPARQL leg (primary): exhaustive SELECT from ``urn:graph:normative`` via
   Neptune/rdflib. Hard-fails with ``NormativeUnavailable`` if unavailable.
2. Vector-threshold leg (additive): OpenSearch kNN with similarity >= threshold.
   Gracefully degrades on failure (returns SPARQL-only result).

``StrategyTrace`` is NOT constructed here — that is the routing dispatch
layer's responsibility (owner: ``graphrag.routing.route_get_policies``).
``retrieve()`` returns a :class:`NormativeResponse` with raw results and
a ``pii_withheld_count`` envelope field.
"""

from __future__ import annotations

import logging

from ..embed import Embedder
from ..store.sparql_base import SparqlStore
from ._sparql import sparql_leg
from ._types import NormativeResponse, NormativeResult
from ._vector import DEFAULT_THRESHOLD, NormativeVectorClient, vector_leg

log = logging.getLogger(__name__)

__all__ = ["NormativeRetriever"]


class NormativeRetriever:
    """Exhaustive retrieval for the ``get_policies`` MCP tool.

    Dependency-injected — all three store clients are passed to ``__init__``,
    none constructed internally. This enables offline testing with
    ``MemorySparqlStore`` + a simple in-memory vector client without patching.

    The vector similarity threshold defaults to the ADR-0012 value (0.7);
    it is configurable via a constructor parameter to keep the retriever pure
    and testable without env-var plumbing.

    Usage::

        retriever = NormativeRetriever(neptune_store, vector_client, embedder)
        response = retriever.retrieve(context="onboarding checklist", domain="HR")
    """

    def __init__(
        self,
        neptune: SparqlStore,
        vector_client: NormativeVectorClient,
        embedder: Embedder,
        *,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._neptune = neptune
        self._vector = vector_client
        self._embedder = embedder
        self._threshold = threshold

    def retrieve(
        self,
        context: str,
        *,
        domain: str | None = None,
        include_pii: bool = False,
        include_future: bool = False,
        today: str | None = None,
    ) -> NormativeResponse:
        """Return all applicable normative documents for ``context``.

        Hard-fails (re-raises ``NormativeUnavailable``) if Neptune is
        unavailable — a partial result is worse than no result (ADR-0012).

        ``domain`` filters to a specific business domain (e.g. ``"HR"``);
        ``None`` returns all domains.

        ``include_pii=True`` includes PII-flagged documents; the default
        ``False`` excludes them and reports the count in
        ``NormativeResponse.pii_withheld_count``.

        ``include_future=True`` includes documents whose
        ``biz:effectiveDate > today``.

        ``today`` is a testing seam (ISO date string); ``None`` uses
        ``datetime.date.today()``.

        The vector-threshold leg gracefully degrades on embedder / kNN failure
        and the SPARQL-only result is returned.
        """
        # ── Step 1: SPARQL leg (primary, exhaustive) ──────────────────────────
        # Raises NormativeUnavailable on Neptune failure — propagated directly.
        # PII filter is NOT applied here; it is applied in Python below so that
        # pii_withheld_count can be computed without a second COUNT query.
        sparql_all: list[NormativeResult] = sparql_leg(
            self._neptune,
            domain=domain,
            include_future=include_future,
            today=today,
        )

        # ── Step 2: vector-threshold leg (additive only) ──────────────────────
        # Deduplication is against the *full* SPARQL result set (pre-PII-filter)
        # so a PII-flagged policy already found by SPARQL is not re-surfaced.
        sparql_all_uris: frozenset[str] = frozenset(r.uri for r in sparql_all)
        vector_additions: list[NormativeResult] = vector_leg(
            self._vector,
            self._embedder,
            context,
            threshold=self._threshold,
            sparql_uris=sparql_all_uris,
        )

        # ── Step 3: union + PII filter ────────────────────────────────────────
        combined: list[NormativeResult] = sparql_all + vector_additions

        if include_pii:
            results = combined
            pii_withheld_count = 0
        else:
            results = [r for r in combined if not r.pii_flagged]
            pii_withheld_count = len(combined) - len(results)

        return NormativeResponse(results=results, pii_withheld_count=pii_withheld_count)
