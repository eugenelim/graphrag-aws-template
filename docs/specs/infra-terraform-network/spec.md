# Spec: infra-terraform-network

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
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
> every compute SG) is preserved byte-for-byte.

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
- **S3 gateway endpoint egress via `var.s3_prefix_list_id`.** The CDK
  `ec2.Peer.prefix_list(self._s3_prefix_list_id)` translates to an
  `aws_vpc_security_group_egress_rule` with `prefix_list_id = var.s3_prefix_list_id`.
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
  uses the AWS-managed prefix list, not a CIDR — a CIDR would be rejected by the
  `var.s3_prefix_list_id` validation but is explicitly disallowed here too.

## Testing Strategy

- **AC1–AC5 — goal-based check.** Verified by `terraform plan -json` output showing
  the correct resource types and counts. The plan-assertion test suite (spec
  `infra-terraform-verification`) provides the full assertion coverage; this spec's
  own gate is `terraform validate` + `terraform fmt -check` + a count check on the
  plan JSON.
- **AC6 — infra/deploy (plan phase).** `terraform plan -out=tfplan && terraform show
  -json tfplan` shows: 1 VPC, 2 subnets, 0 NAT gateways, 1 internet gateway absent,
  6 VPC endpoints, 6 security groups, correct egress rule counts.
- **AC7 — infra/deploy (live).** *(Deferred to `infra-terraform-verification` live AC
  or to a combined live cycle with `infra-terraform-compute`.)* VPC endpoints
  are accessible from an in-VPC Lambda; closed-egress SGs are confirmed by the smoke
  probe passing.

Gates: `terraform fmt -check`, `terraform validate`, plan JSON resource count check.

## Acceptance Criteria

- [ ] **AC1 — VPC: 2 private isolated subnets, no NAT, no IGW.** *(goal-based check)*
  `aws_vpc` resource with 1 instance. Two `aws_subnet` resources in distinct AZs,
  `map_public_ip_on_launch = false`. No `aws_nat_gateway`, no `aws_internet_gateway`.
  Subnet CIDRs are `/24` (matching CDK's `cidr_mask=24`).

- [ ] **AC2 — 6 VPC endpoints: 1 gateway S3, 5 interface.** *(goal-based check)*
  `aws_vpc_endpoint` resource count = 6. One with `vpc_endpoint_type = "Gateway"` for
  S3. Five with `vpc_endpoint_type = "Interface"`: ECR API (`ecr.api`), ECR Docker
  (`ecr.dkr`), CloudWatch Logs (`logs`), STS (`sts`), Bedrock Runtime
  (`bedrock-runtime`). Each interface endpoint is associated with the private subnets
  and the correct security group for inbound 443.

- [ ] **AC3 — 6 security groups, all `egress = []` by default.** *(goal-based check)*
  `aws_security_group` resources: `neptune_sg`, `opensearch_sg`,
  `ingestion_task_sg`, `smoke_probe_sg`, `vector_smoke_sg`, `query_lambda_sg`. Each
  has no inline `egress` block (or `egress = []` explicitly) — no
  `0.0.0.0/0`/`-1` allow-all rule. Descriptions use EC2-valid ASCII charset.

- [ ] **AC4 — Closed-egress rules match exact `_COMPUTE_SG_EGRESS` table.** *(goal-based
  check)* `aws_vpc_security_group_egress_rule` resources match the CDK per-SG egress
  table exactly (set equality, not subset):
  - `ingestion_task_sg`: 8 rules (Neptune 8182, OpenSearch 443, Bedrock 443, EcrApi 443, EcrDocker 443, Logs 443, Sts 443, S3 prefix-list 443).
  - `smoke_probe_sg`: 3 rules (Neptune 8182, Logs 443, Sts 443).
  - `vector_smoke_sg`: 4 rules (OpenSearch 443, Bedrock 443, Logs 443, Sts 443).
  - `query_lambda_sg`: 5 rules (Neptune 8182, OpenSearch 443, Bedrock 443, Logs 443, Sts 443).

- [ ] **AC5 — No public ingress on any security group.** *(goal-based check)*
  No `aws_vpc_security_group_ingress_rule` resource has `cidr_ipv4 = "0.0.0.0/0"` or
  `cidr_ipv6 = "::/0"`. All ingress rules reference security group IDs (peer SG rules:
  Neptune from compute SGs on 8182; OpenSearch from compute SGs on 443; VPC endpoint
  SGs accept 443 from the VPC CIDR).

- [ ] **AC6 — Store-SG ingress rules: exact peer-SG set for Neptune and OpenSearch.** *(goal-based check)*
  `aws_vpc_security_group_ingress_rule` resources for store SGs match exactly:
  - Neptune SG accepts port 8182 from: `ingestion_task_sg`, `smoke_probe_sg`, `query_lambda_sg` (3 rules).
  - OpenSearch SG accepts port 443 from: `ingestion_task_sg`, `vector_smoke_sg`, `query_lambda_sg` (3 rules).
  No other ingress rule references Neptune or OpenSearch SGs. (Source: CDK
  `neptune_sg.add_ingress_rule(task_sg, ...)` / `opensearch_sg.add_ingress_rule(sg, ...)` calls.)

- [ ] **AC7 — `terraform plan` shows correct resource counts for the named compute/store resources.** *(goal-based
  check)* `terraform plan -json` output confirms:
  `aws_vpc` = 1, `aws_subnet` = 2, `aws_nat_gateway` = 0 (absent from plan),
  `aws_vpc_endpoint` = 6. The 6 named compute/store security groups (`neptune_sg`,
  `opensearch_sg`, `ingestion_task_sg`, `smoke_probe_sg`, `vector_smoke_sg`,
  `query_lambda_sg`) all appear in the plan; endpoint SGs (one per interface endpoint,
  5 additional) bring the total `aws_security_group` count to 11.

## Assumptions

- Technical: the CDK VPC default CIDR is `10.0.0.0/16`; subnets are `/24` per AZ
  (source: CDK `SubnetConfiguration cidr_mask=24`).
- Technical: the 5 interface endpoint service names are resolvable in the target
  region (`us-east-1` default, overridable via `var.aws_region`); Bedrock Runtime is
  `com.amazonaws.<region>.bedrock-runtime` (source: CDK `InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME`).
- Technical: the `var.s3_prefix_list_id` variable (validated `^pl-[0-9a-f]+$` in the
  scaffold spec) resolves to the AWS-managed S3 gateway-endpoint prefix list for the
  target region (source: CDK `deploy.sh` `describe-managed-prefix-lists` resolution).
- Technical: the `infra-terraform-scaffold` spec is complete and `terraform init` succeeds
  before this spec's tasks begin (source: dependency ordering in workspace.toml backlog).
- Process: the CDK `_COMPUTE_SG_EGRESS` table in `test_stack.py` is the authoritative
  per-SG egress specification; the Terraform implementation must match it set-exactly
  (source: `apps/infra/tests/test_stack.py` `test_compute_sgs_egress_equals_exact_call_set`).

## Changelog

- 2026-07-22 — Spec authored. Network tier: VPC, subnets, VPC endpoints (6), security
  groups (6 with closed egress matching _COMPUTE_SG_EGRESS exactly). Goal-based ACs
  verified by terraform plan JSON. Depends on infra-terraform-scaffold.
