# Spec: infra-terraform-network

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0010](../../adr/0010-terraform-migration.md) (migrate to Terraform); [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (no-NAT, PRIVATE_ISOLATED, VPC-endpoint-only egress, teardown-first); spec [`infra-terraform-scaffold`](../infra-terraform-scaffold/spec.md) (foundation this spec builds on); `apps/infra/stacks/graphrag_stack.py` `_vpc()` + `_allow_egress()` + `_COMPUTE_SG_EGRESS` (source of truth for the exact topology being translated)
- **Shape:** data (network infrastructure; no application logic)

> **Spec contract:** this document defines "done" for the network tier of the
> Terraform migration. Every VPC resource and security group egress rule in the CDK
> stack has a named, testable counterpart here.

> **Network tier** — VPC, subnets, VPC endpoints, and security groups with exact
> closed-egress rules — translated from `apps/infra/stacks/graphrag_stack.py`
> to `apps/infra-tf/network.tf` + `apps/infra-tf/security_groups.tf`. The ADR-0002
> topology (no NAT, 2 private isolated subnets, 6 VPC endpoints, closed egress on
> every compute SG) is preserved as a **topology- and egress-equivalent** translation
> (two deliberate endpoint-SG translation choices are documented in the plan's Design
> section — VPC-CIDR endpoint ingress and `egress = []` on endpoint SGs).

## Objective

Provision the network tier in Terraform so that subsequent specs (data + IAM,
compute) have the VPC, subnets, security group IDs, and endpoint IDs they depend on.
The deliverables are `network.tf` and `security_groups.tf`. The output of this spec
is a `terraform plan` that matches the CDK synth's network resources exactly in
topology and egress semantics, verified by the plan-assertion suite authored in the
`infra-terraform-verification` spec.

The load-bearing security property is **closed egress**: every compute security
group denies all-outbound by default and allows only the exact port-and-destination
pairs the CDK `_COMPUTE_SG_EGRESS` table specifies. This is the defence-in-depth
layer that the CDK `allow_all_outbound=False` + explicit `add_egress_rule` calls
implement; in Terraform it translates to no default `0.0.0.0/0 -1` egress rules and
explicit `aws_vpc_security_group_egress_rule` resources.

## Boundaries

### Always do

- **Exactly 2 private isolated subnets across 2 AZs, no NAT gateways, no internet
  gateway.** Neptune requires a subnet group spanning ≥ 2 AZs; this is an AWS API
  requirement, not an HA choice (ADR-0002). No `aws_internet_gateway`, no
  `aws_nat_gateway`.
- **6 VPC endpoints: 1 gateway (S3) + 5 interface.** Gateway endpoint:
  `com.amazonaws.<region>.s3`. Interface endpoints: ECR API, ECR Docker, CloudWatch
  Logs, STS, Bedrock Runtime. These are the exact endpoints the CDK `_INTERFACE_ENDPOINTS`
  dict provisions; the Bedrock Runtime endpoint is required for in-VPC Lambda calls
  to Bedrock (no NAT path).
- **6 security groups, all with `egress = []` (no allow-all).** Neptune SG,
  OpenSearch SG, IngestionTask SG, SmokeProbe SG, VectorSmoke SG, QueryLambda SG.
  Each is created with no egress rules in the `aws_security_group` resource; egress
  rules are added as separate `aws_vpc_security_group_egress_rule` resources.
- **Exact closed-egress sets per the `_COMPUTE_SG_EGRESS` table.** Match the CDK
  `test_stack.py` `_COMPUTE_SG_EGRESS` dict exactly:
  - IngestionTask: Neptune 8182, OpenSearch 443, BedrockRuntime 443, EcrApi 443, EcrDocker 443, CloudWatchLogs 443, Sts 443, S3 prefix-list 443.
  - SmokeProbe: Neptune 8182, CloudWatchLogs 443, Sts 443.
  - VectorSmoke: OpenSearch 443, BedrockRuntime 443, CloudWatchLogs 443, Sts 443.
  - QueryLambda: Neptune 8182, OpenSearch 443, BedrockRuntime 443, CloudWatchLogs 443, Sts 443.
- **S3 gateway endpoint egress via a data-source-resolved managed prefix list.**
  The CDK `ec2.Peer.prefix_list(self._s3_prefix_list_id)` translates to an
  `aws_vpc_security_group_egress_rule` whose `prefix_list_id` is resolved from
  `data "aws_ec2_managed_prefix_list" "s3"` (name `com.amazonaws.<region>.s3`) —
  **not** an operator-supplied variable. This closes SEC-2: no wrong/wide
  customer-managed prefix list can be injected. It is the declarative equivalent
  of the CDK `deploy.sh` `describe-managed-prefix-lists` resolution
  (egress-equivalent, strictly safer; an intentional divergence from raw
  CfnParameter parity, motivated by the closed-egress security property).
- **Ingress rules for store SGs from compute SGs.** Neptune SG accepts 8182 from
  IngestionTask, SmokeProbe, and QueryLambda SGs. OpenSearch SG accepts 443 from
  IngestionTask, VectorSmoke, and QueryLambda SGs.
- **Security group descriptions use the EC2 charset.** The CDK test
  `_EC2_DESC` regex: `^[A-Za-z0-9 ._\-:/()#,@\[\]+=&;{}!$*]*$` — no em-dashes,
  no `>` character.

### Ask first

- Changing the VPC CIDR range (currently CDK default `10.0.0.0/16`, subnets `/24`).
- Adding a NAT gateway or internet gateway for any purpose.
- Adding a 7th VPC endpoint.
- Changing the Bedrock Runtime endpoint name or service string.

### Never do

- **Never allow 0.0.0.0/0 or ::/0 ingress on any security group.** The CDK
  `test_no_security_group_allows_public_ingress` assertion must pass on the
  equivalent plan assertion.
- **Never create a NAT gateway** — ADR-0002 hard rule; all egress via VPC endpoints.
- **Never reference a CIDR for the S3 endpoint egress.** The S3 gateway endpoint
  uses the AWS-managed prefix list (`data.aws_ec2_managed_prefix_list.s3.id`), not
  a CIDR. Because the id is resolved from the account by name rather than supplied,
  a wrong/wide value cannot be injected at all.

## Testing Strategy

This spec is **plan-only** — no `terraform apply`, no live smoke, no teardown. The
infra/deploy GATES layers exercised here are **static preflight** (`fmt`/`validate`)
and **plan/preview** (plan-JSON assertions); apply/smoke/rollback are deferred (AC10).

- **Offline tier (no AWS credentials).** `terraform fmt -check` and `terraform
  validate` — these evaluate syntax, types, and references without contacting AWS.
- **Credentials-required tier.** The plan-JSON count/set assertions (AC1–AC9) consume
  `terraform plan -json`, and the `data "aws_availability_zones"` source is read
  against the live API during plan; this tier therefore requires AWS credentials +
  region + STS. `terraform init -backend=false` first (S3 backend not needed for a
  local plan), then `terraform plan -out=tfplan`, then `terraform show -json tfplan`.
- **AC1–AC9 — goal-based check.** Verified by the plan JSON showing the correct
  resource types, counts, and egress/ingress sets. The plan-assertion test suite
  (spec `infra-terraform-verification`) provides the full assertion coverage; this
  spec's own gate is `terraform validate` + `terraform fmt -check` + the plan-JSON
  count/negative checks below.
- **AC10 — infra/deploy (live).** *(Deferred to `infra-terraform-verification` live AC
  or to a combined live cycle with `infra-terraform-compute`.)* VPC endpoints
  are accessible from an in-VPC Lambda; closed-egress SGs are confirmed by the smoke
  probe passing; the S3 gateway-endpoint route is confirmed by the corpus read.

Gates: `terraform fmt -check`, `terraform validate`, plan JSON resource count check,
plan JSON negative-CIDR check (no public ingress/egress).

## Acceptance Criteria

- [x] **AC1 — VPC: 2 private isolated subnets, no NAT, no IGW.** *(goal-based check)*
  `aws_vpc` resource with 1 instance. Two `aws_subnet` resources in distinct AZs,
  `map_public_ip_on_launch = false`. No `aws_nat_gateway`, no `aws_internet_gateway`.
  Subnet CIDRs are `/24` within `10.0.0.0/16` (matching CDK's `cidr_mask=24`
  allocation: `10.0.0.0/24`, `10.0.1.0/24`). Each subnet has an `aws_route_table` +
  `aws_route_table_association` (private-isolated: local routes only, no
  IGW/NAT route).

- [x] **AC2 — 6 VPC endpoints: 1 gateway S3, 5 interface.** *(goal-based check)*
  `aws_vpc_endpoint` resource count = 6. One with `vpc_endpoint_type = "Gateway"` for
  S3. Five with `vpc_endpoint_type = "Interface"`: ECR API (`ecr.api`), ECR Docker
  (`ecr.dkr`), CloudWatch Logs (`logs`), STS (`sts`), Bedrock Runtime
  (`bedrock-runtime`). Each interface endpoint is associated with the private subnets
  and the correct security group for inbound 443.

- [x] **AC3 — 6 security groups, all `egress = []` by default.** *(goal-based check)*
  `aws_security_group` resources: `neptune_sg`, `opensearch_sg`,
  `ingestion_task_sg`, `smoke_probe_sg`, `vector_smoke_sg`, `query_lambda_sg`. Each
  has no inline `egress` block (or `egress = []` explicitly) — no
  `0.0.0.0/0`/`-1` allow-all rule. Descriptions use EC2-valid ASCII charset.

- [x] **AC4 — Closed-egress rules match exact `_COMPUTE_SG_EGRESS` table.** *(goal-based
  check)* `aws_vpc_security_group_egress_rule` resources match the CDK per-SG egress
  table exactly (set equality, not subset):
  - `ingestion_task_sg`: 8 rules (Neptune 8182, OpenSearch 443, Bedrock 443, EcrApi 443, EcrDocker 443, Logs 443, Sts 443, S3 prefix-list 443).
  - `smoke_probe_sg`: 3 rules (Neptune 8182, Logs 443, Sts 443).
  - `vector_smoke_sg`: 4 rules (OpenSearch 443, Bedrock 443, Logs 443, Sts 443).
  - `query_lambda_sg`: 5 rules (Neptune 8182, OpenSearch 443, Bedrock 443, Logs 443, Sts 443).
  **Negative + total (self-contained, this spec's own gate):** total
  `aws_vpc_security_group_egress_rule` count = **20** (8+3+4+5); egress rules are
  owned **only** by the 4 compute SGs (the 2 store SGs and 5 endpoint SGs own
  **zero** egress rules); **no** egress rule has `cidr_ipv4 = "0.0.0.0/0"` or
  `cidr_ipv6 = "::/0"`; every egress rule targets a `referenced_security_group_id`
  or a `prefix_list_id` — **never a raw CIDR** (the S3 rule is the sole
  prefix-list target).

- [x] **AC5 — No public ingress on any security group.** *(goal-based check)*
  No `aws_vpc_security_group_ingress_rule` resource has `cidr_ipv4 = "0.0.0.0/0"` or
  `cidr_ipv6 = "::/0"`. Peer SG ingress rules reference security group IDs (Neptune
  from compute SGs on 8182; OpenSearch from compute SGs on 443); the only CIDR ingress
  is the VPC endpoint SGs accepting 443 from the **VPC CIDR** (`aws_vpc.main.cidr_block`,
  a private `/16` — never a public CIDR). The implementation declares **no inline
  `ingress`/`egress` blocks** on any `aws_security_group` (all rules are separate
  `aws_vpc_security_group_*_rule` resources, and each SG sets `egress = []`
  explicitly); the check therefore also holds for the inline-block form.

- [x] **AC6 — Store-SG ingress rules: exact peer-SG set for Neptune and OpenSearch.** *(goal-based check)*
  `aws_vpc_security_group_ingress_rule` resources for store SGs match exactly:
  - Neptune SG accepts port 8182 from: `ingestion_task_sg`, `smoke_probe_sg`, `query_lambda_sg` (3 rules).
  - OpenSearch SG accepts port 443 from: `ingestion_task_sg`, `vector_smoke_sg`, `query_lambda_sg` (3 rules).
  No other ingress rule references Neptune or OpenSearch SGs. (Source: CDK
  `neptune_sg.add_ingress_rule(task_sg, ...)` / `opensearch_sg.add_ingress_rule(sg, ...)` calls.)

- [x] **AC7 — `terraform plan` shows correct resource counts for the named compute/store resources.** *(goal-based
  check)* `terraform plan -json` output confirms:
  `aws_vpc` = 1, `aws_subnet` = 2, `aws_nat_gateway` = 0 (absent from plan),
  `aws_internet_gateway` = 0 (absent), `aws_vpc_endpoint` = 6, `aws_route_table` = 2,
  `aws_route_table_association` = 2. The 6 named compute/store security groups
  (`neptune_sg`, `opensearch_sg`, `ingestion_task_sg`, `smoke_probe_sg`,
  `vector_smoke_sg`, `query_lambda_sg`) all appear in the plan; endpoint SGs (one per
  interface endpoint, 5 additional) bring the total `aws_security_group` count to 11.

- [x] **AC8 — Interface-endpoint SGs: exactly 443-from-VPC-CIDR ingress.** *(goal-based
  check)* Each of the 5 interface-endpoint SGs has exactly one
  `aws_vpc_security_group_ingress_rule` on TCP 443 with `cidr_ipv4 =
  aws_vpc.main.cidr_block` (5 rules total). This is the deliberate translation of
  CDK's `open=True` interface-endpoint default (see plan Design); the effective
  outbound gate remains the compute SGs' closed egress. Store-SG ingress (AC6, 6
  rules) + endpoint-SG ingress (5 rules) = **11** `aws_vpc_security_group_ingress_rule`
  resources total; none has a public CIDR (AC5).

- [x] **AC9 — S3 gateway endpoint is route-table associated.** *(goal-based check)*
  The S3 gateway `aws_vpc_endpoint` sets `route_table_ids` to the two private-subnet
  route tables (`aws_route_table.private[*].id`), so the AWS-managed S3 prefix-list
  route is installed and the in-VPC corpus read actually routes (no-NAT S3 path,
  ADR-0002). Without this the endpoint plans clean but never routes.

- [x] **AC10 — Live reachability.** *(infra/deploy — live)* (deferred: terraform-compute-live-cycle)
  Deferred to the combined full-stack live cycle (backlog `terraform-compute-live-cycle`,
  run by the `infra-terraform-verification` tier). (Anchor repointed from the former
  `infra-terraform-verification` backlog slug when the 5 build tiers moved to the
  `["ini-001"]` initiative — the live-reachability checks below are confirmed by that
  cycle's smoke probe + corpus read.) VPC endpoints are reachable from an in-VPC Lambda;
  closed-egress SGs are confirmed by the smoke probe passing; the S3 gateway-endpoint
  route is confirmed by the corpus read succeeding with no NAT path.

## Assumptions

- Technical: the CDK VPC default CIDR is `10.0.0.0/16`; subnets are `/24` per AZ
  (source: CDK `SubnetConfiguration cidr_mask=24`).
- Technical: the 5 interface endpoint service names are resolvable in the target
  region (`us-east-1` default, overridable via `var.aws_region`); Bedrock Runtime is
  `com.amazonaws.<region>.bedrock-runtime` (source: CDK `InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME`).
- Technical: the AWS-managed S3 gateway-endpoint prefix list `com.amazonaws.<region>.s3`
  exists in the target region (it is created by default in every region) and is
  resolvable by `data "aws_ec2_managed_prefix_list" "s3"` at plan time (verified:
  resolves to `pl-63a5400a` in `us-east-1`).
- Technical: the `infra-terraform-scaffold` spec is complete and `terraform init` succeeds
  before this spec's tasks begin (source: dependency ordering in workspace.toml backlog).
- Process: the CDK `_COMPUTE_SG_EGRESS` table in `test_stack.py` is the authoritative
  per-SG egress specification; the Terraform implementation must match it set-exactly
  (source: `apps/infra/tests/test_stack.py` `test_compute_sgs_egress_equals_exact_call_set`).
- Security (SEC-2 RESOLVED — data-source resolution): the S3 egress target is
  resolved from the account via `data "aws_ec2_managed_prefix_list" "s3"` (name
  `com.amazonaws.<region>.s3`), **not** an operator-supplied value. This removed the
  former `var.s3_prefix_list_id` (a scaffold-spec variable): a format-valid but
  wrong/wide customer-managed prefix list can no longer be injected to widen the one
  egress hole closed egress exists to control. Intentional divergence from raw CDK
  `S3PrefixListId` CfnParameter parity — egress-equivalent (same AWS-managed S3 list
  the CDK `deploy.sh` resolves) and strictly safer. Cross-spec impact: the scaffold
  spec's `s3_prefix_list_id` variable + its CIDR-rejection AC are superseded (see
  that spec's changelog); the verification spec's plan invocation drops the `-var`.
- Security (scanner): no IaC policy-as-code scanner (Checkov/tfsec) is wired yet, so
  the per-provider secure-config depth rests on hand-written plan-JSON asserts here
  (`degraded: no scanner`). Acceptable for a plan-only spec; wiring a scanner into CI
  against `apps/infra-tf/` is owned by the sibling `infra-terraform-verification` spec
  (backlog `infra-terraform-scanner-ci`).

## Changelog

- 2026-07-22 — Spec authored. Network tier: VPC, subnets, VPC endpoints (6), security
  groups (6 with closed egress matching _COMPUTE_SG_EGRESS exactly). Goal-based ACs
  verified by terraform plan JSON. Depends on infra-terraform-scaffold.
- 2026-07-22 — SEC-2 hardening pulled into this PR (was deferred). S3 egress
  `prefix_list_id` now resolved via `data "aws_ec2_managed_prefix_list" "s3"` instead
  of the operator-supplied `var.s3_prefix_list_id` (removed). Boundaries, Never-do, and
  Assumptions updated; SEC-2 disposition flipped DEFER→RESOLVED. Cross-spec: scaffold
  variable/AC superseded, verification plan `-var` dropped, backlog item removed.
- 2026-07-22 — Pre-EXECUTE review amendments (work-loop full mode). AC1: route tables.
  AC4: negative-egress + total-count + only-compute-SGs-own-egress. AC5: inline-block
  coverage + VPC-CIDR ingress clarified. AC7: route-table + IGW-absent counts. Added
  AC8 (endpoint-SG ingress), AC9 (S3 gateway route-table association), AC10 (renamed
  live AC). Testing Strategy split into offline/creds tiers. Assumptions: s3_prefix_list
  parity rationale + scanner deferral. Dispositions: `notes/pre-execute-review-dispositions.md`.
