# ADR-0015: OTEL observability: AWS ADOT Lambda layer with X-Ray traces and CloudWatch EMF metrics

- **Status:** Accepted
- **Date:** 2026-07-23
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** [RFC-0004 §D5](../rfc/0004-biz-ops-kg-pivot.md) (offline-first posture — observability must not break offline CI); [ADR-0013](0013-multi-strategy-server-side-routing.md) (strategy trace already defined; OTEL spans add timing on top); [ADR-0014](0014-mcp-tool-server.md) (MCP Lambda instrumented by this ADR); `spec-mcp-tool-server`; `spec-otel-observability`

## Decision summary

- **Decision:** Instrument the MCP Lambda and ingestion Fargate task with the AWS ADOT Lambda Python layer (`AWSOpenTelemetryDistro`); export traces to AWS X-Ray via OTLP; emit metrics via `aws_embedded_metrics` (CloudWatch EMF); write structured JSON logs to CloudWatch Logs; enforce a content-capture policy that never records question text in spans or log lines above DEBUG.
- **Because:** The ADOT Lambda layer auto-instruments `boto3` (Bedrock, S3) and `urllib3` (Neptune SPARQL HTTP) without manual span creation, covering the dominant latency contributors; X-Ray is native to the AWS service map with zero standing-cost backend; CloudWatch EMF delivers histogram-quality metrics from existing CloudWatch Logs with no additional service. Running a standalone OTEL collector (ECS task) or AWS Managed Prometheus would raise the standing cost floor already flagged in backlog item `biz-ops-budgets-threshold-above-standing-floor`.
- **Applies to:** The MCP Lambda function (`packages/graphrag/mcp`), the ingestion Fargate task's structured-logging convention, and the OTEL span attribute vocabulary for the multi-leg retrieval trace.
- **Tradeoff accepted:** X-Ray is not OTEL-portable as a backend — traces arrive via the ADOT layer's OTLP exporter targeting X-Ray; migrating to a non-AWS backend (Datadog, Honeycomb) requires reconfiguring the ADOT exporter pipeline, not rewriting spans. EMF metrics are CloudWatch-native and not portable to Prometheus without additional tooling.
- **Revisit if:** An adopter requires traces to a non-AWS OTEL backend — reconfigure the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable on the Lambda; span attributes and the content-capture policy are unchanged.

## Context

The MCP tool server (`ask`, `get_policies`, and four other tools) calls multiple AWS services per request: Bedrock (routing, synthesis, embedding), Neptune SPARQL (retrieval), and OpenSearch kNN (retrieval). The strategy trace (ADR-0013) records which strategy was chosen and which stores were touched per `ask` call, but it carries no timing and no error-type signal. Without distributed tracing, diagnosing a slow `ask` call requires log correlation by request ID — labor-intensive. A slow call could be dominated by any leg (Bedrock routing, Neptune expand, OpenSearch kNN, Bedrock synthesis); the distribution shifts by query type.

The platform has three structural constraints that shape the observability choice:

1. **Zero standing-cost observability backend.** The standing cost floor is already near the Budgets alarm threshold (see backlog `biz-ops-budgets-threshold-above-standing-floor`). A standalone OTEL collector ECS task (~$5–20/mo), AWS Managed Prometheus AMP workspace (~$10+/mo), or a third-party APM service rules themselves out.

2. **Offline-first posture (ADR-0014/RFC-0004 §D5).** The Lambda must start and run all six tools in the mock/offline path without AWS credentials. The observability instrumentation must not block startup or fail the offline test suite when the X-Ray backend is unreachable. ADOT handles this: the collector is embedded in the Lambda layer and silently drops spans when no OTLP endpoint is reachable.

3. **Content-capture policy (non-negotiable).** The `ask` question may contain business-sensitive or PII content. Recording it in spans creates a secondary disclosure surface in X-Ray and CloudWatch — a surface with broader access permissions than the MCP audit log. Question text must never appear in spans or logs at INFO level or above. This is codified as an OTEL attribute-filter rule at the instrumentation layer, not as a post-export filter.

