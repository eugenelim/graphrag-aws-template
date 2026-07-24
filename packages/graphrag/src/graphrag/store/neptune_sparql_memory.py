"""In-memory SPARQL store — the offline CI and local-dev backend.

Implements the same ``SparqlStore`` interface as ``NeptuneSparqlStore`` via
``rdflib.Dataset``, which supports full SPARQL 1.1 with named-graph
``FROM NAMED`` scoping (ADR-0011 offline-substitute requirement).  No AWS
credentials required; the entire named-graph partition model is testable in CI.

.. note:: **Process-global side effect.**  This module sets
   ``rdflib.plugins.sparql.SPARQL_LOAD_GRAPHS = False`` at import time.  This
   disables rdflib's default behaviour of dereferencing ``FROM NAMED <uri>``
   clauses over the network and is the correct setting for any in-process
   in-memory SPARQL evaluation (the live Neptune path sends queries to Neptune's
   HTTP endpoint, not through rdflib's SPARQL processor).  Import this module
   only from code that runs in the same process as rdflib SPARQL evaluation.
"""

from __future__ import annotations

from typing import Any

import rdflib.plugins.sparql
from rdflib import Dataset, Graph, URIRef

from .sparql_base import SparqlStore, check_read_only

# Prevent rdflib from dereferencing FROM NAMED <urn:...> URIs as network resources.
# With SPARQL_LOAD_GRAPHS=True (rdflib default), any FROM NAMED clause triggers an
# HTTP/network fetch of the named-graph URI — which fails for urn: schemes and silently
# drops data for empty named graphs.  Setting this flag to False makes rdflib use the
# existing in-memory Dataset context instead, which is the correct behaviour for an
# offline substitute store.
rdflib.plugins.sparql.SPARQL_LOAD_GRAPHS = False


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
