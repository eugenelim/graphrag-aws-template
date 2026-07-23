# Plan: infra-terraform-verification

- **Spec:** [`spec.md`](spec.md)
- **Status:** Executing <!-- Drafting | Executing | Done -->

> **Plan contract:** implementation strategy for the Terraform verification tier.
> May change as implementation proceeds; note substantial changes in the changelog.

## Approach

Four files in `apps/infra-tf/tests/`: `conftest.py` (plan fixture), `test_plan.py`
(plan assertions), `fixtures/plan.json` (pre-captured plan JSON). One script at
`apps/infra-tf/scripts/probe.sh`. One config file `apps/infra-tf/pyproject.toml`
for pytest configuration.

Authoring order follows test-file-before-assertions:
1. `conftest.py` + `pyproject.toml` — fixture infrastructure.
2. Helper functions (`_resource_changes`, `_iam_policies`) in `test_plan.py`.
3. Plan assertions: topology tests, security-invariant tests, compute tests.
4. Generate fixture plan JSON.
5. `probe.sh` — live readiness + invocation script.

## Constraints

- The test suite uses `planned_values.root_module.resources` (not `resource_changes`)
  as the primary data source. `planned_values` provides resource values for both fresh
  plans and applied-state (no-op) plans. For a fresh plan, computed attributes
  (`neptune_cluster_arn`, `aws_s3_bucket.corpus.arn`, role ARNs) are null; for an
  applied-state plan, all attributes are fully resolved.
- The committed fixture (`tests/fixtures/plan.json`) is generated from **applied state**
  (post-`terraform apply`, no-op plan), so all attribute values are fully resolved.
  Tests fall back to proxy assertions (resource name) when attribute is null, to support
  both fresh-plan and applied-state fixture execution.
- IAM policy JSON lives in `planned_values.root_module.resources[*].values.policy` (for
  `aws_iam_role_policy`) and `planned_values.root_module.resources[*].values.access_policies`
  (for `aws_opensearch_domain`). These are JSON-encoded strings; parse with `json.loads()`.
  If null (fresh plan), fall back to proxy (resource name).
- The `_COMPUTE_SG_EGRESS` table from `apps/infra/tests/test_stack.py` is the
  authoritative egress-set specification; the Terraform plan uses
  `aws_vpc_security_group_egress_rule` resources (separate resources, not inline
  `egress` blocks), so the assertion logic walks egress rule resources and groups
  them by `security_group_id`.
- `probe.sh` must not call `terraform destroy` — lifecycle ownership stays with the
  operator; the probe is a read-forward script, not a lifecycle script.
- `shellcheck` lints `probe.sh` for POSIX correctness.

## Design (LLD)

### `conftest.py` fixture design

```python
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def tfplan(tmp_path_factory):
    override = os.environ.get("TFPLAN_JSON_PATH")
    if override:
        return json.loads(Path(override).read_text())
    # Generate plan live — note: computed attributes (Neptune ARN, S3 bucket name,
    # role ARNs) are null in fresh plans; use committed fixture for full coverage.
    terraform_bin = os.environ.get("TERRAFORM_BIN", "terraform")
    infra_dir = Path(__file__).parent.parent
    plan_file = tmp_path_factory.mktemp("tfplan") / "plan.tfplan"

    # Create stub zip if Lambda package is absent (plan-only testing).
    lambda_zip = infra_dir / "../graphrag/dist/graphrag.zip"
    stub_created = False
    if not lambda_zip.exists():
        lambda_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(lambda_zip, "w") as zf:
            zf.writestr("stub.txt", "stub")
        stub_created = True

    try:
        # init required before plan; -backend=false avoids S3 state bucket dependency.
        subprocess.run(
            [terraform_bin, "init", "-backend=false", "-input=false"],
            cwd=infra_dir, check=True,
        )
        subprocess.run(
            [terraform_bin, "plan", "-out", str(plan_file), "-input=false",
             "-var=budget_alarm_email=test@example.com",
             "-var=invoker_role_arn=arn:aws:iam::123456789012:role/invoker"],
            cwd=infra_dir, check=True,
        )
    finally:
        if stub_created:
            lambda_zip.unlink(missing_ok=True)

    result = subprocess.run(
        [terraform_bin, "show", "-json", str(plan_file)],
        cwd=infra_dir, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)
```

