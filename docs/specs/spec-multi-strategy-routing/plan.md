# Plan: spec-multi-strategy-routing

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Four tasks in a clean dependency chain. T1 (`StrategyEnum` + `StrategyTrace` data structures) is independent and establishes the shared vocabulary. T2 (`RuleQueryRouter`) and T3 (`BedrockQueryRouter`) depend on T1 but are independent of each other — write T2 first since it has no external service dependency and can be TDD-driven entirely offline. T4 (integration with the `get_policies` isolation test and the response envelope) depends on T1, T2, and T3.

The riskiest part is the `BedrockQueryRouter` prompt construction: the question must be embedded as data (not instruction) to satisfy the untrusted-data guard. This is tested by an injection fixture in T3 before any live Bedrock call.

No AWS credentials are needed for T1 or T2. T3 requires a mocked Bedrock client for unit tests; live Bedrock is not needed for the gate suite.

## Constraints

- ADR-0013: routing is server-side; callers do not supply a `strategy` parameter.
- ADR-0013: `get_policies` is always `normative_exhaustive` — no routing step.
- ADR-0013: `BedrockQueryRouter` output must be strict-validated against `StrategyEnum`; invalid output defaults to `hybrid_graph`.
- ADR-0011: question text is untrusted data; SPARQL Update keywords in the question must not alter routing output.
- ADR-0014: question text is not logged at INFO or above; `StrategyTrace` is included in every `ask`/`get_policies` response.
- Ruff + mypy CI gates must stay green.
- `RuleQueryRouter` must be importable without boto3/botocore installed.

## Construction tests

**T1 (data structures):**
- `StrategyEnum` has exactly 6 values: `hybrid_graph`, `structured`, `graph_expand`, `vector_only`, `global`, `normative_exhaustive`. Assert `len(StrategyEnum) == 6`.
- `StrategyTrace(strategy=StrategyEnum.hybrid_graph, decided_by="rule", legs=["vector", "graph_expand"])` serialises to a dict with the expected keys.

**T2 (`RuleQueryRouter`):**
- Each of the 6 ACs (AC1–AC6) has its own parametrized test; all run without boto3 installed.
- Import isolation: subprocess check `python -c "from graphrag.routing._rule_router import RuleQueryRouter"` exits 0 without boto3.

**T3 (`BedrockQueryRouter`):**
- Mock Bedrock `invoke_model` to return `{"strategy": "structured"}`; assert `route()` returns `StrategyEnum.structured`.
- Mock Bedrock to return `{"strategy": "not_a_strategy"}`; assert fallback to `StrategyEnum.hybrid_graph` and a warning log.
- Injection fixture: question `"DROP GRAPH <urn:graph:normative>; SELECT …"` — mock Bedrock to return the question text itself (a naive echo); assert the routing output is strict-validated to `hybrid_graph`, not the injected SPARQL.

**T4 (integration):**
- `get_policies` isolation: replace `RuleQueryRouter` and `BedrockQueryRouter` with spies that raise on invocation; assert no exception is raised when dispatching a `get_policies` intent.
- `StrategyTrace` completeness: response fixture for `ask` carries `strategy`, `decided_by`, and `legs` — all non-null.

## Design (LLD)

### Design decisions

- **`StrategyEnum` as `enum.StrEnum`.** Provides both string serialisation (for JSON response embedding) and enum membership validation (for the strict-validation gate in `BedrockQueryRouter`). Python 3.11+ `StrEnum` means `StrategyEnum("structured") == StrategyEnum.structured` — the strict-validation check is `StrategyEnum(bedrock_output)` in a try/except `ValueError`.
- **`BedrockQueryRouter` prompt structure (data-slot pattern).** The system prompt describes the routing task and lists valid strategy values. The question is injected into a `<question>` XML tag inside the human turn — a structural separator that LLM best practices recommend for untrusted input. Output is expected as a JSON object `{"strategy": "<value>"}` — parsed with `json.loads()`, then enum-validated.
- **Throttle fallback maps to `decided_by="bedrock"`** — not a new fourth vocabulary value. The fallback to `hybrid_graph` after Bedrock throttle exhaustion is recorded as a `LegSpan` with `store="bedrock"` and an error note in the span; `decided_by` remains `"bedrock"`. Callers observe the same vocabulary; the throttle detail is visible in the span tree.
- **`RuleQueryRouter` returns `ambiguous` as a sentinel, not part of `StrategyEnum`.** The sentinel is an internal routing artifact, never stored in a `StrategyTrace`. The callers check `result is None` (or a dedicated `Ambiguous` singleton) to decide whether to invoke `BedrockQueryRouter`.
- **Routing precedence (evaluation order in `RuleQueryRouter`).** Signals are evaluated in this fixed priority order to resolve ties: (1) aggregation verb → `structured`; (2) relationship verb + any entity URI → `graph_expand`; (3) entity URIs: two or more with no dominant signal → `ambiguous`; one entity URI, no aggregation/relationship verb → `hybrid_graph`; (4) thematic marker → `global`; (5) no signal → `vector_only`. AC3 (single entity URI + factual question) routes via rule 3 (single entity, no relationship verb, no aggregation verb → `hybrid_graph`).
- **Signal detection via compiled regex + verb lists.** Entity URI detection: `r"(urn:|https?://|biz:)\S+"`. Aggregation verbs: `{"count", "how many", "list all", "total", "sum"}`. Relationship verbs: `{"related to", "relate to", "relates to", "connected to", "links to", "refers to"}`. Thematic markers: `{"broadly", "in general", "overview", "tell me about"}`. These are tunable constants in `_signals.py`, separated from the routing logic in `_rule_router.py`.

