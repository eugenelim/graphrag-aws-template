"""graphrag.ingestion._neptune — NeptuneLoadClient: SPARQL INSERT + DELETE wrappers.

Wraps the shared ``SparqlStore`` seam (``NeptuneSparqlStore`` in production,
``MemorySparqlStore`` in tests) with ingestion-task-specific write operations.

Security invariants (ADR-0011):
- No ``DROP GRAPH`` in any generated query (asserted in tests via string search).
- All writes use ``sparql_update`` / ``load_turtle`` — the ingestion-role write path.
- Taxonomy SELECT uses ``sparql_select``, which enforces the ADR-0011 denylist.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from graphrag.store.sparql_base import SparqlStore

__all__ = ["NeptuneLoadClient"]

_BIZ = "https://graphrag-aws.demo/biz-ops/ontology#"
_PROV = "http://www.w3.org/ns/prov#"
_XSD = "http://www.w3.org/2001/XMLSchema#"
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_TAXONOMY_GRAPH = "urn:graph:taxonomy"
_QUARANTINE_GRAPH = "urn:graph:quarantine"

log = logging.getLogger(__name__)


def _uri(u: str) -> str:
    """Format a URI for safe interpolation into a SPARQL query."""
    if ">" in u or any(c in u for c in " \t\n\r"):
        raise ValueError(f"invalid URI for SPARQL interpolation: {u!r}")
    return f"<{u}>"


def _str_literal(s: str) -> str:
    """Format a plain string literal for SPARQL interpolation.

    Escapes backslash, double-quote, newline, and carriage-return so the resulting
    literal is valid in a single-line SPARQL string.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


class NeptuneLoadClient:
    """Ingestion-role SPARQL write client.

    All methods use ``ingestion_task_role`` credentials (``WriteDataViaQuery`` +
    ``connect``) via the injected :class:`~graphrag.store.sparql_base.SparqlStore`.

    In production inject ``NeptuneSparqlStore``; in tests inject ``MemorySparqlStore``.
    """

    def __init__(self, store: SparqlStore) -> None:
        self._store = store

    # ── insert operations ───────────────────────────────────────────────────────

    def insert_document(self, doc_uri: str, partition_graph: str, turtle: str) -> None:
        """Load ``turtle`` triples into ``partition_graph`` + insert taxonomy entry.

        Steps (in order):
        1. Parse and INSERT all triples from ``turtle`` into ``partition_graph`` via
           ``load_turtle`` (N-Triples-encoded; no injection surface from Turtle content).
        2. INSERT ``<doc_uri> biz:inPartition <partition_graph>`` into the taxonomy graph.

        Args:
            doc_uri:         Stable document URI (e.g. ``"urn:doc:repo:path/file.md"``).
            partition_graph: Named-graph URI (e.g. ``"urn:graph:descriptive"``).
            turtle:          Gold-tier Turtle serialisation of the document.
        """
        self._store.load_turtle(turtle, partition_graph)
        taxonomy_update = (
            f"INSERT DATA {{ GRAPH {_uri(_TAXONOMY_GRAPH)} {{ "
            f"{_uri(doc_uri)} {_uri(_BIZ + 'inPartition')} {_uri(partition_graph)} "
            f"}} }}"
        )
        self._store.sparql_update(taxonomy_update)
        log.info(
            "inserted document",
            extra={"doc_uri": doc_uri, "partition": partition_graph, "outcome": "loaded"},
        )

    def insert_quarantine_record(self, doc_uri: str, sha: str, reason: str) -> None:
        """Insert a ``biz:QuarantinedDocument`` record into ``urn:graph:quarantine``.

        Args:
            doc_uri: Stable document URI.
            sha:     Git commit SHA at the time of quarantine.
            reason:  Human-readable quarantine reason (SHACL violation message, etc.).
        """
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        dt_literal = f'"{now_iso}"^^{_uri(_XSD + "dateTime")}'
        update = (
            f"INSERT DATA {{ GRAPH {_uri(_QUARANTINE_GRAPH)} {{ "
            f"{_uri(doc_uri)} {_uri(_RDF_TYPE)} {_uri(_BIZ + 'QuarantinedDocument')} . "
            f"{_uri(doc_uri)} {_uri(_BIZ + 'quarantineReason')} {_str_literal(reason)} . "
            f"{_uri(doc_uri)} {_uri(_BIZ + 'gitCommitSHA')} {_str_literal(sha)} . "
            f"{_uri(doc_uri)} {_uri(_BIZ + 'quarantineTime')} {dt_literal} . "
            f"}} }}"
        )
        self._store.sparql_update(update)
        log.info(
            "quarantined document",
            extra={"doc_uri": doc_uri, "sha": sha, "outcome": "quarantined"},
        )

    # ── lookup operations ───────────────────────────────────────────────────────

    def lookup_partition(self, doc_uri: str) -> str | None:
        """Look up the partition graph for a document from the taxonomy graph.

        Args:
            doc_uri: Stable document URI.

        Returns:
            The partition graph URI, or ``None`` if no taxonomy entry exists.
        """
        query = (
            f"SELECT ?partition WHERE {{ "
            f"GRAPH {_uri(_TAXONOMY_GRAPH)} {{ "
            f"{_uri(doc_uri)} {_uri(_BIZ + 'inPartition')} ?partition "
            f"}} }}"
        )
        rows = self._store.sparql_select(query)
        if not rows:
            return None
        return str(rows[0].get("partition", ""))

    # ── delete operations ───────────────────────────────────────────────────────

    def delete_document(self, doc_uri: str, partition_graph: str) -> None:
        """Delete all triples for a document from its partition and the taxonomy graph.

        Deletion order:
        1. Chunk triples (``?chunk prov:wasDerivedFrom <doc_uri>``) from partition.
        2. Document triples (``<doc_uri> ?p ?o``) from partition.
        3. Taxonomy entry (``<doc_uri> ?p ?o``) from ``urn:graph:taxonomy``.

        No ``DROP GRAPH`` is ever issued — only scoped ``DELETE WHERE`` / ``DELETE …
        WHERE`` statements.

        Args:
            doc_uri:         Stable document URI.
            partition_graph: Named graph from which to remove the document.
        """
        prov_derived = _uri(_PROV + "wasDerivedFrom")

        # 1. Delete chunk triples derived from this document.
        chunk_delete = (
            f"DELETE {{ GRAPH {_uri(partition_graph)} {{ ?chunk ?p ?o }} }} "
            f"WHERE {{ GRAPH {_uri(partition_graph)} {{ "
            f"?chunk ?p ?o . ?chunk {prov_derived} {_uri(doc_uri)} "
            f"}} }}"
        )
        self._store.sparql_update(chunk_delete)

        # 2. Delete document triples.
        doc_delete = f"DELETE WHERE {{ GRAPH {_uri(partition_graph)} {{ {_uri(doc_uri)} ?p ?o }} }}"
        self._store.sparql_update(doc_delete)

        # 3. Delete taxonomy entry.
        taxonomy_delete = (
            f"DELETE WHERE {{ GRAPH {_uri(_TAXONOMY_GRAPH)} {{ {_uri(doc_uri)} ?p ?o }} }}"
        )
        self._store.sparql_update(taxonomy_delete)

        log.info(
            "deleted document",
            extra={"doc_uri": doc_uri, "partition": partition_graph, "outcome": "deleted"},
        )
