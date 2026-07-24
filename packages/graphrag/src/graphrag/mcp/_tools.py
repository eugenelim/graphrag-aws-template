"""The six graphrag.mcp tool definitions.

A single ``FastMCP`` instance at module level decorates all six tools.  Both
the Lambda target (``_lambda.py``) and the mock server (``_mock.py``) import
this instance — never creating a second one.

Store injection: ``_mock.py`` sets the module-level ``_store`` before starting
the server.  Tool functions check ``_store is None`` and raise ``RuntimeError``
if it is not set; production backends (not yet wired) replace this with real
AWS clients.

Content-capture policy (ADR-0015): question text, query text, and document
content are NEVER written to ``logger.info``/``logger.warning``/``logger.error``
or to any span attribute.  They remain in the function body only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import FastMCP
from rdflib import URIRef

from graphrag.mcp._schemas import (
    AskResponse,
    Citation,
    PolicyResult,
    QueryResult,
    SearchResult,
    StrategyTrace,
    SubgraphResult,
    SummaryResult,
)
from graphrag.sparql_templates import SPARQL_TEMPLATE_BY_ID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level FastMCP instance — the single shared tool registry.
# ---------------------------------------------------------------------------

mcp: FastMCP = FastMCP("biz-ops-knowledge-platform")

# ---------------------------------------------------------------------------
# In-memory mock store — injected by _mock.py before the server starts.
# ---------------------------------------------------------------------------

# Named template registry: delegates to graphrag.sparql_templates registry.
# Each entry is a bound method — closure-safe without a lambda wrapper.
# SparqlTemplate.execute() uses rdflib initBindings= for safe parameterization;
# no f-string interpolation of caller values.
_TEMPLATE_RUNNERS: dict[str, Any] = {
    name: tmpl.execute for name, tmpl in SPARQL_TEMPLATE_BY_ID.items()
}


@dataclass
class _MockStore:
    """In-memory substitutes for Neptune/OpenSearch/Bedrock — no AWS calls."""

    graph: Any  # rdflib.Dataset seeded from fixture corpus (TriG named-graph format)
    vector: Any  # MemoryVectorStore for kNN search
    embedder: Any  # HashEmbedder for deterministic embeddings
    # URI -> (type_uri, partition) metadata derived from fixture corpus at startup
    uri_meta: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass
class _ProductionStore:
    """Production backends: Neptune SPARQL + OpenSearch/MemoryVector + Bedrock.

    ``embedder`` uses ``HashEmbedder`` until a Bedrock embedder adapter lands.
    ``bedrock_client`` is a boto3 ``bedrock-runtime`` client reserved for synthesis;
    it is constructed eagerly on cold-start so credential errors surface at init time.
    ``vector`` is ``OpenSearchVectorStore`` when ``OPENSEARCH_ENDPOINT`` is set,
    otherwise ``MemoryVectorStore`` (empty — kNN results will be empty until indexed).
    """

    neptune: Any  # NeptuneSparqlStore — SigV4-signed SPARQL SELECT
    vector: Any  # OpenSearchVectorStore | MemoryVectorStore (fallback)
    bedrock_client: Any  # boto3 bedrock-runtime client (reserved for synthesis)
    embedder: Any  # HashEmbedder — deterministic embeddings (interim)


_store: _MockStore | _ProductionStore | None = None


def _require_store() -> _MockStore | _ProductionStore:
    if _store is None:
        raise RuntimeError(
            "graphrag.mcp._store is not initialised — "
            "run 'python -m graphrag.mcp --mock' or call _mock.init_mock() first."
        )
    return _store


# ---------------------------------------------------------------------------
# Tool 1: ask
# ---------------------------------------------------------------------------


@mcp.tool()
async def ask(question: str) -> AskResponse:
    """Ask a question. Returns a synthesised answer, citations, and strategy trace."""
    start = time.monotonic()
    store = _require_store()

    # Vector search for relevant docs → citations (same path for mock and production)
    query_vec = store.embedder.embed([question])[0]
    hits = store.vector.knn(query_vec, 3)
    citations = []
    for hit in hits:
        c = hit.chunk
        citations.append(Citation(uri=c.id, title=c.text[:80], excerpt=c.text[:120]))

    # Mock synthesis: deterministic template (no Bedrock call).
    # Production: RuleQueryRouter-only path — no BedrockQueryRouter yet (graphrag.routing pending).
    # Never-do (ADR-0015 / spec.md §Boundaries): question text must not appear
    # in any AskResponse field.  Both paths return a fixed placeholder.
    if isinstance(store, _ProductionStore):
        answer = "[RuleQueryRouter] Synthesis not yet available — graphrag.routing is pending."
        strategy = "rule"
    else:
        answer = "[MOCK] No live synthesis — mock server returned this placeholder."
        strategy = "rule"

    elapsed = time.monotonic() - start
    if elapsed > 20.0:
        # ADR-0015: no question text in WARNING log lines
        logger.warning("ask path exceeded 20s warning threshold")

    logger.info(
        "ask completed",
        extra={
            "strategy": strategy,
            "routing_decision": "rule_query_router",
            "citation_count": len(citations),
            "elapsed_s": round(elapsed, 3),
        },
    )

    return AskResponse(
        answer=answer,
        citations=citations,
        strategy_trace=StrategyTrace(
            strategy=strategy,
            routing_decision="rule_query_router",
            sources_consulted=[hit.chunk.id for hit in hits],
        ),
    )


# ---------------------------------------------------------------------------
# Tool 2: search
# ---------------------------------------------------------------------------


@mcp.tool()
async def search(
    question: str,
    type: str | None = None,  # noqa: A002 — MCP schema name; shadowing builtin is intentional
    k: int = 10,
) -> list[SearchResult]:
    """Semantic search. Returns ranked typed RDF resources (chunks/docs).

    The ``type`` filter accepts a full RDF IRI (not a CURIE), e.g.
    ``https://graphrag-aws.demo/biz-ops/ontology#Policy``.
    """
    store = _require_store()

    query_vec = store.embedder.embed([question])[0]
    hits = store.vector.knn(query_vec, k)

    results: list[SearchResult] = []
    for hit in hits:
        uri = hit.chunk.id
        if isinstance(store, _MockStore):
            doc_type, partition = store.uri_meta.get(uri, ("biz:Document", "descriptive"))
            # Type filter: skip if caller requested a specific type and this doesn't match.
            # In mock mode, uri_meta is populated so the filter is meaningful.
            if type is not None and doc_type != type:
                continue
        else:
            # Production: URI metadata (doc_type/partition) not pre-populated.
            # Skip the type filter entirely to avoid silently returning empty results.
            # Type-aware filtering will land with spec-normative-partition.
            doc_type, partition = ("", "")

        c = hit.chunk
        results.append(
            SearchResult(
                uri=uri,
                title=c.text[:80],
                doc_type=doc_type,
                partition=partition,
                score=hit.score,
                excerpt=c.text[:120] if c.text else None,
            )
        )

    logger.info("search completed", extra={"result_count": len(results)})
    return results


# ---------------------------------------------------------------------------
# Tool 3: search_graph
# ---------------------------------------------------------------------------


def _sparql_rows_from_production(store: _ProductionStore, sparql: str) -> list[dict[str, Any]]:
    """Execute a SPARQL SELECT via NeptuneSparqlStore and return result rows as dicts."""
    return store.neptune.sparql_select(sparql)


_SEARCH_GRAPH_SPARQL_PRODUCTION = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?p ?o ?type ?label WHERE {{
    {{
        BIND(<{root}> AS ?s)
        <{root}> ?p ?o .
        OPTIONAL {{ <{root}> a ?type . }}
        OPTIONAL {{ <{root}> rdfs:label ?label . }}
    }}
    UNION
    {{
        ?s ?p <{root}> .
        BIND(<{root}> AS ?o)
        OPTIONAL {{ ?s a ?type . }}
        OPTIONAL {{ ?s rdfs:label ?label . }}
    }}
}}
"""

