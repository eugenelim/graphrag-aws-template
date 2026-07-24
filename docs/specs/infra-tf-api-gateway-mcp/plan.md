# Plan: infra-tf-api-gateway-mcp

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

## Approach

Three tasks. T1 (MCP Lambda + IAM + SG) provisions the Lambda function, its execution role, and security group — the foundation both ingress paths build on. T2 (Function URL for automation/AgentCore) adds the IAM-auth Function URL and its invoke permission. T3 (API Gateway HTTP API for human/IDE) adds the HTTP API, default stage, Lambda integration, and route.

T1 must complete before T2 and T3 (both depend on the Lambda ARN). T2 and T3 are independent of each other.

Security group (T1) updates two existing SGs (neptune_sg, opensearch_sg) with new ingress rules from mcp_lambda_sg — this is additive and safe (no rules removed).

The riskiest part is the existing test_plan.py test `test_store_sg_ingress_rules_exact`, which asserts exact ingress-rule counts (3 for Neptune, 3 for OpenSearch). Adding MCP Lambda ingress rules changes these to 4. The test update must be exact.

The second risk is the test `test_smoke_probe_is_in_vpc_with_no_public_url`, which asserts `len(aws_lambda_function_url) == 1`. Adding the MCP Function URL changes this to 2. Must update to `>= 1` or enumerate both expected URLs.

## Constraints

- ADR-0011: `mcp_lambda_role` Neptune grant is `ReadDataViaQuery + connect` only — never Write/Delete.
- ADR-0014: two ingress paths — HTTP API for human/IDE, Function URL for automation/AgentCore.
- HTTP API (v2): integration `timeout_milliseconds` max 29000 (under the 30 s APIGW hard limit).
- ADR-0002: VPC-resident, no NAT, no IGW; Lambda uses private subnets + its own SG.
- `function_url_auth_type = "AWS_IAM"` on the Function URL invoke permission — never NONE.
- `var.mcp_invoker_role_arn` validated with the same end-anchored role ARN regex as `var.invoker_role_arn`.

## Construction tests

All tasks verified via **goal-based check** (plan-assertion suite):

**T1 (Lambda + IAM + SG):**
- `aws_lambda_function.mcp_lambda` present; handler = `graphrag.mcp._lambda.handler`; runtime = python3.12; memory_size = 512; timeout = 120; reserved_concurrent_executions = 10; VPC-attached.
- `mcp_lambda_role` has exactly one Neptune inline policy; actions = {connect, ReadDataViaQuery} only.
- `mcp_lambda_sg` has exactly 5 egress rules (neptune 8182, opensearch 443, BedrockRuntime 443, CloudWatchLogs 443, STS 443).
- `neptune_sg` ingress = 4; `opensearch_sg` ingress = 4 (MCP added to each).

**T2 (Function URL):**
- `aws_lambda_function_url.mcp_url` has `authorization_type = "AWS_IAM"`.
- `aws_lambda_permission.mcp_url_invoke` principal = `var.mcp_invoker_role_arn` (non-wildcard).
- `len(aws_lambda_function_url) == 2` (query + mcp).

**T3 (API Gateway):**
- `aws_apigatewayv2_api.mcp` present; `protocol_type = "HTTP"`.
- `aws_apigatewayv2_stage.mcp_default` has `auto_deploy = true` and throttling set.
- `aws_apigatewayv2_integration.mcp_lambda` has `integration_type = "AWS_PROXY"` and `timeout_milliseconds <= 29000`.
- `aws_apigatewayv2_route.mcp_default` has `route_key = "$default"`.
- `aws_lambda_permission.mcp_apigw_invoke` allows `execute-api.amazonaws.com` to invoke MCP Lambda.

## Design (LLD)

### `mcp_lambda.tf` — new file

```
# MCP Lambda function, execution role, security group, log group, Function URL.
```

Resources:
- `aws_cloudwatch_log_group.mcp_lambda` — `/graphrag/mcp-tool-server`, retention 7d.
- `aws_iam_role.mcp_lambda_role` — trust: lambda.amazonaws.com.
- `aws_iam_role_policy_attachment.mcp_vpc_access` — AWSLambdaVPCAccessExecutionRole.
- `aws_iam_role_policy.mcp_neptune_readonly` — neptune-data-readonly (same local as query_role).
- `aws_iam_role_policy.mcp_opensearch` — opensearch-data.
- `aws_iam_role_policy.mcp_bedrock_titan` — bedrock-titan.
- `aws_iam_role_policy.mcp_bedrock_synthesis` — bedrock-synthesis.
- `aws_lambda_function.mcp_lambda` — handler, runtime, VPC, env vars, log group, concurrency cap.
- `aws_lambda_function_url.mcp_url` — authorization_type = AWS_IAM.
- `aws_lambda_permission.mcp_url_invoke` — scoped to var.mcp_invoker_role_arn.