The strategy trace (ADR-0013) already provides the per-request routing signal. OTEL adds timing and error-type visibility on top without duplicating the routing contract.

## Decision

> We will instrument the MCP Lambda with the AWS ADOT Lambda Python layer (`AWSOpenTelemetryDistro`), exporting traces to AWS X-Ray via OTLP; emit per-tool latency and error metrics via `aws_embedded_metrics` (CloudWatch EMF); write structured JSON logs to CloudWatch Logs. A content-capture attribute filter is configured at the SDK level to prevent question text from appearing in any span attribute. No standing-cost observability backend is added.

Concretely:

1. **ADOT Lambda layer.** The Lambda function's `layers` list includes the `AWSOpenTelemetryDistro` layer ARN for the function's runtime region and Python version (pinned in the Terraform module; updated by the `spec-otel-observability` implementation task). Activated by `AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument`. Auto-instruments: `boto3` clients (Bedrock, S3), `urllib3` HTTP (Neptune SPARQL HTTPS endpoint), and the Lambda handler itself (root span per invocation).

2. **Trace export: OTLP → AWS X-Ray.** The ADOT layer bundles a lightweight OTEL collector process as a Lambda extension; no sidecar ECS task. Configuration via `OTEL_TRACES_EXPORTER=otlp` and `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` (the layer's collector listener). The collector exports to X-Ray via the X-Ray OTLP ingest endpoint. `mcp_lambda_role` gains `AWSXRayDaemonWriteAccess` (managed policy).

3. **Metrics export: `aws_embedded_metrics` (CloudWatch EMF).** No additional AWS service — metrics are structured JSON embedded in CloudWatch Logs and extracted by CloudWatch automatically. Key metrics emitted per tool invocation:

   | Metric | Type | Dimensions |
   |--------|------|-----------|
   | `mcp.tool.duration_ms` | Histogram | `tool_name` |
   | `mcp.tool.error_count` | Count | `tool_name`, `error_type` |
   | `routing.decided_by.bedrock.fraction` | Gauge | _(none)_ |
   | `retrieval.neptune.duration_ms` | Histogram | `strategy` |
   | `retrieval.opensearch.duration_ms` | Histogram | `strategy` |

4. **Structured JSON logs.** `python-json-logger` formats all log output as `{"timestamp": ..., "level": ..., "name": ..., "message": ..., "request_id": ...}`. Lambda log group retention: 30 days. CloudWatch Logs Insights queries can correlate trace IDs across spans, metrics, and logs.

5. **Span model for `ask`.** Auto-instrumented spans (ADOT) marked with _(auto)_; manually created spans marked with _(manual)_:

   ```
   mcp.ask (root — Lambda handler, auto)
     ├── routing.rule_router (manual, kind=INTERNAL)
     ├── routing.bedrock_router (manual, kind=INTERNAL — fires only if RuleQueryRouter returns ambiguous)
     └── retrieval.<strategy> (manual, kind=CLIENT)
           ├── neptune.sparql.execute (auto, via urllib3 instrumentation)
           ├── opensearch.knn.search (auto, via urllib3 instrumentation)
           └── bedrock.embed (auto, via boto3 instrumentation)
     └── bedrock.synthesize (auto, via boto3 instrumentation)
   ```

   `get_policies` root span carries `strategy=normative_exhaustive` and `decided_by=none` as span attributes. The four retrieval-only tools (`search`, `search_graph`, `query`, `summarize`) each have a root span with their tool name and retrieval leg spans auto-instrumented.

6. **Content-capture attribute filter.** An OTEL `SpanProcessor` is configured at SDK initialization that removes any attribute whose key is in the deny-set `{"question.text", "query.text", "sparql.query", "document.content", "chunk.text"}`. These attribute names are also prohibited in the instrumentation conventions. Interim coverage (until `spec-otel-observability` ships the programmatic `SpanProcessor`): a static linter in `packages/graphrag/tests/mcp/test_content_capture_conventions.py` (`spec-mcp-tool-server` AC5) asserts no attribute with these keys appears in `_tools.py`, `_orchestrator.py`, or `_generator.py`. The full programmatic `SpanProcessor` test lives in `packages/graphrag/tests/test_otel_conventions.py` — that is `spec-otel-observability`'s scope. Question text is NEVER passed through to any span attribute, log field above DEBUG, or EMF metric dimension.

7. **Sampling.** AWS X-Ray default reservoir sampling: 10 req/s + 5% tail. At the demo platform's traffic level (< 10 req/s), all requests are sampled; no additional sampling configuration is needed. Trace sampling does not affect EMF metrics (metrics are emitted unconditionally).

8. **Fargate ingestion task.** The ingestion Fargate task does not use the Lambda layer (not applicable). Structured JSON logging (`python-json-logger`) follows the same log format convention. Neptune SPARQL HTTP calls from the ingestion task are logged at DEBUG level with request ID; no distributed trace spans are added to the ingestion task for ini-002 (deferred to `spec-otel-observability` which adds OTEL to the ingestion path if the adopter enables it).

## Decision drivers

- **Zero standing-cost backend.** The cost floor is already elevated by Neptune (~$110/mo min 1 NCU), OpenSearch (~$26/mo), and VPC endpoints (~$90/mo). CloudWatch Logs and X-Ray ingest are pay-per-use at the demo platform's volume; both are within the free tier for the first year.
- **ADOT auto-instruments the dominant latency contributors.** Bedrock and Neptune SPARQL HTTP are the two call paths that dominate `ask` latency. Both are auto-instrumented by ADOT without manual span creation — the implementation cost is one Terraform layer ARN and one env var.
- **X-Ray service map surfaces the call chain.** The AWS console service map shows API Gateway → Lambda → Neptune → OpenSearch → Bedrock with latency histograms per edge — directly answering "which leg dominated that slow call?" without a custom dashboard.
- **EMF metrics are free at demo scale.** CloudWatch EMF extracts metrics from log lines; no PutMetricData API calls. At < 1M metric data points/month, cost is within the free tier.
- **Content-capture policy enforced at the attribute level.** The OTEL SDK attribute filter runs before any export; question text cannot reach X-Ray even if a developer adds a span attribute inadvertently. This is more reliable than a post-export filter.
- **Offline-first posture preserved.** The ADOT collector silently drops spans when the X-Ray endpoint is unreachable (offline CI). The EMF library falls back to plain log output when the structured log sink is absent. The mock server path is unaffected by observability instrumentation.

## Consequences

**Positive:**
- Zero observability backend standing cost — no ECS collector task, no AMP workspace, no third-party APM subscription.
- ADOT auto-instrumentation covers `boto3` and `urllib3` with no code changes; Bedrock, Neptune, S3, and OpenSearch calls gain spans automatically.
- X-Ray service map provides a visual call-chain view in the AWS console without a custom dashboard.
- Content-capture policy is enforced at the OTEL SDK layer — a programmatic guarantee, not a documentation convention.
- CloudWatch Logs Insights correlates traces, metrics, and logs by request ID in one place.
- Offline CI is unaffected; the mock server path runs with no observability backend.

**Negative:**
- X-Ray is not OTEL-portable as a backend. Migrating to Datadog/Honeycomb requires reconfiguring the ADOT collector pipeline and the IAM grants — not rewriting spans, but not a one-env-var change either.
- The ADOT Lambda layer is the largest Lambda layer at ~8 MB compressed; it adds ~100–200 ms to cold start on the first invocation after a layer update. Warm invocations are unaffected.
- The ADOT layer ARN is versioned; it must be updated in the Terraform module when a new ADOT version ships. There is no auto-update mechanism.
- `aws_embedded_metrics` is a new `[observability]` dependency group in `pyproject.toml`; it increases the Lambda package size by ~50 KB.
- Sampling at 10 req/s + 5% tail means a high-traffic burst (> 10 req/s) leaves 95% of requests untraced. The demo platform is not expected to exceed 10 req/s; this tradeoff is accepted for ini-002.

## Confirmation

- **Mode:** lint/CI + goal-based check
- **Signal (attribute filter):** a unit test in `packages/graphrag/tests/test_otel_conventions.py` constructs a span, attempts to set `{"question.text": "test question"}`, exports the span to an in-process OTEL exporter, and asserts the attribute is absent from the exported span data. Part of the offline CI gate suite — no AWS credentials.
- **Signal (EMF metric format):** a unit test asserts that after calling a tool handler against the mock server, the `aws_embedded_metrics` context emits a CloudWatch EMF JSON payload containing `mcp.tool.duration_ms` under the correct metric namespace. Verified against the EMF library's test-capture facility (no AWS credentials).
- **Signal (ADOT layer in Terraform):** `terraform plan` output for the MCP Lambda resource contains `AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument` in the `environment` block and the pinned ADOT layer ARN in the `layers` list. Part of the `test_plan.py` assertion suite.
- **Signal (offline isolation):** `python -m graphrag.mcp --mock` starts and runs all six tools without AWS credentials; no span export error is raised (the ADOT collector drops silently when X-Ray is unreachable).
- **Owner:** eugenelim; spec owner: `spec-otel-observability` (Wave 6 — queued; not yet authored). Until `spec-otel-observability` ships, the ADOT SpanProcessor deny-set filter (item 6) is covered by `spec-mcp-tool-server`'s static linter AC5; the full programmatic `SpanProcessor` implementation is `spec-otel-observability`'s scope.

## Alternatives considered

- **AWS X-Ray SDK (Python) directly.** Use `aws_xray_sdk` for Lambda instrumentation. *Rejected:* the X-Ray SDK is AWS-proprietary; OTEL spans are more portable and more widely understood. Migrating from X-Ray SDK to OTEL requires replacing all instrumentation calls. ADOT provides OTEL-standard spans stored in X-Ray — the instrumentation layer is standard even if the backend is AWS-specific.

- **OTEL SDK + standalone OTEL collector (ECS task or Lambda extension).** Run a standalone collector sidecar exporting to X-Ray, CloudWatch, or a third-party backend. *Rejected against the zero-standing-cost driver:* an ECS collector task adds ~$5–20/mo standing cost and an additional operational surface inside the VPC. The ADOT Lambda layer bundles a lightweight collector per Lambda invocation — no standing service.

- **AWS Managed Prometheus (AMP) + Grafana.** Metrics to AMP, dashboards in Grafana Managed Service. *Rejected:* AMP + Grafana adds $30–100/mo standing cost — directly raising the cost floor flagged in `biz-ops-budgets-threshold-above-standing-floor`. The demo platform's observability need is latency visibility and error tracking, not sophisticated alerting dashboards.

- **CloudWatch Logs only (no distributed traces).** Log a JSON event at INFO for each retrieval leg; use CloudWatch Logs Insights for ad hoc queries. *Rejected:* correlating multi-leg latency from logs requires joining on request ID manually — labor-intensive for a 4–5 leg `ask` call. Distributed traces make the correlation automatic and visual at no meaningful additional cost at demo scale.

- **AWS Lambda Powertools (Tracer + Metrics).** Use `aws_lambda_powertools` for tracing and CloudWatch high-resolution metrics. *Rejected:* Powertools Tracer wraps the X-Ray SDK (same portability concern as option 1). Powertools Metrics uses CloudWatch PutMetricData (higher per-metric cost vs. EMF). The ADOT layer + EMF combination delivers OTEL portability for traces and zero extra cost for metrics.

## References

- [AWS Distro for OpenTelemetry — Lambda Python layer](https://aws-otel.github.io/docs/getting-started/lambda/lambda-python)
- [CloudWatch Embedded Metric Format (EMF)](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html)
- [AWS X-Ray OTLP ingest endpoint](https://docs.aws.amazon.com/xray/latest/devguide/xray-otlp.html)
- [OTEL Python SDK — SpanProcessor + attribute filtering](https://opentelemetry.io/docs/languages/python/sdk/)
- [RFC-0004 §D5 — offline-first posture](../rfc/0004-biz-ops-kg-pivot.md)
- [ADR-0013](0013-multi-strategy-server-side-routing.md) — strategy trace (timing layer sits on top)
- [ADR-0014](0014-mcp-tool-server.md) — MCP Lambda function instrumented by this ADR
- `spec-otel-observability` (the build spec that implements this decision)
