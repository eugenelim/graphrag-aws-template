# ADR-0019: Neptune Graph Explorer runs on a private-subnet notebook with an ECR-mirrored image

- **Status:** Accepted
- **Date:** 2026-09-11
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Amends:** ADR-0002 — extends its enumerated VPC-endpoint set with `sagemaker.api`.
  The private, no-NAT topology itself is unchanged and is the reason this ADR exists.
- **Related:** ADR-0002 (ephemeral private VPC topology, the no-NAT posture this must
  respect); ADR-0011 (Text2SPARQL read-only guard, the read-only intent this extends to
  human queries); ADR-0018 (managed OpenSearch domain)

## Context

A Neptune **Graph Explorer** — a SageMaker notebook instance running the
`neptune/graph-explorer` container and proxying its UI on port 9250 — was deployed into
the demo account on 2026-08-07 by a second maintainer, via Terraform, from a working copy
whose HCL was never committed to this repository. It has run since. The resources are
present in remote state but absent from `apps/infra-tf`, so `terraform plan` from a clean
checkout proposes **17 destroys**, and `apply` would delete a working capability.

The capability is worth keeping. This repo exists to show architects *when
graph-augmented retrieval beats vector search*; a visual graph explorer is close to that
thesis, and reviewers ask to see the graph. The question is not whether to keep it, but
what shape to codify.

The as-deployed shape conflicts with three accepted decisions:

- A dedicated **public** subnet (`10.0.2.0/24`, `map_public_ip_on_launch = true`), an
  **internet gateway**, and a `0.0.0.0/0 → igw` route, with the notebook's
  `DirectInternetAccess: Enabled`. ADR-0002 commits to a private VPC with *"no NAT
  gateway — all egress via VPC endpoints"*. A public subnet plus IGW is a different
  mechanism but the same outcome the topology set out to avoid: a standing public network
  path into the store VPC.
- A `sagemaker.api` interface endpoint. ADR-0002 states the required endpoint set is
  *"part of the decision, not an implementation detail"* and enumerates six; this is a
  seventh, unrecorded.
- An IAM inline policy **named** `neptune-data-readonly` that in fact grants
  `neptune-db:*` — full data-plane read *and write* on the cluster — alongside the
  AWS-managed `AmazonSageMakerFullAccess`. ADR-0011 routes LLM-authored queries through a
  read-only guard; an unguarded write-capable human path beside it makes that guard a
  partial control. The policy name actively misleads a reader.

The binding technical constraint is the image pull. The lifecycle configuration runs
`docker pull public.ecr.aws/neptune/graph-explorer:sagemaker-3.2.0`. **ECR Public has no
PrivateLink endpoint.** With internet access disabled and no NAT, that pull cannot
succeed — which is presumably why the public subnet was introduced in the first place.

## Decision

> We will keep the Graph Explorer capability and run it **private**: the notebook
> instance moves to an existing private subnet with `DirectInternetAccess: Disabled` and
> `RootAccess: Disabled`; the `neptune/graph-explorer` image is **mirrored into a private
> ECR repository** in the account and pulled through the existing `ecr.api` / `ecr.dkr` /
> S3-gateway endpoints; the notebook role is scoped to **read-only Neptune data actions**
> and a minimal SageMaker policy; and `sagemaker.api` joins ADR-0002's endpoint set. The
> dedicated public subnet, internet gateway, and route table are **not** codified — they
> are deleted.

The image mirror is the load-bearing part. It follows a precedent ADR-0002 already set:
when the corpus needed a live `git clone` that would have required NAT, the answer was to
pin an **S3 snapshot** rather than add NAT. The same shape applies here — pin a
**mirrored image digest** rather than add a public path. Both trade a live internet
dependency for a reproducible in-account artifact, which is also better for a
clone-and-deploy demo: the version is pinned, not "whatever `:sagemaker-3.2.0` resolves to
today".

Reaching the UI is **unaffected** by this change. Users open Graph Explorer through the
SageMaker-hosted proxy URL (`…notebook.<region>.sagemaker.aws/proxy/9250/explorer/`),
which is a control-plane path served by SageMaker. `DirectInternetAccess` governs the
notebook's *outbound* egress, not the operator's inbound route, so no VPN or bastion is
introduced.

## Decision drivers

