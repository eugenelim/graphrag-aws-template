# Spec: spec-text2sparql-guarded

- **Status:** Approved <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine; read-only guard re-ratified for SPARQL grammar — primary decision this spec implements); [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (named-graph scope — generated SPARQL must target a specific partition graph); [ADR-0013](../../adr/0013-multi-strategy-server-side-routing.md) (`text2sparql` is called by the `structured`, `hybrid_graph`, and `graph_expand` strategy executors); [ADR-0014](../../adr/0014-mcp-tool-server.md) (content-capture policy — question text never in spans or responses)
- **Supersedes:** [`text2opencypher-guarded`](../text2opencypher-guarded/spec.md) (openCypher/LPG era; same guard shape, re-authored for SPARQL grammar)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** algorithm

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.text2sparql` module implements the guarded natural language → SPARQL SELECT translation path used by the multi-strategy router's `structured`, `hybrid_graph`, and `graph_expand` strategy executors inside the MCP tool server. Given a question and a schema-context snippet, it:

1. **Generates** a SPARQL SELECT query via a Bedrock Claude (Converse) call, with the question and schema treated as untrusted data in the prompt (OWASP LLM01).

2. **Validates** the generated query against a two-layer guard:
   - **App-layer mutation denylist (layer 1, belt-and-suspenders):** rejects any query containing SPARQL Update keywords (`INSERT`, `DELETE`, `DROP`, `CLEAR`, `LOAD`, `CREATE`, `COPY`, `MOVE`, `ADD`) — word-boundary, case-insensitive.
   - **Structural validator (layer 1, continued):** rejects queries that are not a `SELECT`; rejects queries without a `FROM NAMED` clause (partition scope is required); rejects unbounded property paths (e.g. `*` with no upper bound) — read-cost amplification guard; rejects `SERVICE` clauses (federation exfiltration / SSRF vector — `mcp_lambda_role` is read-only but `SERVICE` is a read that issues outbound HTTP).

3. **Self-heals** within a bounded cap: on validation failure or Neptune execution error, re-generates once with the error as feedback (max 1 re-generation; 2 LLM calls total). After the cap, returns a governed refusal with no executed query.

4. **Executes** the validated query via `rdflib` in-memory SPARQL (offline CI path) or the Neptune SPARQL store (live path), returning rows + an audit trace.

The module is stateless — it receives injected store clients and a Bedrock client; it owns no persistent state. The IAM backstop (`mcp_lambda_role` read-only: `ReadDataViaQuery` + `connect`) is the load-bearing guard. The app-layer validator is belt-and-suspenders.

Key difference from `text2opencypher-guarded`: `rdflib` is a production-quality Python SPARQL engine with full named-graph support — the offline path exercises the same SPARQL SELECT semantics as Neptune, unlike openCypher's bounded subset evaluator. The offline path is the full-fidelity CI path.

## Boundaries

### Always do

- Validate every model-authored SPARQL query against the mutation denylist **before** executing it. A query containing any SPARQL Update keyword is never sent to Neptune.
- Enforce the `FROM NAMED <graph_uri>` requirement — the strategy executor provides the target partition URI; the validator confirms the generated query uses it. A query without named-graph scope is rejected.
- Treat the question and schema context as untrusted data in the Bedrock Converse call: both ride the `messages` block as data; the `system` block carries the defensive untrusted-data directive only (OWASP LLM01). The raw Neptune execution error is never returned to the caller — feed it to the internal self-heal loop; surface only the sanitized refusal envelope.
- Bound the self-heal loop: 1 initial generation + at most 1 re-generation = 2 LLM calls maximum. After the cap, return a `Text2SparqlResult(executed_query=None, rows=[], refusal_reason=...)` with no executed query.
- Keep `mcp_lambda_role` as the load-bearing IAM backstop. This spec confirms (via a live-smoke AC) that the `mcp_lambda_role` IAM grant blocks `DROP GRAPH` and `INSERT DATA` at the SPARQL endpoint — the validator is a secondary layer, not the guarantee.

### Ask first

- Raising `MAX_HEAL_ATTEMPTS` above 1 — affects the worst-case LLM call budget.
- Changing the Bedrock model away from the synthesis default (`DEFAULT_SYNTHESIS_MODEL_ID`) — a different model would widen the Bedrock IAM grant.
- Adding a runtime dependency beyond `rdflib` + `boto3` — each new library must be assessed for offline CI compatibility.
- Relaxing the `FROM NAMED` requirement — doing so would allow unscoped queries to touch all named graphs.

### Never do

- Execute a query that failed validation or after the self-heal cap — return `Text2SparqlResult(executed_query=None, rows=[], refusal_reason=...)`.
- Return the raw Neptune error, SPARQL query text, schema context, or any endpoint/ARN detail to the caller — sanitized refusal envelope only.
- Execute a `SELECT` without a `FROM NAMED` clause — the partition scope is load-bearing for retrieval correctness (ADR-0012).
- Log question text at INFO level or above — question text is never in spans, log lines, or audit traces returned to callers (ADR-0014 content-capture policy).
- Issue a SPARQL Update statement from this module — the module is a query-only path; all writes use `ingestion_task_role`.

## Testing Strategy

- **TDD** — mutation denylist (AC1): parametrized table of SPARQL Update keywords; each rejected; a well-formed SELECT is accepted. `_validator.py` is pure Python, no AWS dependency — importable without boto3.
- **TDD** — structural validator (AC2): no `FROM NAMED` → rejected; no `SELECT` keyword → rejected; unbounded `*` property path → rejected; valid bounded SELECT with `FROM NAMED` → accepted.
- **TDD** — `BedrockText2SparqlGenerator` (AC3): mock Bedrock client; asserts the Converse request places schema + question in `messages` as data (never `system`); asserts the system block contains the defensive untrusted-data directive; asserts code-fence stripping; asserts `DEFAULT_SYNTHESIS_MODEL_ID` is the default model.
- **TDD** — self-heal loop (AC4): first generation fails validation → re-generation with error feedback in `messages` as data → second attempt passes → executed. Persistently-failing query → refusal after cap with no executed query and no third LLM call.
- **TDD** — re-injection guard (AC4): the `feedback` string (validation error or Neptune error) is placed in `messages` as data, not in `system`; a fixture `feedback` containing SPARQL Update keywords (`DROP GRAPH urn:graph:normative`) does not alter the system framing.
- **TDD** — rdflib offline execution (AC5): construct an in-memory `rdflib` named graph seeded with fixture triples; a validated SELECT with `FROM NAMED` returns the expected rows; an unscoped SELECT returns zero rows (rdflib named-graph isolation confirmed).
- **TDD** — audit trace completeness (AC6): `Text2SparqlResult` carries schema_context, all generated queries with validation verdicts, executed query, rows, and refusal reason when no query ran.
- **Goal-based check** — import isolation (AC7): `python -c "from graphrag.text2sparql._validator import SparqlValidator"` exits 0 without boto3 or rdflib installed (the validator is pure-string, no graph library dependency).
- **Goal-based check** — live-smoke IAM backstop (AC8): against the deployed MCP Lambda under `mcp_lambda_role`, a test-forced `DROP GRAPH urn:graph:normative` and `INSERT DATA { ... }` are rejected by Neptune IAM at the `/sparql` endpoint — proving the load-bearing backstop at the engine, not just the grant's shape. (Live deploy only; tagged `@pytest.mark.live_aws`.)

## Acceptance Criteria

- [x] `SparqlValidator.validate(query)` returns `ValidationResult(valid=False, rule="mutation_keyword")` for each of: `INSERT DATA { ... }`, `DELETE WHERE { ... }`, `DROP GRAPH urn:graph:normative`, `CLEAR GRAPH urn:graph:normative`, `LOAD <http://example.org>`, `CREATE GRAPH urn:graph:new`, `COPY urn:graph:a TO urn:graph:b`, `MOVE urn:graph:a TO urn:graph:b`, `ADD urn:graph:a TO urn:graph:b` (word-boundary, case-insensitive). A well-formed `SELECT ?s FROM NAMED <urn:graph:normative> WHERE { GRAPH <urn:graph:normative> { ?s a biz:Policy } }` returns `ValidationResult(valid=True)`.
- [x] `SparqlValidator.validate(query)` returns `ValidationResult(valid=False, rule="service_clause")` for a query containing a `SERVICE <...> { ... }` block (word-boundary, case-insensitive). This blocks SPARQL federation as an SSRF / exfiltration vector — the IAM backstop permits `SERVICE` reads but the app layer guard prevents them regardless.
- [x] `SparqlValidator.validate(query)` returns `ValidationResult(valid=False, rule="missing_from_named")` for a SELECT without a `FROM NAMED` clause (including a query using only an inline `GRAPH {}` without the dataset-level `FROM NAMED` declaration); returns `ValidationResult(valid=False, rule="not_a_select")` for a SPARQL CONSTRUCT; returns `ValidationResult(valid=False, rule="unbounded_property_path")` for a query containing an unbound `*` path (e.g. `?s biz:hasChunk* ?o` with no upper bound `{0,N}`).
- [x] `BedrockText2SparqlGenerator.generate(question, schema_context, graph_uri)` issues a Converse request where: `system` contains the defensive untrusted-data directive and `modelId` equals `DEFAULT_SYNTHESIS_MODEL_ID`; `messages` contains the schema_context and question as data (not instruction text); a Markdown code fence (` ```sparql ... ``` `) is stripped from the response; the returned string is a well-formed SPARQL SELECT.
- [x] On a validation failure, the orchestrator re-invokes the generator with the validation rule name in `messages` as data; the `system` block is unchanged; the feedback string (even if it contains SPARQL Update keywords) does not alter the system framing. After 1 re-generation attempt (2 LLM calls total), a persistently-failing query produces a `Text2SparqlResult(executed_query=None, rows=[], refusal_reason="max heal attempts reached")`; no third Bedrock call is made.
- [x] `text2sparql_query(question, schema_context, graph_uri, store=rdflib_store)` against a fixture `rdflib` named graph seeded with 3 `biz:Policy` triples returns the 3 URIs in the result rows; the executed query contains `FROM NAMED <graph_uri>`.
- [x] `Text2SparqlResult` returned by `text2sparql_query()` carries: `schema_context`, `generated_queries` (list, one entry per attempt, each with `query_text` and `validation_verdict`), `executed_query` (the query that actually ran, or `None` if refused), `rows` (list), `refusal_reason` (str or `None`). The `question` text does not appear in any field of the result.
- [x] `python -c "from graphrag.text2sparql._validator import SparqlValidator"` exits 0 in an environment where boto3 and rdflib are not installed — the validator has no external dependencies.
- [ ] Against the deployed MCP Lambda, a `mcp_lambda_role`-credentialed SPARQL POST of `DROP GRAPH <urn:graph:normative>` to the Neptune `/sparql` endpoint returns an IAM `AccessDeniedException` — confirming the IAM backstop at the engine. (Live-smoke AC, `@pytest.mark.live_aws`.) (deferred: text2sparql-live-smoke-iam-backstop)
- [x] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/text2sparql/` with zero errors.

## Assumptions

- Technical: `graphrag.text2sparql` lives in `packages/graphrag/src/graphrag/text2sparql/`; tests in `packages/graphrag/tests/text2sparql/`.
- Technical: `DEFAULT_SYNTHESIS_MODEL_ID` is the constant already used by the synthesizer (from `graphrag.synthesize`); no new Bedrock model grant is needed.
- Technical: The Bedrock client is injected at construction (`BedrockText2SparqlGenerator(bedrock_client)`), not constructed internally — dependency injection for testability and for the mock server path.
- Technical: The Neptune SPARQL client is the `NeptuneSparqlStore` from `packages/graphrag/neptune-sparql-store`; the offline substitute is `rdflib` `ConjunctiveGraph` with named-graph support (same offline substitute used throughout the platform).
- Technical: The `FROM NAMED <graph_uri>` requirement means the strategy executor always provides the target partition URI. For `structured` queries targeting the taxonomy, this is `urn:graph:taxonomy`; for `hybrid_graph` / `graph_expand` it is `urn:graph:descriptive` (entity neighbourhood). The `get_policies` path never calls text2sparql — it uses `NormativeRetriever` directly.
- Technical: Ruff and mypy CI gates apply; all public functions carry full type annotations.
- Product: The SPARQL denylist is conservative on string literals — a mutation keyword inside a string literal (e.g. `FILTER(?name = "DROP GRAPH test")`) triggers a false-reject. This is the accepted trade-off per ADR-0011 (the IAM backstop, not validator completeness, is the load-bearing guarantee).
- Product: The live-smoke IAM backstop AC (AC8) confirms the ADR-0011 Confirmation gate: "a test-forced SPARQL `DROP GRAPH` and `INSERT DATA` under `mcp_lambda_role` are rejected by IAM at the `/sparql` endpoint." This is the final confirmation that the production guard works at the engine, independent of the app-layer validator.
