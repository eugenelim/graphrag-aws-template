# Spec: spec-multi-strategy-routing

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0013](../../adr/0013-multi-strategy-server-side-routing.md) (multi-strategy routing — primary decision this spec implements); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine; untrusted-data guard carried forward); [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition the strategies operate over); [ADR-0014](../../adr/0014-mcp-tool-server.md) (MCP tool server whose `ask` tool this router powers)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** algorithm

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.routing` module delivers the server-side strategy routing layer that powers the `ask` tool's internal path, as specified by ADR-0013. It provides:

1. **`RuleQueryRouter`** — deterministic signal detection over question text and any entity URIs extracted by the upstream analyzer. Fires first; returns a strategy or `ambiguous` with no LLM call.

2. **`BedrockQueryRouter`** — LLM fallback that fires only when `RuleQueryRouter` returns `ambiguous`. The question is treated as untrusted data; the routing output is strict-validated against the fixed strategy vocabulary before being used.

3. **`StrategyTrace`** — a typed data structure carrying `strategy`, `decided_by` (`rule` | `bedrock`), and a per-leg span tree. Attached to every `ask` and `get_policies` response. Required by ADR-0013's honesty constraint and the strategy-trace confirmation gate.

4. **`get_policies` isolation** — the `get_policies` tool path bypasses both routers. `strategy = normative_exhaustive` and `decided_by = none` are set unconditionally before retrieval begins. No router is invoked; the trace carries this as a constant.

This module owns the routing decision logic only. The retrieval executors that act on the chosen strategy (OpenSearch kNN, Neptune SPARQL expand, Bedrock synthesis) are out of scope — they are the `packages/graphrag/mcp-tool-server` and `packages/graphrag/normative-retrieval` concerns.

## Boundaries

### Always do

- Return `strategy` and `decided_by` in the `StrategyTrace` of every `ask` and `get_policies` response — a response without a trace fails the strategy-trace confirmation gate (ADR-0013).
- Strict-validate `BedrockQueryRouter` output against the fixed strategy vocabulary (`StrategyEnum`) before returning it. If the Bedrock response contains a value not in the enum, default to `hybrid_graph` and log a warning.
- Set `strategy = normative_exhaustive` and `decided_by = none` unconditionally in the `get_policies` path — never route through `RuleQueryRouter` or `BedrockQueryRouter` on a `get_policies` call.
- Treat the `question` parameter in `BedrockQueryRouter` as untrusted data: pass it to the Bedrock prompt as a data field, not as instruction text. The prompt structure must be fixed; the question is substituted into a safe slot only.
- Run `RuleQueryRouter` without any external service call — pure Python, no AWS dependencies, no network I/O.

### Ask first

- Changing routing matrix thresholds or adding a new strategy to `StrategyEnum`: each change affects every `ask` caller and requires ADR-0013 to be updated (or a superseding ADR).
- Changing the `BedrockQueryRouter` prompt structure: the prompt is the untrusted-data boundary; changes must go through a security review.
- Adding a `strategy` hint parameter to the `ask` tool: ADR-0013 explicitly deferred this as a "revisit if" condition; do not add it without re-opening the ADR.

### Never do

- Invoke `RuleQueryRouter` or `BedrockQueryRouter` on the `get_policies` path — `strategy = normative_exhaustive` is structurally required, not decided.
- Return a strategy not in `StrategyEnum` — this would produce a retrieval executor dispatch error and breaks the strategy-trace assertion in all callers.
- Trust `BedrockQueryRouter` output before strict-validation — even a valid enum string from the Bedrock response must be validated against the enum (not compared as a raw string), to prevent prompt-injection-influenced routing.
- Import boto3 or botocore in `_rule_router.py` — `RuleQueryRouter` must be importable and testable with no AWS SDK present.
- Log the `question` text at INFO level or above — question text carries disclosure risk (ADR-0014 `ask` content-off-by-default principle; see also OTEL content-capture convention in design.md).

## Testing Strategy

- **TDD** — `RuleQueryRouter`: one unit test per routing matrix row (AC1–AC6). Each test supplies a fixture question and asserts the expected strategy; the router is instantiated with no external dependencies. Red-green-refactor; tests in `packages/graphrag/tests/routing/test_rule_router.py`.
- **TDD** — `BedrockQueryRouter`: prompt construction, output validation, fallback to `hybrid_graph` on invalid output. Mock the Bedrock client; assert no raw question string appears in the prompt outside the designated data slot. Tests in `packages/graphrag/tests/routing/test_bedrock_router.py`.
- **TDD** — `get_policies` isolation (AC11–AC12): fixture that calls the routing dispatch with a `get_policies` intent; asserts `strategy == normative_exhaustive` and `decided_by == none` (AC11); asserts neither `RuleQueryRouter` nor `BedrockQueryRouter` is constructed or invoked (spy/mock — AC12).
- **TDD** — `StrategyTrace` completeness (AC9–AC10): every `ask` fixture response carries `strategy`, `decided_by`, and `legs`; a response missing any field fails the test.
- **Goal-based check** — import isolation (AC13): `python -c "import graphrag.routing._rule_router"` exits 0 without boto3/botocore installed.

## Acceptance Criteria

- [ ] `RuleQueryRouter.route(question="How many employees are in the Finance department?")` returns `strategy=structured` — aggregation verb ("how many") detected; `RuleQueryRouter` routes to `structured` on aggregation verb presence regardless of entity class (class detection is not implemented). The routing matrix row "Aggregation verb + entity or class" is satisfied by the aggregation verb alone in the rule implementation; the entity/class qualifier distinguishes it from other strategies at the Bedrock layer for ambiguous cases.
- [ ] `RuleQueryRouter.route(question="What does biz:Finance relate to in the graph?")` returns `strategy=graph_expand` — entity URI pattern (`biz:` prefix) plus relationship verb ("relate to") detected.
- [ ] `RuleQueryRouter.route(question="What does the Incident Response SOP say about severity levels?", entity_uris=["urn:doc:my-repo:sops/ir.md"])` returns `strategy=hybrid_graph` — entity URI provided plus factual verb.
- [ ] `RuleQueryRouter.route(question="What is the best practice for customer onboarding?")` returns `strategy=vector_only` — no entity present, specific factual question.
- [ ] `RuleQueryRouter.route(question="Tell me broadly about how the Finance domain operates")` returns `strategy=global` — no entity present, thematic/broad question detected.
- [ ] `RuleQueryRouter.route(question="Can you explain the relationship between the IR SOP and the Finance policy?", entity_uris=["urn:doc:my-repo:sops/ir.md", "urn:doc:my-repo:policies/finance.md"])` returns `strategy=ambiguous` — multiple entity URIs with a non-relationship-verb question trigger the multi-entity/mixed-signal → `ambiguous` rule explicitly defined in `_signals.py`. The rule: two or more entity URIs present AND no single dominant signal (no aggregation verb, no clear relationship verb, no clear factual pattern) → `ambiguous`. The `explain` verb is not in the relationship-verb set; "explain the relationship between X and Y" is the mixed-signal case.
- [ ] `BedrockQueryRouter.route(ambiguous_question)` returns a value in `StrategyEnum`; the returned value is not `ambiguous` (Bedrock must resolve to a concrete strategy).
- [ ] `BedrockQueryRouter.route(question)` where the Bedrock response contains a string not in `StrategyEnum` returns `strategy=hybrid_graph` (safe default) and logs a warning — strict-validation fallback.
- [ ] Every `ask` tool response carries a `StrategyTrace` with non-null `strategy`, `decided_by`, and `legs` fields.
- [ ] `decided_by` is `"rule"` when `RuleQueryRouter` returned a non-ambiguous strategy; `"bedrock"` when `BedrockQueryRouter` was invoked.
- [ ] `decided_by` is `"none"` and `strategy` is `"normative_exhaustive"` in all `get_policies` responses.
- [ ] On a `get_policies` call, neither `RuleQueryRouter` nor `BedrockQueryRouter` is constructed or invoked — confirmed by replacing both with a spy that raises on invocation.
- [ ] `python -c "from graphrag.routing._rule_router import RuleQueryRouter"` exits 0 in an environment where boto3 and botocore are not installed.
- [ ] `BedrockQueryRouter` prompt construction places the `question` text inside a structured data field, not as instruction text; a fixture question containing SPARQL Update keywords (`INSERT`, `DROP`) does not alter the routing output.

## Assumptions

- Technical: `graphrag.routing` lives in `packages/graphrag/src/graphrag/routing/`; test files in `packages/graphrag/tests/routing/`.
- Technical: `StrategyEnum` is a Python `enum.StrEnum` (Python 3.11+) with values: `hybrid_graph`, `structured`, `graph_expand`, `vector_only`, `global`, `normative_exhaustive`. `ambiguous` is a sentinel returned only by `RuleQueryRouter` and never stored in a `StrategyTrace`.
- Technical: The Bedrock client used by `BedrockQueryRouter` is the same `bedrock-runtime` client already used by the synthesizer — passed in at construction, not constructed internally (dependency injection for testability).
- Technical: The entity URI list fed to `RuleQueryRouter` comes from an upstream NER/question-analyzer step that is not part of this spec's scope. `RuleQueryRouter.route()` accepts an optional `entity_uris: list[str]` parameter; when absent or empty, entity-URI signals are not available.
- Technical: Ruff and mypy CI gates apply; all public functions carry full type annotations.
- Product: Signal detection in `RuleQueryRouter` uses keyword matching and simple heuristics (regex patterns, verb lists) — not an ML classifier. The routing matrix in ADR-0013 is the source of truth for which signals map to which strategies.
- Product: `BedrockQueryRouter` model is `amazon.nova-lite-v1:0` (low-latency Bedrock model suitable for classification tasks) — configurable via env var, defaulting to Nova Lite.
