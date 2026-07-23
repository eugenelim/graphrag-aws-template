# Plan: infra-terraform-compute

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** implementation strategy for the compute tier. May change as
> implementation proceeds; note substantial changes in the changelog.

## Approach

Three files: `compute.tf` (ECS/Fargate + ECR), `lambda.tf` (Lambda functions +
Function URL + permission + SmokeProbe role + log groups), `budget.tf` (Budget
alarm). The ECS task execution role is created in `compute.tf` alongside the task
definition that needs it.

Authoring order:
1. `compute.tf`: ECS cluster, ECR repo, ECS task execution role, log group for
   ingestion, Fargate task definition (references data+IAM tier outputs for task role).
2. `lambda.tf`: 4 log groups (smoke, vector-smoke, query), SmokeProbe role, 3 Lambda
   functions, Function URL, Lambda permission.
3. `budget.tf`: Budget alarm (independent of all other resources).
4. `outputs.tf` completion: fill remaining 9 output stubs from compute resources.
5. Verification pass: `terraform fmt -check`, plan JSON security checks.

## Constraints

- ADR-0004: `query_role` Neptune grant stays read-only; this spec does not modify it.
- CDK `empty_on_delete=True` → `force_delete = true` on `aws_ecr_repository`.
- CDK `RemovalPolicy.DESTROY` on log groups → `force_destroy = true` on
  `aws_cloudwatch_log_group`.
- Lambda code is sourced from `apps/graphrag/dist/graphrag.zip` (pre-built by CI);
  a placeholder zip (`touch apps/graphrag/dist/graphrag.zip`) is required for plan
  to succeed locally. The Terraform source hash validates the file presence, not
  its contents.
- `aws_lambda_function` `logging_config` block is the correct way to point a Lambda
  at a pre-existing log group (provider >= 5.40). Verify argument name against schema.

## Design (LLD)

### CDK resource → Terraform resource mapping

| CDK method / resource | Terraform resource |
|---|---|
| `ecs.Cluster` | `aws_ecs_cluster.main` |
| `ecr.Repository` | `aws_ecr_repository.ingestion` |
| auto-created `ecsTaskExecutionRole` | `aws_iam_role.ecs_task_execution_role` + attachment |
| `ecs.FargateTaskDefinition` | `aws_ecs_task_definition.ingestion` |
| `logs.LogGroup` × 4 | `aws_cloudwatch_log_group` × 4 |
| auto-created SmokeProbe Lambda role | `aws_iam_role.smoke_probe_role` + attachment + policy |
| `lambda_.Function` SmokeProbe | `aws_lambda_function.smoke_probe` |
| `lambda_.Function` VectorSmokeProbe | `aws_lambda_function.vector_smoke_probe` |
| `lambda_.Function` QueryLambda | `aws_lambda_function.query_lambda` |
| `fn.add_function_url(auth_type=AWS_IAM)` | `aws_lambda_function_url.query_url` |
| `lambda_.CfnPermission` | `aws_lambda_permission.query_url_invoke` |
| `budgets.CfnBudget` | `aws_budgets_budget.monthly` |

### Fargate container definitions JSON structure

```hcl
container_definitions = jsonencode([{
  name      = "ingestion"
  image     = "${aws_ecr_repository.ingestion.repository_url}:latest"
  essential = true
  environment = [
    { name = "NEPTUNE_ENDPOINT",    value = "https://${aws_neptune_cluster.main.endpoint}:8182" },
    { name = "OPENSEARCH_ENDPOINT", value = "https://${aws_opensearch_domain.graphrag_vectors.endpoint}" },
    { name = "CORPUS_BUCKET",       value = aws_s3_bucket.corpus.id },
    { name = "SCHEMA_EXTRACTION",   value = "false" },
  ]
  logConfiguration = {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.ingestion.name
      "awslogs-region"        = var.aws_region
      "awslogs-stream-prefix" = "ingestion"
    }
  }
}])
```

Note: the `image` tag `:latest` is a placeholder (the CI pipeline pushes the actual
tag and the ECS run-task call specifies it at runtime; the task definition image field
is overridable at `run-task` time).

### SmokeProbe IAM role

```hcl
resource "aws_iam_role" "smoke_probe_role" {
  name               = "graphrag-smoke-probe-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}
resource "aws_iam_role_policy_attachment" "smoke_probe_vpc" {
  role       = aws_iam_role.smoke_probe_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}
resource "aws_iam_role_policy" "smoke_probe_neptune" {
  name   = "smoke-probe-neptune-full-rw"
  role   = aws_iam_role.smoke_probe_role.id
  policy = jsonencode({
    Statement = [{
      Effect   = "Allow"
      Action   = ["neptune-db:connect", "neptune-db:ReadDataViaQuery",
                  "neptune-db:WriteDataViaQuery", "neptune-db:DeleteDataViaQuery"]
      Resource = "arn:aws:neptune-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${aws_neptune_cluster.main.cluster_resource_id}/*"
    }]
  })
}
```

### Lambda logging_config pattern

