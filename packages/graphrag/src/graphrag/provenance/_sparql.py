"""SPARQL query strings for graphrag.provenance.CitationResolver.

All queries use full IRIs to avoid prefix-declaration issues across SPARQL backends.
SELECT variables map directly to Citation dataclass fields — the resolver does
Python post-processing for URI truncation (doc_type, extractor).
"""

from __future__ import annotations

import re

_PROV = "http://www.w3.org/ns/prov#"
_BIZ = "https://graphrag-aws.demo/biz-ops/ontology#"
_SCHEMA = "https://schema.org/"

# Retrieves partition (named graph), rdf:type filtered to biz:/schema:/skos: URIs,
# schema:name (title), biz:effectiveDate, and the full PROV-O chain up to the
# bronze entity for commit_sha / git_path / git_repo / extractor (agent URI).
_SAFE_URI_RE = re.compile(r'^[^\s<>"{}|\\^`]*$')


def _check_uri(uri: str) -> None:
    """Raise ValueError if ``uri`` contains characters that would break SPARQL.

    Protects against injection through the ``<{uri}>`` interpolation in query
    templates.  Disallowed: whitespace, angle brackets, double-quotes, and other
    characters that would break the IRI production or allow SPARQL injection.
    """
    if not _SAFE_URI_RE.match(uri):
        raise ValueError(f"CitationResolver: URI contains characters unsafe for SPARQL: {uri!r}")


_METADATA_TMPL = (
    "SELECT ?title ?type ?partition ?sha ?gitPath ?gitRepo ?agentUri ?effectiveDate WHERE {{"
    "  GRAPH ?partition {{"
    "    <{uri}> a ?type ."
    "    FILTER("
    '      STRSTARTS(STR(?type), "{biz}") ||'
    '      STRSTARTS(STR(?type), "{schema}") ||'
    '      STRSTARTS(STR(?type), "http://www.w3.org/2004/02/skos/core#")'
    "    )"
    "    OPTIONAL {{ <{uri}> <{schema}name> ?title }}"
    "    OPTIONAL {{ <{uri}> <{biz}effectiveDate> ?effectiveDate }}"
    "    OPTIONAL {{"
    "      <{uri}> <{prov}wasGeneratedBy> ?emitAct ."
    "      ?emitAct <{prov}used> ?silverEnt ."
    "      ?silverEnt <{prov}wasGeneratedBy> ?extractAct ."
    "      ?extractAct <{prov}used> ?bronzeEnt ."
    "      OPTIONAL {{ ?bronzeEnt <{biz}gitCommitSHA> ?sha }}"
    "      OPTIONAL {{ ?bronzeEnt <{biz}gitPath> ?gitPath }}"
    "      OPTIONAL {{ ?bronzeEnt <{biz}gitRepo> ?gitRepo }}"
    "      OPTIONAL {{ ?extractAct <{prov}wasAssociatedWith> ?agentUri }}"
    "    }}"
    "  }}"
    "}} LIMIT 1"
)

_EXCERPT_TMPL = "SELECT ?text WHERE {{  GRAPH ?g {{    <{uri}> <{biz}chunkText> ?text  }}}} LIMIT 1"

_CHUNK_DATE_TMPL = (
    "SELECT ?effectiveDate WHERE {{"
    "  GRAPH ?g {{"
    "    <{uri}> <{prov}wasDerivedFrom> ?parentDoc ."
    "    ?parentDoc <{biz}effectiveDate> ?effectiveDate"
    "  }}"
    "}} LIMIT 1"
)


def metadata_query(uri: str) -> str:
    """Return a SPARQL SELECT query for document metadata + provenance chain."""
    _check_uri(uri)
    return _METADATA_TMPL.format(uri=uri, biz=_BIZ, schema=_SCHEMA, prov=_PROV)


def excerpt_query(uri: str) -> str:
    """Return a SPARQL SELECT query for the chunk excerpt text."""
    _check_uri(uri)
    return _EXCERPT_TMPL.format(uri=uri, biz=_BIZ)


def chunk_parent_date_query(uri: str) -> str:
    """Return a SPARQL SELECT query for the chunk parent document effective date."""
    _check_uri(uri)
    return _CHUNK_DATE_TMPL.format(uri=uri, biz=_BIZ, prov=_PROV)
