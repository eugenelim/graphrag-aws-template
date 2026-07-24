# ADR-0014: MCP tool server as the primary query interface: generic typed tools over per-class or per-strategy tools

- **Status:** Accepted
- **Date:** 2026-07-23
- **Decision-makers:** eugenelim
- **Supersedes:** [ADR-0005](0005-community-detection-in-fargate-louvain.md) mechanism
- **Implemented by:** `spec-mcp-tool-server` (the build spec that turns this ADR into shippable code) — the `summarize` tool's `global` strategy replaces the Louvain community-detection mechanism; ADR-0005's "communities" conceptually carry forward as the `global` strategy output, but no standing Neptune Analytics service is required
- **Related:** [RFC-0004 §D1, §D5](../rfc/0004-biz-ops-kg-pivot.md); [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine the tools query against); [ADR-0012](0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition the tools operate over); [ADR-0013](0013-multi-strategy-server-side-routing.md) (routing the `ask` tool uses internally); `spec-mcp-tool-server`; `spec-multi-strategy-routing`

## Decision summary

- **Decision:** We will build the MCP server on the official Python MCP SDK (`mcp` on PyPI, `FastMCP` high-level API) with Mangum as the ASGI-to-Lambda adapter; expose **six generic typed tools** rather than per-class or per-strategy tools; support two deployment targets (Lambda and local stdio) from the same tool definitions; and provide a mock server for offline-first development that exercises the full tool surface with no AWS credentials.
- **Because:** One generic tool set requires one org MCP approval; per-class or per-strategy tools require re-approval with every new class or strategy added; FastMCP is Anthropic's canonical Python MCP server implementation; Mangum bridges FastMCP's ASGI interface to Lambda without a separate adapter layer.
- **Applies to:** The MCP tool surface, its deployment targets, the client connection model, and the mock server; routing inside `ask` is governed by ADR-0013; the retrieval implementations are each spec's concern.
- **Tradeoff accepted:** Callers cannot distinguish tool semantics by tool name alone — they rely on the type annotations, tool docstrings, and response schema to understand each tool's contract. The tool names (`ask`, `get_policies`, etc.) are stable, but the internal routing and retrieval strategies are opaque to callers.
- **Revisit if:** Org MCP approval policy changes to permit per-class tool surfaces without per-class re-approval; or a caller type emerges that structurally needs separate tool endpoints (e.g. a tool marketplace that requires single-schema tools for indexing).

## Context

RFC-0004 chose MCP as the primary interface for both human-in-AI-IDE and AI agent callers. Three design pressures shape this decision:

**Org approval policy.** Enterprise orgs reviewing MCP tool integrations typically approve the full tool set at once — one approval per MCP server. A per-class surface (`search_policy`, `search_sop`, `search_transcript`, …) requires a new approval each time a class is added to the ontology. A per-strategy surface (`hybrid_query`, `structured_query`, `graph_expand`, …) couples the caller's tool selection to internal routing decisions that ADR-0013 made caller-opaque. Both approaches violate the single-approval intent.

**Offline-first posture.** The repo's core constraint (from ADR-0002 and RFC-0004 §§D5) is that all tooling must work without AWS credentials. An MCP server that requires a live Neptune SPARQL cluster to import cannot be tested in CI, cannot be run by a developer before deploying, and cannot be validated by the work-loop's gate suite. The mock server must replicate the production tool surface exactly — same FastMCP tool definitions, same response schema — with in-memory stores substituted for AWS services.

**Two principal types, two ingress paths.** Human developers in an AI IDE connect via API key over HTTPS. Automation roles and Bedrock AgentCore connect with SigV4 IAM auth. These are structurally different auth models that warrant separate ingress points (API Gateway HTTP API vs. IAM-auth Function URL) so each can be revoked independently without affecting the other.