### Data & schema

```python
# graphrag/routing/_types.py
import enum

class StrategyEnum(enum.StrEnum):
    hybrid_graph        = "hybrid_graph"
    structured          = "structured"
    graph_expand        = "graph_expand"
    vector_only         = "vector_only"
    global_             = "global"           # "global" is a Python keyword; use global_
    normative_exhaustive = "normative_exhaustive"

from dataclasses import dataclass, field

@dataclass
class LegSpan:
    store: str           # "opensearch" | "neptune" | "bedrock"
    latency_ms: int | None = None
    error: str | None = None  # e.g. "throttle-exhausted" for the retry-exhaustion path

@dataclass
class StrategyTrace:
    strategy: StrategyEnum
    decided_by: Literal["rule", "bedrock", "none"]  # import from typing
    legs: list[LegSpan]      # per-leg span with store + latency_ms (ADR-0013 requirement)
    router_latency_ms: int | None = None   # routing decision latency (separate from retrieval legs)
```

### Component / module decomposition

```
packages/graphrag/src/graphrag/routing/
├── __init__.py          # exports: RuleQueryRouter, BedrockQueryRouter, StrategyEnum, StrategyTrace
├── _types.py            # StrategyEnum, StrategyTrace
├── _signals.py          # compiled regex patterns and verb sets (constants only)
├── _rule_router.py      # RuleQueryRouter — no boto3 import
└── _bedrock_router.py   # BedrockQueryRouter — boto3 client injected at construction

packages/graphrag/tests/routing/
├── test_rule_router.py
├── test_bedrock_router.py
└── test_strategy_trace.py
```

### Failure, edge cases & resilience

- **Bedrock throttle on routing call.** `BedrockQueryRouter.route()` retries with exponential backoff (max 3 attempts, base 0.5 s) on `ThrottlingException`. After 3 failures, returns `hybrid_graph` with `decided_by="bedrock"` and a `LegSpan(store="bedrock", error="throttle-exhausted")` appended; logs a WARNING. The vocabulary stays `"rule" | "bedrock" | "none"` — throttle detail is visible in the span, not the `decided_by` field. This keeps the `ask` path alive under Bedrock throttle without blocking.
- **Question too long for routing prompt.** If the question exceeds 4 000 characters, truncate to 4 000 chars before constructing the `BedrockQueryRouter` prompt. Log a DEBUG line with the truncation length.
- **Empty question.** `RuleQueryRouter.route("")` returns `ambiguous` (no signals detected). `route_ask` does NOT short-circuit empty input — it lets both routers run normally: `RuleQueryRouter` returns `ambiguous`, then `BedrockQueryRouter` runs and returns `hybrid_graph`. The resulting `decided_by="bedrock"` is accurate. No special guard is needed; both routers already handle the degenerate case per their own logic above. The MCP tool validates non-empty `question` so empty input never reaches `route_ask` in production.

### Quality attributes (NFRs)

- **Import isolation**: `RuleQueryRouter` importable without boto3 — confirmed by subprocess test.
- **Latency**: `RuleQueryRouter` completes in < 1 ms for any question length up to 4 000 chars (no regex catastrophic backtracking — test with a 4 000-char adversarial input).
- **Mypy-clean**: all public functions fully type-annotated.

## Tasks

### T1: `StrategyEnum`, `StrategyTrace`, and shared types

**Depends on:** none

**Touches:**
- `packages/graphrag/src/graphrag/routing/__init__.py`
- `packages/graphrag/src/graphrag/routing/_types.py`
- `packages/graphrag/tests/routing/test_strategy_trace.py`

**Tests:**
- `len(StrategyEnum) == 6` and each member has the expected string value.
- `StrategyTrace(strategy=StrategyEnum.hybrid_graph, decided_by="rule", legs=[LegSpan(store="opensearch", latency_ms=5)])` serialises to a dataclass_asdict dict matching the expected shape.
- `str(StrategyEnum.global_) == "global"` — the Python name `global_` serialises to the JSON string `"global"` (keyword-clash footgun pinned).
- `StrategyEnum("not_valid")` raises `ValueError` — the strict-validation mechanism works.
- `decided_by` field is typed `Literal["rule", "bedrock", "none"]` — constructing `StrategyTrace(decided_by="unknown", …)` is flagged by mypy.

**Approach:**
1. Create `packages/graphrag/src/graphrag/routing/` with `__init__.py` stub.
2. Implement `_types.py` with `StrategyEnum` (StrEnum) and `StrategyTrace` (dataclass).
3. Export from `__init__.py`.

