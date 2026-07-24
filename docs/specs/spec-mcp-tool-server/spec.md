# Spec: spec-mcp-tool-server

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0014](../../adr/0014-mcp-tool-server.md) (six generic typed tools; FastMCP + Mangum; mock server; two deployment targets; content-capture policy — primary decision this spec implements); [ADR-0013](../../adr/0013-multi-strategy-server-side-routing.md) (`ask` delegates to `RuleQueryRouter` → `BedrockQueryRouter` cascade); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (`mcp_lambda_role` read-only: `ReadDataViaQuery` + `connect`); [ADR-0015](../../adr/0015-otel-observability.md) (content-capture policy: question text never in spans or log lines above DEBUG; ADOT layer on Lambda); [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition model the tools operate over)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** service

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.mcp` module implements the MCP tool server defined in ADR-0014 — six generic typed tools exposed via FastMCP, deployed to two targets (AWS Lambda and local stdio), and backed by a mock server for offline CI and local development.

This spec owns the tool surface contract (function signatures, response schemas, and docstrings that are the MCP schema), the two-target wiring (Mangum Lambda adapter + `mcp dev` stdio), and the mock server that backs the offline gate suite. It does not own the routing logic (ADR-0013 + `spec-multi-strategy-routing`), the retrieval implementations (per-strategy specs), or the OTEL instrumentation setup (ADR-0015 + `spec-otel-observability`).

The deliverable is:
1. **Tool definitions** (`_tools.py`) — six `@mcp.tool()` async functions with Pydantic response schemas that FastMCP schema-generates from.
2. **Lambda entrypoint** (`_lambda.py`) — `handler = Mangum(mcp.streamable_http_app(), lifespan="off")`.
3. **Mock server** (`_mock.py`) — same `FastMCP` instance, same six tool definitions, in-memory stores substituted. `python -m graphrag.mcp --mock` starts it on `localhost:8000` (streamable-http) or `mcp dev` starts it (stdio).
4. **Response schemas** (`_schemas.py`) — Pydantic models: `AskResponse`, `SearchResult`, `SubgraphResult`, `PolicyResult`, `QueryResult`, `SummaryResult`.
5. **Module entry point** (`__main__.py`) — `python -m graphrag.mcp` with `--mock` flag.

### Six tool signatures

```python
@mcp.tool()
async def ask(question: str) -> AskResponse:
    """Ask a question. Returns a synthesised answer, citations, and strategy trace."""

@mcp.tool()
async def search(question: str, type: str | None = None, k: int = 10) -> list[SearchResult]:
    """Semantic search. Returns ranked typed RDF resources (chunks/docs)."""

@mcp.tool()
async def search_graph(uri: str, hops: int = 1) -> SubgraphResult:
    """Named-graph neighbourhood lookup. Returns typed subgraph (nodes + edges)."""

@mcp.tool()
async def get_policies(context: str, domain: str | None = None) -> list[PolicyResult]:
    """Retrieve ALL policies applicable to context. Exhaustive — never top-k."""

@mcp.tool()
async def query(template_name: str, params: dict) -> QueryResult:
    """Execute a named SPARQL template. Returns typed result rows."""

@mcp.tool()
async def summarize(topic: str) -> SummaryResult:
    """Thematic synthesis spanning many documents via the global strategy."""
```

### Response schemas

```python
class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    strategy_trace: StrategyTrace

class SearchResult(BaseModel):
    uri: str
    title: str
    doc_type: str
    partition: str
    score: float
    excerpt: str | None

class SubgraphResult(BaseModel):
    root_uri: str
    nodes: list[dict]  # {uri, type, label}
    edges: list[dict]  # {subject, predicate, object}

class PolicyResult(BaseModel):
    uri: str
    title: str
    effective_date: str | None
    domain: str | None
    excerpt: str | None

class QueryResult(BaseModel):
    template_name: str
    rows: list[dict]
    row_count: int

class SummaryResult(BaseModel):
    topic: str
    summary: str
    citations: list[Citation]
