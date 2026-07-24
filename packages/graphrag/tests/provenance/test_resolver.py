"""Tests for CitationResolver — SPARQL provenance resolution.

Covers spec-provenance-citations AC4 (full Citation fields), AC5 (graceful
partial resolution, excerpt, chunk parent date), and AC6 (document URI → excerpt=None).
"""

from __future__ import annotations

from datetime import UTC, datetime

from rdflib import Literal, Namespace, URIRef
from rdflib.namespace import PROV, RDF, XSD

from graphrag.provenance import CitationResolver, ProvenanceEmitter
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
SCHEMA = Namespace("https://schema.org/")

_NORM = "urn:graph:normative"
_DOC_URI = "urn:doc:test-repo:policies/aup.md"
_SHA = "deadbeef" * 5  # 40-char hex
_GIT_PATH = "policies/aup.md"
_GIT_REPO = "test-org/test-repo"
_EXTRACTOR = "pandoc"
_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC)


def _make_store_with_doc() -> MemorySparqlStore:
    """Create a MemorySparqlStore pre-loaded with a Policy document + provenance."""
    store = MemorySparqlStore()
    emitter = ProvenanceEmitter()

    # Document triples
    from rdflib import Graph

    doc_g = Graph()
    doc = URIRef(_DOC_URI)
    doc_g.add((doc, RDF.type, BIZ.Policy))
    doc_g.add((doc, SCHEMA.name, Literal("Acceptable Use Policy", datatype=XSD.string)))
    doc_g.add((doc, BIZ.effectiveDate, Literal("2024-01-01", datatype=XSD.date)))
    doc_g.add((doc, BIZ.scope, Literal("All employees", datatype=XSD.string)))
    doc_g.add((doc, BIZ.hasPII, Literal("false", datatype=XSD.boolean)))
    doc_g.add((doc, BIZ.gitCommitSHA, Literal(_SHA, datatype=XSD.string)))

    # Provenance triples
    prov_g = emitter.emit_provenance(_DOC_URI, _SHA, _GIT_PATH, _GIT_REPO, _EXTRACTOR, _T0, _T1)
    doc_g += prov_g

    ttl = doc_g.serialize(format="turtle")
    store.load_turtle(ttl, _NORM)
    return store


# ── AC4: Full Citation fields ─────────────────────────────────────────────────


def test_resolve_full_citation_all_fields() -> None:
    """CitationResolver resolves all Citation fields from fixture provenance triples."""
    store = _make_store_with_doc()
    resolver = CitationResolver(store)
    citations = resolver.resolve([_DOC_URI])
    assert len(citations) == 1
    c = citations[0]
    assert c.uri == _DOC_URI
    assert c.title == "Acceptable Use Policy"
    assert c.doc_type == "Policy"
    assert c.partition == _NORM
    assert c.commit_sha == _SHA
    assert c.git_path == _GIT_PATH
    assert c.git_repo == _GIT_REPO
    assert c.extractor == _EXTRACTOR
    assert c.effective_date == "2024-01-01"


def test_resolve_returns_list_of_citations() -> None:
    """resolve() returns a list of Citation objects (one per URI)."""
    store = _make_store_with_doc()
    citations = CitationResolver(store).resolve([_DOC_URI])
    assert isinstance(citations, list)
    assert len(citations) == 1
    from graphrag.provenance import Citation

    assert isinstance(citations[0], Citation)


# ── AC5: Graceful partial resolution ─────────────────────────────────────────


def test_resolve_missing_provenance_returns_citation_with_none_fields() -> None:
    """URI with no provenance triples → Citation with commit_sha=None, no exception."""
    store = MemorySparqlStore()  # empty store
    citations = CitationResolver(store).resolve(["urn:doc:ghost"])
    assert len(citations) == 1
    c = citations[0]
    assert c.uri == "urn:doc:ghost"
    assert c.commit_sha is None
    assert c.extractor is None
    assert c.git_path is None
    assert c.title is None
    assert c.partition is None


# ── AC5: Excerpt ──────────────────────────────────────────────────────────────


