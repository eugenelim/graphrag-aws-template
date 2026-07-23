# Spec: infra-terraform-data-and-iam

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0010](../../adr/0010-terraform-migration.md) (Terraform migration); [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (teardown-first, VPC-resident data stores); [ADR-0004](../../adr/0004-text2cypher-read-only-guard.md) (query role Neptune grant is read-only — IAM-layer backstop for LLM-authored openCypher); spec [`infra-terraform-network`](../infra-terraform-network/spec.md) (network tier this spec depends on); `apps/infra/stacks/graphrag_stack.py` `_corpus_bucket()`, `_neptune()`, `_opensearch()`, and all IAM methods (source of truth)
- **Shape:** data (data stores + IAM; no application logic)

> **Spec contract:** this document defines "done" for the data stores and IAM tier
> of the Terraform migration. The load-bearing security invariants are: no wildcard
> IAM resource (except `ecr:GetAuthorizationToken`, which belongs to the compute-tier
> Fargate execution role — no role in *this* tier uses it); Neptune read-only grant on
> the query role (ADR-0004); OpenSearch access policy names exactly 2 role ARNs
> (IngestionTaskRole, VectorProbeRole), never AllPrincipals and never account-root.

> **Data + IAM tier** — S3 corpus bucket, Neptune Serverless cluster, OpenSearch
> domain, and all IAM roles + policies — translated from the CDK stack to
> `apps/infra-tf/{s3,neptune,opensearch,iam}.tf`. The CDK's circular dependency
> (roles needed for OpenSearch access policy, which is created before role policies
> referencing the domain ARN) is resolved naturally by Terraform's dependency graph:
> roles first → OpenSearch domain (with `access_policies` referencing role ARNs) →
> Neptune cluster → IAM inline policies (referencing domain + cluster ARNs).

## Objective

Provision the data stores and IAM roles + policies so the compute tier (spec
`infra-terraform-compute`) can reference live resource ARNs and endpoints. The
deliverables are `s3.tf`, `neptune.tf`, `opensearch.tf`, and `iam.tf`. The
combined plan is verified by the plan-assertion suite in `infra-terraform-verification`.

The load-bearing engineering points: (1) Neptune must be Serverless (min 1.0 NCU, max
2.5 NCU) with IAM auth enabled, the query-timeout parameter group, and storage
encrypted; (2) OpenSearch access policy names only the 2 role ARNs, never
`Principal: "*"`; (3) query role Neptune grant is read-only (connect +
ReadDataViaQuery only, never Write/Delete).

## Boundaries

### Always do

- **S3: `force_destroy = true`, full public block, SSE S3, TLS-deny bucket policy.**
  The CDK `auto_delete_objects=True` translates to `force_destroy = true` on
  `aws_s3_bucket`. The TLS-deny policy is a separate `aws_s3_bucket_policy` with a
  `Deny` statement on `aws:SecureTransport = false`.
- **Neptune: exact engine version `1.3.5.0`, parameter group family `neptune1.3`,
  `neptune_query_timeout = 20000`.** These are pinned per ADR-0004 — the engine
  read-cost backstop that kills a runaway model-authored traversal.
  `auto_minor_version_upgrade = false` on the cluster instance is intentional hardening
  beyond the CDK default (`true`): the fresh-deploy posture has no upgrade path, and a
  silent minor-version bump could break the pinned parameter-group family.
- **Neptune: Serverless configuration `min_capacity = 1.0`, `max_capacity = 2.5`.**
  `aws_neptune_cluster` uses `serverless_v2_scaling_configuration` block; instance
  class is `db.serverless`.
- **Neptune: `iam_database_authentication_enabled = true`, `storage_encrypted = true`.**
- **OpenSearch domain name is exactly `"graphrag-vectors"`.** The name is fixed so
  the ARN is computable as `arn:aws:es:<region>:<account>:domain/graphrag-vectors/*`
  without a self-reference (avoids a Terraform dependency cycle in `access_policies`).
- **OpenSearch access policy: exactly 2 named principals (IngestionTaskRole,
  VectorProbeRole), scoped to `domain/graphrag-vectors/*`.**
  Never `Principal: "*"`, never account-root (`arn:aws:iam::<account>:root`). The
  `access_policies` argument takes a JSON document; the two role ARNs are referenced
  via `aws_iam_role.<name>.arn`. This matches the CDK source of truth
  (`graphrag_stack.py:363` passes `[task_role, vector_probe_role]`). **QueryRole is
  intentionally NOT in the resource policy** — it reaches OpenSearch via its own
  identity policy (the `es:ESHttp*` grant in AC7), relying on same-account IAM
  allow-union, exactly as the deployed CDK stack does. Placing it in the resource
  policy would be an unreviewed posture change with no functional need.
- **Query role Neptune grant is read-only: `neptune-db:connect` +
  `neptune-db:ReadDataViaQuery` only.** Never `WriteDataViaQuery` or
  `DeleteDataViaQuery` on the QueryRole. This is the ADR-0004 IAM-layer backstop —
  the only guarantee that a write is impossible regardless of the app-layer validator.
- **IngestionTaskRole retains the full Neptune read-write grant** (`connect`,
  `ReadDataViaQuery`, `WriteDataViaQuery`, `DeleteDataViaQuery`). The SmokeProbeRole
  also legitimately writes, but that role and its Neptune grant are created wholly in
  the compute tier (`infra-terraform-compute`) — this tier defines only the ingestion
  role's full R/W (see AC6).
- **Bedrock InvokeModel scoped to Titan v2 only.** ARN:
  `arn:aws:bedrock:<region>::foundation-model/amazon.titan-embed-text-v2:0`. No wildcard.
- **Bedrock InvokeModel + Converse scoped to the synthesis model.** Two ARN groups:
  (1) the `us.anthropic.claude-sonnet-4-6` inference-profile ARN (account+region-qualified);
  (2) `anthropic.claude-sonnet-4-6` foundation-model ARNs in `us-east-1`, `us-east-2`,
  `us-west-2` (no account ID). Never a wildcard resource.
- **S3 PutObject grants are key/prefix-scoped.** Three separate grants: `manifest.json`
  (exact key), `schema_extraction_trace.txt` (exact key), `silver/*` (prefix, not
  bucket-wide). Never `s3:PutObject` on the bucket ARN with no key suffix.
- **All IAM policy statements use least-privilege resources.** Neptune data actions
  scoped to the cluster resource ID ARN (not `*`). OpenSearch actions scoped to
  `domain/graphrag-vectors/*` (not `*`). Bedrock actions scoped to model ARNs (not `*`).
  The single exception: `ecr:GetAuthorizationToken` legitimately requires `Resource: "*"`.
- **teardown-first: `prevent_destroy = false` on all stateful resources.** Neptune
  cluster, OpenSearch domain, and S3 bucket must be destroyable by `terraform destroy`.

### Ask first

- Changing Neptune engine version or parameter group family (pinned per ADR-0004;
  a version change requires a matching family update).
- Changing the OpenSearch instance type, storage size, or engine version.
- Adding a new Bedrock model grant (a new model must be explicitly added, not
  covered by a wildcard).

### Never do

- **Never `Principal: "*"` (nor account-root `arn:aws:iam::<account>:root`) in the
  OpenSearch access policy** — the resource-side IAM enforcement requires named
  principals; a VPC network path alone is not sufficient, and account-root would
  delegate to any in-account role holding the `es:ESHttp*` identity grant.
- **Never add a new Terraform provider or module dependency in this tier** — the four
  `.tf` files (`s3.tf`, `neptune.tf`, `opensearch.tf`, `iam.tf`) land in the existing
  `apps/infra-tf/` root module under the already-pinned `hashicorp/aws ~> 5.0`
  provider; no new provider, module, or top-level directory.
- **Never `WriteDataViaQuery` or `DeleteDataViaQuery` on QueryRole** — ADR-0004 hard
  rule.
- **Never `Resource: "*"` on any data-plane action** (Neptune-db, S3, es:ESHttp*,
  bedrock:InvokeModel, bedrock:Converse) — the plan-assertion test enforces this.
- **Never `bucket_key_enabled` alone for TLS** — a bucket policy Deny statement on
  `aws:SecureTransport = false` is the required control (matches CDK `enforce_ssl=True`).

## Testing Strategy

- **AC1–AC8 — goal-based check.** Verified by `terraform plan -json` output. The
  full assertion suite is in `infra-terraform-verification`; this spec's gate is
  `terraform validate` + `terraform fmt -check` + targeted plan JSON checks for the
  load-bearing security invariants (no wildcard IAM, query role read-only, OpenSearch
  no AllPrincipals).
- **AC9 — infra/deploy (plan phase).** `terraform plan` completes without error;
  plan JSON shows the resource set.
- **Live verification (not a checklist AC in this spec).** The live data-plane check —
  Neptune accessible, OpenSearch accessible, S3 bucket reachable from the VPC, IAM
  grants correctly scoped (confirmed via smoke Lambda invocation) — is owned by the
  **combined live AC in `infra-terraform-compute`** (tier 4), which applies the full
  network + data + compute graph in one cycle. It is deliberately not a checklist AC
  here, so this spec ships on the plan-phase gate (AC9) without a dangling deferral.

Gates: `terraform fmt -check`, `terraform validate`, plan JSON IAM no-wildcard check.

- **Scanner-coverage carve-out (explicit, not silent).** The CDK stack had a hard
  cdk-nag gate enforcing per-service secure-config baselines (S3, Neptune, OpenSearch,
  IAM4/IAM5). No Terraform IaC policy-as-code scanner (Checkov / tfsec-Trivy) gates
  `apps/infra-tf/` yet — that gap is tracked in `workspace.toml [backlog].open` as
  `infra-terraform-scanner-ci` (deferred from the network tier, SEC-3), to be owned by
  the verification tier's CI wiring. The three bespoke plan-JSON assertions here
  (no-wildcard, QueryRole read-only, OpenSearch named-principals) verify *this tier's
  load-bearing invariants* — they do **not** substitute for per-service baseline
  coverage. That coverage regression is accepted for now and closes when the scanner
  lands, not silently ignored.

## Acceptance Criteria

- [x] **AC1 — S3 corpus bucket: `force_destroy`, full public block, SSE S3, TLS-deny
  policy.** *(goal-based check)* `aws_s3_bucket` with `force_destroy = true`.
  `aws_s3_bucket_public_access_block` with all four `block_public_*` fields `true`.
  `aws_s3_bucket_server_side_encryption_configuration` with `rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }`.
  `aws_s3_bucket_policy` with a Deny statement on `aws:SecureTransport = false`
  (matching CDK `enforce_ssl=True`).

- [x] **AC2 — Neptune: subnet group (≥2 subnets), pinned parameter group, Serverless
  cluster, `db.serverless` instance.** *(goal-based check)*
  `aws_neptune_subnet_group.main` referencing both private subnet IDs.
  `aws_neptune_cluster_parameter_group` with `family = "neptune1.3"` and
  `neptune_query_timeout = "20000"`.
  `aws_neptune_cluster.main` with `engine_version = "1.3.5.0"`,
  `vpc_security_group_ids = [aws_security_group.neptune_sg.id]`,
  `neptune_subnet_group_name = aws_neptune_subnet_group.main.name`,
  **`neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.main.name`**
  (load-bearing — without this the cluster uses the default parameter group and the
  ADR-0004 20 s query-timeout backstop is inert at the 120 s default; the CDK wires it
  at `graphrag_stack.py:521`),
  `iam_database_authentication_enabled = true`,
  `storage_encrypted = true`, `skip_final_snapshot = true`,
  `serverless_v2_scaling_configuration { min_capacity = 1.0 max_capacity = 2.5 }`.
  `aws_neptune_cluster_instance` with `instance_class = "db.serverless"` and
  `auto_minor_version_upgrade = false` (intentional hardening — CDK default is `true`).

- [x] **AC3 — OpenSearch domain: `graphrag-vectors`, single node, encrypted, VPC-resident.** *(goal-based check)*
  `aws_opensearch_domain` with `domain_name = "graphrag-vectors"`,
  `engine_version = "OpenSearch_2.11"`,
  `cluster_config { instance_count = 1 instance_type = "t3.small.search" zone_awareness_enabled = false }`,
  `ebs_options { ebs_enabled = true volume_size = 10 volume_type = "gp3" }`,
  `encrypt_at_rest { enabled = true }`, `node_to_node_encryption { enabled = true }`,
  `domain_endpoint_options { enforce_https = true }`,
  `vpc_options { subnet_ids = [one subnet] security_group_ids = [opensearch_sg] }`.

- [x] **AC4 — OpenSearch access policy names exactly 2 role ARNs, never AllPrincipals
  or account-root.** *(goal-based check)*
  The `access_policies` attribute of `aws_opensearch_domain.graphrag_vectors` contains a
  JSON policy with `Effect: "Allow"`, principals listing **exactly**
  `aws_iam_role.ingestion_task_role.arn` and `aws_iam_role.vector_probe_role.arn`
  (matching the CDK's 2-principal policy), actions `["es:ESHttp*"]`, resource
  `"arn:aws:es:<region>:<account>:domain/graphrag-vectors/*"`. **QueryRole is not a
  principal here** — it reaches OpenSearch via its identity policy (AC7). The
  assertion must **positively** match the 2 expected ARNs (not merely check for
  absence of `*`): no `Principal: "*"` **and** no account-root
  `arn:aws:iam::<account>:root` (either would broaden the resource-side gate). Scope
  the assertion to the OpenSearch domain's `access_policies` attribute only — the S3
  TLS-deny policy legitimately uses `Principal: "*"`, so a whole-plan scan is wrong.

- [x] **AC5 — 3 IAM roles with correct trust policies; managed policy attachments for Lambda VPC.** *(goal-based check)*
  `aws_iam_role.ingestion_task_role` (trust: `ecs-tasks.amazonaws.com`).
  `aws_iam_role.vector_probe_role` (trust: `lambda.amazonaws.com`).
  `aws_iam_role.query_role` (trust: `lambda.amazonaws.com`).
  `aws_iam_role_policy_attachment` for `AWSLambdaVPCAccessExecutionRole` on
  `vector_probe_role` and `query_role` (the Lambda VPC ENI lifecycle managed policy).

- [x] **AC6 — Neptune IAM grants: full R/W for ingestion; read-only for query.** *(goal-based check)*
  IngestionTaskRole inline policy: `["neptune-db:connect", "neptune-db:ReadDataViaQuery",
  "neptune-db:WriteDataViaQuery", "neptune-db:DeleteDataViaQuery"]` scoped to
  `aws_neptune_cluster.main.cluster_resource_id` ARN. QueryRole: the **union of Neptune
  actions across *every* policy attached to `query_role`** is exactly
  `{neptune-db:connect, neptune-db:ReadDataViaQuery}` — never `WriteDataViaQuery` or
  `DeleteDataViaQuery`. IAM is allow-union, so the assertion must be **role-scoped**
  (enumerate all of `query_role`'s policies) and confirm no attached statement adds a
  write action — a whole-plan `grep` is wrong because IngestionTaskRole *legitimately*
  keeps `WriteDataViaQuery`/`DeleteDataViaQuery` (ADR-0004 constrains only the query
  role). **SmokeProbeRole is created wholly in the `infra-terraform-compute` spec** (CDK
  auto-generates the Lambda execution role; in Terraform it is explicit). This tier does
  not define `smoke_probe_role`.

- [x] **AC7 — OpenSearch, Bedrock, S3 grants: scoped, no wildcard resource.** *(goal-based check)*
  All roles with OpenSearch access: `["es:ESHttpGet", "es:ESHttpPut", "es:ESHttpPost",
  "es:ESHttpDelete", "es:ESHttpHead"]` scoped to `arn:aws:es:<region>:<account>:domain/graphrag-vectors/*`.
  Bedrock InvokeModel: scoped to Titan v2 ARN only on all roles that embed.
  Bedrock InvokeModel + Converse (synthesis): scoped to inference-profile ARN +
  3 regional foundation-model ARNs on IngestionTaskRole and QueryRole.
  S3: IngestionTaskRole gets `s3:GetObject` on `<bucket>/*` + `s3:ListBucket` on the
  bucket ARN (read; matches CDK `bucket.grant_read`), `s3:PutObject` scoped to
  `manifest.json`, `s3:PutObject` scoped to `schema_extraction_trace.txt`,
  `s3:PutObject` scoped to `silver/*` — separate statements, never bucket-wide.
  No `Resource: "*"` on any data-plane action.

- [x] **AC8 — No wildcard resource IAM grants.** *(goal-based check)*
  Scanning the plan JSON for all IAM policy documents in this tier: no statement with
  both a data-plane action (neptune-db:*, s3:Get*, s3:Put*, es:ESHttp*, bedrock:*) and
  a literal `Resource: "*"`. Note the assertion is (a) the action list is correctly
  scoped **and** (b) no literal `"*"` resource — because the Neptune ARN interpolates
  `aws_neptune_cluster.main.cluster_resource_id` (known-after-apply), so at plan time
  its `Resource` renders as an unknown value, not `"*"`; account-id and region are
  plan-known. **No role in this tier uses `ecr:GetAuthorizationToken`** — that wildcard
  belongs to the compute-tier Fargate *execution* role (`infra-terraform-compute`), so
  this tier's plan JSON should contain **no** wildcard-resource statement at all.

- [x] **AC9 — `terraform plan` for the data+IAM tier completes without error, and the
  data-tier outputs are wired to resource attributes.** *(goal-based check)*
  With the network tier applied (or mocked), `terraform plan` exits 0 and shows the
  complete resource set for this spec. The three data-tier outputs resolve to their
  resource attributes (not the `null` stubs in `outputs.tf`):
  `corpus_bucket_name = aws_s3_bucket.corpus.id`,
  `neptune_endpoint = "https://${aws_neptune_cluster.main.endpoint}:8182"`,
  `opensearch_endpoint = "https://${aws_opensearch_domain.graphrag_vectors.endpoint}"`.
  The compute tier and verification suite reference these outputs, so leaving them
  `null` is a cross-tier break.

## Assumptions

- Technical: `aws_neptune_cluster` supports `serverless_v2_scaling_configuration` in
  AWS provider ~> 5.0 (source: contract-acquisition against `terraform providers schema -json`
  during implementation).
- Technical: `aws_opensearch_domain` `access_policies` supports an inline JSON policy
  string referencing `aws_iam_role.X.arn` (computed at plan time); no circular
  dependency because the roles have no initial policies that reference the domain
  (policies are added as separate `aws_iam_role_policy` resources after the domain
  is created).
- Technical: the cluster resource ID ARN for Neptune IAM auth is
  `arn:aws:neptune-db:<region>:<account>:<cluster-resource-id>/*` — the cluster
  resource ID is available as `aws_neptune_cluster.main.cluster_resource_id` output
  from the provider (source: contract-acquisition).
- Technical: `aws_s3_bucket` `force_destroy = true` empties the bucket on destroy
  without requiring a lifecycle rule; this matches the CDK `auto_delete_objects=True`
  observable behavior (the mechanism differs; the result is the same —
  `terraform destroy` leaves no bucket).
- Technical: the network tier spec (`infra-terraform-network`) is complete and the
  `aws_subnet.private[*].id` and security group IDs are available as outputs
  (source: workspace.toml dependency chain).
- Technical: **load-bearing resource address contract** — the compute spec and
  verification suite reference these Terraform resource addresses by name; the
  implementation must use exactly these addresses:
  `aws_s3_bucket.corpus`, `aws_neptune_cluster.main`, `aws_neptune_subnet_group.main`,
  `aws_opensearch_domain.graphrag_vectors`. Renaming any of these breaks cross-tier
  references without a compile-time error.
- Process: the `infra-terraform-verification` spec's plan-assertion test is the
  authoritative gate for the load-bearing security invariants; this spec's own
  tests are sufficient for implementation confidence but the full suite runs in
  the verification spec.

## Changelog

- 2026-07-22 — Spec authored. Data + IAM tier: S3, Neptune Serverless, OpenSearch,
  3 IAM roles + all policies. Load-bearing invariants: no wildcard IAM, Neptune
  read-only on QueryRole (ADR-0004), OpenSearch no AllPrincipals. Depends on
  infra-terraform-network.
- 2026-07-23 — Pre-EXECUTE review amendments (adversarial + security-reviewer).
  **AC4: OpenSearch access policy corrected 3→2 principals** to match the CDK source of
  truth (`graphrag_stack.py:363` names only IngestionTaskRole + VectorProbeRole;
  QueryRole reaches OpenSearch via its identity policy, same-account allow-union) — and
  now asserts the 2 ARNs positively and rejects account-root. **AC2: added the
  load-bearing `neptune_cluster_parameter_group_name` wiring** on the cluster (without
  it the ADR-0004 query-timeout backstop is inert). AC6 reworded to a role-scoped,
  allow-union-aware read-only assertion (IngestionTaskRole legitimately keeps Write).
  AC7 adds `s3:ListBucket`. AC8 clarified (action-list + no-literal-`*`; no `ecr`
  wildcard in this tier). AC9 adds the data-outputs-wired clause. Added a structural
  "Never do" rail and an explicit scanner-coverage carve-out
  (`infra-terraform-scanner-ci` tracked).
