# Plan: spec-otel-observability

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Four tasks. T1 (content filter + OTEL bootstrap) is the primary deliverable and the load-bearing security control on module-owned export paths (tests/local/ingestion) — the `ContentCaptureFilterExporter` and the `configure_observability()` that wraps every exporter it registers. (On the production Lambda path the load-bearing control is the collector attribute processor, a separate work item; see the ownership split below.) T2 (EMF metrics) and T3 (structured JSON logging) are the two independent export legs. T4 (span vocabulary + `traced_leg` helper) delivers the primitives the router/orchestrator call and the mock-path offline-isolation gate.

The riskiest part is the content filter's mechanism. ADR-0015 item 6 says "SpanProcessor", but OTEL Python's `SpanProcessor.on_end(span)` receives a `ReadableSpan` with no attribute-mutation API — stripping there is a silent no-op. The correct mechanism is a `SpanExporter` decorator that filters attributes in `export()` before delegating. This is not a re-interpretation of the decision; it is the technically-correct realization of the same guarantee, and it matches the ADR's Confirmation test exactly (which asserts on *exported* span data). T1 must confirm the OTEL SDK's `ReadableSpan`/`SpanExporter` contract against the pinned version before building.

The second risk is the export-path ownership split. **In Lambda the ADOT layer owns the global `TracerProvider` and the OTLP→collector pipeline** (installed by `AWS_LAMBDA_EXEC_WRAPPER` before the handler runs); a module `set_tracer_provider()` is a no-op, so the module's `ContentCaptureFilterExporter` is not on the Lambda export path. Content-capture enforcement in Lambda is therefore the ADOT collector's attribute processor (owned by `infra-tf/mcp-otel-lambda`, verified by spec AC8). The module registers and filters exporters only where it owns the provider — tests, local/console, and the ingestion task — and there `configure_observability()` must not raise when no OTLP endpoint is reachable and must import without boto3. No AWS credentials are needed for T1–T4; the live X-Ray trace (spec AC8) is a deploy-time gate owned jointly with `infra-tf/mcp-otel-lambda`.

This is a spec-authoring deliverable for the ini-002 shape queue: the tasks below define the contract for the `packages/graphrag/otel-instrumentation` work item, which is built later via `work-loop`. No `graphrag.observability` code lands in this PR.

## Constraints

- ADR-0015 item 6: the deny-set is exactly `{question.text, query.text, sparql.query, document.content, chunk.text}`; question text never reaches a span attribute, a log field at INFO+, or an EMF metric dimension.
- ADR-0015 item 3: the five metrics keep their exact names, types, and dimensions.
- ADR-0015 item 4: log output is `{timestamp, level, name, message, request_id}` JSON.
- ADR-0015 items 2/7: no standing-cost backend; X-Ray default sampling; EMF metrics emitted regardless of trace sampling.
- OTEL Python: attribute stripping is an exporter-decorator concern, never `SpanProcessor.on_end` (immutable `ReadableSpan`).
- `ContentCaptureFilterExporter`, `configure_json_logging`, `DENY_SET`, and the span vocabulary import without boto3/botocore.
- `DENY_SET` is pinned (AC6) to the five ADR-0015 item 6 literals; `spec-mcp-tool-server`'s static linter (which ships first, before this module) pins the same literals in its own test — kept identical by the shared ADR reference, not by one importing the other.
- Ruff (`S` rules included) + mypy CI gates stay green; new mypy `ignore_missing_imports` overrides for `opentelemetry.*`, `aws_embedded_metrics.*`, `pythonjsonlogger.*`.

## Construction tests

