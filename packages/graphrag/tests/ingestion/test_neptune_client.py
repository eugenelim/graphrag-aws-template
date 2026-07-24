"""TDD tests for graphrag.ingestion._neptune — NeptuneLoadClient.

Uses MemorySparqlStore (rdflib Dataset) as the offline substitute for NeptuneSparqlStore.
All assertions are verified via SPARQL SELECT against the in-memory store — no live
Neptune endpoint is required.
"""

from __future__ import annotations

from rdflib import Namespace, URIRef

from graphrag.ingestion._neptune import NeptuneLoadClient
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
PROV = Namespace("http://www.w3.org/ns/prov#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
RDF_TYPE = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")

DOC_URI = "urn:doc:test-repo:policies/hr.md"
PARTITION = "urn:graph:descriptive"
TAXONOMY = "urn:graph:taxonomy"
QUARANTINE = "urn:graph:quarantine"

# Minimal valid Turtle for the document (one triple so insert_document is testable).
_SAMPLE_TURTLE = f"""\
@prefix biz: <https://graphrag-aws.demo/biz-ops/ontology#> .
<{DOC_URI}> biz:title "HR Policy" .
"""

# Chunk turtle — uses prov:wasDerivedFrom so delete_document can clean it up.
_CHUNK_TURTLE = f"""\
@prefix prov: <http://www.w3.org/ns/prov#> .
<urn:chunk:0> prov:wasDerivedFrom <{DOC_URI}> .
<urn:chunk:0> <https://schema.org/text> "chunk text" .
"""

SHA = "cafe0001"  # pragma: allowlist secret


def _make_client() -> NeptuneLoadClient:
    return NeptuneLoadClient(store=MemorySparqlStore())


# ── T2-1: insert_document inserts triples + taxonomy ───────────────────────────


def test_insert_document_loads_triples_into_partition() -> None:
    """insert_document() inserts document triples into the correct named graph."""
    client = _make_client()
    client.insert_document(DOC_URI, PARTITION, _SAMPLE_TURTLE)

    # Verify via SPARQL SELECT that the triple lives in the partition graph.
    rows = client._store.sparql_select(
        f"SELECT ?o WHERE {{ GRAPH <{PARTITION}> {{ <{DOC_URI}> <{BIZ}title> ?o }} }}"
    )
    assert len(rows) == 1
    assert rows[0]["o"] == "HR Policy"


def test_insert_document_creates_taxonomy_entry() -> None:
    """insert_document() also inserts the biz:inPartition taxonomy triple."""
    client = _make_client()
    client.insert_document(DOC_URI, PARTITION, _SAMPLE_TURTLE)

    rows = client._store.sparql_select(
        f"SELECT ?p WHERE {{ GRAPH <{TAXONOMY}> {{ <{DOC_URI}> <{BIZ}inPartition> ?p }} }}"
    )
    assert len(rows) == 1
    assert rows[0]["p"] == PARTITION


# ── T2-2: delete_document removes doc + chunk triples + taxonomy ───────────────


def test_delete_document_removes_doc_and_chunk_triples() -> None:
    """delete_document() removes document triples and chunk triples."""
    client = _make_client()
    # Insert document first.
    client.insert_document(DOC_URI, PARTITION, _SAMPLE_TURTLE)
    # Also insert a chunk triple manually (normally emitted by the pipeline).
    client._store.sparql_update(
        f"INSERT DATA {{ GRAPH <{PARTITION}> {{ "
        f"<urn:chunk:0> <http://www.w3.org/ns/prov#wasDerivedFrom> <{DOC_URI}> . "
        f'<urn:chunk:0> <https://schema.org/text> "chunk text" . '
        f"}} }}"
    )

    client.delete_document(DOC_URI, PARTITION)

    # Document triples should be gone.
    doc_rows = client._store.sparql_select(
        f"SELECT ?p ?o WHERE {{ GRAPH <{PARTITION}> {{ <{DOC_URI}> ?p ?o }} }}"
    )
    assert doc_rows == []

    # Chunk triples should be gone.
    chunk_rows = client._store.sparql_select(
        f"SELECT ?s ?p ?o WHERE {{ GRAPH <{PARTITION}> {{ ?s ?p ?o }} }}"
    )
    assert chunk_rows == []


def test_delete_document_removes_taxonomy_entry() -> None:
    """delete_document() removes the taxonomy entry."""
    client = _make_client()
    client.insert_document(DOC_URI, PARTITION, _SAMPLE_TURTLE)
    client.delete_document(DOC_URI, PARTITION)

    rows = client._store.sparql_select(
        f"SELECT ?p WHERE {{ GRAPH <{TAXONOMY}> {{ <{DOC_URI}> ?p ?o }} }}"
    )
    assert rows == []


# ── T2-3: lookup_partition returns partition from taxonomy ──────────────────────


def test_lookup_partition_returns_correct_partition() -> None:
    """lookup_partition() returns the biz:inPartition value from the taxonomy graph."""
    client = _make_client()
    client.insert_document(DOC_URI, PARTITION, _SAMPLE_TURTLE)

    result = client.lookup_partition(DOC_URI)
    assert result == PARTITION


# ── T2-4: insert_quarantine_record inserts into quarantine graph ───────────────


def test_insert_quarantine_record_populates_quarantine_graph() -> None:
    """insert_quarantine_record() inserts a QuarantinedDocument triple."""
    client = _make_client()
    client.insert_quarantine_record(DOC_URI, SHA, "SHACL validation failed")

    rows = client._store.sparql_select(
        f"SELECT ?reason WHERE {{ GRAPH <{QUARANTINE}> {{ "
        f"<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#quarantineReason> ?reason "
        f"}} }}"
    )
    assert len(rows) == 1
    assert "SHACL" in rows[0]["reason"]


def test_insert_quarantine_record_includes_sha() -> None:
    """insert_quarantine_record() stores the commit SHA."""
    client = _make_client()
    client.insert_quarantine_record(DOC_URI, SHA, "extraction error")

    rows = client._store.sparql_select(
        f"SELECT ?sha WHERE {{ GRAPH <{QUARANTINE}> {{ "
        f"<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#gitCommitSHA> ?sha "
        f"}} }}"
    )
    assert len(rows) == 1
    assert rows[0]["sha"] == SHA  # pragma: allowlist secret


# ── T2-5: no DROP GRAPH in any generated query ─────────────────────────────────


def test_no_drop_graph_in_delete_queries() -> None:
    """Verify that delete_document never generates a DROP GRAPH statement."""
    # We intercept sparql_update calls to collect the SPARQL strings.
    collected: list[str] = []
    store = MemorySparqlStore()
    original_update = store.sparql_update

    def _spy(update: str) -> None:
        collected.append(update)
        original_update(update)

    store.sparql_update = _spy  # type: ignore[method-assign]
    client = NeptuneLoadClient(store=store)

    # Insert first so delete has something to operate on.
    client.insert_document(DOC_URI, PARTITION, _SAMPLE_TURTLE)
    collected.clear()  # reset after inserts

    client.delete_document(DOC_URI, PARTITION)

    for query in collected:
        assert "DROP GRAPH" not in query.upper(), f"DROP GRAPH found in query: {query!r}"


def test_no_drop_graph_in_insert_queries() -> None:
    """Verify that insert_document never generates a DROP GRAPH statement."""
    collected: list[str] = []
    store = MemorySparqlStore()
    original_update = store.sparql_update

    def _spy(update: str) -> None:
        collected.append(update)
        original_update(update)

    store.sparql_update = _spy  # type: ignore[method-assign]
    client = NeptuneLoadClient(store=store)
    client.insert_document(DOC_URI, PARTITION, _SAMPLE_TURTLE)

    for query in collected:
        assert "DROP GRAPH" not in query.upper(), f"DROP GRAPH found in query: {query!r}"


# ── T2-6: missing taxonomy entry → None ───────────────────────────────────────


def test_lookup_partition_returns_none_for_unknown_doc() -> None:
    """lookup_partition() returns None when the document has no taxonomy entry."""
    client = _make_client()
    result = client.lookup_partition("urn:doc:repo:nonexistent.md")
    assert result is None