The `mcp` Python SDK (`modelcontextprotocol/python-sdk`) is Anthropic's canonical Python MCP server implementation. Its `FastMCP` high-level API generates MCP schema from Python type annotations automatically — tool definitions are plain decorated async functions, keeping the tool surface and its schema in one place. Mangum wraps FastMCP's ASGI app for Lambda without a custom adapter layer.

## Decision

> We will implement the MCP server using `FastMCP` from the `mcp` Python SDK with Mangum as the Lambda ASGI adapter, exposing six generic typed tools. Two deployment targets are built from the same tool definitions: Lambda (streamable-http + Mangum) for production and local stdio (`mcp dev`) for development. A mock server backs all non-AWS tool paths with in-memory stores for offline CI and local development.

Concretely:

1. **Six generic typed tools.** A single tool set covers the full retrieval surface. Generic `type?` parameters (where per-class discrimination is needed) rather than per-class tools.

   | Tool | What it returns | When the LLM calls it |
   |---|---|---|
   | `ask(question)` | Synthesised answer + citations + strategy trace | Human wants a direct answer; agent wants pre-synthesised result |
   | `search(question, type?, k?)` | Ranked typed RDF resources (chunks/docs) | Agent inspects or re-ranks raw results before synthesising |
   | `search_graph(uri, hops?)` | Typed subgraph (nodes + edges from named graph) | Agent reasons over relationships; entity neighbourhood lookup |
   | `get_policies(context, domain?)` | All applicable Policy resources (exhaustive) | AI workflow retrieves normative constraints before acting |
   | `query(template_name, params)` | Typed SPARQL template result | Known structural question; no LLM needed for query generation |
   | `summarize(topic)` | Community/thematic synthesis | Broad thematic question spanning many documents |

2. **FastMCP tool definitions.** Tool definitions are plain decorated async functions; FastMCP generates the MCP schema from type annotations automatically. The same definition serves both targets:

   ```python
   from mcp.server.fastmcp import FastMCP

   mcp = FastMCP("biz-ops-knowledge-platform")

   @mcp.tool()
   async def ask(question: str) -> dict:
       """Ask a question. Returns a synthesised answer, citations, and strategy trace."""
       ...

   @mcp.tool()
   async def get_policies(context: str, domain: str | None = None) -> list[dict]:
       """Retrieve ALL policies applicable to this context. Exhaustive — never top-k."""
       ...
   ```

3. **Two deployment targets from the same tool definitions.**

   | Target | Transport | Adapter | When |
   |---|---|---|---|
   | AWS Lambda | `streamable-http` | `mangum` | Production / staging |
   | Local / CI | `stdio` | none (`mcp dev`) | Development, Claude Desktop, Claude Code local |

   Lambda entrypoint: `handler = Mangum(mcp.streamable_http_app(), lifespan="off")`.

4. **Mock server for offline-first development.** The mock runs the same FastMCP tool definitions against in-memory stores — `rdflib` in-memory SPARQL, `store/vector_memory.py` (cosine), `HashEmbedder` (deterministic), `TemplateSynthesizer` (deterministic template), `RuleQueryRouter` only (no Bedrock fallback). The fixture corpus in `packages/graphrag/tests/fixtures/` seeds stores at startup. Started with `mcp dev` (stdio) or `python -m graphrag.mcp --mock` (streamable-http on localhost:8000). The mock is the CI surface — the offline gate suite exercises all six tools against the fixture corpus.

5. **Three client connection modes.** The mode is a local config choice; tool definitions and response schema are identical across all three.

   | Mode | Transport | Auth | Principal |
   |---|---|---|---|
   | **Local mock** | stdio (subprocess) | None | Developer (offline) |
   | **Production — IDE/human** | HTTPS → API Gateway HTTP API | API key (`x-api-key` header) | Developer in AI IDE |
   | **Production — automation/AgentCore** | HTTPS → Function URL | SigV4 (IAM) | AI agent/workflow or Bedrock AgentCore |

