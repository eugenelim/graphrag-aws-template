"""graphrag.ingestion._rdf — RDF triple emission with SHACL gate.

Public API
----------
RDFEmitter  — classifies a cleansed document, emits Turtle RDF, runs SHACL.
EmitResult  — outcome of RDFEmitter.emit().
"""

from __future__ import annotations

from dataclasses import dataclass

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from graphrag.ingestion._rdf._classify import Classification, classify
from graphrag.ingestion._rdf._provenance import BIZ, attach_provenance_triples
from graphrag.ontology import ValidationResult, validate_graph

SCHEMA = Namespace("https://schema.org/")

__all__ = ["EmitResult", "RDFEmitter"]


@dataclass
class EmitResult:
    """Outcome of RDFEmitter.emit()."""

    conforms: bool
    turtle: str  # Serialised Turtle; empty string on validation error
    named_graph: str  # "urn:graph:normative" or "urn:graph:descriptive"
    classification: Classification
    validation_result: ValidationResult
    quarantine_reason: str | None = None


class RDFEmitter:
    """Classify, emit RDF triples, and run the SHACL gate for one document.

    All functions are importable without boto3 or botocore.
    """

    def emit(
        self,
        doc_uri: str,
        path: str,
        sha: str,
        git_repo: str,
        extractor: str,
        clean_text: str,
        pii_flagged: bool,
    ) -> EmitResult:
        """Classify the document and emit a SHACL-validated Turtle graph.

        Args:
            doc_uri: Stable document URI (e.g. ``"urn:doc:my-repo:policies/hr.md"``).
            path: Repository-relative file path (used by the classifier).
            sha: Git commit SHA (40-char hex).
            git_repo: Git repository identifier.
            extractor: Extractor name recorded as biz:extractorUsed.
            clean_text: Cleansed Markdown text (used by the classifier for
                front-matter parsing and structural signals).
            pii_flagged: Whether the PII gate flagged this document.

        Returns:
            EmitResult with conforms=True when SHACL passes, or conforms=False
            with a quarantine_reason when SHACL rejects.
        """
        cls = classify(path, clean_text)

        g = Graph()
        g.bind("biz", BIZ)
        g.bind("schema", SCHEMA)

        doc = URIRef(doc_uri)
        rdf_class = URIRef(cls.rdf_class)

        # Core triples
        g.add((doc, RDF.type, rdf_class))
        g.add((doc, SCHEMA.name, Literal(cls.name, datatype=XSD.string)))

        # Policy-family required fields (from front-matter; absence → SHACL fail)
        if cls.effective_date is not None:
            g.add((doc, BIZ.effectiveDate, Literal(cls.effective_date, datatype=XSD.date)))
        if cls.scope is not None:
            g.add((doc, BIZ.scope, Literal(cls.scope, datatype=XSD.string)))

        # PII flag (required by SHACL on policy-family shapes)
        g.add((doc, BIZ.hasPII, Literal(pii_flagged, datatype=XSD.boolean)))

        # Provenance triples (required by SHACL: biz:gitCommitSHA; AC9: others)
        attach_provenance_triples(
            g,
            doc_subject=doc,
            sha=sha,
            git_path=path,
            git_repo=git_repo,
            extractor=extractor,
        )

        # SHACL validation
        val_result = validate_graph(g)

        if not val_result.conforms:
            violation_paths = ", ".join(
                v.path or v.source_shape or "unknown" for v in val_result.violations
            )
            quarantine_reason = (
                f"SHACL gate failed: {cls.rdf_class} — violations: {violation_paths}"
            )
            return EmitResult(
                conforms=False,
                turtle="",
                named_graph=cls.partition,
                classification=cls,
                validation_result=val_result,
                quarantine_reason=quarantine_reason,
            )

        turtle = g.serialize(format="turtle")
        return EmitResult(
            conforms=True,
            turtle=turtle,
            named_graph=cls.partition,
            classification=cls,
            validation_result=val_result,
        )
