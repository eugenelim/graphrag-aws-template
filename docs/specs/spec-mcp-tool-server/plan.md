# Plan: spec-mcp-tool-server

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Four tasks. T1 (schemas + tool stubs) establishes the Pydantic response models and the six FastMCP `@mcp.tool()` decorated function stubs — returning `NotImplementedError` internally but with correct type annotations so FastMCP generates the MCP schema. T2 (mock server) wires the in-memory stores and fixture corpus so all six tools return non-empty schema-valid responses. T3 (Lambda entrypoint + Mangum) adds the `_lambda.py` entrypoint and the `__main__.py` CLI entry point; also confirms two-target parity. T4 (content-capture + timing gates) adds the static linter and the `ask` wall-clock assertion.

T1 must complete before T2 because the mock server imports the tool definitions. T2 must complete before T3 because two-target parity requires the mock to be runnable. T4 is independent of T3 but depends on T2's mock being runnable.

The riskiest part is FastMCP schema generation: the MCP schema is generated from Python type annotations at import time. If `StrategyTrace` or `Citation` (imported from other modules) have forward-reference issues, FastMCP schema generation fails. T1's AC1 (schema assertion test) is the early-warning canary for this.

The second risk is the `get_policies` exhaustive guarantee: the tool must not apply a `LIMIT` clause in the mock path. T2's AC6 confirms all 3 fixture policies are returned.

No AWS credentials are needed for T1–T4. The `ask` timing gate (T4) uses wall-clock time in CI, not a mocked clock.

## Constraints

- ADR-0014: exactly six tools, named `ask`, `search`, `search_graph`, `get_policies`, `query`, `summarize`; names may not change without an ADR amendment.
- ADR-0014: `get_policies` is exhaustive — never top-k; `strategy = normative_exhaustive` constant.
- ADR-0014: mock server imports only rdflib, vector_memory, HashEmbedder, TemplateSynthesizer, RuleQueryRouter — no boto3, no Bedrock calls.
- ADR-0014: `Mangum(mcp.streamable_http_app(), lifespan="off")` is the Lambda entrypoint form.
- ADR-0015: question text never in spans or log lines above DEBUG — enforced by static linter in T4.
- API Gateway hard timeout: `ask` path must complete in < 30 s; warning if > 20 s.
- `search_graph(uri, hops=...)` `hops` bounded at max 2 (cost guard).
- Ruff + mypy CI gates must stay green; all six `@mcp.tool()` functions carry full type annotations.

## Construction tests

**T1 (schemas + stubs):**
- Parse FastMCP's generated MCP schema JSON; assert exactly 6 tool names: `ask`, `search`, `search_graph`, `get_policies`, `query`, `summarize`.
- Each tool's `inputSchema` matches the decorated function's type annotation (FastMCP introspection).
- Each tool's Pydantic return type is registered in the schema's `components/schemas` (or equivalent).
- `ruff check` and `mypy` pass on `_tools.py` and `_schemas.py` with zero errors; zero type errors on tool return annotations.

**T2 (mock server):**
- `python -m graphrag.mcp --mock` starts without AWS env vars; HTTP POST to each of the 6 tool endpoints returns HTTP 200 and a schema-valid JSON body.
- `ask(question="What are the HR policies?")` → `AskResponse` with `answer` non-empty, `citations` non-empty, `strategy_trace.strategy` is a valid strategy enum value.
- `search(question="approval workflow", k=5)` → list of up to 5 `SearchResult` objects.
- `search_graph(uri=fixture_policy_uri, hops=1)` → `SubgraphResult` with `nodes` non-empty.
- `get_policies(context="workflow approval required")` → list of exactly 3 `PolicyResult` objects (all 3 fixture policies).
- `query(template_name="policies_by_domain", params={"domain": "hr"})` → `QueryResult(rows=[...], row_count=1)`.
- `query(template_name="unknown_template", params={})` → `QueryResult(rows=[], row_count=0, error="template not found")` (no exception).
- `summarize(topic="HR governance")` → `SummaryResult` with `summary` non-empty.

**T3 (Lambda entrypoint + two-target parity):**
- `Mangum(mcp.streamable_http_app(), lifespan="off")` instantiates without error.
- For the same six fixture queries, responses from mock (in-process via `_mock.py`) and from the Mangum-wrapped handler (synthetic API Gateway event) have the same keys and field types.
- No field present in one response and absent in the other.

**T4 (content-capture + timing):**
- Static linter (`test_content_capture_conventions.py`): source of `_tools.py`, `text2sparql/_orchestrator.py`, `text2sparql/_generator.py` contains none of `question.text`, `query.text`, `sparql.query`, `document.content`, `chunk.text` as span attribute strings.
- `ask(question="What are the HR policies?")` against mock fixture completes in < 30 s (wall clock); if > 20 s, a WARNING log line is emitted.

## Design (LLD)

### Design decisions