**Done when:** 5 tests pass; `ruff check` and `mypy` clean.

---

### T2: `RuleQueryRouter` — deterministic signal detection

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/routing/_signals.py`
- `packages/graphrag/src/graphrag/routing/_rule_router.py`
- `packages/graphrag/tests/routing/test_rule_router.py`

**Tests (TDD — one per AC, plus import isolation):**
1. `route("How many employees in Finance?")` → `structured`
2. `route("What does biz:Finance relate to?")` → `graph_expand`
3. `route("What does the IR SOP say?", entity_uris=["urn:doc:…"])` → `hybrid_graph`
4. `route("Best practice for onboarding?")` → `vector_only`
5. `route("Tell me broadly about Finance")` → `global`
6. `route(mixed_signal, entity_uris=[uri1, uri2])` → `ambiguous`
7. Import isolation subprocess check.

**Approach:**
1. Write all 7 failing tests.
2. Implement `_signals.py` with compiled regex and verb sets.
3. Implement `_rule_router.py` with the routing matrix logic.
4. Run tests red → green → refactor.

**Done when:** all 7 tests pass; no boto3 import in `_rule_router.py` or `_signals.py`; `ruff check` and `mypy` clean.

---

### T3: `BedrockQueryRouter` — LLM fallback

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/routing/_bedrock_router.py`
- `packages/graphrag/tests/routing/test_bedrock_router.py`

**Tests (TDD):**
1. Mock Bedrock returns `{"strategy": "structured"}` → `StrategyEnum.structured`.
2. Mock Bedrock returns `{"strategy": "invalid_value"}` → `StrategyEnum.hybrid_graph` + WARNING log.
3. Injection fixture: question containing `DROP GRAPH` → output is still a valid `StrategyEnum` value (not the injected SQL).
4. Bedrock raises `ThrottlingException` × 3 → returns `StrategyEnum.hybrid_graph` with `decided_by="bedrock"` and a `LegSpan` with `error="throttle-exhausted"` appended.
5. Prompt structure: capture the `messages` list passed to `invoke_model`; assert the user turn contains the question text only inside `<question>…</question>` tags and that the question text does not appear outside that tag in either the system or user content.

**Approach:**
1. Write all 5 failing tests using `unittest.mock.patch` on `boto3.client`.
2. Implement `_bedrock_router.py` with the data-slot prompt, JSON parse, enum validation, and retry logic.
3. Run tests red → green → refactor.

**Done when:** all 5 tests pass; `ruff check` and `mypy` clean.

---

### T4: `get_policies` isolation + response envelope integration

**Depends on:** T1, T2, T3

**Touches:**
- `packages/graphrag/src/graphrag/routing/__init__.py` (add `route_ask` and `route_get_policies` dispatch functions)
- `packages/graphrag/tests/routing/test_strategy_trace.py` (add integration cases)

**Tests (TDD):**
1. `get_policies` isolation: dispatch with `intent="get_policies"` → `StrategyTrace(strategy=normative_exhaustive, decided_by="none")` returned; neither router invoked.
2. `ask` dispatch with non-ambiguous signal → `RuleQueryRouter` returns a strategy; `BedrockQueryRouter` is NOT invoked.
3. `ask` dispatch with ambiguous signal → `RuleQueryRouter` returns `ambiguous`; `BedrockQueryRouter` IS invoked.
4. All responses carry `StrategyTrace` with non-null `strategy`, `decided_by`, and `legs` fields.

**Approach:**
1. Add `route_ask(question, entity_uris, bedrock_client)` and `route_get_policies()` functions.
2. Write 4 failing tests.
3. Implement the dispatch logic.
4. Run tests red → green.

**Done when:** all 4 tests pass; full test suite green; `ruff check` and `mypy` clean.

## Rollout

- **Delivery:** no flag — `graphrag.routing` is a new module with no existing callers until `spec-mcp-tool-server` imports it.
- **Infrastructure:** none — `RuleQueryRouter` is pure Python; `BedrockQueryRouter` uses the existing `bedrock-runtime` VPC endpoint already provisioned for the synthesizer.
- **Deployment sequencing:** `spec-multi-strategy-routing` is a prerequisite for `packages/graphrag/mcp-tool-server` (which imports `graphrag.routing.route_ask`).

## Risks

- **Regex catastrophic backtracking.** Entity URI pattern (`r"(urn:|https?://|biz:)\S+"`) applied to adversarial input. Mitigated: the `\S+` pattern is possessive (not backtracking); test with a 4 000-char adversarial string in T2.
- **`StrEnum.global_` naming.** `global` is a Python keyword; `global_` is the enum member name, serialising as `"global"` (the string value). Callers that expect `"global"` in JSON will receive it; Python code references `StrategyEnum.global_`. The mismatch is a footgun — document explicitly in `_types.py`.
- **Bedrock routing prompt drift.** If the `BedrockQueryRouter` prompt's strategy list diverges from `StrategyEnum`, the validator will silently fall back to `hybrid_graph` for valid strategies the prompt forgot to mention. CI test: assert the prompt string contains every `StrategyEnum` member's value.

## Changelog

- 2026-07-23: initial plan
