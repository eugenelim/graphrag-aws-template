"""Integration tests for NormativeRetriever.retrieve() — full two-leg flow.

Uses MemorySparqlStore + MemoryNormativeVectorClient for offline testing.
The vector client returns pre-computed hits rather than real embeddings so
deduplication and PII/date filter logic are tested deterministically.
"""

from __future__ import annotations

import pytest

from graphrag.embed import HashEmbedder
from graphrag.normative import NormativeRetriever, NormativeUnavailable
from graphrag.normative._vector import (
    NORMATIVE_GRAPH,
    NormativeVectorHit,
)
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

# ── Shared constants ──────────────────────────────────────────────────────────

NORMATIVE = NORMATIVE_GRAPH

# ── Reusable offline vector client ───────────────────────────────────────────


class MemoryNormativeVectorClient:
    """Pre-computed hit list; returned for any query vector."""

    def __init__(self, hits: list[NormativeVectorHit] | None = None) -> None:
        self._hits: list[NormativeVectorHit] = hits or []

    def knn(
        self,
        vector: list[float],
        *,
        named_graph: str,
        k_max: int,
    ) -> list[NormativeVectorHit]:
        return self._hits[:k_max]


class _FailingStore:
    """Simulates Neptune connection failure."""

    def sparql_select(self, query: str) -> list[dict]:  # type: ignore[override]
        raise ConnectionError("connection refused")

    def sparql_construct(self, query: str):  # type: ignore[return]
        raise ConnectionError("connection refused")

    def sparql_update(self, update: str) -> None:
        raise ConnectionError("connection refused")

    def load_turtle(self, ttl: str, named_graph: str) -> None:
        raise ConnectionError("connection refused")


_EMBEDDER = HashEmbedder()

# ── Fixture TTL ───────────────────────────────────────────────────────────────

_BASE_TTL = """
@prefix biz:    <https://graphrag-aws.demo/biz-ops/ontology#> .
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<urn:biz:policy:finance-1>
    a biz:Policy ;
    schema:name "Finance Policy 1" ;
    biz:gitCommitSHA "sha001" ;
    biz:gitPath "policies/finance/p1.md" ;
    biz:hasPII false ;
    biz:inDomain biz:Finance ;
    biz:effectiveDate "2024-01-01"^^xsd:date ;
    biz:scope "all-staff" .

<urn:biz:policy:finance-2>
    a biz:Standard ;
    schema:name "Finance Standard 2" ;
    biz:gitCommitSHA "sha002" ;
    biz:gitPath "policies/finance/s2.md" ;
    biz:hasPII false ;
    biz:inDomain biz:Finance ;
    biz:effectiveDate "2024-03-01"^^xsd:date ;
    biz:scope "finance-team" .

<urn:biz:policy:finance-pii>
    a biz:Guideline ;
    schema:name "Finance PII Guideline" ;
    biz:gitCommitSHA "sha003" ;
    biz:hasPII true ;
    biz:inDomain biz:Finance ;
    biz:effectiveDate "2024-06-01"^^xsd:date ;
    biz:scope "data-controllers" .

<urn:biz:policy:hr-1>
    a biz:Policy ;
    schema:name "HR Leave Policy" ;
    biz:gitCommitSHA "sha004" ;
    biz:gitPath "policies/hr/leave.md" ;
    biz:hasPII false ;
    biz:inDomain biz:HR ;
    biz:effectiveDate "2023-07-01"^^xsd:date ;
    biz:scope "all-staff" .
"""

_DATE_TTL = """
@prefix biz:    <https://graphrag-aws.demo/biz-ops/ontology#> .
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<urn:biz:policy:past>
    a biz:Policy ;
    schema:name "Past Policy" ;
    biz:gitCommitSHA "sha010" ;
    biz:hasPII false ;
    biz:effectiveDate "2020-01-01"^^xsd:date ;
    biz:scope "all-staff" .

<urn:biz:policy:future>
    a biz:Policy ;
    schema:name "Future Policy" ;
    biz:gitCommitSHA "sha011" ;
    biz:hasPII false ;
    biz:effectiveDate "2099-01-01"^^xsd:date ;
    biz:scope "tbd" .
"""