- **Tool stubs first, mock wiring second.** T1 creates stub tool bodies that return `NotImplementedError`; T2 replaces those bodies with the mock-wired implementations. This order lets the FastMCP schema test (AC1) pass before any store wiring exists, confirming the type annotation-driven schema generation works independently of the store layer.
- **`FastMCP` instance is a module-level singleton in `_tools.py`.** `mcp = FastMCP("biz-ops-knowledge-platform")` at module level; all six `@mcp.tool()` decorators apply to this instance. `_mock.py` and `_lambda.py` both import `mcp` from `_tools` — they do not create a second instance.
- **Mock store injection via module-level dependency.** `_mock.py` sets module-level store references (`_tools._store = MockStore()`) before starting the server. This avoids dependency injection through each tool function signature (which would change the MCP schema) while keeping the mock substitution clean.
- **`TemplateSynthesizer` for mock `ask` + `summarize`.** The synthesizer returns `f"[MOCK] Answer for: {truncated_question}"` — deterministic and never calls Bedrock. The `truncated_question` is the first 30 chars of the question. This does NOT violate the content-capture policy: the policy governs OTEL span attributes and log lines above DEBUG — the `answer` field in `AskResponse` is the tool's response surface returned to the caller, which is expected to address the question. The caller receives an answer that echoes the question topic; no span attribute or INFO-level log contains the question text.
- **`query` named templates registry.** A `dict[str, str]` of template name → SPARQL SELECT string in `_tools.py` (or `_templates.py`). For ini-002: one template `"policies_by_domain"`. Unknown template names return `QueryResult(rows=[], row_count=0, error="template not found")` without exception.
- **`hops` max-2 guard.** `search_graph(uri, hops)` clamps `hops = min(hops, 2)` before the graph traversal. A `hops > 2` request does not raise — it is silently clamped. This is a cost guard, not a protocol error.
- **`get_policies` uses `NormativeRetriever` in production, direct SPARQL in mock.** In the mock, `get_policies` issues a SPARQL `SELECT * WHERE { GRAPH <urn:graph:normative> { ?doc a ?type . } }` directly against the fixture rdflib store — no router, no Bedrock. In production, the `NormativeRetriever` (`spec-normative-partition`) is called directly (bypassing the router per ADR-0013).

### Data & schema

```python
# graphrag/mcp/_schemas.py

from pydantic import BaseModel
from graphrag.provenance import Citation       # from spec-provenance-citations
from graphrag.routing import StrategyTrace     # from spec-multi-strategy-routing

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
    nodes: list[dict]   # {uri: str, type: str, label: str | None}
    edges: list[dict]   # {subject: str, predicate: str, object: str}

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
    error: str | None = None

class SummaryResult(BaseModel):
    topic: str
    summary: str
    citations: list[Citation]
```

### Component / module decomposition

```
packages/graphrag/src/graphrag/mcp/
├── __init__.py       # exports: mcp (FastMCP instance)
├── _schemas.py       # Pydantic response models
├── _tools.py         # six @mcp.tool() decorated async functions; module-level FastMCP instance
├── _mock.py          # mock store wiring; starts mock server via mcp.streamable_http_app()
├── _lambda.py        # handler = Mangum(mcp.streamable_http_app(), lifespan="off")
└── __main__.py       # python -m graphrag.mcp --mock entry point

packages/graphrag/tests/mcp/
├── test_schema.py                       # FastMCP schema assertion (AC1)
├── test_mock_server.py                  # offline isolation + tool responses (AC2, AC6, AC7)
├── test_two_target_parity.py            # mock vs. Mangum handler parity (AC3)
├── test_timing.py                       # ask < 30s gate (AC4)
└── test_content_capture_conventions.py  # static linter (AC5)

packages/graphrag/tests/fixtures/
├── fixture_corpus.ttl   # 3 biz:Policy + 2 biz:SOP + 1 biz:OrgRole + PROV-O provenance
└── fixture_vectors.json # pre-computed HashEmbedder vectors for fixture corpus
```

### Failure cases & resilience

- **FastMCP schema generation fails on import.** If `StrategyTrace` or `Citation` have forward-reference issues, `mcp = FastMCP(...)` raises at import time. T1's schema assertion test catches this before any other task runs.
- **`_mock.py` store initialization fails (fixture file missing).** `python -m graphrag.mcp --mock` exits with a clear error message: `"Fixture corpus not found: {path}"`. Not a silent hang.
- **Mangum `lifespan="off"` and FastMCP startup hooks.** FastMCP may register startup hooks for store initialization. With `lifespan="off"`, Mangum skips the ASGI lifespan protocol. `_lambda.py` must initialize stores in the module body (before `Mangum(...)`) rather than in FastMCP startup hooks, so Lambda cold-start wires the stores correctly.
- **`ask` exceeds 30 s API Gateway hard timeout.** The tool returns a timeout-shaped error: `AskResponse(answer="[timeout] The request took too long to complete.", citations=[], ...)`. In production, the API Gateway returns 504 before the Lambda can return — but the mock must handle this gracefully to confirm the path.
- **Unknown `type` parameter in `search`.** `search(question, type="biz:UnknownClass", k=10)` — the mock returns `[]` (no fixture documents of that type). No exception. The `type` parameter is a filter, not a validation gate.

