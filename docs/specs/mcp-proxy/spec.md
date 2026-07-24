# Spec: mcp-proxy — local stdio→HTTPS passthrough

**Status:** Shipped
**Mode:** full (security boundary — network I/O + API key handling; structural change — new module)
**Initiative:** ini-002 — Business Operations Knowledge Graph
**Implements:** ADR-0014 §6 — MCP proxy for IDE/human production mode
**Source:** `workspace.toml` `["ini-002".work].queue` `packages/graphrag/mcp-proxy`

## Objective

Implement `graphrag.mcp_proxy` — a thin local subprocess that translates stdio
MCP frames to HTTPS `POST` requests (with `x-api-key` header) against the deployed
MCP Lambda on AWS API Gateway. AI IDE tools (Cursor, VS Code with MCP extension)
speak MCP over stdio; the deployed server speaks MCP over HTTPS. This proxy bridges
the two without any retrieval logic.

## Acceptance Criteria

- [x] AC1: `python -m graphrag.mcp_proxy` starts, reads `MCP_ENDPOINT_URL` and
  `MCP_API_KEY` from env, exits 1 with a clear error if either is missing.
- [x] AC2: Endpoint validation rejects any `MCP_ENDPOINT_URL` that does not start
  with `https://` — exits 1 with a descriptive message.
- [x] AC3: Each newline-delimited JSON frame read from stdin is forwarded as a
  `POST` body to the endpoint with `Content-Type: application/json` and
  `x-api-key: <api_key>` headers. The response body (decoded as UTF-8) is written
  to stdout with a trailing newline and flushed.
- [x] AC4: On any exception during the HTTP request (including `HTTPError` for
  4xx/5xx responses — the server's response body is intentionally not forwarded;
  the caller receives a generic internal-error frame), a valid JSON-RPC error frame
  (`{"jsonrpc":"2.0","error":{"code":-32603,"message":"<str(e)>"},"id":null}`)
  is written to stdout and flushed — the proxy never crashes on a request failure.
- [x] AC5: The API key value is never written to stderr or stdout; startup log
  emits only its length or a `***` placeholder.
- [x] AC6: `MCP_TIMEOUT` env var (optional, default 60) sets the request timeout
  in seconds; a non-integer value causes exit 1 with a clear message.
- [x] AC7: Fully typed (`py.typed` present in parent package); `mypy` passes with
  `disallow_untyped_defs = true`. Stdlib-only — no new entries in `pyproject.toml`.
- [x] AC8: All test scenarios pass under `pytest` (HTTPS enforcement, missing
  env var, round-trip mock, error forwarding, timeout env wiring, empty-line skip,
  API-key redaction in startup log).

## Testing Strategy

Unit tests only (stdlib mock via `unittest.mock.patch`). No live network calls in
CI. Injectable stdin/stdout (`io.StringIO`) for round-trip and error tests.

**Verification mode:** Goal-based (new module; gates: `ruff check`, `ruff format
--check`, `mypy`, `pytest packages/graphrag/tests/mcp_proxy/`).

## Tasks

1. Create `packages/graphrag/src/graphrag/mcp_proxy/__init__.py` — empty package
   marker.
2. Create `packages/graphrag/src/graphrag/mcp_proxy/_proxy.py` — config loading +
   `proxy_loop` (injectable stdin/stdout) + `main`.
3. Create `packages/graphrag/src/graphrag/mcp_proxy/__main__.py` — entry point.
4. Create `packages/graphrag/tests/mcp_proxy/__init__.py` — empty test package.
5. Create `packages/graphrag/tests/mcp_proxy/test_proxy.py` — test scenarios.

## Boundaries

**Touches:** `packages/graphrag/src/graphrag/mcp_proxy/`,
`packages/graphrag/tests/mcp_proxy/`, `docs/specs/mcp-proxy/`

**Does NOT touch:** `pyproject.toml`, `workspace.toml`, any existing source file,
the mock server (`mcp-mock-server`), or infra.

## Declined scope

- Retry logic — spec says single attempt.
- Session management — proxy is stateless by design.
- Async / asyncio — synchronous urllib loop matches the spec.
- httpx / requests — stdlib only per ADR-0014 constraint.
- Logging infrastructure (`logging` module) — stderr print is sufficient.
- Forwarding HTTPError response body — AC4 explicitly covers all exceptions with a
  generic internal-error frame; masking the raw API Gateway error is intentional.