_PII_TTL = """
@prefix biz:    <https://graphrag-aws.demo/biz-ops/ontology#> .
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<urn:biz:policy:clean-1>
    a biz:Policy ;
    schema:name "Clean Policy 1" ;
    biz:gitCommitSHA "sha020" ;
    biz:hasPII false ;
    biz:effectiveDate "2024-01-01"^^xsd:date ;
    biz:scope "all-staff" .

<urn:biz:policy:clean-2>
    a biz:Policy ;
    schema:name "Clean Policy 2" ;
    biz:gitCommitSHA "sha021" ;
    biz:hasPII false ;
    biz:effectiveDate "2024-01-01"^^xsd:date ;
    biz:scope "all-staff" .

<urn:biz:policy:pii-1>
    a biz:Policy ;
    schema:name "PII Policy" ;
    biz:gitCommitSHA "sha022" ;
    biz:hasPII true ;
    biz:effectiveDate "2024-01-01"^^xsd:date ;
    biz:scope "data-team" .
"""


def _make_retriever(
    ttl: str = _BASE_TTL,
    vector_hits: list[NormativeVectorHit] | None = None,
    threshold: float = 0.7,
) -> NormativeRetriever:
    store = MemorySparqlStore()
    store.load_turtle(ttl, NORMATIVE)
    vc = MemoryNormativeVectorClient(vector_hits or [])
    return NormativeRetriever(store, vc, _EMBEDDER, threshold=threshold)


# ── T-NR-1: full retrieve — SPARQL + vector union ────────────────────────────


def test_retrieve_sparql_only_returns_matching_domain() -> None:
    """SPARQL 2 Finance results returned when domain=Finance and no vector hits."""
    retriever = _make_retriever()
    resp = retriever.retrieve("finance query", domain="Finance", today="2025-07-23")
    uris = {r.uri for r in resp.results}
    assert "urn:biz:policy:finance-1" in uris
    assert "urn:biz:policy:finance-2" in uris
    assert "urn:biz:policy:hr-1" not in uris
    assert resp.pii_withheld_count == 1  # finance-pii withheld


def test_retrieve_vector_leg_adds_new_result() -> None:
    """Vector leg adds policy-x (not in SPARQL result) to the union."""
    vector_hit = NormativeVectorHit(
        doc_uri="urn:biz:policy:policy-x",
        score=0.85,
        title="Vector-only Policy",
        doc_type="Policy",
        pii_flagged=False,
    )
    retriever = _make_retriever(
        ttl=_BASE_TTL,
        vector_hits=[vector_hit],
    )
    resp = retriever.retrieve("query", domain="Finance", today="2025-07-23")
    uris = {r.uri for r in resp.results}
    assert "urn:biz:policy:finance-1" in uris
    assert "urn:biz:policy:finance-2" in uris
    assert "urn:biz:policy:policy-x" in uris  # added by vector leg


def test_retrieve_no_duplicate_from_vector_leg() -> None:
    """Vector hit with same URI as SPARQL result is NOT added twice."""
    vector_hit = NormativeVectorHit(
        doc_uri="urn:biz:policy:finance-1",  # already in SPARQL
        score=0.90,
        title="Finance Policy 1 (vector copy)",
        doc_type="Policy",
    )
    retriever = _make_retriever(vector_hits=[vector_hit])
    resp = retriever.retrieve("query", domain="Finance", today="2025-07-23")
    finance_1_items = [r for r in resp.results if r.uri == "urn:biz:policy:finance-1"]
    assert len(finance_1_items) == 1  # not duplicated


# ── T-NR-2: NormativeResult fields — relevance None vs float ─────────────────


def test_retrieve_sparql_items_have_null_relevance() -> None:
    retriever = _make_retriever()
    resp = retriever.retrieve("query", domain="HR", today="2025-07-23")
    assert len(resp.results) == 1
    r = resp.results[0]
    assert r.relevance is None
    assert r.uri == "urn:biz:policy:hr-1"
    assert r.title == "HR Leave Policy"
    assert r.doc_type == "Policy"
    assert r.pii_flagged is False
    assert r.git_commit == "sha004"
    assert r.git_path == "policies/hr/leave.md"


