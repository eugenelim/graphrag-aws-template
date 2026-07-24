"""PROV-O provenance graph emitter for graphrag.provenance.

Emits W3C PROV-O triples for the Bronze → Silver → Gold pipeline.  Pure rdflib;
no AWS SDK imports.  Each call to ``emit_provenance`` or ``emit_chunk_provenance``
returns a new ``rdflib.Graph`` the caller merges with the document triple graph
before writing the Gold Turtle artifact.

URN scheme (ADR-0016 keying, spec-provenance-citations §Boundaries):

    Bronze entity  : urn:entity:bronze:{enc_repo}:{enc_path}:{sha}
    Extract activity: urn:activity:extract:{enc_doc_uri}:{sha}
    Silver entity  : urn:entity:silver:{enc_doc_uri}:{sha}
    Emit activity  : urn:activity:emit:{enc_doc_uri}:{sha}
    Extractor agent: urn:agent:{extractor_name}
    RDF emitter    : urn:agent:rdf-emitter

All path/repo/doc_uri segments are percent-encoded via
``urllib.parse.quote(seg, safe="/:@.-_~")`` before insertion into the URN.
The SHA occupies the rightmost position in Bronze entity URNs and is always a
40-char hex string — unambiguous even if git_path contains ``:{sha}`` literally.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, RDF, XSD

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")


def _enc(segment: str) -> str:
    """Percent-encode a URI path segment for safe embedding in a URN NSS."""
    return urllib.parse.quote(segment, safe="/:@.-_~")


def _fmt_dt(dt: datetime) -> Literal:
    """Return an ``xsd:dateTime`` literal for a Python datetime.

    Appends ``Z`` when ``dt`` has no tzinfo so Neptune always sees a
    fully-qualified timestamp — avoids the ambiguous-timezone silent error.
    """
    iso = dt.isoformat() if dt.tzinfo else dt.isoformat() + "Z"
    return Literal(iso, datatype=XSD.dateTime)


class ProvenanceEmitter:
    """Emit W3C PROV-O triples for the Bronze → Silver → Gold ingestion pipeline.

    Stateless — each call creates and returns a fresh ``rdflib.Graph``.  Callers
    merge the returned graph with the document triple graph (``g += prov_g``).

    No AWS SDK is imported; the emitter is usable in offline CI.
    """

    def emit_provenance(
        self,
        doc_uri: str,
        sha: str,
        git_path: str,
        git_repo: str,
        extractor: str,
        started_at: datetime,
        ended_at: datetime,
    ) -> Graph:
        """Return a PROV-O graph for the full Bronze → Silver → Gold pipeline.

        Args:
            doc_uri: Stable URI of the Gold document entity (e.g. ``urn:doc:…``).
            sha: Git commit SHA (40-char hex) that produced this Gold artifact.
            git_path: Repository-relative file path of the source document.
            git_repo: Git repository identifier (e.g. ``my-org/my-repo``).
            extractor: Extractor name (``pandoc``, ``docling``, ``markitdown``,
                ``textract``, or ``passthrough``); used as-is in the agent URI.
            started_at: UTC datetime when the extraction activity started.
            ended_at: UTC datetime when the Gold artifact was produced.

        Returns:
            An ``rdflib.Graph`` containing all five PROV-O entities/activities.
        """
        g = Graph()
        g.bind("prov", PROV)
        g.bind("biz", BIZ)

        enc_repo = _enc(git_repo)
        enc_path = _enc(git_path)
        enc_doc = _enc(doc_uri)

        bronze_uri = URIRef(f"urn:entity:bronze:{enc_repo}:{enc_path}:{sha}")
        extract_act = URIRef(f"urn:activity:extract:{enc_doc}:{sha}")
        silver_uri = URIRef(f"urn:entity:silver:{enc_doc}:{sha}")
        emit_act = URIRef(f"urn:activity:emit:{enc_doc}:{sha}")
        agent_uri = URIRef(f"urn:agent:{extractor}")
        rdf_agent = URIRef("urn:agent:rdf-emitter")
        doc = URIRef(doc_uri)

        # Bronze entity — git file at the given commit
        g.add((bronze_uri, RDF.type, PROV.Entity))
        g.add((bronze_uri, BIZ.gitCommitSHA, Literal(sha, datatype=XSD.string)))
        g.add((bronze_uri, BIZ.gitPath, Literal(git_path, datatype=XSD.string)))
        g.add((bronze_uri, BIZ.gitRepo, Literal(git_repo, datatype=XSD.string)))

        # Extractor agent
        g.add((agent_uri, RDF.type, PROV.SoftwareAgent))

        # Extraction activity (Bronze → Silver)
        g.add((extract_act, RDF.type, PROV.Activity))
        g.add((extract_act, PROV.used, bronze_uri))
        g.add((extract_act, PROV.wasAssociatedWith, agent_uri))
        g.add((extract_act, PROV.startedAtTime, _fmt_dt(started_at)))
        g.add((extract_act, PROV.endedAtTime, _fmt_dt(ended_at)))

        # Silver entity — extracted Markdown
        g.add((silver_uri, RDF.type, PROV.Entity))
        g.add((silver_uri, PROV.wasGeneratedBy, extract_act))
        g.add((silver_uri, PROV.wasDerivedFrom, bronze_uri))

        # RDF emitter agent
        g.add((rdf_agent, RDF.type, PROV.SoftwareAgent))

        # Gold emit activity (Silver → RDF triples)
        g.add((emit_act, RDF.type, PROV.Activity))
        g.add((emit_act, PROV.used, silver_uri))
        g.add((emit_act, PROV.wasAssociatedWith, rdf_agent))

        # Document entity (Gold) — the named URI in the knowledge graph
        g.add((doc, RDF.type, PROV.Entity))
        g.add((doc, PROV.wasGeneratedBy, emit_act))
        g.add((doc, PROV.wasDerivedFrom, silver_uri))

        return g

    def emit_chunk_provenance(
        self,
        chunk_uri: str,
        doc_uri: str,
        chunk_index: int,
    ) -> Graph:
        """Return a PROV-O graph linking a chunk to its parent document.

        Every ``biz:Chunk`` must carry ``prov:wasDerivedFrom`` pointing to the
        parent document URI (required by the SHACL shape from spec-rdf-owl-ontology)
        and ``biz:chunkIndex`` recording the ordinal position of the chunk.

        Args:
            chunk_uri: Stable URI of the chunk entity (e.g. ``urn:chunk:…``).
            doc_uri: URI of the parent Gold document entity.
            chunk_index: Zero-based ordinal index of the chunk within the document.

        Returns:
            An ``rdflib.Graph`` with ``prov:wasDerivedFrom`` and ``biz:chunkIndex``.
        """
        g = Graph()
        g.bind("prov", PROV)
        g.bind("biz", BIZ)
        chunk = URIRef(chunk_uri)
        doc = URIRef(doc_uri)
        g.add((chunk, PROV.wasDerivedFrom, doc))
        g.add((chunk, BIZ.chunkIndex, Literal(chunk_index, datatype=XSD.integer)))
        return g