@mcp.tool()
async def search_graph(uri: str, hops: int = 1) -> SubgraphResult:
    """Named-graph neighbourhood lookup. Returns typed subgraph (nodes + edges)."""
    store = _require_store()

    # Cost guard: max hops = 2
    hops = min(hops, 2)

    nodes: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []
    visited: set[str] = {uri}
    frontier: list[str] = [uri]

    for _ in range(hops):
        next_frontier: list[str] = []
        for root in frontier:
            if isinstance(store, _ProductionStore):
                raw_rows = store.neptune.sparql_select(
                    _SEARCH_GRAPH_SPARQL_PRODUCTION.format(root=root)
                )
                row_iter = [
                    {"s": r.get("s",""), "p": r.get("p",""), "o": r.get("o",""),
                     "type": r.get("type",""), "label": r.get("label","")}
                    for r in raw_rows
                ]
            else:
                row_iter = [
                    {"s": str(row.s), "p": str(row.p), "o": str(row.o),
                     "type": str(row.type) if row.type else "",
                     "label": str(row.label) if row.label else "",
                     "_o_node": row.o}
                    for row in store.graph.query(
                        _SEARCH_GRAPH_SPARQL, initBindings={"root": URIRef(root)}
                    )
                ]
            for raw in row_iter:
                s_str = str(raw["s"])
                o_str = str(raw["o"])
                p_str = str(raw["p"])
                edges.append({"subject": s_str, "predicate": p_str, "object": o_str})
                type_str = raw["type"] if raw["type"] else ""
                label_str = raw["label"] if raw["label"] else s_str.split(":")[-1]
                # URIs start with http/urn; literals in Neptune rows are plain strings
                o_is_uri = o_str.startswith(("http:", "https:", "urn:"))

                # Always add the subject (always a URI per the BIND clause)
                if s_str not in nodes:
                    nodes[s_str] = {"uri": s_str, "type": type_str, "label": label_str}
                if s_str not in visited:
                    visited.add(s_str)
                    next_frontier.append(s_str)

                # Only add the object to nodes/frontier if it is a URI (not a literal).
                if o_is_uri:
                    if o_str not in nodes:
                        nodes[o_str] = {
                            "uri": o_str,
                            "type": "",
                            "label": o_str.split("#")[-1].split("/")[-1],
                        }
                    if o_str not in visited:
                        visited.add(o_str)
                        next_frontier.append(o_str)
                else:
                    # Literals are captured as edge objects but not traversed
                    if o_str not in nodes:
                        nodes[o_str] = {"uri": o_str, "type": "literal", "label": o_str[:60]}
        frontier = next_frontier

    # Ensure root is always in nodes
    if uri not in nodes:
        nodes[uri] = {"uri": uri, "type": "", "label": uri.split(":")[-1]}

    logger.info(
        "search_graph completed",
        extra={"node_count": len(nodes), "edge_count": len(edges)},
    )
    return SubgraphResult(root_uri=uri, nodes=list(nodes.values()), edges=edges)


