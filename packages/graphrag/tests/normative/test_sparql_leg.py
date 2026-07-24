"""Tests for graphrag.normative._sparql — the SPARQL exhaustive retrieval leg.

Fixture: an in-memory rdflib Dataset (MemorySparqlStore) seeded with 4 policies:
  - <urn:biz:policy:finance-1>  biz:Policy,    domain=Finance, non-PII
  - <urn:biz:policy:finance-2>  biz:Standard,  domain=Finance, non-PII
  - <urn:biz:policy:finance-pii> biz:Guideline, domain=Finance, PII-flagged
  - <urn:biz:policy:hr-1>       biz:Policy,    domain=HR,      non-PII

The SPARQL leg does NOT apply the PII filter (that is NormativeRetriever's job).
"""

from __future__ import annotations

import pytest

from graphrag.normative._sparql import (
    NORMATIVE_GRAPH,
    build_query,
    sparql_leg,
)
from graphrag.normative._types import NormativeUnavailable
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

# ── Ontology prefix constants ─────────────────────────────────────────────────

BIZ = "https://graphrag-aws.demo/biz-ops/ontology#"
SCHEMA = "https://schema.org/"

# ── Fixture Turtle corpus ─────────────────────────────────────────────────────

_FIXTURE_TTL = """
@prefix biz:    <https://graphrag-aws.demo/biz-ops/ontology#> .
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<urn:biz:policy:finance-1>
    a biz:Policy ;
    schema:name "Finance Policy 1" ;
    biz:gitCommitSHA "sha001" ;
    biz:gitPath "policies/finance/policy-1.md" ;
    biz:hasPII false ;
    biz:inDomain biz:Finance ;
    biz:effectiveDate "2024-01-01"^^xsd:date ;
    biz:scope "all-staff" .

<urn:biz:policy:finance-2>
    a biz:Standard ;
    schema:name "Finance Standard 2" ;
    biz:gitCommitSHA "sha002" ;
    biz:gitPath "policies/finance/standard-2.md" ;
    biz:hasPII false ;
    biz:inDomain biz:Finance ;
    biz:effectiveDate "2024-03-01"^^xsd:date ;
    biz:scope "finance-team" .

<urn:biz:policy:finance-pii>
    a biz:Guideline ;
    schema:name "Finance PII Guideline" ;
    biz:gitCommitSHA "sha003" ;
    biz:gitPath "policies/finance/pii-guideline.md" ;
    biz:hasPII true ;
    biz:inDomain biz:Finance ;
    biz:effectiveDate "2024-06-01"^^xsd:date ;
    biz:scope "data-controllers" .

<urn:biz:policy:hr-1>
    a biz:Policy ;
    schema:name "HR Leave Policy" ;
    biz:gitCommitSHA "sha004" ;
    biz:gitPath "policies/hr/leave-policy.md" ;
    biz:hasPII false ;
    biz:inDomain biz:HR ;
    biz:effectiveDate "2023-07-01"^^xsd:date ;
    biz:scope "all-staff" .
"""

_FUTURE_TTL = """
@prefix biz:    <https://graphrag-aws.demo/biz-ops/ontology#> .
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

<urn:biz:policy:future>
    a biz:Policy ;
    schema:name "Future Policy" ;
    biz:gitCommitSHA "sha010" ;
    biz:hasPII false ;
    biz:effectiveDate "2099-01-01"^^xsd:date ;
    biz:scope "tbd" .

<urn:biz:policy:past>
    a biz:Policy ;
    schema:name "Past Policy" ;
    biz:gitCommitSHA "sha011" ;
    biz:hasPII false ;
    biz:effectiveDate "2020-01-01"^^xsd:date ;
    biz:scope "all-staff" .
"""


def _seeded_store(ttl: str = _FIXTURE_TTL) -> MemorySparqlStore:
    store = MemorySparqlStore()
    store.load_turtle(ttl, NORMATIVE_GRAPH)
    return store


# ── T-SL-1: domain filter returns correct subset ──────────────────────────────


def test_sparql_leg_domain_filter_returns_finance_subset() -> None:
    store = _seeded_store()
    results = sparql_leg(store, domain="Finance", today="2025-07-23")
    uris = {r.uri for r in results}
    assert "urn:biz:policy:finance-1" in uris
    assert "urn:biz:policy:finance-2" in uris
    assert "urn:biz:policy:finance-pii" in uris  # PII not filtered by SPARQL leg
    assert "urn:biz:policy:hr-1" not in uris


def test_sparql_leg_domain_filter_returns_hr_subset() -> None:
    store = _seeded_store()
    results = sparql_leg(store, domain="HR", today="2025-07-23")
    uris = {r.uri for r in results}
    assert "urn:biz:policy:hr-1" in uris
    assert "urn:biz:policy:finance-1" not in uris


# ── T-SL-2: no domain filter returns all types ────────────────────────────────


def test_sparql_leg_no_domain_returns_all_policies() -> None:
    store = _seeded_store()
    results = sparql_leg(store, today="2025-07-23")
    uris = {r.uri for r in results}
    assert "urn:biz:policy:finance-1" in uris
    assert "urn:biz:policy:finance-pii" in uris
    assert "urn:biz:policy:hr-1" in uris


# ── T-SL-3: Neptune failure -> NormativeUnavailable ──────────────────────────


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


def test_sparql_leg_neptune_failure_raises_normative_unavailable() -> None:
    store = _FailingStore()
    with pytest.raises(NormativeUnavailable) as exc_info:
        sparql_leg(store, today="2025-07-23")  # type: ignore[arg-type]
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, ConnectionError)


