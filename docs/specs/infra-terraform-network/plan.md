# Plan: infra-terraform-network

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** implementation strategy for the network tier. May change as
> implementation proceeds; note substantial changes in the changelog.

## Approach

Write `network.tf` and `security_groups.tf` in `apps/infra-tf/`. The CDK stack's
`_vpc()` method and `_allow_egress()` helper are the authoritative translation source;
the CDK synth test's `_COMPUTE_SG_EGRESS` table is the per-SG egress specification.

Each CDK resource maps to one or more Terraform resources:
- `ec2.Vpc(nat_gateways=0)` → `aws_vpc` + `aws_subnet` x2 (no `aws_nat_gateway`,
  no `aws_internet_gateway`)
- `vpc.add_gateway_endpoint("S3Endpoint")` → `aws_vpc_endpoint` (Gateway)
- `vpc.add_interface_endpoint(name, service)` x5 → `aws_vpc_endpoint` x5 (Interface)
- `ec2.SecurityGroup(allow_all_outbound=False)` → `aws_security_group` with
  `egress = []`
- `sg.add_egress_rule(target, port)` → `aws_vpc_security_group_egress_rule`
- `sg.connections.allow_to(endpoint, port)` → `aws_vpc_security_group_egress_rule`
  (pointing at the endpoint's security group)
- `neptune_sg.add_ingress_rule(compute_sg, 8182)` → `aws_vpc_security_group_ingress_rule`

The riskiest part is getting the exact egress-rule set per SG correct. The CDK
`_COMPUTE_SG_EGRESS` table is 4 rows × varying sets; each set must match exactly
(set equality). The interface-endpoint SGs (one per endpoint) receive ingress from
the VPC CIDR (CDK's default for interface endpoints).

## Constraints

- ADR-0002: no NAT, no internet path, PRIVATE_ISOLATED subnets only.
- CDK `_COMPUTE_SG_EGRESS` table is the authoritative egress specification; the
  plan-assertion test in `infra-terraform-verification` enforces set equality.
- `var.s3_prefix_list_id` must be the `prefix_list_id` argument in the S3 egress
  rule — not a CIDR.
- EC2 security group description charset: `^[A-Za-z0-9 ._\-:/()#,@\[\]+=&;{}!$*]*$`.

## Design (LLD)

### Resource naming convention

Terraform resources use snake_case logical names matching the CDK construct ID
lowercased. Local outputs reference by Terraform resource address.

| CDK construct ID | Terraform resource |
|---|---|
| `Vpc` | `aws_vpc.main` |
| `Vpcp rivateSubnet1a` (CDK-generated) | `aws_subnet.private[0]`, `aws_subnet.private[1]` |
| `S3Endpoint` | `aws_vpc_endpoint.s3_gateway` |
| `EcrApi`, `EcrDocker`, `CloudWatchLogs`, `Sts`, `BedrockRuntime` | `aws_vpc_endpoint.{ecr_api,ecr_docker,cloudwatch_logs,sts,bedrock_runtime}` |
| `NeptuneSg` | `aws_security_group.neptune_sg` |
| `OpenSearchSg` / `VectorDomain...Sg` | `aws_security_group.opensearch_sg` |
| `IngestionSg` | `aws_security_group.ingestion_task_sg` |
| `SmokeSg` | `aws_security_group.smoke_probe_sg` |
| `VectorSmokeSg` | `aws_security_group.vector_smoke_sg` |
| `QuerySg` | `aws_security_group.query_lambda_sg` |

### AZ selection

CDK uses `max_azs=2`. Terraform must explicitly select 2 AZs. Use a `data
"aws_availability_zones"` data source filtered to `state = "available"` and slice
the first 2. Each `aws_subnet` references `data.aws_availability_zones.available.names[i]`.

### Endpoint-to-SG egress wiring

CDK's `sg.connections.allow_to(endpoint, port)` creates an egress rule on the
compute SG pointing at the endpoint's security group. In Terraform:
1. Each interface endpoint's `aws_vpc_endpoint` is created with a `security_group_ids`
   referencing a dedicated endpoint SG (one `aws_security_group` per endpoint, or
   share the VPC CIDR ingress rule).
2. The compute SG's egress rule references the endpoint SG's ID:
   `aws_vpc_security_group_egress_rule` with `referenced_security_group_id =
   aws_security_group.<endpoint_sg>.id`.

The simpler pattern (matches CDK behavior): give each interface endpoint its own SG
that accepts 443 from the VPC CIDR; compute SGs egress to each endpoint SG by ID.

**Deliberate translation choice (pre-EXECUTE review ADV-7/SEC-6).** CDK's
`sg.connections.allow_to(endpoint, 443)` renders as a *per-compute-SG* ingress rule on
each endpoint SG. The Terraform translation instead gives each endpoint SG a single
`443-from-VPC-CIDR` ingress rule (mirroring CDK's `open=True` interface-endpoint
default). This is fewer rules and is functionally equivalent for the closed posture:
the **effective outbound gate is the compute SGs' closed egress** (AC4) — the endpoint
SG ingress only bounds who *inside the VPC* may reach the endpoint, and the VPC is
private-isolated with no public path. Recorded here so it reads as an intentional
choice, not an omission of the "byte-for-byte" claim.

### Private-subnet route tables + S3 gateway association

CDK's `add_gateway_endpoint` auto-associates the PRIVATE_ISOLATED subnets' route
tables. Terraform does not auto-create per-subnet route tables, so this is explicit:
`aws_route_table.private` (`count = 2`, no IGW/NAT route — local only) +
`aws_route_table_association.private` (`count = 2`), and the S3 gateway
`aws_vpc_endpoint` sets `route_table_ids = aws_route_table.private[*].id`. Without this
the gateway endpoint plans clean but installs no S3 prefix-list route, so the no-NAT
corpus read would hang at apply/live (pre-EXECUTE review Blocker ADV-1).

### S3 gateway endpoint egress

```hcl
resource "aws_vpc_security_group_egress_rule" "ingestion_to_s3" {
  security_group_id = aws_security_group.ingestion_task_sg.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = var.s3_prefix_list_id
  description       = "IngestionSg egress to s3 prefix list 443"
}
```

## Tasks

### T1: Write `network.tf` — VPC + subnets + route tables

**Depends on:** none (scaffold spec complete)
**Touches:** `apps/infra-tf/network.tf`
**Tests:** goal-based — `terraform validate` exits 0; plan shows 1 VPC + 2 subnets +
  2 route tables + 2 associations + 0 nat gateways + 0 internet gateways; subnet
  CIDRs are in `10.0.0.0/16`.
**Approach:** Write `aws_vpc.main` (CIDR `10.0.0.0/16`, `enable_dns_hostnames = true`,
  `enable_dns_support = true`), `data "aws_availability_zones"` (filtered
  `state = "available"`), 2 `aws_subnet` resources via `count = 2`
  (`cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)` → `10.0.0.0/24`,
  `10.0.1.0/24` to match CDK's allocator, distinct AZs from
  `data.aws_availability_zones.available.names[count.index]`,
  `map_public_ip_on_launch = false`). Add `aws_route_table.private` (`count = 2`,
  no IGW/NAT route — local only) + `aws_route_table_association.private` (`count = 2`)
  so the S3 gateway endpoint (T2) has route tables to associate (pre-EXECUTE review
  Blocker ADV-1). No `aws_internet_gateway`, no `aws_nat_gateway`.
**Done when:** plan JSON has `aws_vpc`=1, `aws_subnet`=2, `aws_route_table`=2,
  `aws_route_table_association`=2, `aws_nat_gateway`=0, `aws_internet_gateway`=0.

---

### T2: Write `network.tf` — VPC endpoints

**Depends on:** T1
**Touches:** `apps/infra-tf/network.tf`
**Tests:** goal-based — plan shows 6 `aws_vpc_endpoint` resources; `terraform validate`
  exits 0.
**Approach:** Write 1 `aws_vpc_endpoint` (Gateway, S3, `route_table_ids =
  aws_route_table.private[*].id` — AC9/ADV-1) + 5 `aws_vpc_endpoint` (Interface,
  `for_each` over `local.interface_endpoints`) with `private_dns_enabled = true`.
  Service name for each: `"com.amazonaws.${var.aws_region}.${service}"` where service
  is `s3` (gateway), `ecr.api`, `ecr.dkr`, `logs`, `sts`, `bedrock-runtime`. Each
  interface endpoint references the private subnet IDs and its own endpoint security
  group (defined adjacent, per T3). Endpoint SGs + their 443-from-VPC-CIDR ingress
  rules live in `network.tf` next to the endpoints.
**Done when:** 6 VPC endpoint resources in the plan; `bedrock-runtime` endpoint present;
  S3 gateway endpoint has `route_table_ids` set.

---

### T3: Write `security_groups.tf` — 6 SGs with no egress

**Depends on:** T1
**Touches:** `apps/infra-tf/security_groups.tf`
**Tests:** goal-based — plan shows 6 `aws_security_group` resources; `grep -c
  '"0.0.0.0/0"' <(terraform show -json tfplan)` returns 0 in egress context;
  `terraform validate` exits 0.
**Approach:** Write 6 `aws_security_group` resources with `egress = []` explicitly.
  Descriptions use EC2-valid ASCII. Names: `neptune_sg`, `opensearch_sg`,
  `ingestion_task_sg`, `smoke_probe_sg`, `vector_smoke_sg`, `query_lambda_sg`.
  Endpoint SGs (one per interface endpoint, accepting VPC CIDR 443 ingress) are also
  defined here or in `network.tf` — place them adjacent to their endpoint resources.
**Done when:** 6 compute/store SGs + 5 endpoint SGs in the plan; no 0.0.0.0/0 egress.

---

### T4: Write `security_groups.tf` — closed-egress rules (compute SGs)

**Depends on:** T3, T2
**Touches:** `apps/infra-tf/security_groups.tf`
**Tests:** goal-based — count of `aws_vpc_security_group_egress_rule` resources matches:
  IngestionTask=8, SmokeProbe=3, VectorSmoke=4, QueryLambda=5 (total=20); `terraform
  validate` exits 0; `terraform fmt -check` exits 0.
**Approach:** Write `aws_vpc_security_group_egress_rule` resources for each compute SG,
  matching the `_COMPUTE_SG_EGRESS` table:
  - IngestionTask: Neptune 8182 (referenced_sg), OpenSearch 443 (referenced_sg),
    BedrockRuntime 443 (endpoint sg), EcrApi 443 (endpoint sg), EcrDocker 443 (endpoint sg),
    Logs 443 (endpoint sg), Sts 443 (endpoint sg), S3 443 (prefix_list_id).
  - SmokeProbe: Neptune 8182 (referenced_sg), Logs 443 (endpoint sg), Sts 443 (endpoint sg).
  - VectorSmoke: OpenSearch 443 (referenced_sg), BedrockRuntime 443 (endpoint sg),
    Logs 443 (endpoint sg), Sts 443 (endpoint sg).
  - QueryLambda: Neptune 8182 (referenced_sg), OpenSearch 443 (referenced_sg),
    BedrockRuntime 443 (endpoint sg), Logs 443 (endpoint sg), Sts 443 (endpoint sg).
  Write ingress rules: Neptune SG accepts 8182 from IngestionTask, SmokeProbe,
  QueryLambda; OpenSearch SG accepts 443 from IngestionTask, VectorSmoke, QueryLambda.
**Done when:** Egress rule counts match the table exactly; no extra rules added.

---

### T5: Update `outputs.tf` with network output values

**Depends on:** T1, T3
**Touches:** `apps/infra-tf/outputs.tf`
**Tests:** goal-based — `terraform validate` exits 0; `grep 'private_subnet_id'
  apps/infra-tf/outputs.tf` shows `aws_subnet.private[0].id`.
**Approach:** Fill in the network-layer output stubs:
  - `private_subnet_id = aws_subnet.private[0].id`
  - `ingestion_security_group_id = aws_security_group.ingestion_task_sg.id`
  Other outputs remain stubs (filled by subsequent specs).
**Done when:** 2 network outputs filled; `terraform validate` exits 0.

---

### T6: Run `terraform fmt -check` + plan-count + negative-CIDR verification

**Depends on:** T4, T5
**Touches:** none (verification only)
**Tests:** goal-based — `terraform fmt -check` exits 0; plan JSON resource counts +
  negative-CIDR checks match the spec ACs.
**Approach:** Run `terraform fmt -recursive apps/infra-tf/` then `terraform fmt -check`.
  `terraform init -backend=false` (S3 backend not needed for a local plan; ADV-5 —
  `-backend=false` is an *init* flag, not a `plan` flag), then
  `terraform plan -out=tfplan -var="budget_alarm_email=x@example.com"
  -var="invoker_role_arn=arn:aws:iam::123456789012:role/x"
  -var="s3_prefix_list_id=pl-abc123ef"` (needs live AWS creds — the
  `aws_availability_zones` data source is read at plan time). Run `terraform show
  -json tfplan` and assert, over `planned_values.root_module.resources`:
  - counts: `aws_vpc`=1, `aws_subnet`=2, `aws_route_table`=2,
    `aws_route_table_association`=2, `aws_vpc_endpoint`=6, `aws_nat_gateway`=0,
    `aws_internet_gateway`=0, `aws_security_group`=11, egress rules=20, ingress rules=11;
  - the 6 named compute/store SGs all present; store-ingress: Neptune SG 3 rules,
    OpenSearch SG 3 rules; 5 endpoint-SG ingress rules on 443 (AC8);
  - **negative (AC4/AC5):** no `aws_vpc_security_group_egress_rule` or
    `_ingress_rule` has `cidr_ipv4 = "0.0.0.0/0"` / `cidr_ipv6 = "::/0"`; no
    `aws_security_group` declares an inline `ingress`/`egress` rule; every egress rule
    resolves to a `referenced_security_group_id` or `prefix_list_id`.
**Done when:** fmt exits 0; all counts match; 6 named compute/store SGs present; ingress
  counts correct; negative-CIDR checks pass (no public ingress/egress, no inline rule blocks).

## Rollout

No AWS resources are created by this spec alone. The network tier is applied as part of
the combined `terraform apply` in the `infra-terraform-compute` spec's live AC. The
network outputs (`private_subnet_id`, `ingestion_security_group_id`) feed into subsequent
specs during plan.

## Risks

- **AZ availability:** the data source `aws_availability_zones` returns region-specific
  AZs; if the target region has fewer than 2 available AZs, `aws_subnet.private[1]`
  fails. Mitigation: `us-east-1` (the default) has 6 AZs; warn in docs if deploying
  to a region with limited AZs.
- **Interface endpoint service name format:** service names differ by region for some
  services (e.g., `bedrock-runtime` vs `bedrock`). Mitigation: verify against the AWS
  provider documentation for the target region during implementation.
- **`prefix_list_id` argument availability:** ✓ verified against the live AWS provider
  schema (aws 5.100.0) via `terraform providers schema -json` at the EXECUTE
  contract-grounding gate — `aws_vpc_security_group_egress_rule` exposes
  `prefix_list_id` and `referenced_security_group_id` alongside `cidr_ipv4`,
  `from_port`, `to_port`, `ip_protocol` (req), `security_group_id` (req), `description`.

## Changelog

- 2026-07-22 — Plan authored for infra-terraform-network spec. Six tasks: VPC + subnets,
  VPC endpoints, 6 security groups (closed egress), exact egress rules per
  _COMPUTE_SG_EGRESS, network outputs, fmt + plan count verification.
- 2026-07-22 — REVIEW fix (QE-1): all 11 SG `name` → `name_prefix` (removes the
  immutable-description replacement-collision trap; matches CDK's auto-generated SG
  names). QE-3 (lock VPC default SG) deferred into backlog `infra-terraform-scanner-ci`;
  QE-2 (count/AZ coupling) accepted per Risks. Spec ACs 1-9 checked, AC10 deferred.
- 2026-07-22 — Pre-EXECUTE review amendments. T1: added route tables + associations,
  CDK-faithful subnet CIDRs (10.0.0.0/24, 10.0.1.0/24). T2: S3 gateway endpoint
  `route_table_ids`, interface endpoints via `for_each`. T6: fixed `-backend=false`
  (init not plan), added negative-CIDR + inline-block + count checks. Design: recorded
  endpoint-SG VPC-CIDR ingress as deliberate CDK-parity choice; added route-table
  subsection. Risk: `prefix_list_id` verified against provider schema oracle.
