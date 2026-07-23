# Pre-EXECUTE review dispositions — infra-terraform-network

Resolve-vs-surface record for the pre-EXECUTE adversarial + security spec-stage
review (work-loop full mode). Findings resolved into spec/plan/impl before code.

| # | Finding | Severity | Disposition |
|---|---------|----------|-------------|
| ADV-1 | S3 gateway endpoint has no route-table association — plans clean, won't route | Blocker | RESOLVE: add `aws_route_table` ×2 + associations + `route_table_ids` on S3 endpoint; AC9 |
| ADV-2 | AC5 (no public ingress) has no implementing/verifying task | Blocker | RESOLVE: add public-ingress plan-JSON check (T6); strengthen AC5 |
| SEC-1 | No self-contained negative-egress AC — load-bearing property unenforced by this spec's gate | Blocker | RESOLVE: strengthen AC4 — no 0.0.0.0/0 egress, every egress uses referenced_sg_id or prefix_list_id |
| SEC-4 | Store/endpoint SGs not pinned to zero egress | Concern | RESOLVE: AC4 — total egress rule count = 20, only 4 compute SGs own egress |
| ADV-4 | Endpoint-SG VPC-CIDR ingress load-bearing but uncovered | Concern | RESOLVE: add AC8 (5 endpoint SGs, 443 from VPC CIDR) |
| SEC-5 | AC5 inspects only separate ingress resources, not inline blocks | Concern | RESOLVE: impl declares NO inline rule blocks; AC5 covers both forms |
| ADV-3 | Goal-based ACs need live AWS (aws_availability_zones) at plan time | Concern | RESOLVE: clarify Testing Strategy — validate/fmt offline, plan-JSON needs creds (available in this env) |
| ADV-6 | Hardcoded subnet CIDRs diverge from CDK allocation | Nit | RESOLVE: use CDK-faithful 10.0.0.0/24, 10.0.1.0/24 (cidrsubnet index 0,1) |
| ADV-5 | `terraform plan -backend=false` is not a valid plan invocation | Nit | RESOLVE: fix T6 — `init -backend=false` then `plan` |
| ADV-7 / SEC-6 | Endpoint-SG ingress uses VPC CIDR not per-compute-SG (CDK connections.allow_to) | Nit | RESOLVE-as-documented: deliberate translation choice noted in plan Design; effective gate is the compute-SG egress rules |
| SEC-3 | No IaC security scanner (Checkov/tfsec) wired — `degraded: no scanner` | Concern | DEFER: owned by sibling `infra-terraform-verification`/CI; backlog item `infra-terraform-scanner-ci` |
| SEC-2 | `s3_prefix_list_id` is a format-validated free variable — a footgun (a customer-managed list could contain 0.0.0.0/0) | Concern | **RESOLVED (user-authorized, pulled into PR):** removed `var.s3_prefix_list_id`; the S3 managed prefix list is now resolved via `data "aws_ec2_managed_prefix_list" "s3"` (name `com.amazonaws.<region>.s3`) — no operator input, no injectable wide CIDR. Cross-spec: scaffold variable/AC superseded (changelog), verification plan `-var` dropped, backlog item removed. Egress-equivalent to CDK deploy.sh resolution, strictly safer. Was originally SURFACE+DEFER (parity); user asked to harden now. |
