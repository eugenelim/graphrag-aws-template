# Spec: infra-tf-api-gateway-mcp

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0014](../../adr/0014-mcp-tool-server.md) (two ingress paths: API Gateway for human/IDE, Function URL for automation/AgentCore); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (`mcp_lambda_role` Neptune read-only); [ADR-0015](../../adr/0015-otel-observability.md) (ADOT layer deferred to `infra-tf/mcp-otel-lambda`); [RFC-0004](../../rfc/0004-biz-ops-kg-pivot.md) §D1
- **Shape:** infra (new resources: Lambda + IAM role + SG + API Gateway HTTP API + Function URL)
- **Initiative:** ini-002 wave 4

## Objective

Provision the MCP Lambda function and its two ingress paths in `apps/infra-tf/`:

1. **MCP Lambda** — `graphrag-mcp-tool-server`, handler `graphrag.mcp._lambda.handler`, Python 3.12, 512 MB, 120 s timeout, 10 concurrent, VPC-attached, with its own IAM execution role (`mcp_lambda_role`) and security group.
2. **IAM-auth Function URL** — the automation + AgentCore ingress path; `authorization_type = "AWS_IAM"`; invoke permission scoped to `var.mcp_invoker_role_arn`.
3. **HTTP API Gateway** — the human/IDE ingress path; `aws_apigatewayv2_api` with `protocol_type = "HTTP"`, `$default` stage with stage-level throttling, Lambda proxy integration (29 s timeout), and a `$default` catch-all route. API key identification via `x-api-key` header passes through to Lambda (not enforced at the APIGW layer — ADR-0014 and design.md: "request identification and throttling, not authentication").

`mcp_lambda_role` is Neptune read-only (ADR-0011 backstop): `ReadDataViaQuery + connect` only.

The ADOT Lambda layer is deferred to `infra-tf/mcp-otel-lambda` (ADR-0015).

## Boundaries

### Always do

- Create exactly one MCP Lambda function (`graphrag-mcp-tool-server`), one execution role (`mcp_lambda_role`), and one security group (`mcp_lambda_sg`).
- Wire `mcp_lambda_role` Neptune grant as READ-ONLY: `ReadDataViaQuery + connect` only — ADR-0011 backstop.
- Create an IAM-auth Function URL for the MCP Lambda (`authorization_type = "AWS_IAM"`); invoke permission principal = `var.mcp_invoker_role_arn`, never `"*"`.
- Create an HTTP API (`aws_apigatewayv2_api`, `protocol_type = "HTTP"`) with a `$default` stage (auto-deploy = true), a Lambda proxy integration with `timeout_milliseconds = 29000` (under the 30 s APIGW hard limit), and a `$default` catch-all route.
- Add egress rules from `mcp_lambda_sg` to Neptune (8182), OpenSearch (443), BedrockRuntime (443), CloudWatchLogs (443), STS (443).
- Add ingress rules on `neptune_sg` and `opensearch_sg` from `mcp_lambda_sg`.
- Add a stack-managed CloudWatch log group `/graphrag/mcp-tool-server` (retention 7 d, no `skip_destroy`).
- Pass `NEPTUNE_SPARQL_ENDPOINT`, `OPENSEARCH_ENDPOINT`, and `SYNTHESIS_MODEL_ID` env vars to the MCP Lambda.
- Keep all Lambda resources VPC-resident; Lambda uses private subnets + `mcp_lambda_sg`.
- Update `test_plan.py` and regenerate the committed `plan.json` fixture.

### Ask first

- Adding WAF to the API Gateway (cost + complexity; demo does not model real threat actors).
- Enforcing API key at the API Gateway layer via a Lambda authorizer (design.md scopes this to "identification and throttling, not authentication").
- Enabling API Gateway access logs (deferred to `infra-tf/mcp-otel-lambda`).

### Never do

- Give `mcp_lambda_role` Neptune Write or Delete actions (`WriteDataViaQuery`, `DeleteDataViaQuery`) — ADR-0011.
- Set `authorization_type = "NONE"` on the Function URL — same "never NONE" rule as the existing `query_url`.
- Set the APIGW integration `timeout_milliseconds` above 29000.
- Use `aws_api_gateway_rest_api` (REST API v1) — this spec requires HTTP API v2 (`aws_apigatewayv2_*`).
- Merge the MCP Lambda with the existing `query_lambda` — separate Lambda per ADR-0014.
- Add the ADOT Lambda layer here — that belongs to `infra-tf/mcp-otel-lambda`.
- Never give `mcp_lambda_role` OpenSearch write or delete access (`es:ESHttpPut`, `es:ESHttpDelete`) — the MCP tool server is a retrieval-only path; use `opensearch_readonly_policy` (ESHttpGet + ESHttpPost + ESHttpHead only).