```hcl
logging_config {
  log_format = "Text"
  log_group  = aws_cloudwatch_log_group.smoke_probe.name
}
```

This prevents the Lambda service from creating an auto-`/aws/lambda/<name>` log group
that would survive `terraform destroy`. All 3 Lambda functions must use this pattern.

## Tasks

### T1: Write `compute.tf` — ECS cluster, ECR repo, ECS task execution role, Fargate task def

**Depends on:** none (scaffold + network + data+IAM complete)
**Touches:** `apps/infra-tf/compute.tf`
**Tests:** goal-based — plan shows 1 `aws_ecs_cluster`, 1 `aws_ecr_repository`
  (`force_delete = true`), 1 `aws_iam_role.ecs_task_execution_role`, 1 `aws_iam_role_policy_attachment`,
  1 `aws_ecs_task_definition`; `terraform validate` exits 0.
**Approach:** Write the 5 resources. The `aws_cloudwatch_log_group.ingestion` for the
  ingestion container log group is written here alongside the task definition (keeps
  the container log wiring local). `container_definitions` references data+IAM tier
  resources (Neptune endpoint, OpenSearch endpoint, S3 bucket). Verify
  `aws_ecs_task_definition` argument names (esp. `container_definitions` JSON format)
  against provider schema (contract-acquisition gate).
**Done when:** 5+ resources in plan; `terraform validate` exits 0.

---

### T2: Write `lambda.tf` — 3 log groups (smoke probes + query), SmokeProbe role

**Depends on:** T1
**Touches:** `apps/infra-tf/lambda.tf`
**Tests:** goal-based — plan shows 3 additional `aws_cloudwatch_log_group` resources
  (plus ingestion from T1 = 4 total); all with `retention_in_days = 7` and
  `force_destroy = true`; `aws_iam_role.smoke_probe_role` present with Neptune
  full-RW inline policy; `terraform validate` exits 0.
**Approach:** Write 3 `aws_cloudwatch_log_group` (smoke probe, vector smoke probe,
  query Lambda). Write `aws_iam_role.smoke_probe_role` + 1 attachment +
  `aws_iam_role_policy.smoke_probe_neptune`. The Neptune policy ARN construction
  uses `data.aws_caller_identity.current.account_id` and
  `aws_neptune_cluster.main.cluster_resource_id` (same pattern as data+IAM tier).
**Done when:** 3 log groups in plan; SmokeProbe role with full-RW Neptune policy.

---

### T3: Write `lambda.tf` — 3 Lambda functions

**Depends on:** T2
**Touches:** `apps/infra-tf/lambda.tf`
**Tests:** goal-based — plan shows 3 `aws_lambda_function` resources; each has
  `vpc_config`, `logging_config.log_group` set; SmokeProbe role ARN on smoke function;
  query Lambda has `NEPTUNE_ENDPOINT`, `OPENSEARCH_ENDPOINT`, `SYNTHESIS_MODEL_ID` env vars;
  `terraform validate` exits 0.
**Approach:** Write 3 `aws_lambda_function` resources. Use
  `filename = "../../apps/graphrag/dist/graphrag.zip"` (relative to the `apps/infra-tf/`
  working directory). Add `source_code_hash = filebase64sha256(...)` for change detection.
  Verify `logging_config` argument structure against provider schema (contract-acquisition
  gate). The `vpc_config` references the private subnet IDs and respective security group
  IDs from the network tier outputs.
**Done when:** 3 Lambda functions in plan; all VPC-attached; all have stack-managed
  log groups; no auto-created `/aws/lambda/` groups.

---

### T4: Write `lambda.tf` — Function URL + Lambda permission

**Depends on:** T3
**Touches:** `apps/infra-tf/lambda.tf`
**Tests:** goal-based — plan shows 1 `aws_lambda_function_url` with
  `authorization_type = "AWS_IAM"`; 1 `aws_lambda_permission` with
  `action = "lambda:InvokeFunctionUrl"`, `principal = var.invoker_role_arn`,
  `function_url_auth_type = "AWS_IAM"`; no `aws_lambda_permission` with `principal = "*"`;
  `terraform validate` exits 0.
**Approach:** Write `aws_lambda_function_url.query_url` (target = `aws_lambda_function.query_lambda`,
  `authorization_type = "AWS_IAM"`). Write `aws_lambda_permission.query_url_invoke`
  (action = `lambda:InvokeFunctionUrl`, principal = `var.invoker_role_arn`,
  function_url_auth_type = `AWS_IAM`). Verify `aws_lambda_function_url` argument
  names against provider schema (contract-acquisition gate — `authorization_type`
  vs `auth_type` differ across provider versions).
**Done when:** Function URL and permission in plan; auth type is AWS_IAM; principal
  is `var.invoker_role_arn` (not `*`).

---

### T5: Write `budget.tf` — monthly cost budget