```

### Mock server substitutions

| Production | Mock substitute |
|-----------|----------------|
| Neptune SPARQL cluster | `rdflib` `ConjunctiveGraph` (in-memory, seeded from `tests/fixtures/`) |
| OpenSearch kNN | `store/vector_memory.py` (cosine similarity) |
| Bedrock embedding | `HashEmbedder` (deterministic SHA-256 embedding) |
| Bedrock synthesis | `TemplateSynthesizer` (deterministic template: `"[MOCK] ..."`) |
| Bedrock routing | `RuleQueryRouter` only (no `BedrockQueryRouter` fallback) |
| `mcp_lambda_role` IAM | No auth; accepted from localhost |

The fixture corpus (`packages/graphrag/tests/fixtures/`) contains: 3 `biz:Policy` triples, 2 `biz:SOP` triples, 1 `biz:OrgRole` triple, and their chunk-level PROV-O provenance — enough to exercise all six tools with non-empty responses.

## Boundaries

### Always do

- Expose exactly six tools via `@mcp.tool()` decorators — not per-class, not per-strategy; names, signatures, and docstrings are the MCP protocol contract and may not be changed without ADR amendment.
- Use the same `FastMCP` instance and the same `@mcp.tool()` decorated functions for both Lambda and stdio targets — both run from `_tools.py`; no divergent tool implementations.
- Keep question text out of spans and log lines above DEBUG — question text is never passed to `create_span()`, `logger.info()`, `logger.warning()`, or `logger.error()`; it stays in the tool handler body only. (ADR-0015 content-capture policy.)
- Return Pydantic models from tool handlers — FastMCP serialises them to JSON for the MCP schema; raw dicts or strings are not acceptable return types for typed tools.
- Log the `strategy_trace` at INFO level (it contains no question text) per the OTEL span model.
- Use `mcp_lambda_role` (read-only) for all Neptune SPARQL calls from the Lambda target — never `ingestion_task_role`.

### Ask first

- Adding a seventh tool — requires a new org MCP approval (ADR-0014 Decision driver 1).
- Changing a tool name or removing a parameter — callers have pinned the tool names; breaking changes require a migration plan.
- Enabling response streaming — API Gateway HTTP API does not support SSE streaming; the Function URL path supports it but the client model differs; reconsider if the latency profile changes.
- Changing the `lifespan="off"` Mangum setting — affects Lambda startup behaviour and cold-start model.

### Never do

- Create a per-class tool (e.g. `search_policy`, `search_sop`) — violates the single-approval principle and the ADR-0014 decision.
- Create a per-strategy tool (e.g. `hybrid_query`, `graph_expand_query`) — violates ADR-0013's caller-opaque routing.
- Return question text in any field of `AskResponse`, `PolicyResult`, or any other response schema — the content-capture policy is non-negotiable.
- Import boto3, botocore, or any AWS SDK in `_mock.py` — the mock server must start without AWS credentials.
- Issue SPARQL Update (INSERT/DELETE/DROP) from any tool handler — all tools are read-only; `mcp_lambda_role` enforces this at IAM, but it is also a code invariant.
- Synthesise inside `search`, `search_graph`, or `query` — these are raw-retrieval tools; synthesis is the caller's or the agent's responsibility.

## Testing Strategy

- **TDD** — FastMCP schema assertion (AC1): start the `FastMCP` instance in test mode; parse the generated MCP schema JSON; assert all six tool names are present; assert each tool's input schema matches the decorated function's type annotations; assert each tool's output schema matches the Pydantic response model.
- **TDD** — offline isolation (AC2): `python -m graphrag.mcp --mock` started in a subprocess with no AWS env vars set; all six tools invoked via the streamable-http interface with the fixture corpus; each returns a non-empty, schema-valid response; the subprocess exits cleanly.
- **TDD** — two-target parity (AC3): invoke the same six queries against the mock (via `_mock.py` in-process) and against the Mangum-wrapped Lambda handler (synthetic API Gateway event); responses are schema-identical (same keys, same field types); no field present in one response and absent in the other.
- **TDD** — `ask` timing gate (AC4): invoke `ask(question="...")` against the mock; execution completes in < 30 s (warning emitted, not test failure, if > 20 s); confirmed in CI with a generous wall-clock assertion.
- **TDD** — content-capture convention (AC5): a static linter (`test_content_capture_conventions.py`) asserts no `question`, `query.text`, `sparql.query`, `document.content`, or `chunk.text` attribute is passed to any span creation call in `_tools.py`, `_orchestrator.py` (text2sparql), or `_generator.py`; confirmed by static string search on those files.
- **Goal-based check** — `get_policies` exhaustive (AC6): fixture corpus has 3 `biz:Policy` triples; `get_policies(context="...", domain=None)` returns all 3 `PolicyResult` objects; no top-k cutoff applied; confirmed by asserting `len(results) == 3`.
- **Goal-based check** — `query` template execution (AC7): a named template `"policies_by_domain"` is pre-registered; `query(template_name="policies_by_domain", params={"domain": "hr"})` returns `QueryResult(rows=[...], row_count>0)` against the fixture corpus.

## Acceptance Criteria

- [ ] The FastMCP instance's generated MCP schema, parsed as JSON, contains exactly six tools: `ask`, `search`, `search_graph`, `get_policies`, `query`, `summarize`. Each tool's `inputSchema` matches the decorated function's type annotations (verified by FastMCP's own schema introspection test fixture).
- [ ] `python -m graphrag.mcp --mock` starts without any AWS environment variables set and without network access (confirmed via mock network). All six tools invoked via HTTP POST to `localhost:8000` with the fixture corpus return HTTP 200 and schema-valid JSON bodies (validated against the Pydantic response model).
- [ ] For the same six fixture queries, the mock (invoked via `_mock.py` directly) and the Lambda handler (invoked via `Mangum(mcp.streamable_http_app(), lifespan="off")` with a synthetic API Gateway event) return responses with the same keys and field types. No field is present in one response and absent in the other.
- [ ] `ask(question="What are the HR policies?")` against the mock fixture corpus completes in < 30 s (wall clock) — this confirms the mock path has no runaway loop or blocking call; it does not validate the real API Gateway constraint (which is dominated by Bedrock latency elided by the mock). The live Bedrock path timing is a live-deploy gate (`@pytest.mark.live_aws`, `spec-otel-observability`). If mock execution exceeds 20 s, a `WARNING`-level log line is emitted: `"ask path exceeded 20s warning threshold"`.
- [ ] A pytest test in `test_content_capture_conventions.py` reads the source of `_tools.py`, `text2sparql/_orchestrator.py`, and `text2sparql/_generator.py` and asserts none of the following strings appear as span attribute keys in any `create_span()` / `set_attribute()` call: `question.text`, `query.text`, `sparql.query`, `document.content`, `chunk.text`. (Static source inspection — no runtime needed.)
- [ ] `get_policies(context="workflow approval required", domain=None)` against the fixture corpus (which contains 3 `biz:Policy` triples) returns a list of exactly 3 `PolicyResult` objects. No pagination, no top-k truncation. The result is the same regardless of `domain` filter (fixture has no domain metadata).
- [ ] `query(template_name="policies_by_domain", params={"domain": "hr"})` against the fixture corpus returns `QueryResult(template_name="policies_by_domain", rows=[...], row_count=1)` (one fixture policy has `domain="hr"`). An unknown `template_name` returns `QueryResult(rows=[], row_count=0, error="template not found")` without raising an exception.
- [ ] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/mcp/` with zero errors. All six `@mcp.tool()` functions carry full type annotations; FastMCP generates the schema without type errors.