def _store_with_chunk(chunk_text: str) -> MemorySparqlStore:
    """Pre-loaded store with a chunk carrying biz:chunkText."""
    from rdflib import Graph

    store = MemorySparqlStore()
    chunk_uri = "urn:chunk:test-1"
    g = Graph()
    chunk = URIRef(chunk_uri)
    g.add((chunk, RDF.type, BIZ.Chunk))
    g.add((chunk, BIZ.chunkText, Literal(chunk_text, datatype=XSD.string)))
    g.add((chunk, PROV.wasDerivedFrom, URIRef(_DOC_URI)))
    g.add((chunk, BIZ.chunkIndex, Literal(0, datatype=XSD.integer)))
    g.add((chunk, BIZ.embeddingModel, Literal("titan-v2", datatype=XSD.string)))
    store.load_turtle(g.serialize(format="turtle"), _NORM)
    return store


def test_resolve_excerpt_first_200_chars() -> None:
    """Citation.excerpt is the first 200 chars of biz:chunkText."""
    long_text = "A" * 300
    store = _store_with_chunk(long_text)
    citations = CitationResolver(store).resolve(["urn:chunk:test-1"])
    assert len(citations) == 1
    assert citations[0].excerpt == "A" * 200


def test_resolve_excerpt_short_body_returns_full() -> None:
    """A chunk body < 200 chars → excerpt equals the full body."""
    short_text = "Short text."
    store = _store_with_chunk(short_text)
    citations = CitationResolver(store).resolve(["urn:chunk:test-1"])
    assert citations[0].excerpt == short_text


def test_resolve_doc_uri_returns_no_excerpt() -> None:
    """A document URI (not a chunk) returns excerpt=None."""
    store = _make_store_with_doc()
    citations = CitationResolver(store).resolve([_DOC_URI])
    assert citations[0].excerpt is None


# ── Concern 2: _safe_select error path ───────────────────────────────────────


def test_resolve_sparql_error_returns_none_fields_with_no_exception() -> None:
    """_safe_select swallows exceptions: a SPARQL-raising store returns a Citation
    with all None fields; CitationResolver does not propagate the error."""

    class RaisingSparqlStore(MemorySparqlStore):
        def sparql_select(self, query: str) -> list:  # type: ignore[override]
            raise RuntimeError("injected SPARQL failure")

    store = RaisingSparqlStore()
    with self_log_captured() as records:
        citations = CitationResolver(store).resolve(["urn:doc:ghost-error"])

    assert len(citations) == 1
    c = citations[0]
    assert c.uri == "urn:doc:ghost-error"
    assert c.commit_sha is None
    assert c.extractor is None
    # Logger must have emitted a warning (not swallowed silently)
    assert any("SPARQL error" in r.getMessage() for r in records), (
        f"No SPARQL-error warning logged; got: {[r.getMessage() for r in records]}"
    )


def self_log_captured():
    """Context manager that captures log records from graphrag.provenance._resolver."""
    import contextlib
    import logging

    @contextlib.contextmanager
    def _cm():
        logger = logging.getLogger("graphrag.provenance._resolver")
        records: list[logging.LogRecord] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = CapturingHandler()
        logger.addHandler(handler)
        try:
            yield records
        finally:
            logger.removeHandler(handler)

    return _cm()


# ── Concern 4: relevance field ────────────────────────────────────────────────


def test_resolve_relevance_attached_to_citation() -> None:
    """resolve(uris, relevance=X) sets Citation.relevance == X for each result."""
    store = _make_store_with_doc()
    citations = CitationResolver(store).resolve([_DOC_URI], relevance=0.87)
    assert len(citations) == 1
    assert citations[0].relevance == 0.87


# ── Concern 5: _check_uri injection guard ────────────────────────────────────


def test_check_uri_raises_on_injection_chars() -> None:
    """metadata_query / excerpt_query raise ValueError on URIs with unsafe chars."""
    from graphrag.provenance._sparql import excerpt_query, metadata_query

    for bad_uri in [
        "urn:doc:x> SELECT * WHERE { }#",  # angle bracket injection
        "urn:doc with space",  # whitespace
        'urn:doc:"quoted"',  # double-quote
        "urn:doc:{brace}",  # curly brace
    ]:
        for fn in (metadata_query, excerpt_query):
            try:
                fn(bad_uri)
                raise AssertionError(f"{fn.__name__} should have raised ValueError for {bad_uri!r}")
            except ValueError:
                pass  # expected