def test_sparql_leg_neptune_failure_yields_no_partial_result() -> None:
    """Confirm retrieve does not return a partial result when Neptune fails."""
    store = _FailingStore()
    with pytest.raises(NormativeUnavailable):
        sparql_leg(store, today="2025-07-23")  # type: ignore[arg-type]
    # No result is returned — the exception is the only outcome.


def test_sparql_leg_neptune_failure_emits_error_log(caplog: pytest.LogCaptureFixture) -> None:
    """AC10: an ERROR log line with exception detail is emitted before raising."""
    import logging

    store = _FailingStore()
    with caplog.at_level(logging.ERROR, logger="graphrag.normative._sparql"):
        with pytest.raises(NormativeUnavailable):
            sparql_leg(store, today="2025-07-23")  # type: ignore[arg-type]
    assert any(
        r.levelno == logging.ERROR and "normative" in r.message.lower() for r in caplog.records
    ), "Expected an ERROR log from the normative SPARQL leg on Neptune failure"


# ── T-SL-4: empty graph returns empty list ────────────────────────────────────


def test_sparql_leg_empty_graph_returns_empty_list() -> None:
    # MemorySparqlStore sets rdflib.plugins.sparql.SPARQL_LOAD_GRAPHS = False at
    # import time, so FROM NAMED on a non-existent named graph returns [] rather
    # than attempting to dereference the urn: URI over the network (which would
    # raise URLError and be caught as NormativeUnavailable).
    store = MemorySparqlStore()
    results = sparql_leg(store, today="2025-07-23")
    assert results == []


# ── T-SL-5: query string has no LIMIT clause ─────────────────────────────────


def test_build_query_has_no_limit_clause() -> None:
    query = build_query(domain=None, include_future=True, today="2025-07-23")
    assert "LIMIT" not in query.upper()


def test_build_query_no_limit_with_domain() -> None:
    query = build_query(domain="Finance", include_future=False, today="2025-07-23")
    assert "LIMIT" not in query.upper()


# ── T-SL-6: query scoped with FROM NAMED ─────────────────────────────────────


def test_build_query_contains_from_named_normative() -> None:
    query = build_query(domain=None, include_future=True, today="2025-07-23")
    assert "FROM NAMED <urn:graph:normative>" in query


# ── T-SL-7: result fields populated correctly ────────────────────────────────


def test_sparql_leg_result_fields_populated() -> None:
    store = _seeded_store()
    results = sparql_leg(store, domain="HR", today="2025-07-23")
    assert len(results) == 1
    r = results[0]
    assert r.uri == "urn:biz:policy:hr-1"
    assert r.title == "HR Leave Policy"
    assert r.doc_type == "Policy"
    assert r.domain == "HR"
    assert r.effective_date == "2023-07-01"
    assert r.scope == "all-staff"
    assert r.pii_flagged is False
    assert r.relevance is None  # SPARQL leg items have no relevance score
    assert r.git_commit == "sha004"
    assert r.git_path == "policies/hr/leave-policy.md"


def test_sparql_leg_pii_flag_set_for_pii_policy() -> None:
    store = _seeded_store()
    results = sparql_leg(store, domain="Finance", today="2025-07-23")
    pii_results = [r for r in results if r.uri == "urn:biz:policy:finance-pii"]
    assert len(pii_results) == 1
    assert pii_results[0].pii_flagged is True


def test_sparql_leg_doc_type_standard_and_guideline() -> None:
    store = _seeded_store()
    results = sparql_leg(store, today="2025-07-23")
    by_uri = {r.uri: r for r in results}
    assert by_uri["urn:biz:policy:finance-2"].doc_type == "Standard"
    assert by_uri["urn:biz:policy:finance-pii"].doc_type == "Guideline"


# ── T-SL-8: effective-date filter excludes future policies ───────────────────


def test_sparql_leg_date_filter_excludes_future_policies() -> None:
    store = _seeded_store(_FUTURE_TTL)
    results = sparql_leg(store, today="2025-07-23")  # include_future=False default
    uris = {r.uri for r in results}
    assert "urn:biz:policy:past" in uris
    assert "urn:biz:policy:future" not in uris


def test_sparql_leg_include_future_returns_future_policies() -> None:
    store = _seeded_store(_FUTURE_TTL)
    results = sparql_leg(store, include_future=True, today="2025-07-23")
    uris = {r.uri for r in results}
    assert "urn:biz:policy:future" in uris
    assert "urn:biz:policy:past" in uris


# ── T-SL-9: unknown domain raises ValueError (injection guard) ───────────────


def test_build_query_unknown_domain_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown domain"):
        build_query(
            domain="Injected } SELECT * { ?s ?p ?o",
            include_future=False,
            today="2025-07-23",
        )


def test_sparql_leg_unknown_domain_raises_value_error() -> None:
    store = _seeded_store()
    with pytest.raises(ValueError, match="Unknown domain"):
        sparql_leg(store, domain="NotADomain", today="2025-07-23")


# ── T-SL-10: 20 policies, all returned (no top-k truncation) ─────────────────


def test_sparql_leg_returns_all_twenty_policies() -> None:
    """AC8: fixture with 20 matching policies; assert all 20 returned (no LIMIT)."""
    lines = [
        "@prefix biz:    <https://graphrag-aws.demo/biz-ops/ontology#> .",
        "@prefix schema: <https://schema.org/> .",
        "@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .",
    ]
    for i in range(20):
        lines.append(f"""
<urn:biz:policy:bulk-{i}>
    a biz:Policy ;
    schema:name "Bulk Policy {i}" ;
    biz:gitCommitSHA "sha{i:03d}" ;
    biz:hasPII false ;
    biz:effectiveDate "2024-01-01"^^xsd:date ;
    biz:scope "all-staff" .
""")
    ttl = "\n".join(lines)
    store = _seeded_store(ttl)
    results = sparql_leg(store, today="2025-07-23")
    assert len(results) == 20