**T1 (content filter + bootstrap):**
- `TracerProvider` + `ContentCaptureFilterExporter(InMemorySpanExporter())`: a span with all five `DENY_SET` keys **and** the `AUTO_CAPTURE_KEYS` (`db.statement`=SPARQL string, `http.url`, `url.query`, gen-AI prompt attr) + benign keys (`tool_name="ask"`, `db.system`, `http.status_code`) exports with none of the sensitive keys and with every benign key present.
- Each sensitive key set individually is likewise stripped; benign auto keys (`db.system`, `http.status_code`) survive.
- `configure_observability("graphrag-mcp")` runs with no OTLP endpoint reachable and no AWS env vars set; no exception, no ERROR log; the returned/registered provider's exporter is a `ContentCaptureFilterExporter`; the `boto3`/`urllib3` instrumentation is configured with statement/body/URL-query/prompt capture off.
- `from graphrag.observability import ContentCaptureFilterExporter, DENY_SET, AUTO_CAPTURE_KEYS` succeeds without boto3 installed; `DENY_SET == {"question.text","query.text","sparql.query","document.content","chunk.text"}`.

**T2 (EMF metrics):**
- `emit_tool_metrics(tool_name="ask", duration_ms=12.0)` → captured EMF JSON contains `mcp.tool.duration_ms` (unit `Milliseconds`, dimension `tool_name="ask"`) under the configured namespace.
- `emit_tool_metrics(tool_name="ask", duration_ms=12.0, exc=TimeoutError("what are the HR policies?"))` additionally emits `mcp.tool.error_count` (dimensions `tool_name`, `error_type`), where `error_type == "TimeoutError"` (the class name) and the message text appears in no dimension value — bounded enum, no content leak, no cardinality blow-up.
- With no EMF sink configured (offline), `emit_tool_metrics` falls back to a plain log line and does not raise.

**T3 (structured JSON logging):**
- After `configure_json_logging()`, `logging.getLogger("graphrag.mcp").info("routed", extra={"request_id":"r-1"})` emits a line parsing as JSON with `timestamp`, `level="INFO"`, `name="graphrag.mcp"`, `message="routed"`, `request_id="r-1"`.
- A question string passed at INFO does not appear in any emitted field.