**Depends on:** none (independent resource)
**Touches:** `apps/infra-tf/budget.tf`
**Tests:** goal-based — plan shows 1 `aws_budgets_budget` with `limit_amount = "150"`,
  `budget_type = "COST"`, `time_unit = "MONTHLY"`; notification has `threshold = 80`
  and `notification_type = "ACTUAL"`; subscriber has `subscription_type = "EMAIL"`;
  `terraform validate` exits 0.
**Approach:** Write `aws_budgets_budget.monthly`. Verify `aws_budgets_budget`
  argument names (esp. `notification.threshold`, `notification.threshold_type`,
  `subscriber`) against provider schema — the argument structure changed between
  provider 4.x and 5.x (contract-acquisition gate).
**Done when:** Budget resource in plan; all 5 CDK-matching fields present.

---

### T6: Complete `outputs.tf` — fill all compute-layer outputs

**Depends on:** T1, T3, T4
**Touches:** `apps/infra-tf/outputs.tf`
**Tests:** goal-based — `terraform validate` exits 0; `grep -c 'null' apps/infra-tf/outputs.tf`
  returns 0 (no remaining null stubs).
**Approach:** Fill in the remaining 9 output stubs:
  - `ecs_cluster_name = aws_ecs_cluster.main.name`
  - `ingestion_task_def_arn = aws_ecs_task_definition.ingestion.arn`
  - `ingestion_security_group_id = aws_security_group.ingestion_task_sg.id`
  - `private_subnet_id = aws_subnet.private[0].id` *(already filled in network tier)*
  - `ingestion_repo_uri = aws_ecr_repository.ingestion.repository_url`
  - `smoke_probe_name = aws_lambda_function.smoke_probe.function_name`
  - `vector_smoke_probe_name = aws_lambda_function.vector_smoke_probe.function_name`
  - `query_function_url = aws_lambda_function_url.query_url.function_url`
  - `query_lambda_name = aws_lambda_function.query_lambda.function_name`
**Done when:** All 12 outputs have real resource references; no null stubs remain.

---

### T7: Run `terraform fmt -check` + plan-level security verification

**Depends on:** T4, T5, T6
**Touches:** none (verification only)
**Tests:** goal-based — `terraform fmt -check` exits 0; plan JSON checks:
  (1) Function URL `authorization_type = "AWS_IAM"` (no `NONE`);
  (2) Lambda permission `principal = var.invoker_role_arn` value (not `*`);
  (3) `aws_lambda_function_url` count = 1;
  (4) SmokeProbe role has all 4 Neptune actions;
  (5) QueryRole Neptune actions do NOT include Write/Delete (ADR-0004 invariant preserved).
**Approach:** Run `terraform fmt -recursive apps/infra-tf/`. Run
  `terraform plan -out=tfplan`. Run `terraform show -json tfplan` and parse JSON.
  Five targeted assertions. The ADR-0004 assertion (no Write/Delete on QueryRole)
  confirms the compute spec did not accidentally modify the query_role policies from
  the data+IAM tier.
**Done when:** fmt exits 0; all 5 security assertions pass.

## Rollout

This is the final build spec; the live acceptance test is AC10. After `terraform apply`:
1. Build the ingestion container image and push to the ECR repo.
2. Invoke SmokeProbe: `aws lambda invoke --function-name <SmokeProbeName> /tmp/out.json`
3. Invoke VectorSmokeProbe: same pattern.
4. Invoke QueryLambda via Function URL.
5. `terraform destroy` — confirm clean removal.

Neptune clusters and OpenSearch domains take 5–30 minutes to become available.
Smoke probe invocations should wait for the cluster/domain status to be AVAILABLE
before being invoked. The `infra-terraform-verification` spec's `probe.sh` handles
the readiness wait.

## Risks

- **Lambda `logging_config` argument name:** may be `logging_config`, `log_config`,
  or nested differently. Contract-acquisition gate must verify before writing HCL.
- **`aws_lambda_function_url` `authorization_type` vs `auth_type`:** argument name
  changes between provider minor versions. Verify against `terraform providers schema
  -json` before writing.
- **`aws_budgets_budget` `notification` block structure:** nested blocks changed
  between provider 4.x and 5.x; verify argument list and nesting before writing.
- **ECS task definition `container_definitions` image tag `:latest`:** a plan run
  with no pushed image in ECR will succeed (Terraform does not validate ECR image
  existence at plan time); the apply will fail only at the `ecs run-task` step.
  Mitigation: the live AC (AC10) covers this; a placeholder or the pre-built zip
  is enough for the plan-only gate.
- **Lambda zip file absence:** if `apps/graphrag/dist/graphrag.zip` does not exist,
  `terraform plan` fails with a file-not-found error on `filebase64sha256`. Mitigation:
  create the dist directory and a placeholder zip as task-zero during implementation.

## Changelog

- 2026-07-22 — Plan authored for infra-terraform-compute spec. Seven tasks:
  ECS/ECR/execution role/task def, 3 log groups + SmokeProbe role, 3 Lambda functions,
  Function URL + permission, Budget alarm, outputs completion, fmt + security verification.
  Compute tier depends on data+IAM for role ARNs and data store endpoints.
