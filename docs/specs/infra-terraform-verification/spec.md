# Spec: infra-terraform-verification

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0010](../../adr/0010-terraform-migration.md) (Terraform migration —
  the verification suite is the authoritative gate for the migration's security invariants);
  spec [`infra-terraform-compute`](../infra-terraform-compute/spec.md) (all 5 build specs
  must be complete before the full-stack assertion suite can run);
  `apps/infra/tests/test_stack.py` (CDK synth assertions — the Terraform suite must
  assert equivalent invariants); generate-iac skill (§ Stage 5 — test authoring);
  `apps/infra/stacks/graphrag_stack.py` (source of truth for resource shape)
- **Shape:** verification (test authoring; no application logic)

> **Spec contract:** this document defines "done" for the Terraform migration test
> suite and end-to-end probe script. The suite must achieve equivalent assertion depth
> to `apps/infra/tests/test_stack.py` — every CDK synth assertion has a named
> Terraform plan-assertion counterpart, or an explicit note explaining why one is not
> needed (e.g. `terraform validate` already enforces the constraint).

> **Verification tier** — a pytest plan-assertion suite (`apps/infra-tf/tests/`) plus
> a live end-to-end probe script (`apps/infra-tf/scripts/probe.sh`) — authored last
> because it depends on all four build specs being complete. The suite parses
> `terraform show -json tfplan` instead of `Template.from_stack()`, and the probe
> script replaces the CDK live AC pattern.

## Objective

Author the test suite and probe script that prove the Terraform migration is
semantically equivalent to the CDK stack in security posture, topology, and
operational behavior. The suite is the primary regression gate: when any future
`.tf` change is made, running the suite catches regressions without a live deploy.

The probe script is the live gate: it applies the stack, invokes the 3 Lambda
probes (smoke, vector-smoke, query), asserts on the responses, and destroys the
stack — confirming end-to-end correctness.

## Boundaries

### Always do

- **Pytest plan-assertion suite in `apps/infra-tf/tests/test_plan.py`.**
  Parse `terraform show -json tfplan` with `json.load()`; walk resource changes;
  assert the same invariants as `apps/infra/tests/test_stack.py`. Test names should
  mirror the CDK test names (e.g., `test_vpc_has_no_nat_gateway`,
  `test_has_6_vpc_endpoints`, `test_function_url_is_iam_auth`).
- **Assert every load-bearing security invariant from all 4 build specs:**
  - No NAT gateway (ADR-0002).
  - 6 VPC endpoints (1 gateway + 5 interface, including bedrock-runtime).
  - No public SG ingress (0.0.0.0/0 / ::/0).
  - Closed egress counts match `_COMPUTE_SG_EGRESS` exactly (set equality).
  - Store-SG ingress: Neptune SG accepts 8182 from ingestion/smoke/query (3 rules);
    OpenSearch SG accepts 443 from ingestion/vector-smoke/query (3 rules); no public CIDR.
  - S3 bucket: `force_destroy`, all 4 public-block fields true, SSE AES256,
    TLS-deny bucket policy (`aws:SecureTransport = "false"`).
  - Neptune: `iam_database_authentication_enabled = true`, `storage_encrypted = true`,
    Serverless (min 1.0 / max 2.5), engine `1.3.5.0`, parameter group `neptune1.3`,
    `neptune_query_timeout = "20000"`, `vpc_security_group_ids` = neptune_sg.
  - OpenSearch: domain name `graphrag-vectors`, no AllPrincipals in `access_policies`
    JSON (scoped assertion — not a whole-plan scan), **2 named resource-policy principals**
    (IngestionTaskRole + VectorProbeRole; QueryRole uses identity policy, not the resource
    policy), `es:ESHttp*` scoped to `domain/graphrag-vectors/*`.
  - IAM no wildcard resource on data-plane actions (except `ecr:GetAuthorizationToken`).
  - QueryRole Neptune: only `connect` + `ReadDataViaQuery` — never Write/Delete (ADR-0004).
  - IngestionTaskRole and SmokeProbeRole Neptune: all 4 actions (connect, Read, Write, Delete).
  - Bedrock synthesis grant: scoped to BOTH the `us.anthropic.claude-sonnet-4-6`
    inference-profile ARN (account+region-qualified) AND the `anthropic.claude-sonnet-4-6`
    foundation-model ARNs for `us-east-1`, `us-east-2`, `us-west-2` — never a wildcard
    resource. No synthesis grant on `vector_probe_role`.
  - S3 PutObject: scoped to 3 specific keys/prefixes — never bucket-wide.
  - Function URL `authorization_type = "AWS_IAM"` (never `NONE`).
  - Lambda permission `principal = var.invoker_role_arn` (never `*`).
  - ECR `force_delete = true`.
  - 4 `aws_cloudwatch_log_group` resources with `retention_in_days = 7`, each
    stack-managed with no `skip_destroy` (the provider has no `force_destroy` on log
    groups — `terraform destroy` deletes the group + events by default; corrected from
    the original `force_destroy = true`, which fails `terraform validate` on provider 5.x).
  - Budget alarm: `limit_amount = "150"`, `threshold = 80`, `notification_type = "ACTUAL"`.
  - Governance tags propagated to VPC, S3, Neptune, ECS, ECR resources (5 tag keys).
- **`trivy config apps/infra-tf/` exits 0 with no HIGH/CRITICAL findings.**
  Replaces the CDK `cdk-nag` gate (per ADR-0010). Run `trivy config --exit-code 1
  --severity HIGH,CRITICAL apps/infra-tf/` in the verification task. If Trivy is
  unavailable in the local environment, document the gap and run in CI instead.
- **`probe.sh` — live end-to-end script.** Reads Terraform outputs via `terraform
  output -json`. Waits for Neptune and OpenSearch to be AVAILABLE (readiness-aware
  poll, max 30 minutes). Invokes SmokeProbe, VectorSmokeProbe, and QueryLambda.
  Asserts HTTP/Lambda success responses. Emits a structured report. Does not
  `terraform destroy` (destroy is a separate command — the operator decides when to
  tear down; the probe script does not own the lifecycle).
- **`conftest.py` with a session-scoped `tfplan` fixture.** Runs `terraform plan
  -out=tfplan` (with required var overrides) once per test session; runs `terraform
  show -json tfplan` and returns the parsed dict. All test functions receive `tfplan`
  as a parameter. The fixture skips Terraform execution if the env var
  `TFPLAN_JSON_PATH` is set (allows the test suite to be fed a pre-existing plan
  JSON in CI).
- **`pytest.ini` or `pyproject.toml` test configuration** for the
  `apps/infra-tf/tests/` directory, with `testpaths = ["apps/infra-tf/tests"]` and
  the Terraform binary location configurable via `TERRAFORM_BIN` env var (default
  `terraform`).
- **`probe.sh` readiness gate.** Neptune: poll
  `aws neptune describe-db-clusters` until status = `available`. OpenSearch: poll
  `aws opensearch describe-domain` until `Processing = false`. Max wait 30 minutes
  with 60-second sleep intervals. Exit 1 if not ready after timeout.

### Ask first

- Adding a new test that requires a live apply (most assertions should be plan-level).
- Changing the probe script's `terraform destroy` behavior (the script does not destroy
  by design; an operator calls `terraform destroy` separately).