### Quality attributes (NFRs)

- **Offline CI.** Mock server and all tests run without AWS credentials; no boto3 calls in `_mock.py`.
- **Mypy-clean.** Full type annotations on all six tool functions; Pydantic models are typed; FastMCP generates schema from annotations.
- **Content-capture enforced statically.** The linter in `test_content_capture_conventions.py` prevents question text from appearing in span attributes — a programmatic guarantee, not a convention.
- **API Gateway 30 s constraint.** Wall-clock timing gate in CI ensures the `ask` path does not drift above the hard timeout.

## Tasks

### T1: Pydantic schemas + FastMCP tool stubs + schema assertion

**Depends on:** `graphrag.provenance.Citation`, `graphrag.routing.StrategyTrace` (cross-module imports — must be available or stubbed)

**Touches:**
- `packages/graphrag/src/graphrag/mcp/__init__.py`
- `packages/graphrag/src/graphrag/mcp/_schemas.py`
- `packages/graphrag/src/graphrag/mcp/_tools.py` (stubs)
- `packages/graphrag/tests/mcp/test_schema.py`

**Tests (TDD):** FastMCP schema JSON contains exactly 6 tool names; each tool's `inputSchema` matches annotations; Pydantic return types registered; `ruff check` and `mypy` pass.

**Done when:** schema assertion test passes; `mypy` clean on `_tools.py` and `_schemas.py`.

---

### T2: Mock server — in-memory stores + fixture corpus + tool implementations

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/mcp/_mock.py`
- `packages/graphrag/src/graphrag/mcp/__main__.py`
- `packages/graphrag/tests/fixtures/fixture_corpus.ttl`
- `packages/graphrag/tests/fixtures/fixture_vectors.json`
- `packages/graphrag/tests/mcp/test_mock_server.py`

**Tests (TDD):** mock starts without AWS env vars; all 6 tools return HTTP 200 + schema-valid JSON; `get_policies` returns all 3 fixture policies; `query` returns 1 hr-domain policy; unknown template returns `error="template not found"`.

**Done when:** mock server tests pass; `ruff check` and `mypy` clean.

---

### T3: Lambda entrypoint + two-target parity

**Depends on:** T1, T2

**Touches:**
- `packages/graphrag/src/graphrag/mcp/_lambda.py`
- `packages/graphrag/tests/mcp/test_two_target_parity.py`

**Tests (TDD):** `Mangum(...)` instantiates; same 6 queries yield schema-identical responses from mock and Mangum handler; no key present in one and absent in the other.

**Done when:** parity tests pass; `ruff check` and `mypy` clean.

---

### T4: Content-capture static linter + ask timing gate

**Depends on:** T2

**Touches:**
- `packages/graphrag/tests/mcp/test_content_capture_conventions.py`
- `packages/graphrag/tests/mcp/test_timing.py`

**Tests (TDD):** static linter finds zero forbidden attribute keys in tool + generator sources; `ask` mock completes in < 30 s; WARNING emitted if > 20 s.

**Done when:** linter and timing tests pass; full test suite green; `ruff check` and `mypy` clean on all `graphrag/mcp/` files.

## Rollout

- **Delivery:** no flag — `graphrag.mcp` is a new module; no existing callers.
- **Infrastructure:** Lambda function and API Gateway provisioned by `infra-tf/neptune-sparql-engine` (or a companion `infra-tf/mcp-lambda` module). ADOT Lambda layer ARN pinned in Terraform (ADR-0015). `mcp_lambda_role` gains `AWSXRayDaemonWriteAccess` (ADR-0015).
- **Deployment sequencing:** depends on `packages/graphrag/routing` (`spec-multi-strategy-routing`), `packages/graphrag/normative` (`spec-normative-partition`), and `packages/graphrag/provenance` (`spec-provenance-citations`).

## Risks

- **FastMCP version drift.** FastMCP's schema generation API may change across minor versions. Pin the `mcp` package version in `pyproject.toml`; the schema assertion test (AC1) catches schema drift on any package update.
- **Mangum + FastMCP ASGI lifespan incompatibility.** FastMCP uses ASGI lifespan protocol for startup/shutdown hooks. `lifespan="off"` in Mangum skips those hooks — any store initialization in FastMCP lifespan events won't run in Lambda. Mitigation: wire all store initialization in module body (`_lambda.py`) before `Mangum(...)` is called; not in FastMCP hooks.
- **`StrategyTrace` / `Citation` forward references.** Pydantic v2 with FastMCP may fail to resolve forward references for imported types. If this occurs, use `model_rebuild()` at module import time in `_schemas.py`. The T1 schema test catches this early.
- **`get_policies` result set size approaching Lambda response limit (6 MB).** A large normative corpus could exceed the 6 MB Lambda payload limit. For ini-002, the fixture corpus is small; add a response-size warning in the tool handler (log at WARNING if serialized response > 5 MB) as a future safeguard.

## Changelog

- 2026-07-23: initial plan
