"""Tests for provenance graph merge — spec-provenance-citations AC3 (merge).

Confirms that a PROV-O graph returned by emit_provenance() merges cleanly with
a document triple graph (no blank-node collision, no namespace conflicts) and that
CitationResolver resolves citations correctly from the merged corpus.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, RDF, XSD

from graphrag.provenance import CitationResolver, ProvenanceEmitter
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
SCHEMA = Namespace("https://schema.org/")

_DOC = "urn:doc:merge-test:policies/merge.md"
_SHA = "cafebabe" * 5
_T0 = datetime(2024, 3, 1, 8, 0, 0, tzinfo=UTC)
_T1 = datetime(2024, 3, 1, 8, 2, 0, tzinfo=UTC)
_NORM = "urn:graph:normative"


def _merged_graph() -> Graph:
    """Return doc triples + provenance triples merged into one rdflib.Graph."""
    doc_g = Graph()
    doc = URIRef(_DOC)
    doc_g.add((doc, RDF.type, BIZ.Policy))
    doc_g.add((doc, SCHEMA.name, Literal("Merge Test Policy", datatype=XSD.string)))
    doc_g.add((doc, BIZ.effectiveDate, Literal("2024-03-01", datatype=XSD.date)))
    doc_g.add((doc, BIZ.scope, Literal("Engineering", datatype=XSD.string)))
    doc_g.add((doc, BIZ.hasPII, Literal("false", datatype=XSD.boolean)))
    doc_g.add((doc, BIZ.gitCommitSHA, Literal(_SHA, datatype=XSD.string)))

    prov_g = ProvenanceEmitter().emit_provenance(
        _DOC, _SHA, "policies/merge.md", "merge-org/merge-repo", "docling", _T0, _T1
    )
    doc_g += prov_g
    return doc_g


# ── No blank-node collision ───────────────────────────────────────────────────


def test_merged_graph_parses_cleanly() -> None:
    """Merged Turtle round-trips without error — no blank-node collision."""
    g = _merged_graph()
    ttl = g.serialize(format="turtle")
    g2 = Graph()
    g2.parse(data=ttl, format="turtle")  # must not raise


# ── Both doc and prov triples queryable ──────────────────────────────────────


def test_merged_graph_yields_doc_and_prov_triples() -> None:
    """SPARQL SELECT returns both a biz:Policy type and a prov:wasGeneratedBy link."""
    g = _merged_graph()
    sparql = f"""
    SELECT ?doc ?act WHERE {{
      ?doc a <{BIZ}Policy> .
      ?doc <{PROV}wasGeneratedBy> ?act .
    }}
    """
    rows = list(g.query(sparql))
    assert len(rows) == 1
    assert str(rows[0][0]) == _DOC


# ── CitationResolver on merged corpus ────────────────────────────────────────


def test_resolver_resolves_from_merged_corpus() -> None:
    """CitationResolver resolves doc + chunk citations from the merged store."""
    store = MemorySparqlStore()

    # Load merged doc + provenance
    store.load_turtle(_merged_graph().serialize(format="turtle"), _NORM)

    # Load a chunk with wasDerivedFrom + chunkText
    chunk_g = Graph()
    chunk_uri = "urn:chunk:merge-1"
    chunk_g.add((URIRef(chunk_uri), RDF.type, BIZ.Chunk))
    chunk_g.add((URIRef(chunk_uri), PROV.wasDerivedFrom, URIRef(_DOC)))
    chunk_g.add((URIRef(chunk_uri), BIZ.chunkIndex, Literal(0, datatype=XSD.integer)))
    chunk_g.add(
        (URIRef(chunk_uri), BIZ.chunkText, Literal("First chunk body.", datatype=XSD.string))
    )
    chunk_g.add((URIRef(chunk_uri), BIZ.embeddingModel, Literal("titan-v2", datatype=XSD.string)))
    store.load_turtle(chunk_g.serialize(format="turtle"), _NORM)

    resolver = CitationResolver(store)
    doc_cit = resolver.resolve([_DOC])[0]
    chunk_cit = resolver.resolve([chunk_uri])[0]

    assert doc_cit.partition == _NORM
    assert doc_cit.commit_sha == _SHA
    assert doc_cit.excerpt is None  # doc URI → no excerpt
    assert doc_cit.effective_date == "2024-03-01"  # biz:effectiveDate on doc

    assert chunk_cit.partition == _NORM
    assert chunk_cit.excerpt == "First chunk body."
    assert chunk_cit.effective_date == "2024-03-01"  # inherited via prov:wasDerivedFrom