### Never do

- **Never `terraform apply` in the test suite or `conftest.py`.** The plan-assertion
  suite is plan-only; it must be runnable without AWS credentials after the plan JSON
  is captured.
- **Never hardcode account IDs, region strings, or ARNs in the test assertions.**
  Use pattern matching (regex or substring) rather than exact ARN strings.
- **Never use `assert "X" in str(tfplan)` for structural assertions.** Use proper
  JSON path traversal via Python helper functions — the same approach as the CDK
  `_iam_statements()`, `_resources()` helpers in `test_stack.py`.

## Testing Strategy

- **AC1–AC4 — goal-based check.** Each is verified by running `pytest
  apps/infra-tf/tests/` with a pre-generated plan JSON and confirming exit 0.
  The `TFPLAN_JSON_PATH` env var allows CI to pre-generate the plan JSON in a
  separate step (with AWS creds) and run the assertions in a subsequent step
  (without creds, using the cached JSON).
- **AC5 — goal-based check.** `trivy config --exit-code 1 --severity HIGH,CRITICAL
  apps/infra-tf/` exits 0. This is the ADR-0010 replacement for `cdk-nag`.
- **AC6 — infra/deploy (live).** `probe.sh` run against a live `terraform apply`
  confirms the stack is functionally correct end-to-end.

Gates: `pytest apps/infra-tf/tests/` exits 0; `trivy config` exits 0;
`probe.sh` exits 0 against a live stack.

## Acceptance Criteria