**T4 (span vocabulary + `traced_leg`):**
- `traced_leg("retrieval", strategy="hybrid")` → span `retrieval.hybrid`, `SpanKind.CLIENT`.
- `traced_leg("routing.rule_router")` → `SpanKind.INTERNAL`; `traced_leg("routing.bedrock_router")` → `SpanKind.INTERNAL`.
- A `DENY_SET` attribute set on a `traced_leg` span is absent from the exported span.
- Offline isolation: `python -m graphrag.mcp --mock` with `configure_observability` active starts, runs six tools, exits 0, no span-export error (this exercises the integrated bootstrap; depends on `spec-mcp-tool-server`'s mock server existing).

## Design (LLD)

### Design decisions

- **Content filter is a `SpanExporter` decorator, not a `SpanProcessor`.** `ContentCaptureFilterExporter(inner: SpanExporter)` implements `export(spans)` by producing, for each `ReadableSpan`, a view whose attributes exclude every key in `DENY_SET ∪ AUTO_CAPTURE_KEYS`, then delegating to `inner.export(...)`. `shutdown()` and `force_flush()` delegate. Because `ReadableSpan.attributes` is a read-only `BoundedAttributes` mapping, the filter builds a filtered attribute dict and wraps the span in a small `_FilteredSpan` proxy (or reconstructs via `ReadableSpan(...)` if the pinned SDK exposes a stable constructor — confirmed at T1). The decorator is the single registration point: `configure_observability` never registers an inner exporter directly. The filter is **span-source-agnostic** — it strips manual, router, and auto-instrumented spans alike, which is why it (not the three-file AC5 linter) is the load-bearing runtime control on module-owned paths.
- **Auto-instrumentation capture is suppressed at config, stripped at export.** ADOT auto-instruments `boto3` (Bedrock) and `urllib3` (Neptune SPARQL HTTPS); by default these can record the SPARQL statement, request URL/body, and Bedrock prompt under `AUTO_CAPTURE_KEYS`. Primary control: configure the instrumentations with capture off (`urllib3`/`botocore` instrumentation options where the module owns setup; `OTEL_PYTHON_*` / instrumentation env vars on the Lambda, asserted by AC7). Backstop: `AUTO_CAPTURE_KEYS` is in both the filter's strip-set and the collector processor's delete-set. The exact key list + capture-off knobs are version-confirmed at T1 against the pinned `opentelemetry-instrumentation-*` packages (semantic conventions still evolving — hence config-off is primary, key-strip is backstop). An **allowlist** (deny-by-default) was rejected: ADOT emits many benign useful keys (`db.system`, `http.status_code`, `net.peer.name`) an allowlist would suppress.
- **`DENY_SET` is a frozenset in `_content_filter.py`.** It is pinned by AC6 to the five ADR-0015 item 6 canonical names. `spec-mcp-tool-server`'s AC5 linter is a static string search that ships before this module exists, so it cannot import `DENY_SET`; the two are held identical by each carrying its own pin test against the same ADR literals. (A later consolidation onto a shared import, once both have shipped, is a possible follow-up — not assumed here.)
- **`configure_observability(service_name)` behaves by environment.** In Lambda the ADOT layer has already installed the global provider + OTLP exporter, so the module does **not** install its own provider there (it would be a no-op) — it configures logging + metrics and relies on the ADOT pipeline (with the collector processor as the content-capture enforcement point). Where the module *does* own the provider — tests (`InMemorySpanExporter`), local runs (`ConsoleSpanExporter`/no-op), and the ingestion task — **it wraps every exporter it registers in `ContentCaptureFilterExporter`.** Export failures are swallowed (the ADOT collector drops silently; the SDK's `BatchSpanProcessor` logs at most a warning, never raises into the handler).
- **`emit_tool_metrics` wraps `aws_embedded_metrics`.** It uses `metric_scope`/`MetricsLogger` to `set_namespace`, `set_dimensions({"tool_name": ...})`, and `put_metric("mcp.tool.duration_ms", duration_ms, "Milliseconds")`; when given an exception it emits `mcp.tool.error_count` with `error_type = type(exc).__name__` — the **class name only**, never `str(exc)`. This is a deliberate content-capture + cardinality control: an exception message can carry question-derived text and is unbounded, so dimension values are restricted to the bounded set of exception class names / error codes. Gauges (`routing.decided_by.bedrock.fraction`) and the retrieval-leg histograms are emitted by sibling helpers or by the router calling the same logger; T2 delivers the tool-level pair and the namespace/dimension convention, and the retrieval/gauge helpers reuse it.
- **`configure_json_logging()` installs a `pythonjsonlogger.jsonlogger.JsonFormatter`** on the root handler with the field set `timestamp level name message request_id`. `request_id` is read from a `logging` filter that injects the Lambda request id (or a generated UUID off-Lambda). No question text is ever passed to a logger; the log-layer content-capture is a convention reinforced by `spec-mcp-tool-server` AC5's linter.
- **Span vocabulary is constants + a context manager.** Span names and their `SpanKind` live in `_tracing.py` as constants; `traced_leg(name, **attrs)` opens a span with the mapped kind, sets the (non-deny) attributes, and yields. The `ask` orchestrator / router are *expected* to import `traced_leg` (a seam owned by `spec-multi-strategy-routing`, which today models its own `LegSpan.latency_ms` and does not yet reference it); this module's tests exercise the helper directly so the contract holds regardless of when the router adopts it.

### Data & schema

```python
# graphrag/observability/_content_filter.py
DENY_SET: frozenset[str] = frozenset({          # manual keys — pinned to ADR-0015 item 6
    "question.text", "query.text", "sparql.query",
    "document.content", "chunk.text",
})
AUTO_CAPTURE_KEYS: frozenset[str] = frozenset({  # OTEL semantic-conv keys ADOT auto-sets
    "db.statement", "db.query.text", "http.url",  # (version-confirmed at T1)
    "url.full", "url.query", "http.request.body",
    "gen_ai.prompt",                              # + gen-AI prompt/content attrs
})
_STRIP = DENY_SET | AUTO_CAPTURE_KEYS

class ContentCaptureFilterExporter(SpanExporter):
    def __init__(self, inner: SpanExporter) -> None: ...
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        filtered = [self._strip(s) for s in spans]   # drop every _STRIP attribute key
        return self._inner.export(filtered)
    def shutdown(self) -> None: ...
    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...
```

```python
# graphrag/observability/_tracing.py
SPAN_KINDS: dict[str, SpanKind] = {
    "mcp.ask": SpanKind.SERVER,
    "routing.rule_router": SpanKind.INTERNAL,
    "routing.bedrock_router": SpanKind.INTERNAL,
    "retrieval": SpanKind.CLIENT,   # name formatted as retrieval.<strategy>
}

@contextmanager
def traced_leg(name: str, *, strategy: str | None = None, **attrs) -> Iterator[Span]: ...
```

**Metric contract (ADR-0015 item 3), emitted via EMF:**

| Metric | Type | Unit | Dimensions |
|--------|------|------|-----------|
| `mcp.tool.duration_ms` | Histogram | Milliseconds | `tool_name` |
| `mcp.tool.error_count` | Count | Count | `tool_name`, `error_type` |
| `routing.decided_by.bedrock.fraction` | Gauge | None | — |
| `retrieval.neptune.duration_ms` | Histogram | Milliseconds | `strategy` |
| `retrieval.opensearch.duration_ms` | Histogram | Milliseconds | `strategy` |

### Component / module decomposition

```
packages/graphrag/src/graphrag/observability/
├── __init__.py          # exports: configure_observability, ContentCaptureFilterExporter,
│                        #          DENY_SET, emit_tool_metrics, configure_json_logging,
│                        #          traced_leg, SPAN_KINDS
├── _content_filter.py   # DENY_SET + ContentCaptureFilterExporter (SpanExporter decorator)
├── _bootstrap.py        # configure_observability(service_name) — env-selected, filter-wrapped
├── _metrics.py          # emit_tool_metrics(...) — aws_embedded_metrics EMF
├── _logging.py          # configure_json_logging() — python-json-logger + request_id filter
└── _tracing.py          # span-name/kind constants + traced_leg context manager

packages/graphrag/tests/
├── test_otel_conventions.py     # content-capture filter test (ADR-0015-named location)
└── observability/
    ├── test_content_filter.py
    ├── test_bootstrap_offline.py
    ├── test_metrics_emf.py
    ├── test_logging_json.py
    └── test_tracing_legs.py
```

### Failure cases & resilience

- **OTLP endpoint unreachable (offline / X-Ray down).** The SDK `BatchSpanProcessor` retries then drops; no exception propagates to the handler. `configure_observability` never fails startup on export configuration.
- **`aws_embedded_metrics` sink absent (offline).** `emit_tool_metrics` falls back to a single plain-log line at INFO (metric name + value + dimensions, no content) and does not raise.
- **A developer sets a `DENY_SET` attribute, or auto-instrumentation sets an `AUTO_CAPTURE_KEYS` attribute.** On module-owned export paths the `ContentCaptureFilterExporter` strips both classes before the span leaves the process; on the ADOT-owned Lambda path the collector attribute processor strips them before they reach X-Ray (spec AC7 offline, AC8 end-to-end), and capture-off config stops the auto keys being set at all. The static linter (`spec-mcp-tool-server` AC5) flags manual keys at author time but cannot see the runtime auto keys — hence the config-off + filter/collector layers.
- **A future OTEL SDK version changes the `ReadableSpan` shape.** The exporter decorator is version-sensitive at the `_strip` implementation; T1 pins `opentelemetry-sdk` and the AC1 test fails loudly if the strip stops working, so a silent bypass cannot ship.
- **`request_id` unavailable off-Lambda.** The logging filter generates a UUID; the field is always present.

### Quality attributes (NFRs)

- **AWS-free core.** `_content_filter.py`, `_logging.py`, and `_tracing.py` import only `opentelemetry-sdk`, `logging`, `contextlib`, stdlib. Only `_metrics.py` and the OTLP branch of `_bootstrap.py` touch AWS-adjacent libraries; none import boto3/botocore.
- **Offline CI.** T1–T4 run with no AWS credentials and no network; the live X-Ray trace is a separate `@pytest.mark.live_aws` gate.
- **Zero standing cost.** No collector task, no AMP; the ADOT layer's collector is per-invocation (owned by `infra-tf/mcp-otel-lambda`).
- **Mypy-clean** with `ignore_missing_imports` overrides for the three new third-party namespaces.

## Tasks

### T1: Content-capture filter + OTEL bootstrap

**Depends on:** none (pins `opentelemetry-sdk`; adds the `[observability]` dependency group)

**Touches:**
- `pyproject.toml` (`[project.optional-dependencies]` → new `observability` group; `[[tool.mypy.overrides]]` for `opentelemetry.*`, `aws_embedded_metrics.*`, `pythonjsonlogger.*`)
- `packages/graphrag/src/graphrag/observability/__init__.py`
- `packages/graphrag/src/graphrag/observability/_content_filter.py`
- `packages/graphrag/src/graphrag/observability/_bootstrap.py`
- `packages/graphrag/tests/test_otel_conventions.py`
- `packages/graphrag/tests/observability/test_content_filter.py`
- `packages/graphrag/tests/observability/test_bootstrap_offline.py`

**Tests (TDD):** `DENY_SET` ∪ `AUTO_CAPTURE_KEYS` stripped (all-keys + per-key), benign auto keys (`db.system`, `http.status_code`) survive, boto3-free import, `DENY_SET` value pinned, offline bootstrap raises nothing + wraps every module-registered exporter in the filter + configures `boto3`/`urllib3` capture-off.

**Done when:** filter + bootstrap tests pass; `python -c "from graphrag.observability import ContentCaptureFilterExporter, DENY_SET, AUTO_CAPTURE_KEYS"` exits 0 without boto3; `ruff check` + `mypy` clean.

---

### T2: EMF metrics helper

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/observability/_metrics.py`
- `packages/graphrag/tests/observability/test_metrics_emf.py`

**Tests (TDD):** `mcp.tool.duration_ms` name/unit/dimension; `mcp.tool.error_count` with `error_type` = exception **class name** (never the message; a content-bearing message appears in no dimension value); offline fallback to plain log without raising.

**Done when:** metric tests pass; `ruff check` + `mypy` clean.

---

### T3: Structured JSON logging

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/observability/_logging.py`
- `packages/graphrag/tests/observability/test_logging_json.py`

**Tests (TDD):** JSON record with the five keys; `request_id` present on and off Lambda; question string never in a field.

**Done when:** logging tests pass; `ruff check` + `mypy` clean.

---

### T4: Span vocabulary + `traced_leg` + offline-isolation gate

**Depends on:** T1; **integration gate depends on** `packages/graphrag/mcp` (spec-mcp-tool-server mock server)

**Touches:**
- `packages/graphrag/src/graphrag/observability/_tracing.py`
- `packages/graphrag/tests/observability/test_tracing_legs.py`

**Tests (TDD):** `retrieval.hybrid` / `SpanKind.CLIENT`; `routing.*` / `SpanKind.INTERNAL`; deny-set stripped on leg spans. **Goal-based:** `python -m graphrag.mcp --mock` with observability active starts, runs six tools, exits 0, no export error.

**Done when:** tracing tests pass; the mock offline-isolation gate is green; full suite green; `ruff check` + `mypy` clean.

## Rollout

- **Delivery:** no flag. `graphrag.observability` is a new module; `configure_observability()` is called at `graphrag.mcp` import and at ingestion-task startup. Until those call sites land it has no runtime effect. The content filter is on from the first call — there is no "observability off" mode to gate behind a flag, because the filter is a security control, not a feature.
- **Infrastructure:** the ADOT Lambda layer, `AWS_LAMBDA_EXEC_WRAPPER`, the OTLP endpoint env var, the `AWSXRayDaemonWriteAccess` grant, **the auto-instrumentation capture-off env vars, and the collector-side `attributes` delete processor (delete-set = `DENY_SET` ∪ `AUTO_CAPTURE_KEYS`) that together are the content-capture enforcement point on the Lambda export path** are all provisioned by `infra-tf/mcp-otel-lambda` (constrained by ADR-0015 directly) and offline-asserted by spec AC7. This module needs none of them to run offline; in Lambda it consumes the ADOT-owned pipeline rather than installing its own.
- **Deployment sequencing:** the Python module (this spec → `packages/graphrag/otel-instrumentation`) and the Terraform (`infra-tf/mcp-otel-lambda`) both depend on `packages/graphrag/mcp-tool-server` (work) being present; they can be built in parallel and are joined at the live AC8 trace gate. Rollback is a re-deploy of the prior Lambda package + Terraform state — no data migration.

## Risks

- **`ReadableSpan` attribute-stripping mechanism.** The exact way to produce a filtered span view depends on the pinned `opentelemetry-sdk` version — a `_FilteredSpan` proxy vs. `ReadableSpan` reconstruction. T1 confirms the contract against the pinned version before building; the AC1 test is the guardrail that a silent bypass cannot ship. This is the single highest-risk item and is why T1 is the primary task.
- **EMF library API surface.** `aws_embedded_metrics`' Python `metric_scope`/`MetricsLogger` API and its local-sink capture facility are the T2 contract; the exact capture call is confirmed at implementation (spec Assumption). Metric names/units/dimensions are fixed by ADR-0015 and are the load-bearing contract, not the capture mechanism.
- **Deny-set drift with `spec-mcp-tool-server`.** The linter (static string search, ships first) and `DENY_SET` cannot share an import today, so drift is prevented by each side pinning to the five ADR-0015 item 6 literals in its own test (spec AC6). The residual risk is a change to one literal set without the other; the ADR is the single reference both cite, and the seam is flagged in Boundaries (`Ask first` → deny-set changes).
- **ADOT global-provider ownership (highest cross-item risk).** Because ADOT installs the Lambda `TracerProvider`, the module's in-process filter does **not** run on the Lambda export path — Lambda content-capture depends entirely on the collector attribute processor being present and correct in `infra-tf/mcp-otel-lambda`, plus the never-set convention (AC5 linter). The two work items must land the collector processor and the module filter together. AC7 now statically asserts the collector delete-set (`DENY_SET` ∪ `AUTO_CAPTURE_KEYS`) and the capture-off env pre-deploy (offline), so the control's *config* is verified without a deploy; AC8 (live) confirms the end-to-end behaviour on the real Lambda path — the one path where auto-instrumentation actually populates the auto keys. AC8 remains a named live gate as the end-to-end confirmation, no longer the sole guard.
- **Auto-instrumentation is the largest content-capture vector.** ADOT auto-captures the Neptune SPARQL statement and the Bedrock prompt under `AUTO_CAPTURE_KEYS`, which the AC5 linter cannot see (set at runtime, not in source). Two-layer mitigation: capture-off at instrumentation config (primary) + `AUTO_CAPTURE_KEYS` in the filter and collector delete-set (backstop). Residual risk: an ADOT / semantic-convention version bump introduces a new content-bearing key not yet in `AUTO_CAPTURE_KEYS` — mitigated by pinning the instrumentation versions (T1), re-confirming the key list on upgrade, and capture-off being the primary (key-independent) control.
- **ADOT layer cold-start cost.** The ~8 MB layer adds ~100–200 ms to the first cold start after a layer update (ADR-0015 Negative consequences). Accepted for ini-002; not this module's concern (layer is `infra-tf/mcp-otel-lambda`).

## Changelog

- 2026-07-23: initial plan
- 2026-07-23: security-review revision — added `AUTO_CAPTURE_KEYS` (the auto-instrumentation content-capture vector) with config-level capture-off; bounded `error_type` to the exception class name; folded the offline collector-processor + capture-off assertion into AC7 (so the live AC8 is not the only guard); clarified the filter/collector — not the linter — as the load-bearing runtime control (allowlist considered, rejected); recorded the X-Ray IAM least-privilege rationale; noted production export-failure detection as a CloudWatch-alarm backlog item.