6. **MCP proxy for IDE/human production mode.** A thin local subprocess (`packages/graphrag/mcp_proxy`) translates stdio MCP frames to HTTPS requests with the API key header added. Its only job is the translation — it carries no retrieval logic. Configured identically to the mock server subprocess.

7. **API Gateway HTTP API + Function URL as separate ingress paths.** The two can be independently revoked. API Gateway enforces a hard 30 s integration timeout — the `ask` synthesis path must complete within 30 s on the human path. The Function URL path has up to 15 min. Response streaming is not used behind API Gateway (not supported by the HTTP API integration); `streamable-http` runs in non-streaming request/response mode.

8. **`ask`, `get_policies`, and `summarize` synthesise internally.** The other three tools (`search`, `search_graph`, `query`) return raw typed resources for the caller (IDE LLM or agent) to synthesise. `summarize` uses Bedrock to produce a thematic synthesis from the Neptune taxonomy graph — it is not a raw retrieval tool. This distinction keeps authoritative-answer tools separate from raw-retrieval tools, giving the agent tools full access to underlying retrieval results when needed.

9. **`summarize` supersedes ADR-0005's community-detection mechanism.** The `global` strategy (routed by `summarize(topic)`) synthesises thematic answers from the Neptune taxonomy graph + Bedrock; it does not require a standing Neptune Analytics service or a pre-computed Louvain graph. ADR-0005's "communities" concept is preserved in the output semantics; the Fargate Louvain step is dropped. ADR-0005's header is updated to `Superseded by ADR-0013/ADR-0014` once this ADR lands (tracked: `adr-0005-supersession-record` in `[backlog].open`).

## Decision drivers

- **Org approval policy.** One MCP server, one tool-set approval. Adding a new document class or retrieval strategy does not trigger a re-approval.
- **Offline-first is a repo constraint.** The mock server is not optional hardening — it is required to honour the offline-first posture. All CI gates must run without AWS credentials.
- **`FastMCP` is the canonical implementation.** Anthropic maintains the `mcp` SDK; `FastMCP` generates the MCP protocol schema from type annotations, reducing the surface area that can drift between the tool's behaviour and its advertised schema.
- **Mangum avoids a custom Lambda adapter.** The ASGI interface is stable; Mangum translates it to the Lambda event model without a custom shim. The same ASGI app runs locally with any ASGI dev server.
- **Separate ingress for separate principal types.** API Gateway (API key) for humans in IDEs; Function URL (SigV4) for automation and AgentCore. Different auth models, different revocation blast radius, different timeout constraints — they must be separate.
- **Raw-vs-synthesised split follows caller intent.** `ask` and `get_policies` are the "give me an answer" tools; the other four are the "give me the data" tools for agent-side synthesis and reranking. Merging them into one synthesising tool removes agent control over the raw retrieval results.

## Consequences

**Positive:**
- One org MCP approval covers the entire retrieval surface; adding classes or strategies is internal.
- `FastMCP` type-annotation-driven schema generation keeps tool interface and implementation in sync.
- The same tool definitions power offline CI, local dev, and Lambda production — no parallel test harness.
- Separate ingress paths for separate principal types can be independently throttled, logged, and revoked.
- The `summarize` tool replaces the standing Neptune Analytics dependency of ADR-0005 with an on-demand Bedrock synthesis path.

**Negative:**
- Callers cannot infer retrieval semantics from tool names alone — they rely on docstrings and response schema. A caller that calls `ask` expecting exhaustive normative recall gets best-match descriptive; it must call `get_policies` for normative.
- The API Gateway 30 s hard timeout constrains the `ask` synthesis path on the human/IDE mode. Deep graph expansions that exceed 30 s must be handled by the `search_graph` tool + agent-side synthesis instead.
- `streamable-http` in non-streaming mode behind API Gateway means the full response is buffered before delivery — very large `get_policies` result sets may approach Lambda response body limits (6 MB).
- The MCP proxy adds a local subprocess dependency for IDE/human production mode; a proxy crash drops the IDE's MCP connection silently.