- [x] **AC1 — `apps/infra-tf/tests/test_plan.py` exists with ≥24 test functions
  covering all load-bearing invariants.** *(goal-based check)*
  Test count: `pytest --collect-only -q apps/infra-tf/tests/` shows ≥ 24 collected.
  Each test from the "always do" invariant list has a named test function, including:
  `test_store_sg_ingress_rules_exact`, `test_ingestion_and_smoke_roles_retain_neptune_rw`,
  `test_bedrock_synthesis_grant_scopes_profile_and_foundation_arns`,
  `test_query_lambda_concurrency_cap` (backlog: `terraform-query-lambda-concurrency-cap`).
  Test names mirror CDK test names where applicable.

  **Note on computed attributes:** tests use `planned_values.root_module.resources` (not
  `resource_changes`). The fixture must be generated from applied state (post-apply
  no-op plan) so that computed attributes (Neptune cluster_resource_id, S3 bucket name,
  role ARNs) are fully resolved. Tests gracefully fall back to proxy assertions (resource
  name) when the fixture is a fresh plan with null computed attrs.

- [x] **AC2 — `conftest.py` `tfplan` fixture works with `TFPLAN_JSON_PATH` override.** *(goal-based check)*
  Running `TFPLAN_JSON_PATH=<path-to-fixture-plan.json> pytest apps/infra-tf/tests/`
  uses the provided JSON without running `terraform plan`. A fixture plan JSON exists
  at `apps/infra-tf/tests/fixtures/plan.json` for local iteration.

- [x] **AC3 — All plan-assertion tests pass against the fixture plan JSON.** *(goal-based check)*
  `TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json pytest apps/infra-tf/tests/`
  exits 0. The fixture plan JSON is generated from **applied state** (post-`terraform apply`,
  no-op plan): `terraform plan -out=tfplan -var=... && terraform show -json tfplan`.
  Applied-state fixture is required so `planned_values` has fully resolved values
  (cluster_resource_id, bucket name, role ARNs). The fixture is committed and used by
  CI via `TFPLAN_JSON_PATH`.

- [x] **AC4 — `probe.sh` is executable, passes shellcheck, and contains the readiness
  poll + 3 Lambda invocations.** *(goal-based check)*
  `shellcheck apps/infra-tf/scripts/probe.sh` exits 0.
  `grep -c 'aws lambda invoke' apps/infra-tf/scripts/probe.sh` returns 3.
  `grep 'describe-db-clusters\|describe-domain' apps/infra-tf/scripts/probe.sh`
  shows the readiness poll commands.

- [x] **AC5 — `trivy config` exits 0 with no HIGH/CRITICAL findings.** *(goal-based check)*
  `trivy config --exit-code 1 --severity HIGH,CRITICAL apps/infra-tf/` exits 0.
  Implements the ADR-0010 trivy gate requirement (replacement for CDK `cdk-nag`).
  If Trivy ≥ 0.50 is not installed locally, the gate must pass in CI (document as
  a known-skip with a backlog entry if deferred from the local run).

- [x] **AC6 — Live: `probe.sh` exits 0 against a live `terraform apply` stack.** *(infra/deploy — live)*
  After `terraform apply` completes:
  - Neptune cluster status = `available`.
  - OpenSearch domain `Processing = false`.
  - `aws lambda invoke --function-name <SmokeProbeName>` returns exit 0 with
    `"success"` in the response payload.
  - `aws lambda invoke --function-name <VectorSmokeProbeName>` returns exit 0.
  - `aws lambda invoke --function-name <QueryLambdaName>` returns exit 0.
  - `probe.sh` script exit code = 0.
  - `terraform destroy` completes without error; no residual resources.

## Assumptions

- Technical: `terraform show -json tfplan` produces a `resource_changes` array with
  `change.after` nested objects; helper functions walk this structure the same way
  the CDK `_resources()` helper walks `Template.to_json()["Resources"]`.
- Technical: `shellcheck` is available in the CI environment for the `probe.sh`
  linting gate; if absent during local development, the gate can be skipped.
- Technical: the `TFPLAN_JSON_PATH` fixture override allows the test suite to run
  in a zero-AWS-creds environment, making it suitable for CI assertion steps that
  run after a separate plan step.
- Process: the fixture plan JSON in `apps/infra-tf/tests/fixtures/plan.json` is
  generated once (during the first `infra-terraform-verification` implementation)
  and committed; it is re-generated whenever any `.tf` file changes (the CI plan
  step handles this).

## Changelog

- 2026-07-22 — Spec authored. Verification tier: plan-assertion pytest suite
  (≥20 tests mirroring CDK synth assertions), conftest.py with TFPLAN_JSON_PATH
  fixture, probe.sh (readiness poll + 3 Lambda invocations), fixture plan.json.
  Load-bearing: every security invariant from all 4 build specs has a named test.
  Depends on all 4 build specs being complete.