- **Consistency with ADR-0002** — the private, no-NAT topology is the repo's defining
  infrastructure decision; a public subnet added silently erodes it.
- **Least privilege** — a notebook with root access, full Neptune write, and
  `AmazonSageMakerFullAccess`, reachable from a public subnet, is the account's widest
  single blast radius.
- **Reproducibility** — a pinned in-account image beats a live public-registry pull for a
  clone-and-deploy demo.
- **Truthful naming** — a policy called `neptune-data-readonly` must be read-only.
- **Keep the capability** — the explorer earns its place; hardening must not remove it.

## Consequences

**Positive:**
- The store VPC returns to a single documented posture: private subnets, no NAT, no IGW,
  egress only via enumerated endpoints. One fewer exception to reason about.
- The notebook can no longer write to or delete from the graph, and cannot reach the
  internet, so a compromised notebook session is contained to reads of demo data.
- The explorer version is pinned by digest and survives upstream tag churn or deletion.
- Closes one of the SI6005 "Asset Missing AIR ID" findings by bringing the notebook under
  the tagged, Terraform-managed set.

**Negative:**
- A mirror step is now required before first deploy — someone or something must copy
  `public.ecr.aws/neptune/graph-explorer:sagemaker-3.2.0` into the account's ECR. This is
  a new manual or CI prerequisite, and a fresh clone-and-deploy fails at notebook start
  until it is done. Mitigated by a documented one-liner in
  `deployment-and-verification.md` and a loud lifecycle-script failure.
- Upgrading the explorer is no longer "restart the notebook" — it is re-mirror, then
  restart.
- Applying this **replaces the running notebook**. Subnet and network-interface changes
  force replacement; any work saved only on the instance's local volume is lost.

**Neutral / to revisit:**
- `AmazonSageMakerFullAccess` is replaced by a minimal inline policy. If notebook users
  later need SageMaker training or endpoints, that policy grows deliberately rather than
  arriving as a managed-policy blanket.
- If a NAT gateway is ever accepted for other reasons, the mirror becomes optional
  convenience rather than a requirement — but ADR-0002 would have to change first.

## Confirmation

- Plan assertions pin the hardened shape: the notebook is in a private subnet, both
  `direct_internet_access` and `root_access` are `Disabled`, the Neptune policy contains
  no `neptune-db:*` wildcard, and no internet gateway exists in the configuration.
- The security-group header contract in `security_groups.tf` and the
  `_TF_COMPUTE_SG_EGRESS` table stay in lockstep, as enforced by
  `test_sg_header_totals_match_egress_table`.
- A `terraform plan` from a clean checkout proposes no destroys for the explorer set once
  the out-of-band resources are reconciled into state.

## Alternatives considered

- **Codify the public-subnet shape as deployed.** Zero delta against live infrastructure,
  so `apply` is a no-op and nothing breaks. *Rejected:* it requires superseding ADR-0002
  to bless a public path into the store VPC, and it permanently accepts root access plus
  `neptune-db:*` on an internet-reachable host. The cost of the mirror is much smaller
  than the cost of that precedent.
- **Add a NAT gateway so the public pull works from a private subnet.** The obvious fix.
  *Rejected:* ADR-0002 names "no NAT gateway" as part of the decision, for a standing
  hourly cost the ephemeral-demo shape exists to avoid. Re-litigating that for one image
  pull is the tail wagging the dog.
- **Keep the public path but fix only the IAM.** Cheaper, and closes the worst of the
  blast radius. *Rejected:* it leaves ADR-0002 quietly contradicted with no record, which
  is the failure mode that produced this ADR.
- **Drop Graph Explorer entirely.** Removes 16 resources and one finding. *Rejected:* the
  capability is on-thesis for the product, and a second maintainer found it valuable
  enough to build.
- **Bake the explorer into a custom notebook AMI or lifecycle-free image.** Avoids the
  pull at start. *Rejected:* heavier to build and maintain than an ECR mirror, and it
  hides the version in an image rather than showing it in HCL.

## References

- ADR-0002 § Decision (the no-NAT endpoint set and the S3-snapshot-over-git-clone
  precedent this mirrors)
- [`docs/architecture/security.md`](../architecture/security.md) — notebook posture
- [`docs/architecture/deployment-and-verification.md`](../architecture/deployment-and-verification.md) — the mirror prerequisite