## Assumptions

- Technical: `graphrag.mcp` lives in `packages/graphrag/src/graphrag/mcp/`; files: `__init__.py`, `_tools.py`, `_lambda.py`, `_mock.py`, `_schemas.py`, `__main__.py`; tests in `packages/graphrag/tests/mcp/`.
- Technical: `FastMCP` from `mcp` PyPI package (Anthropic's canonical Python MCP SDK) is available in `pyproject.toml [server]` dependency group. `mangum` is in `[lambda]` dependency group. Neither is in `[mock]` or `[dev]` — mock uses only in-memory stores.
- Technical: The `Citation` and `StrategyTrace` types referenced in `AskResponse` are imported from `graphrag.provenance` and `graphrag.routing` respectively. The `spec-provenance-citations` and `spec-multi-strategy-routing` specs define their fields; this spec imports them.
- Technical: The Lambda Lambda entrypoint module is `graphrag.mcp._lambda`; the Terraform module sets `handler = "graphrag.mcp._lambda.handler"`.
- Technical: The mock server uses `RuleQueryRouter` only — no Bedrock call is issued in the mock path. `get_policies` in the mock routes directly to the `NormativeRetriever` substitute (`rdflib` named-graph SPARQL), bypassing the router.
- Technical: The fixture corpus in `packages/graphrag/tests/fixtures/` is Turtle (`.ttl`) format. `_mock.py` loads it via `rdflib.ConjunctiveGraph().parse()` at startup. The fixture is read from a path relative to the installed package's tests directory.
- Technical: The OTEL attribute filter (ADR-0015) is wired in `_tools.py` at the SDK initialization point — not in this spec's scope to implement the ADOT layer, but the content-capture attribute names (`question.text`, etc.) are defined here as the canonical list.
- Product: The `query` tool's named templates are a small registry in `_tools.py` or a sibling `_templates.py` file. For ini-002, one template is required to pass AC7: `"policies_by_domain"`. Additional templates are implementation-time additions; this spec only requires the `query` tool to handle the template lookup and return an empty/error result for unknown names.
- Product: `get_policies` is exhaustive because ADR-0012 mandates `normative_exhaustive` strategy for all policy retrieval — no top-k. The tool's docstring must say "Exhaustive — never top-k" exactly as shown in the tool signature above.
- Product: `search_graph(uri, hops=1)` returns nodes and edges from the named graph containing the given URI. For `hops=1`, it returns direct neighbours only; for `hops=2`, one extra hop. The mock fixture has depth-2 entity neighbourhood for 2 fixture entities. The `hops` parameter is bounded at max 2 in the tool implementation (cost guard).
