"""In-memory SPARQL store — the offline CI and local-dev backend.

Implements the same ``SparqlStore`` interface as ``NeptuneSparqlStore`` via
``rdflib.Dataset``, which supports full SPARQL 1.1 with named-graph
``FROM NAMED`` scoping (ADR-0011 offline-substitute requirement).  No AWS
credentials required; the entire named-graph partition model is testable in CI.
"""

from __future__ import annotations

from typing import Any

from rdflib import Dataset, Graph, URIRef

from .sparql_base import SparqlStore, check_read_only


class MemorySparqlStore(SparqlStore):
    """rdflib Dataset-backed SPARQL store.

    Offline substitute for ``NeptuneSparqlStore`` using ``rdflib.Dataset``; same
    interface, no network.
    """

    def __init__(self) -> None:
        self._graph = Dataset()

    def sparql_select(self, query: str) -> list[dict[str, Any]]:
        """Execute a SPARQL SELECT; return a list of binding dicts.

        Raises ``ValueError`` if the query contains a mutation keyword.
        """
        check_read_only(query)
        result = self._graph.query(query)
        out: list[dict[str, Any]] = []
        for row in result:
            out.append(
                {
                    str(var): str(val)
                    for var, val in zip(result.vars or [], row, strict=False)  # type: ignore[arg-type]
                    if val is not None  # omit unbound vars to match live JSON shape
                }
            )
        return out

    def sparql_construct(self, query: str) -> Graph:
        """Execute a SPARQL CONSTRUCT; return an rdflib Graph.

        Raises ``ValueError`` if the query contains a mutation keyword.
        """
        check_read_only(query)
        result = self._graph.query(query)
        g = Graph()
        for triple in result:
            g.add(triple)  # type: ignore[arg-type]
        return g

    def sparql_update(self, update: str) -> None:
        """Execute a SPARQL Update statement (ingestion role only)."""
        self._graph.update(update)

    def load_turtle(self, ttl: str, named_graph: str) -> None:
        """Parse Turtle and load all triples into the named graph."""
        context = self._graph.get_context(URIRef(named_graph))
        context.parse(data=ttl, format="turtle")