## Testing Strategy

All tasks verified via **goal-based check** (infra/deploy mode — plan-assertion suite):

- Plan assertions in `test_plan.py` against the committed `plan.json` fixture (`TFPLAN_JSON_PATH=...`).
- Positive: MCP Lambda resource present with correct handler, runtime, VPC config, concurrency cap.
- Positive: `mcp_lambda_role` has exactly one Neptune policy named `neptune-data-readonly`; no Write/Delete actions.
- Positive: MCP Function URL has `authorization_type = "AWS_IAM"`; invoke permission principal is non-wildcard.
- Positive: API Gateway HTTP API present; `$default` stage has auto-deploy and throttling; integration timeout ≤ 29000 ms.
- Positive: MCP SG egress exactly 5 rules; Neptune + OpenSearch ingress each grow to 4.
- Suite: `TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json pytest apps/infra-tf/tests/ -q` exits 0.
- Format: `terraform fmt -check apps/infra-tf/` exits 0.
- Validate: `terraform validate` (after `terraform init -backend=false`) exits 0.

## Acceptance Criteria

- [x] **AC1 — MCP Lambda resource with correct config.**
  `aws_lambda_function.mcp_lambda` present; `handler = "graphrag.mcp._lambda.handler"`; `runtime = "python3.12"`; `memory_size = 512`; `timeout = 120`; `reserved_concurrent_executions = 10`; VPC-attached; log group `/graphrag/mcp-tool-server`.
  *(goal-based check — plan-assertion test `test_mcp_lambda_present`)*

- [x] **AC2 — `mcp_lambda_role` Neptune grant is READ-ONLY.**
  Exactly one Neptune inline policy on the role; actions = `{neptune-db:connect, neptune-db:ReadDataViaQuery}`; no `WriteDataViaQuery` or `DeleteDataViaQuery`.
  *(goal-based check — plan-assertion test `test_mcp_role_neptune_readonly`)*

- [x] **AC3 — MCP Function URL is IAM-auth; invoke principal is named (never `*`).**
  `aws_lambda_function_url.mcp_url` has `authorization_type = "AWS_IAM"`; `aws_lambda_permission.mcp_url_invoke` principal is a non-wildcard role ARN.
  *(goal-based check — plan-assertion test `test_mcp_function_url_iam_auth`)*

- [x] **AC4 — HTTP API present with throttling and correct integration timeout.**
  `aws_apigatewayv2_api.mcp` present with `protocol_type = "HTTP"`; `aws_apigatewayv2_stage.mcp_default` with `auto_deploy = true` and throttling; `aws_apigatewayv2_integration.mcp_lambda` has `timeout_milliseconds <= 29000`.
  *(goal-based check — plan-assertion test `test_mcp_api_gateway_present`)*

- [x] **AC5 — MCP SG egress 5 rules; Neptune + OpenSearch ingress grow to 4 each.**
  `mcp_lambda_sg` has exactly 5 egress rules matching the spec targets. `neptune_sg` ingress = 4 (adds `neptune_from_mcp`). `opensearch_sg` ingress = 4 (adds `opensearch_from_mcp`).
  *(goal-based check — updated `test_store_sg_ingress_rules_exact`, updated `test_compute_sgs_egress_equals_exact_call_set`)*

- [x] **AC6 — All plan-assertion tests pass against the updated fixture.**
  `TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json pytest apps/infra-tf/tests/ -q` exits 0.
  *(goal-based check)*

- [x] **AC7 — `terraform fmt -check` and `terraform validate` pass on the module.**
  *(goal-based check)*

## Assumptions

- Technical: The pre-built Lambda zip (`apps/graphrag/dist/graphrag.zip`) contains `graphrag.mcp._lambda` from `packages/graphrag/mcp-tool-server` (PR #79).
- Technical: `var.mcp_invoker_role_arn` is a new required variable with the same shape and validation as `var.invoker_role_arn`.
- Technical: HTTP API (v2) does not support native usage plans or API keys — stage-level throttling is the equivalent. The `x-api-key` header passes through to Lambda for identification; it is not enforced at the APIGW layer.
- Technical: The ADOT Lambda layer is NOT wired here — it belongs to `infra-tf/mcp-otel-lambda`.
- Technical: `neptune_endpoint_url`, `opensearch_endpoint_url`, and `lambda_zip` locals defined in `lambda.tf` are shared across the root module.
- Technical: `local.synthesis_model_id` from `iam.tf` is shared with the MCP Lambda env var.
- Technical: The plan.json fixture is regenerated by running `terraform init -backend=false && terraform plan` with stub var values.
