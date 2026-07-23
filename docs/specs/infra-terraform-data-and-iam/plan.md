# Plan: infra-terraform-data-and-iam

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** implementation strategy for the data + IAM tier. May change
> as implementation proceeds; note substantial changes in the changelog.

## Approach

Four files: `s3.tf`, `neptune.tf`, `opensearch.tf`, `iam.tf`. The dependency
resolution order in Terraform is automatic, but the authoring order follows the
dependency chain:

1. `iam.tf`: Create IAM roles first (no dependencies) — the OpenSearch access
   policy needs their ARNs.
2. `opensearch.tf`: Create domain with `access_policies` referencing role ARNs
   (depends on IAM roles, not on policies; no cycle).
3. `neptune.tf`: Create subnet group → parameter group → cluster → instance
   (depends on network subnet IDs).
4. `s3.tf`: Create bucket → public access block → SSE config → bucket policy
   (independent of IAM and Neptune).
5. Back to `iam.tf`: Add `aws_iam_role_policy` resources referencing Neptune
   cluster resource ID + OpenSearch domain ARN (depends on neptune + opensearch).

The CDK `_bedrock_synthesis_invoke()` method generates ARNs for 4 resources
(1 inference-profile + 3 foundation-model regional ARNs); these must be constructed
using `data.aws_caller_identity.current.account_id` + `var.aws_region` in Terraform.

## Constraints

- ADR-0004: QueryRole Neptune grant is strictly read-only (IAM layer, not
  app-layer — this constraint cannot be relaxed even temporarily).
- CDK `_neptune_cluster_arn()` → `format_arn(service="neptune-db",
  resource=cluster.attr_cluster_resource_id, resource_name="*")` translates to
  `"arn:aws:neptune-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${aws_neptune_cluster.main.cluster_resource_id}/*"`.
- CDK `_opensearch_domain_arn()` → fixed domain name → translate to
  `"arn:aws:es:${var.aws_region}:${data.aws_caller_identity.current.account_id}:domain/graphrag-vectors/*"`.
- OpenSearch domain name `"graphrag-vectors"` is fixed (source: CDK
  `_OPENSEARCH_DOMAIN_NAME`); changing it changes the ARN and breaks the access policy.
- Neptune `engine_version = "1.3.5.0"` and `family = "neptune1.3"` are pinned;
  `auto_minor_version_upgrade = false` prevents drift (source: CDK stack + ADR-0004).

## Design (LLD)

### IAM role → inline policy structure

| Role | Assume trust | Managed policy | Inline policies |
|---|---|---|---|
| `ingestion_task_role` | `ecs-tasks.amazonaws.com` | none | neptune-full-rw, opensearch-data, bedrock-titan, bedrock-synthesis, s3-read, s3-put-manifest, s3-put-trace, s3-put-silver |
| `vector_probe_role` | `lambda.amazonaws.com` | AWSLambdaVPCAccessExecutionRole | opensearch-data, bedrock-titan |
| `query_role` | `lambda.amazonaws.com` | AWSLambdaVPCAccessExecutionRole | neptune-read-only, opensearch-data, bedrock-titan, bedrock-synthesis |

### Bedrock synthesis ARN set (Terraform locals)

```hcl
locals {
  synthesis_model_id       = "us.anthropic.claude-sonnet-4-6"
  synthesis_foundation_id  = "anthropic.claude-sonnet-4-6"
  synthesis_profile_regions = ["us-east-1", "us-east-2", "us-west-2"]

  synthesis_arns = concat(
    ["arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${local.synthesis_model_id}"],
    [for r in local.synthesis_profile_regions :
     "arn:aws:bedrock:${r}::foundation-model/${local.synthesis_foundation_id}"]
  )
}
```

### S3 PutObject grant structure (3 separate statements)

```
s3:PutObject on arn:aws:s3:::<bucket>/manifest.json
s3:PutObject on arn:aws:s3:::<bucket>/schema_extraction_trace.txt
s3:PutObject on arn:aws:s3:::<bucket>/silver/*
```

Never merged into one statement with `/*` or a wildcard suffix — the
`test_schema_extraction_trace_putobject_grant_is_key_scoped` and
`test_silver_putobject_grant_is_prefix_bounded` assertions enforce this.

## Tasks

