"""SHACL validation gate — wraps validate_graph() and issues quarantine INSERT on violation."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Protocol

import rdflib
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

from graphrag.ontology import BIZ, ValidationResult, validate_graph
from graphrag.validation.shacl._types import GateResult

logger = logging.getLogger(__name__)

_QUARANTINE_GRAPH = "urn:graph:quarantine"


class _UpdateClient(Protocol):
    """Minimal protocol for a SPARQL update client (duck-typed for testability)."""

    def sparql_update(self, update: str) -> None: ...


def _hash16(value: str) -> str:
    """Return the first 16 hex chars of SHA-256(value)."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _build_quarantine_sparql(
    *,
    record_uri: str,
    doc_uri: str,
    reason: str,
    timestamp: str,
    violation_paths: list[str],
) -> str:
    """Build a SPARQL INSERT DATA for the quarantine record.

    Uses rdflib.Graph serialization to N-Triples so that all string values
    (especially quarantineReason, which may derive from untrusted source
    document content) are properly escaped by rdflib's Literal.n3()
    serializer.  N-Triples uses only absolute URIs — no @prefix directives
    that would be invalid inside a SPARQL INSERT DATA quad block.

    This is the only safe approach; never build the SPARQL by f-string
    interpolation of values that may contain SPARQL structural characters.
    """
    g = Graph()
    r = URIRef(record_uri)
    g.add((r, RDF.type, BIZ.QuarantineRecord))
    g.add((r, BIZ.documentURI, URIRef(doc_uri)))
    g.add((r, BIZ.quarantineReason, Literal(reason, datatype=XSD.string)))
    g.add((r, BIZ.quarantinedAt, Literal(timestamp, datatype=XSD.dateTime)))
    for path in violation_paths:
        if path:
            g.add((r, BIZ.violationPath, URIRef(path)))
    nt_body = g.serialize(format="nt").strip()
    return f"INSERT DATA {{ GRAPH <{_QUARANTINE_GRAPH}> {{ {nt_body} }} }}"


class ShaclGate:
    """SHACL validation gate that issues a quarantine INSERT on violation.

    The Neptune SPARQL client is injected at construction for testability.
    ShaclGate itself imports only rdflib, pyshacl (via graphrag.ontology),
    hashlib, datetime, and logging — no boto3 or botocore.

    Usage::

        gate = ShaclGate(neptune_sparql_store)
        result = gate.validate(rdf_graph, doc_uri="urn:doc:...", sha="abc123")
        if result.outcome != "passed":
            # skip Gold partition INSERT
            ...
    """

    def __init__(self, neptune_client: _UpdateClient) -> None:
        self._client = neptune_client

    def validate(
        self,
        graph: rdflib.Graph,
        doc_uri: str,
        sha: str,
    ) -> GateResult:
        """Validate an RDF graph against the bundled SHACL shapes.

        On pass, returns GateResult(outcome="passed") with no Neptune call.
        On violation, issues a SPARQL INSERT DATA into urn:graph:quarantine
        and returns GateResult(outcome="quarantined").
        If the INSERT fails, returns GateResult(outcome="quarantine_insert_failed",
        error=str(exception)) without raising.

        Args:
            graph: The RDF graph to validate (typically the Gold Turtle graph).
            doc_uri: URI of the source document — stored in the quarantine record
                and used as part of the record subject URI.
            sha: Git commit SHA of the ingestion run — first segment of the record
                subject URI (makes records sortable by commit in the graph).

        Returns:
            GateResult with outcome "passed", "quarantined", or
            "quarantine_insert_failed".
        """
        result: ValidationResult = validate_graph(graph)

        if result.conforms:
            return GateResult(outcome="passed")

        record_uri = f"urn:quarantine:{sha}:{_hash16(doc_uri)}"
        timestamp = datetime.now(UTC).isoformat()

        if not result.violations:
            # validate_graph returned conforms=False but no ShapeViolation objects.
            # This is unexpected from pyshacl; emit a warning and use a fallback reason.
            logger.warning(
                "validate_graph returned conforms=False with no violations; doc_uri=%s sha=%s",
                doc_uri,
                sha,
            )
            reason = "validation failed: no violation details available"
            violation_paths: list[str] = []
        else:
            reason = "; ".join(
                v.message if v.message else f"violation at path {v.path}" for v in result.violations
            )
            violation_paths = [v.path for v in result.violations if v.path]

        # Build the quarantine SPARQL in its own try/except — a URIRef ValueError
        # (e.g. invalid doc_uri) is a caller-input error, diagnostically distinct
        # from a Neptune network failure.  Both yield quarantine_insert_failed but
        # the error prefix ("build: ") disambiguates for operators and retry logic.
        try:
            sparql = _build_quarantine_sparql(
                record_uri=record_uri,
                doc_uri=doc_uri,
                reason=reason,
                timestamp=timestamp,
                violation_paths=violation_paths,
            )
        except Exception as exc:
            logger.error(
                "Quarantine record build failed; record_uri=%s doc_uri=%s sha=%s",
                record_uri,
                doc_uri,
                sha,
                exc_info=True,
            )
            return GateResult(outcome="quarantine_insert_failed", error="build: " + str(exc))

        try:
            self._client.sparql_update(sparql)
            return GateResult(outcome="quarantined")
        except Exception as exc:
            logger.error(
                "Quarantine INSERT failed; record_uri=%s doc_uri=%s sha=%s",
                record_uri,
                doc_uri,
                sha,
                exc_info=True,
            )
            return GateResult(outcome="quarantine_insert_failed", error=str(exc))
