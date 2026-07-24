"""Pydantic response schemas for the graphrag.mcp tool surface.

``Citation`` and ``StrategyTrace`` are defined here as stubs because the
``graphrag.provenance`` and ``graphrag.routing`` modules are not yet shipped.
They will be replaced by cross-package imports once those specs land.

All ``str | None`` fields carry a default of ``None`` so FastMCP schema
generation does not require them as required fields.
"""

from __future__ import annotations

from pydantic import BaseModel


class Citation(BaseModel):
    """A cited source returned alongside a synthesised answer."""

    uri: str
    title: str
    excerpt: str | None = None


class StrategyTrace(BaseModel):
    """Routing and retrieval provenance — safe to log at INFO (no question text)."""

    strategy: str
    routing_decision: str
    sources_consulted: list[str] = []


class AskResponse(BaseModel):
    """Synthesised answer with citations and strategy provenance."""

    answer: str
    citations: list[Citation]
    strategy_trace: StrategyTrace


class SearchResult(BaseModel):
    """A ranked typed RDF resource returned by the ``search`` tool."""

    uri: str
    title: str
    doc_type: str
    partition: str
    score: float
    excerpt: str | None = None


class SubgraphResult(BaseModel):
    """Named-graph neighbourhood for the ``search_graph`` tool."""

    root_uri: str
    nodes: list[dict[str, str]]  # {uri, type, label}
    edges: list[dict[str, str]]  # {subject, predicate, object}


class PolicyResult(BaseModel):
    """A policy document returned by the exhaustive ``get_policies`` tool."""

    uri: str
    title: str
    effective_date: str | None = None
    domain: str | None = None
    excerpt: str | None = None


class QueryResult(BaseModel):
    """Named SPARQL template execution result."""

    template_name: str
    rows: list[dict[str, str]]
    row_count: int
    error: str | None = None


class SummaryResult(BaseModel):
    """Thematic synthesis result from the ``summarize`` tool."""

    topic: str
    summary: str
    citations: list[Citation]
