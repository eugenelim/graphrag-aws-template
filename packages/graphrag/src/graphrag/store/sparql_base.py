"""SparqlStore ABC — the seam between SPARQL retrieval and an RDF backend.

Named graphs (``urn:graph:normative``, ``urn:graph:descriptive``, etc.) are
the partition boundary (ADR-0011, ADR-0012).  All read methods must scope
queries via ``FROM NAMED`` / ``GRAPH {}`` to enforce isolation structurally.

Two concrete implementations:

- ``NeptuneSparqlStore`` — live Neptune SPARQL 1.1 endpoint (SigV4-signed).
- ``MemorySparqlStore`` — rdflib in-memory (offline CI and local dev).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from rdflib import Graph

# Shared ADR-0011 app-layer denylist (belt-and-suspenders; IAM ReadDataViaQuery
# is the load-bearing control).  Word-boundary anchors catch keyword-as-token
# occurrences but do NOT protect against URIs or literals that contain these
# substrings (e.g. <urn:graph:load>, ex:create).  Acknowledged limitation.
MUTATION_RE = re.compile(r"\b(INSERT|DELETE|DROP|CLEAR|LOAD|CREATE)\b", re.IGNORECASE)


def check_read_only(query: str) -> None:
    """Raise ValueError if query contains a SPARQL mutation keyword."""
    m = MUTATION_RE.search(query)
    if m:
        raise ValueError(f"mutation keyword in read query: {m.group()!r}")


class SparqlStore(ABC):
    """Backend that stores RDF triples and answers SPARQL 1.1 queries.

    ``sparql_select`` and ``sparql_construct`` are read-only methods: both
    implementations must reject queries that contain SPARQL Update mutation
    keywords (``INSERT``, ``DELETE``, ``DROP``, ``CLEAR``, ``LOAD``,
    ``CREATE``) and raise ``ValueError``.  This is the app-layer denylist
    (ADR-0011 layer 1 — belt-and-suspenders); the IAM
    ``ReadDataViaQuery``-only grant on ``mcp_lambda_role`` is the
    load-bearing control.

    ``sparql_update`` and ``load_turtle`` are for the ingestion role
    (``ingestion_task_role`` with ``WriteDataViaQuery``).
    """

    @abstractmethod
    def sparql_select(self, query: str) -> list[dict[str, Any]]:
        """Execute a SPARQL SELECT query; return a list of binding dicts.

        Each dict maps variable name (``str``) to bound value (``str``
        representation of the RDF term).

        Raises ``ValueError`` if ``query`` contains a mutation keyword.
        """

    @abstractmethod
    def sparql_construct(self, query: str) -> Graph:
        """Execute a SPARQL CONSTRUCT query; return an ``rdflib.Graph``.

        Raises ``ValueError`` if ``query`` contains a mutation keyword.
        """

    @abstractmethod
    def sparql_update(self, update: str) -> None:
        """Execute a SPARQL Update statement (ingestion role only).

        The read client (``mcp_lambda_role``) must not call this method;
        the IAM grant (``ReadDataViaQuery`` + ``connect`` only) is the
        load-bearing control.
        """

    @abstractmethod
    def load_turtle(self, ttl: str, named_graph: str) -> None:
        """Parse Turtle and insert all triples into the named graph.

        ``named_graph`` must be a full URI (e.g. ``urn:graph:normative``).
        This is an ingestion-role-only method — see ``sparql_update``.
        """