## Confirmation

- **Mode:** lint/CI + offline fixture suite
- **Signal (tool schema):** FastMCP generates the MCP schema at startup; the six tool schemas are asserted by a test that parses the schema JSON and confirms each tool name, parameter types, and return type annotation — validates that the schema matches the decorated function signatures.
- **Signal (offline isolation):** `python -m graphrag.mcp --mock` starts without AWS credentials and without network access; the fixture corpus seeds the in-memory stores; all six tools return non-empty responses against the fixture. Part of the offline CI gate suite.
- **Signal (two-target parity):** the fixture corpus exercises the same queries against both mock (stdio) and the Lambda handler (Mangum-wrapped ASGI, invoked directly with a synthetic event); response schema is asserted identical.
- **Signal (API Gateway timeout):** the `ask` synthesis path is timed in a load fixture; a slow path trigger (deep graph expand against the fixture corpus) completes in < 30 s; a warning is emitted (not a test failure) if it exceeds 20 s.
- **Owner:** eugenelim; spec owner: `spec-mcp-tool-server`

## Alternatives considered

- **Per-class tools** (`search_policy`, `search_sop`, `search_transcript`, …). Expose a separate tool per document class. *Rejected against the approval-policy driver:* a new class triggers a re-approval; the ontology's `skos:Concept` taxonomy means class-level discrimination is properly a filter parameter, not a tool-name-level distinction. A tool surface that mirrors the OWL class hierarchy also leaks the schema into the MCP approval process.

- **Per-strategy tools** (`hybrid_query`, `structured_query`, `graph_expand`, …). Expose a separate tool per retrieval strategy. *Rejected against ADR-0013:* ADR-0013 explicitly made strategy routing server-side and caller-opaque. Per-strategy tools reverse that decision — the caller now must implement the routing matrix. An IDE LLM selecting among six strategy-named tools is the scenario ADR-0013 was written to avoid.

- **Third-party MCP framework** (LangChain MCP adapter, LlamaIndex MCP integration). Use an ecosystem framework that wraps the MCP SDK. *Rejected:* adds a framework dependency whose version lifecycle is not controlled by this project; schema generation behaviour may diverge from the canonical `mcp` SDK; the canonical SDK already provides `FastMCP`'s high-level API.

- **WebSocket or SSE streaming transport.** Use SSE for streaming `ask` responses. *Rejected:* API Gateway HTTP API does not support SSE streaming to the backend integration; adding it would require upgrading to API Gateway REST API (higher cost, more complex configuration) or a WebSocket API (different model entirely). Streaming is a latency optimisation for the human path; the Function URL automation path already has 15 min and does not need it. Deferred as a future consideration if interactive UX requires streaming partial synthesis results.

- **Separate Lambda per tool.** One Lambda function per MCP tool — isolated cold-start, isolated IAM scope per tool. *Rejected:* cold-start multiplied 6×; shared embedding cache and connection pool across tools is a meaningful future optimisation; the MCP protocol already handles concurrent tool calls on one server; IAM scope is better controlled at the server level (one `mcp_lambda_role`), not per-tool.

## References

- [RFC-0004 §D1 — MCP as primary interface; §D5 — offline-first posture](../rfc/0004-biz-ops-kg-pivot.md)
- [ADR-0005](0005-community-detection-in-fargate-louvain.md) — superseded community-detection mechanism
- [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) — SPARQL/RDF engine the tools query against
- [ADR-0012](0012-owl-schema-only-and-named-graph-partition.md) — named-graph partition model
- [ADR-0013](0013-multi-strategy-server-side-routing.md) — routing inside the `ask` tool
- [biz-ops architecture design.md §MCP server implementation stack](../architecture/biz-ops-knowledge-graph/design.md)
- [FastMCP documentation — Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Mangum — ASGI adapter for AWS Lambda](https://mangum.fastapiexpert.com/)
- `spec-mcp-tool-server`; `spec-multi-strategy-routing`