### T1: Write `iam.tf` — 3 IAM roles + managed policy attachments

**Depends on:** none (scaffold + network complete)
**Touches:** `apps/infra-tf/iam.tf`
**Tests:** goal-based — plan shows 3 `aws_iam_role` + 2 `aws_iam_role_policy_attachment`
  resources; `terraform validate` exits 0.
**Approach:** Write `aws_iam_role` for ingestion_task_role (ecs-tasks trust),
  vector_probe_role (lambda trust), query_role (lambda trust). Write 2
  `aws_iam_role_policy_attachment` for AWSLambdaVPCAccessExecutionRole on
  vector_probe_role and query_role. No inline policies yet (they reference
  resources not yet defined).
**Done when:** 3 roles + 2 attachments in plan; `terraform validate` exits 0.

---

### T2: Write `s3.tf` — corpus bucket + public access block + SSE + bucket policy

**Depends on:** T1
**Touches:** `apps/infra-tf/s3.tf`
**Tests:** goal-based — plan shows 1 `aws_s3_bucket` + 1 `aws_s3_bucket_public_access_block` +
  1 `aws_s3_bucket_server_side_encryption_configuration` + 1 `aws_s3_bucket_policy`;
  policy JSON contains `"aws:SecureTransport": "false"` Deny statement; `terraform validate` exits 0.
**Approach:** Write 4 resources. Bucket: `force_destroy = true`. Public access block:
  all 4 fields `true`. SSE config: `AES256`. Bucket policy: a single-statement policy
  with `Effect: "Deny"`, `Principal: "*"`, `Action: "s3:*"`, `Condition: { Bool: {
  "aws:SecureTransport": "false" } }`, `Resource: ["${bucket_arn}", "${bucket_arn}/*"]`.
**Done when:** 4 S3 resources in plan; Deny statement on SecureTransport present in policy JSON.

---

### T3: Write `neptune.tf` — subnet group, parameter group, cluster, instance

**Depends on:** T1 (for later IAM policy; Neptune itself has no IAM dep)
**Touches:** `apps/infra-tf/neptune.tf`
**Tests:** goal-based — plan shows 1 `aws_neptune_subnet_group` + 1
  `aws_neptune_cluster_parameter_group` + 1 `aws_neptune_cluster` + 1
  `aws_neptune_cluster_instance`; cluster has `iam_database_authentication_enabled = true`,
  `storage_encrypted = true`, and
  `neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.main.name`
  (the cluster must reference the timeout param group, else the ADR-0004 backstop is
  inert); parameter group has `neptune_query_timeout = "20000"`;
  `auto_minor_version_upgrade = false` on the instance.
**Approach:** Write 4 Neptune resources in dependency order (subnet group →
  parameter group → cluster → instance). Cluster uses `serverless_v2_scaling_configuration`
  and sets `neptune_cluster_parameter_group_name` to the param group's name (verified
  provider arg names against the v5.100 schema — contract-acquisition gate). Set
  `skip_final_snapshot = true` (teardown-first).
**Done when:** 4 Neptune resources in plan with correct arguments, cluster references the
  parameter group by name; `terraform validate` exits 0.

---

### T4: Write `opensearch.tf` — domain + access policy (references role ARNs)

**Depends on:** T1 (role ARNs needed in access policy)
**Touches:** `apps/infra-tf/opensearch.tf`
**Tests:** goal-based — plan shows 1 `aws_opensearch_domain`; `access_policies`
  JSON contains **exactly 2** role ARNs (ingestion_task + vector_probe) and the domain
  ARN; no `"Principal": "*"` and no account-root in the access policy JSON;
  `terraform validate` exits 0.
**Approach:** Write `aws_opensearch_domain.graphrag_vectors`. The `access_policies`
  attribute uses `jsonencode()` to build the policy inline, referencing **only**
  `aws_iam_role.ingestion_task_role.arn` and `aws_iam_role.vector_probe_role.arn`
  (matching the CDK — QueryRole is NOT in the resource policy; it reaches OpenSearch via
  its identity grant in T5). VPC options: one private subnet ID +
  `aws_security_group.opensearch_sg.id`. Verify `aws_opensearch_domain` argument
  names against live provider schema (contract-acquisition gate) — the `vpc_options`,
  `cluster_config`, `ebs_options` argument names have changed across provider versions.