def test_retrieve_vector_items_have_float_relevance() -> None:
    hit = NormativeVectorHit(
        doc_uri="urn:biz:policy:vector-only",
        score=0.91,
        title="Vector Only",
        doc_type="Standard",
    )
    retriever = _make_retriever(vector_hits=[hit])
    resp = retriever.retrieve("query", today="2025-07-23")
    vector_items = [r for r in resp.results if r.uri == "urn:biz:policy:vector-only"]
    assert len(vector_items) == 1
    assert vector_items[0].relevance == pytest.approx(0.91)


# ── T-NR-3: effective-date filter ────────────────────────────────────────────


def test_retrieve_excludes_future_dated_policy_by_default() -> None:
    retriever = _make_retriever(ttl=_DATE_TTL)
    resp = retriever.retrieve("query", today="2025-07-23")
    uris = {r.uri for r in resp.results}
    assert "urn:biz:policy:past" in uris
    assert "urn:biz:policy:future" not in uris


def test_retrieve_include_future_returns_future_policies() -> None:
    retriever = _make_retriever(ttl=_DATE_TTL)
    resp = retriever.retrieve("query", include_future=True, today="2025-07-23")
    uris = {r.uri for r in resp.results}
    assert "urn:biz:policy:future" in uris
    assert "urn:biz:policy:past" in uris


# ── T-NR-4: pii_withheld_count ───────────────────────────────────────────────


def test_retrieve_pii_withheld_count_default() -> None:
    """Default call excludes PII-flagged policy; pii_withheld_count=1."""
    retriever = _make_retriever(ttl=_PII_TTL)
    resp = retriever.retrieve("query", today="2025-07-23")
    assert resp.pii_withheld_count == 1
    assert len(resp.results) == 2
    uris = {r.uri for r in resp.results}
    assert "urn:biz:policy:clean-1" in uris
    assert "urn:biz:policy:clean-2" in uris
    assert "urn:biz:policy:pii-1" not in uris


def test_retrieve_include_pii_returns_all_policies() -> None:
    """include_pii=True returns all 3; pii_withheld_count=0."""
    retriever = _make_retriever(ttl=_PII_TTL)
    resp = retriever.retrieve("query", include_pii=True, today="2025-07-23")
    assert resp.pii_withheld_count == 0
    assert len(resp.results) == 3


# ── T-NR-5: Neptune unavailable — NormativeUnavailable propagated ─────────────


def test_retrieve_neptune_unavailable_raises() -> None:
    """NormativeUnavailable is raised; no partial result returned."""
    retriever = NormativeRetriever(
        _FailingStore(),  # type: ignore[arg-type]
        MemoryNormativeVectorClient(),
        _EMBEDDER,
    )
    with pytest.raises(NormativeUnavailable) as exc_info:
        retriever.retrieve("query", today="2025-07-23")
    assert exc_info.value.__cause__ is not None


# ── T-NR-6: import isolation ──────────────────────────────────────────────────


def test_import_isolation() -> None:
    """graphrag.normative imports cleanly with no AWS credentials."""
    import graphrag.normative as norm

    assert hasattr(norm, "NormativeRetriever")
    assert hasattr(norm, "NormativeUnavailable")


# ── T-NR-7: all-domain retrieve returns all non-PII normative docs ────────────


def test_retrieve_all_domains_no_filter() -> None:
    retriever = _make_retriever()
    resp = retriever.retrieve("context", today="2025-07-23")
    uris = {r.uri for r in resp.results}
    # Non-PII from all domains included
    assert "urn:biz:policy:finance-1" in uris
    assert "urn:biz:policy:finance-2" in uris
    assert "urn:biz:policy:hr-1" in uris
    # PII excluded by default
    assert "urn:biz:policy:finance-pii" not in uris
    assert resp.pii_withheld_count == 1