### Helper pattern for planned_values

```python
def _pv_by_type(tfplan, rtype):
    """Resources of a given type from planned_values (works for both fresh and applied-state plans)."""
    return [r for r in tfplan["planned_values"]["root_module"]["resources"]
            if r["type"] == rtype]

def _iam_inline_policies(tfplan):
    """Parsed inline policy dicts; falls back to empty list for null (fresh-plan) policies."""
    result = []
    for r in _pv_by_type(tfplan, "aws_iam_role_policy"):
        policy_str = r["values"].get("policy")
        if policy_str:
            result.append(json.loads(policy_str))
    return result

def _as_list(v):
    return v if isinstance(v, list) else [v]
```

### CDK test → Terraform plan assertion mapping

| CDK test | Terraform assertion |
|---|---|
| `test_vpc_has_no_nat_gateway` | `len(_rc_by_type(tf, "aws_nat_gateway")) == 0` |
| `test_has_the_required_vpc_endpoints` | `len(_rc_by_type(tf, "aws_vpc_endpoint")) == 6` |
| `test_bedrock_runtime_endpoint_present` | any endpoint has `service_name` containing `bedrock-runtime` |
| `test_neptune_serverless_vpc_resident` | cluster has `serverless_v2_scaling_configuration`, `iam_database_authentication_enabled`, `storage_encrypted` |
| `test_corpus_bucket_is_private_and_encrypted` | public_access_block resource has all 4 fields true; SSE config has `AES256` |
| `test_corpus_bucket_enforces_tls` | bucket policy doc has Deny / `aws:SecureTransport` = `"false"` |
| `test_fargate_task_definition_present` | `len(_rc_by_type(tf, "aws_ecs_task_definition")) == 1` |
| `test_budget_alarm_has_threshold_and_subscriber` | budget resource has `limit_amount = "150"`, notification threshold = 80 |
| `test_no_iam_statement_grants_app_actions_on_wildcard_resource` | walk all inline policy statements; no data-plane action with `"*"` resource |
| `test_no_security_group_allows_public_ingress` | no `aws_vpc_security_group_ingress_rule` with `cidr_ipv4 = "0.0.0.0/0"` |
| `test_security_group_descriptions_use_ec2_charset` | all SG `description` fields match `_EC2_DESC` regex |
| `test_governance_tags_on_taggable_resources` | provider `default_tags` has all 5 keys; VPC/S3/Neptune/ECS/ECR resources have tags block via provider |
| `test_neptune_data_access_actions_present_and_scoped` | neptune-db:ReadDataViaQuery present, resource != `"*"` |
| `test_function_url_is_iam_auth` | `aws_lambda_function_url` `authorization_type = "AWS_IAM"`, count = 1 |
| `test_function_url_invoke_permission_scoped_to_named_principal` | `aws_lambda_permission` principal != `"*"` and != `None`, action = `lambda:InvokeFunctionUrl` |
| `test_query_lambda_sg_reaches_neptune_and_opensearch` | query_lambda SG has egress rules for ports 8182 and 443 |
| `test_compute_sgs_egress_equals_exact_call_set` | per-SG egress rule count matches `_COMPUTE_SG_EGRESS` table exactly |
| `test_opensearch_access_policy_is_scoped_not_all_principals` | `access_policies` JSON has 3 named principals, no `Principal: "*"` |
| `test_vector_actions_are_scoped_no_wildcard_resource` | bedrock:InvokeModel and es:ESHttp* both scoped; Titan v2 ARN present |
| `test_ingestion_task_can_write_manifest_scoped_to_manifest_key` | s3:PutObject present, scoped to manifest.json / schema_extraction_trace.txt / silver/* |
| `test_log_groups_are_stack_managed_and_destroyed` | 4 `aws_cloudwatch_log_group` with `retention_in_days = 7`, none with `skip_destroy = true` (provider has no `force_destroy` on log groups; `terraform destroy` deletes them + events by default) |
| `test_store_sg_ingress_rules_exact` *(new — no CDK equivalent name)* | Neptune SG has exactly 3 ingress rules on 8182; OpenSearch SG has exactly 3 on 443; peer SGs match ingestion/smoke/query and ingestion/vector-smoke/query respectively |
| `test_ingestion_and_smoke_roles_retain_neptune_rw` *(CDK: `test_ingestion_and_smoke_roles_retain_read_write`)* | IngestionTaskRole and SmokeProbeRole policies contain all 4 neptune-db actions (connect, Read, Write, Delete) |
| `test_bedrock_synthesis_grant_scopes_profile_and_foundation_arns` *(CDK: `test_bedrock_claude_grant_scopes_profile_and_foundation_no_wildcard`)* | roles with synthesis grant have: inference-profile ARN (account+region-qualified) AND 3 regional foundation-model ARNs (`us-east-1/2` + `us-west-2`); no wildcard resource; `vector_probe_role` has no synthesis grant |

### `probe.sh` structure

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Read outputs
OUTPUTS=$(terraform -chdir="$(dirname "$0")/.." output -json)
SMOKE=$(echo "$OUTPUTS" | jq -r .smoke_probe_name.value)
VECTOR=$(echo "$OUTPUTS" | jq -r .vector_smoke_probe_name.value)
QUERY_URL=$(echo "$OUTPUTS" | jq -r .query_function_url.value)

# 2. Wait for Neptune
# ... poll aws neptune describe-db-clusters; exit 1 if timeout ...

# 3. Wait for OpenSearch
# ... poll aws opensearch describe-domain; exit 1 if timeout ...

# 4. Invoke SmokeProbe
aws lambda invoke --function-name "$SMOKE" /tmp/smoke_out.json
grep -q '"success"' /tmp/smoke_out.json || { echo "FAIL: smoke probe"; exit 1; }

# 5. Invoke VectorSmoke
aws lambda invoke --function-name "$VECTOR" /tmp/vector_out.json
grep -q '"success"' /tmp/vector_out.json || { echo "FAIL: vector smoke probe"; exit 1; }

# 6. Invoke QueryLambda via Function URL (IAM sigv4 auth)
STATUS=$(aws lambda invoke --function-name ... --payload '{"query":"test"}' /tmp/query_out.json; echo $?)
# ... parse HTTP response ...

echo "ALL PROBES PASSED"
```

## Tasks

### T1: Write `apps/infra-tf/tests/conftest.py` + `pyproject.toml`

**Depends on:** none (all build specs complete by the time this runs)
**Touches:** `apps/infra-tf/tests/conftest.py`, `apps/infra-tf/pyproject.toml`
**Tests:** goal-based — `TFPLAN_JSON_PATH=/dev/null pytest --collect-only -q
  apps/infra-tf/tests/` exits 0 with tests collected (fixture creation is skipped
  via the env var override).
**Approach:** Write `conftest.py` with the `tfplan` session-scoped fixture.
  Write `pyproject.toml` with `[tool.pytest.ini_options]` setting
  `testpaths = ["tests"]` (relative to `apps/infra-tf/`). Import guards match
  the CDK test's `pytest.importorskip` pattern — here just `terraform` CLI presence
  checked by `shutil.which("terraform")` with a `pytest.skip` if absent.
**Done when:** `pytest --collect-only` exits 0; `pyproject.toml` has the test path
  configured.

---

### T2: Write `test_plan.py` — helper functions + topology tests (10 tests)

**Depends on:** T1
**Touches:** `apps/infra-tf/tests/test_plan.py`
**Tests:** goal-based — `pytest --collect-only -q apps/infra-tf/tests/` shows ≥ 10
  tests collected; `terraform validate` exits 0 (no test file affects the tf sources).
**Approach:** Write the `_rc_by_type`, `_iam_inline_policies`, `_iam_statements`,
  and `_all_iam_policy_statements` helper functions. Then write 10 topology tests:
  `test_vpc_has_no_nat_gateway`, `test_has_6_vpc_endpoints`,
  `test_bedrock_runtime_endpoint_present`, `test_neptune_serverless_vpc_resident`,
  `test_corpus_bucket_is_private_and_encrypted`, `test_corpus_bucket_enforces_tls`,
  `test_fargate_task_definition_present`, `test_log_groups_are_stack_managed_and_destroyed`,
  `test_ecr_repository_force_delete`, `test_governance_tags_applied_to_provider`.
**Done when:** 10 tests collected; test code follows the CDK test naming convention.

---

### T3: Write `test_plan.py` — security and IAM invariant tests (13 tests)

**Depends on:** T2
**Touches:** `apps/infra-tf/tests/test_plan.py`
**Tests:** goal-based — `pytest --collect-only -q` shows ≥ 23 tests collected.
**Approach:** Add 13 security tests:
  `test_no_security_group_allows_public_ingress`,
  `test_security_group_descriptions_use_ec2_charset`,
  `test_compute_sgs_egress_equals_exact_call_set`,
  `test_no_iam_statement_grants_app_actions_on_wildcard_resource`,
  `test_neptune_data_access_actions_present_and_scoped`,
  `test_query_role_neptune_grant_is_read_only`,
  `test_opensearch_access_policy_is_scoped_not_all_principals`,
  `test_vector_actions_are_scoped_no_wildcard_resource`,
  `test_ingestion_task_can_write_manifest_scoped_to_manifest_key`,
  `test_function_url_is_iam_auth`,
  `test_function_url_invoke_permission_scoped_to_named_principal`,
  `test_budget_alarm_has_threshold_and_subscriber`,
  `test_query_lambda_sg_reaches_neptune_and_opensearch`.
  The ADR-0004 test (`test_query_role_neptune_grant_is_read_only`) is the
  load-bearing regression guard: it must fail if Write/Delete actions appear on any
  policy attached to the query_role.
**Done when:** 23 tests collected; `test_query_role_neptune_grant_is_read_only` is present.

---

### T4: Generate fixture plan JSON

**Depends on:** T3 (tests exist to validate against the fixture)
**Touches:** `apps/infra-tf/tests/fixtures/plan.json`
**Tests:** goal-based — `TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json
  pytest apps/infra-tf/tests/` exits 0; all tests pass.
**Approach:** Run `terraform plan -out=tfplan
  -var=budget_alarm_email=test@example.com
  -var=invoker_role_arn=arn:aws:iam::123456789012:role/invoker` from `apps/infra-tf/`
  (no `s3_prefix_list_id` — infra-terraform-network resolves the S3 managed prefix
  list via a data source, SEC-2). Run
  `terraform show -json tfplan > apps/infra-tf/tests/fixtures/plan.json`. Commit
  the fixture JSON. Run the full test suite against it; fix any failures.
**Done when:** pytest exits 0 with the fixture JSON; all ≥23 tests pass.

---

### T5: Write `apps/infra-tf/scripts/probe.sh` + shellcheck gate

**Depends on:** none (independent of the pytest suite)
**Touches:** `apps/infra-tf/scripts/probe.sh`
**Tests:** goal-based — `shellcheck apps/infra-tf/scripts/probe.sh` exits 0;
  `grep -c 'aws lambda invoke' apps/infra-tf/scripts/probe.sh` returns 3;
  `grep 'describe-db-clusters\|describe-domain' apps/infra-tf/scripts/probe.sh`
  shows the readiness poll commands; `chmod +x` is set.
**Approach:** Write `probe.sh` with the pattern from the Design section. Neptune
  readiness: poll `aws neptune describe-db-clusters --db-cluster-identifier <id>`
  and check `DBClusters[0].Status` == `"available"`. OpenSearch readiness: poll
  `aws opensearch describe-domain --domain-name graphrag-vectors` and check
  `DomainStatus.Processing` == `false`. Lambda invocations via `aws lambda invoke`.
  Query Lambda invocation via `aws lambda invoke` (not direct HTTP curl, which would
  require SigV4 signing from bash — simpler to use the Lambda API directly for the
  probe gate; the Function URL auth is tested by the plan-assertion, not the probe).
  Run `shellcheck --severity=warning probe.sh`.
**Done when:** shellcheck exits 0; 3 lambda invoke calls; readiness polls present;
  `chmod +x` set.

---

### T6: Run `trivy config` static configuration scan (ADR-0010 gate)

**Depends on:** T4
**Touches:** none (scan only)
**Tests:** goal-based — `trivy config --exit-code 1 --severity HIGH,CRITICAL
  apps/infra-tf/` exits 0.
**Approach:** Install Trivy ≥ 0.50 if absent (`brew install trivy` / GitHub Releases).
  Run `trivy config --exit-code 1 --severity HIGH,CRITICAL apps/infra-tf/`.
  Address any HIGH/CRITICAL findings by correcting the `.tf` resource config (not by
  adding `.trivyignore` suppressions unless the finding is a confirmed false-positive
  with a documented reason). This gate replaces the CDK `test_cdk_nag_no_unsuppressed_findings`
  that the CDK stack passes.
**Done when:** `trivy config` exits 0 with no HIGH/CRITICAL findings.

---

### T7: Final gate — all tests pass, fmt clean, trivy clean

**Depends on:** T4, T5, T6
**Touches:** none (verification only)
**Tests:** goal-based — `terraform fmt -check apps/infra-tf/` exits 0; `shellcheck
  apps/infra-tf/scripts/probe.sh` exits 0; `TFPLAN_JSON_PATH=... pytest
  apps/infra-tf/tests/` exits 0 with ≥23 tests passing; `pytest --collect-only -q`
  count >= 23; `trivy config` exits 0.
**Approach:** Run the full gate sequence. Surface any remaining failures. This is
  the spec-level done condition — all build specs + verification spec gates passing.
**Done when:** All 4 commands exit 0.

## Rollout

The verification suite runs in CI on every PR that touches `apps/infra-tf/`. The
`TFPLAN_JSON_PATH` env var allows the plan JSON to be pre-generated in a plan step
(with AWS creds) and consumed in a subsequent assertion step (without creds). The
`probe.sh` script is run manually after a live `terraform apply` to confirm the
stack is functional before `terraform destroy`.

The combined CDK + Terraform stack state after this spec ships: CDK stack is
unchanged and still deployable; Terraform stack is fully implemented and verified;
the two are not deployed simultaneously (they share the same AWS resources by name).
CDK stack decommission is a separate follow-on item — not in scope of this spec.

## Risks

- **`terraform show -json` schema stability:** the `resource_changes` field structure
  is stable across Terraform >= 1.11 but the nesting of computed attributes (e.g.,
  `aws_iam_role_policy.policy` may be null in plan output because it references a
  computed resource). Assertions against these attributes must use `in str(tfplan)`
  or the `change.after_unknown` field rather than `change.after`. Mitigation:
  generate the fixture plan and iterate tests against it before finalizing.
- **`test_compute_sgs_egress_equals_exact_call_set`:** the Terraform plan uses
  separate `aws_vpc_security_group_egress_rule` resources (not inline blocks);
  the helper must group them by `security_group_id` and compare to the
  `_COMPUTE_SG_EGRESS` table. The CDK test grouped by `GroupDescription`; the
  Terraform test groups by security_group_id (which is a computed attribute,
  shown as `(known after apply)` in the plan). Mitigation: the test may need to
  match by resource address suffix rather than security_group_id; verify during
  implementation.
- **OpenSearch `access_policies` JSON in plan output:** for VPC domains, the
  access policy may be a computed attribute in the plan (not known until apply).
  Mitigation: if the access policy is unknown at plan time, the test asserts on
  the role ARN references in the `aws_opensearch_domain` resource's `access_policies`
  attribute using the HCL expressions before resolution.

## Changelog

- 2026-07-22 — Plan authored for infra-terraform-verification spec. Six tasks:
  conftest.py + pytest config, 10 topology tests, 13 security/IAM tests, fixture
  plan JSON, probe.sh, final gate. Mapped all CDK synth assertions to Terraform plan
  assertions. Risk noted for computed attributes in plan output.