### `api_gateway_mcp.tf` — new file

```
# HTTP API Gateway (v2) for human/IDE MCP ingress.
```

Resources:
- `aws_apigatewayv2_api.mcp` — name=graphrag-mcp, protocol_type=HTTP, cors_configuration disabled.
- `aws_apigatewayv2_stage.mcp_default` — name=$default, auto_deploy=true, with throttling.
- `aws_apigatewayv2_integration.mcp_lambda` — AWS_PROXY, payload_format_version=2.0, timeout_milliseconds=29000.
- `aws_apigatewayv2_route.mcp_default` — route_key=$default, target=integrations/{id}.
- `aws_lambda_permission.mcp_apigw_invoke` — principal=execute-api.amazonaws.com, source_arn=APIGW ARN.

### Updates to existing files

- `variables.tf`: add `mcp_invoker_role_arn` required variable with same ARN regex as `invoker_role_arn`.
- `security_groups.tf`: add `mcp_lambda_sg` SG + 5 egress rules + 2 ingress rules on neptune/opensearch SGs.
- `outputs.tf`: add `mcp_api_gateway_url`, `mcp_function_url`.
- `tests/test_plan.py`: update 3 existing tests + add 4 new MCP-specific tests.
- `tests/conftest.py`: add `mcp_invoker_role_arn` stub var to the live-plan invocation.
- `tests/fixtures/plan.json`: regenerate from `terraform plan`.

### Failure cases

- **`source_code_hash` fails if zip absent.** conftest.py creates a stub zip if missing — same pattern as existing query_lambda. No change needed.
- **APIGW integration timeout > 29000.** Hard API validation error at apply time. Caught by `terraform validate` + plan assertion.
- **mcp_lambda_role accidentally gains Write Neptune action.** Caught by `test_mcp_role_neptune_readonly`.

## Tasks

### T1: MCP Lambda + IAM role + security group

**Depends on:** none

**Touches:**
- `apps/infra-tf/mcp_lambda.tf` (new)
- `apps/infra-tf/security_groups.tf` (add MCP SG + egress + 2 ingress rules on Neptune/OpenSearch SGs)
- `apps/infra-tf/variables.tf` (add mcp_invoker_role_arn)
- `apps/infra-tf/outputs.tf` (add mcp_function_url)
- `apps/infra-tf/tests/test_plan.py` (update existing tests, add MCP Lambda + Function URL tests)

**Done when:** `terraform validate` passes; plan assertions for AC1–AC3 + AC5 pass.

---

### T2: API Gateway HTTP API

**Depends on:** T1

**Touches:**
- `apps/infra-tf/api_gateway_mcp.tf` (new)
- `apps/infra-tf/outputs.tf` (add mcp_api_gateway_url)
- `apps/infra-tf/tests/test_plan.py` (add APIGW test)

**Done when:** `terraform validate` passes; plan assertions for AC4 pass.

---

### T3: Fixture regeneration + test run

**Depends on:** T1, T2

**Touches:**
- `apps/infra-tf/tests/conftest.py` (add mcp_invoker_role_arn stub var)
- `apps/infra-tf/tests/fixtures/plan.json` (regenerated)

**Done when:** `TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json pytest apps/infra-tf/tests/ -q` exits 0.

## Rollout

- **Delivery:** no flag — new resources only; no existing callers.
- **Infrastructure:** this is the infra task; no application code changes.
- **Deployment sequencing:** requires `packages/graphrag/mcp-tool-server` (shipped PR #79) for the Lambda handler to exist in the zip.

## Risks

- **Existing test assertions break on resource count changes.** Mitigated by updating tests before regenerating fixture.
- **Terraform provider version drift on apigatewayv2.** The `aws_apigatewayv2_*` resources are stable in AWS provider v5. `versions.tf` pins `~> 5.0`.
- **Lambda concurrency cap shared with query_lambda.** Both use 10 reserved concurrent executions. If the account has low concurrency limits, this may conflict. Accepted for demo; production would raise the limit.
- **`terraform validate` may fail if provider not initialized.** Mitigated by running `terraform init -backend=false` before validate/plan.

## Changelog

- 2026-07-24: initial plan
