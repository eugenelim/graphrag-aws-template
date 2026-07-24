"""PROV-O helpers for graphrag.ingestion._rdf.

The RDFEmitter calls these functions to attach provenance triples directly
on the document subject URI (required by SHACL: biz:gitCommitSHA, etc.)
and to integrate graphrag.provenance.ProvenanceEmitter for the full Bronze
→ Silver → Gold chain.
"""

from __future__ import annotations

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")


def attach_provenance_triples(
    g: Graph,
    doc_subject: URIRef,
    sha: str,
    git_path: str,
    git_repo: str,
    extractor: str,
) -> None:
    """Add provenance annotation triples directly on ``doc_subject``.

    These are distinct from the full W3C PROV-O activity chain (which is
    emitted via graphrag.provenance.ProvenanceEmitter).  These triples are
    on the document subject itself, as required by SHACL (biz:gitCommitSHA)
    and by AC9 (biz:gitRepo, biz:gitPath, biz:extractorUsed).

    Args:
        g: The RDF graph to add triples into.
        doc_subject: The document URI (e.g. URIRef("urn:doc:…")).
        sha: 40-char git commit SHA.
        git_path: Repository-relative file path.
        git_repo: Git repository identifier (e.g. "org/repo").
        extractor: Extractor name (e.g. "pandoc", "docling").
    """
    g.add((doc_subject, BIZ.gitCommitSHA, Literal(sha, datatype=XSD.string)))
    g.add((doc_subject, BIZ.gitPath, Literal(git_path, datatype=XSD.string)))
    g.add((doc_subject, BIZ.gitRepo, Literal(git_repo, datatype=XSD.string)))
    g.add((doc_subject, BIZ.extractorUsed, Literal(extractor, datatype=XSD.string)))