**Done when:** Domain in plan with correct config; access policy has exactly 2 named
  principals (ingestion_task + vector_probe), never a third.

---

### T5: Write `iam.tf` — all inline policies (references Neptune ARN; OpenSearch ARN is a constructed string)

**Depends on:** T3 (real reference — the Neptune `cluster_resource_id`). T4 is
  authoring-order only, NOT a Terraform dependency: the OpenSearch ARN used in the
  inline policies is a constructed string from the fixed domain name
  (`arn:aws:es:<region>:<account>:domain/graphrag-vectors/*`), never
  `aws_opensearch_domain.graphrag_vectors.arn`.
**Touches:** `apps/infra-tf/iam.tf`
**Tests:** goal-based — plan shows correct `aws_iam_role_policy` count (ingestion_task: 8
  policies, vector_probe: 2, query_role: 4); verify read-only Neptune grant on QueryRole
  **role-scoped and allow-union-aware**: the union of neptune-db actions across *every*
  policy attached to `query_role` is exactly `{connect, ReadDataViaQuery}` (a whole-plan
  grep is wrong — IngestionTaskRole legitimately keeps Write/Delete);
  `terraform validate` exits 0.
**Approach:** Write `aws_iam_role_policy` resources (or one combined policy per role
  using `aws_iam_role_policy`). Use `data "aws_caller_identity" "current" {}` and
  locals for ARN construction. Key policies:
  - Neptune full-RW: actions = 4, resource = cluster resource ARN (IngestionTask + SmokeProbeRole in compute spec).
  - Neptune read-only: actions = 2 (connect + ReadDataViaQuery), resource = cluster resource ARN (QueryRole).
  - OpenSearch data: 5 es:ESHttp* actions scoped to domain ARN (all 3 roles).
  - Bedrock Titan: InvokeModel scoped to Titan foundation-model ARN (all 3 roles).
  - Bedrock synthesis: InvokeModel + Converse scoped to inference-profile ARN + 3 foundation-model ARNs (IngestionTask + QueryRole).
  - S3 read: GetObject + ListBucket (IngestionTask).
  - S3 PutObject x3: manifest.json, schema_extraction_trace.txt, silver/* (IngestionTask, separate statements).
  Note: SmokeProbeRole's Neptune grant is wired in the compute spec (the role is the Lambda execution role created there).
**Done when:** All policies in plan; QueryRole has no Write/Delete Neptune actions.

---

### T6: Update `outputs.tf` with data + IAM tier outputs

**Depends on:** T2, T3, T4
**Touches:** `apps/infra-tf/outputs.tf`
**Tests:** goal-based — `terraform validate` exits 0; outputs show Neptune endpoint +
  OpenSearch endpoint + S3 bucket name.
**Approach:** Fill in the data-layer output stubs:
  - `corpus_bucket_name = aws_s3_bucket.corpus.id`
  - `neptune_endpoint = "https://${aws_neptune_cluster.main.endpoint}:8182"`
  - `opensearch_endpoint = "https://${aws_opensearch_domain.graphrag_vectors.endpoint}"`
**Done when:** 3 data outputs filled; `terraform validate` exits 0.

---

### T7: Run `terraform fmt -check` + plan-level IAM security verification

**Depends on:** T5, T6
**Touches:** none (verification only)
**Tests:** goal-based — `terraform fmt -check` exits 0; plan JSON check confirms
  no `Resource: "*"` on data-plane actions; QueryRole has only 2 Neptune actions.
**Approach:** Run `terraform fmt -recursive apps/infra-tf/`. Run plan. Parse plan JSON
  to verify the load-bearing IAM invariants: (1) no wildcard resource on data-plane
  actions; (2) QueryRole Neptune actions = `{connect, ReadDataViaQuery}` only;
  (3) OpenSearch access policy: exactly 2 named principals, no AllPrincipals, no account-root.
**Done when:** fmt exits 0; all 3 IAM invariants confirmed in plan JSON.

## Rollout

This tier depends on the network tier (`infra-terraform-network`). In a live deploy,
both tiers are applied via a single `terraform apply` that respects the dependency
graph. The first live apply cycle happens in `infra-terraform-compute`.

Neptune clusters take 5–15 minutes to become available after `apply`; OpenSearch
domains take 10–30 minutes. The smoke probe validation (in `infra-terraform-compute`)
accounts for these warm-up delays.

**Teardown-stall convergence (fixed `domain_name`).** The OpenSearch `domain_name` is
pinned to `graphrag-vectors` (mandated for the self-reference-free ARN), so — unlike the
`name_prefix`/`bucket_prefix` resources — a re-apply cannot dodge a name collision. If a
`terraform destroy` is interrupted mid-delete (the documented teardown-stall failure
mode: the client dies during the 10–30 min OpenSearch delete window), the next `apply`
hits `ResourceAlreadyExistsException` on the fixed name — a non-convergent collision. The
live apply cycle (tier-4) must **poll domain deletion to completion** (or run a pre-apply
existence check) before re-applying, so a stalled teardown converges rather than collides.

**Rollback path.** This tier ships plan-phase only (AC9); the first live `apply` and its
recovery path are owned by `infra-terraform-compute` (tier-4) `## Rollout` — the
known-good rollback is `terraform destroy` of the combined graph (all resources here set
`skip_final_snapshot`/`force_destroy` and no `prevent_destroy`, so destroy is clean) or a
re-apply of the prior tagged config. Named here so the recovery path is not silently
dropped between tiers.

## Risks

- **Neptune `serverless_v2_scaling_configuration` argument name:** The AWS provider
  may use `serverless_v2_scaling_configuration` or a different argument name across
  versions. The contract-acquisition gate (EXECUTE phase) must verify the exact
  argument against `terraform providers schema -json` before emitting HCL.
- **OpenSearch `access_policies` for VPC domains:** for VPC-resident domains, the
  access policy must be provided inline in `aws_opensearch_domain.access_policies`
  (not as a separate `aws_opensearch_domain_policy` resource, which is deprecated
  for VPC domains in provider >= 5.x). Verify during implementation.
- **Neptune cluster resource ID in policy ARN:** The cluster resource ID
  (`aws_neptune_cluster.main.cluster_resource_id`) is a computed attribute not known
  until apply. The IAM policy ARN using it will show `(known after apply)` in the
  plan — this is expected behavior; the plan-assertion test must account for this
  (test the action list rather than the full ARN).
- **Plan-time-unknown ARNs defeat `planned_values` positive assertions — tier-5 must
  read the `configuration` block.** Because the roles use `name_prefix` and the bucket
  uses `bucket_prefix`, `aws_iam_role.*.arn` and `aws_s3_bucket.corpus.arn` are
  known-after-apply. `jsonencode()` over any unknown input collapses the *whole* policy
  string to `(known after apply)` in `terraform plan -json` `planned_values`. This
  affects **three** load-bearing positive assertions: AC4 (OpenSearch `access_policies`
  names the 2 role ARNs), AC7 (S3 PutObject key/prefix suffixes `manifest.json` /
  `schema_extraction_trace.txt` / `silver/*`), and the AC6 Neptune ARN scope. The
  `infra-terraform-verification` (tier-5) suite must assert these against the plan's
  **`configuration.root_module.resources[*].expressions`** (the unresolved reference
  expressions + literal jsonencode templates), NOT `planned_values` — reading
  `planned_values` yields an unknown and a naive assertion silently skips (a skipped
  security assertion is theatre). Keeping `name_prefix`/`bucket_prefix` (vs fixed names
  that would render in `planned_values`) is deliberate: fixed names worsen the
  teardown-stall re-apply collision surface (see Rollout).

## Changelog

- 2026-07-22 — Plan authored for infra-terraform-data-and-iam spec. Seven tasks:
  IAM roles, S3, Neptune, OpenSearch, IAM inline policies, outputs, fmt + security
  verification. Authoring order accounts for Terraform dependency resolution
  (roles first → OpenSearch → Neptune → IAM policies).
- 2026-07-23 — Pre-EXECUTE review amendments: T3 adds the
  `neptune_cluster_parameter_group_name` cluster wiring (ADR-0004 backstop); T4
  corrected to 2 access-policy principals (CDK fidelity); T5 `Depends on` corrected to
  T3 only (T4 is authoring-order — the domain ARN is a constructed string) and the
  read-only assertion reworded to role-scoped/allow-union-aware.
