"""SPARQL executor — bridges the validated query to the injected store.

The executor accepts any ``SparqlStore``-compatible object (``NeptuneSparqlStore``
on the live path; ``MemorySparqlStore`` / a test double for offline CI).  It calls
``sparql_select`` and returns the rows as a list of binding dicts — the same shape
the orchestrator records in ``Text2SparqlResult.rows``.
"""

from __future__ import annotations

from typing import Any

from ..store.sparql_base import SparqlStore


def execute_select(store: SparqlStore, query: str) -> list[dict[str, Any]]:
    """Execute a validated SPARQL SELECT against ``store`` and return binding rows.

    The caller (orchestrator) guarantees ``query`` has passed ``SparqlValidator``
    before this function is called.  Any ``ValueError`` raised by the store's
    read-only check (belt-and-suspenders) or any ``RuntimeError`` from the Neptune
    client propagates up to the orchestrator's self-heal loop.
    """
    return store.sparql_select(query)