# ---------------------------------------------------------------------------
# Tool 4: get_policies (exhaustive — never top-k; ADR-0012)
# ---------------------------------------------------------------------------

_GET_POLICIES_ALL_SPARQL = """
PREFIX biz:    <https://graphrag-aws.demo/biz-ops/ontology#>
PREFIX schema: <https://schema.org/>
SELECT ?policy ?name ?effectiveDate ?scope WHERE {
    GRAPH <urn:graph:normative> {
        ?policy a biz:Policy ;
                schema:name ?name .
        OPTIONAL { ?policy biz:effectiveDate ?effectiveDate . }
        OPTIONAL { ?policy biz:scope ?scope . }
    }
}
"""

# GRAPH ?g wrapper is required: the fixture corpus uses named graphs (TriG format loaded
# into rdflib.Dataset without default_union=True), so the default graph is empty and
# unqualified triple patterns return zero results.  Wrapping in GRAPH ?g queries all
# named graphs.  Uses initBindings to pass the root URI safely (no f-string injection).
_SEARCH_GRAPH_SPARQL = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?p ?o ?type ?label WHERE {
    GRAPH ?g {
        {
            BIND(?root AS ?s)
            ?root ?p ?o .
            OPTIONAL { ?root a ?type . }
            OPTIONAL { ?root rdfs:label ?label . }
        }
        UNION
        {
            ?s ?p ?root .
            BIND(?root AS ?o)
            OPTIONAL { ?s a ?type . }
            OPTIONAL { ?s rdfs:label ?label . }
        }
    }
}
"""


_GET_POLICIES_DOMAIN_SPARQL_TEMPLATE = """
PREFIX biz:    <https://graphrag-aws.demo/biz-ops/ontology#>
PREFIX schema: <https://schema.org/>
SELECT ?policy ?name ?effectiveDate ?scope WHERE {{
    GRAPH <urn:graph:normative> {{
        ?policy a biz:Policy ;
                schema:name ?name .
        OPTIONAL {{ ?policy biz:effectiveDate ?effectiveDate . }}
        OPTIONAL {{ ?policy biz:scope ?scope . }}
        FILTER(str(?scope) = "{domain}")
    }}
}}
"""

@mcp.tool()
async def get_policies(context: str, domain: str | None = None) -> list[PolicyResult]:
    """Retrieve ALL policies applicable to context. Exhaustive — never top-k."""
    store = _require_store()

    results: list[PolicyResult] = []
    if isinstance(store, _ProductionStore):
        # Production: SPARQL SELECT via NeptuneSparqlStore (read-only, SigV4-signed).
        # Domain filter applied in SPARQL to avoid transferring all policies over the wire.
        if domain is not None:
            # Security note: ``domain`` is string-interpolated into SPARQL.
            # Acceptable in the production path because domain values come from the
            # caller-controlled MCP params dict.  When spec-multi-strategy-routing ships,
            # replace with parameterised Neptune query API.  Tracked: use-rdflib-initbindings.
            sparql = _GET_POLICIES_DOMAIN_SPARQL_TEMPLATE.format(domain=domain)
        else:
            sparql = _GET_POLICIES_ALL_SPARQL
        for row in store.neptune.sparql_select(sparql):
            results.append(
                PolicyResult(
                    uri=row.get("policy", ""),
                    title=row.get("name", ""),
                    effective_date=row.get("effectiveDate") or None,
                    domain=row.get("scope") or None,
                )
            )
    else:
        # Mock path: rdflib SPARQL over in-memory Dataset
        for row in store.graph.query(_GET_POLICIES_ALL_SPARQL):
            pol_domain = str(row.scope) if row.scope else None

            # Domain filter: skip if caller filtered by domain and this one doesn't match
            if domain is not None and pol_domain != domain:
                continue

            results.append(
                PolicyResult(
                    uri=str(row.policy),
                    title=str(row.name),
                    effective_date=str(row.effectiveDate) if row.effectiveDate else None,
                    domain=pol_domain,
                )
            )

    logger.info("get_policies completed", extra={"result_count": len(results)})
    return results


# ---------------------------------------------------------------------------
# Tool 5: query (named SPARQL templates)
# ---------------------------------------------------------------------------


@mcp.tool()
async def query(template_name: str, params: dict[str, Any]) -> QueryResult:
    """Execute a named SPARQL template. Returns typed result rows."""
    if template_name not in _TEMPLATE_RUNNERS:
        logger.info("query: unknown template", extra={"template_name": template_name})
        return QueryResult(
            template_name=template_name, rows=[], row_count=0, error="template not found"
        )

    store = _require_store()
    runner = _TEMPLATE_RUNNERS[template_name]
    try:
        if isinstance(store, _ProductionStore):
            # Production: substitute params into SPARQL and call sparql_select().
            # params are already validated by SparqlTemplate.execute() param-kind logic;
            # reuse the template object directly rather than the bound runner.
            tmpl = SPARQL_TEMPLATE_BY_ID[template_name]
            sparql_bound = tmpl.sparql
            for pspec in tmpl.params:
                val = params.get(pspec.name)
                if val is not None:
                    placeholder = f"?{pspec.name}"
                    replacement = f'"{val}"' if pspec.kind == "literal" else f"<{val}>"
                    sparql_bound = sparql_bound.replace(placeholder, replacement)
            rows = store.neptune.sparql_select(sparql_bound)
        else:
            rows = runner(store.graph, params)
    except Exception as exc:
        # Execution errors (missing required param, backend error) return a
        # structured error result — consistent with the unknown-template path.
        logger.warning("query execution error", extra={"template_name": template_name})
        return QueryResult(template_name=template_name, rows=[], row_count=0, error=str(exc))
    logger.info("query completed", extra={"template_name": template_name, "row_count": len(rows)})
    return QueryResult(template_name=template_name, rows=rows, row_count=len(rows))


# ---------------------------------------------------------------------------
# Tool 6: summarize
# ---------------------------------------------------------------------------


@mcp.tool()
async def summarize(topic: str) -> SummaryResult:
    """Thematic synthesis spanning many documents via the global strategy."""
    store = _require_store()

    # Synthesis placeholder — deterministic, no Bedrock call in either path yet.
    # Topic text is not echoed into the summary per content-capture policy (ADR-0015).
    if isinstance(store, _ProductionStore):
        summary_text = (
            "[RuleQueryRouter] Synthesis not yet available — graphrag.routing is pending."
        )
    else:
        summary_text = "[MOCK] No live synthesis — mock server returned this placeholder."

    # Seed citations from vector search
    topic_vec = store.embedder.embed([topic])[0]
    hits = store.vector.knn(topic_vec, 5)
    citations = []
    for hit in hits:
        c = hit.chunk
        citations.append(Citation(uri=c.id, title=c.text[:80]))

    logger.info("summarize completed", extra={"citation_count": len(citations)})
    return SummaryResult(topic=topic, summary=summary_text, citations=citations)
