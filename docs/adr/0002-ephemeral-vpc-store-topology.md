# ADR-0002: The demo stack is an ephemeral, teardown-first VPC topology

- **Status:** Accepted
- **Correction (2026-06):** The Context rationale "Neptune is VPC-only — no public
  endpoint" is over-absolute; since engine 1.4.6.0 an optional public endpoint
  exists (off by default, IAM-enforceable). The decision (private, in-VPC topology)
  is unchanged — only the wording overstates the constraint. See
  [`docs/rfc/0001-notes/aws-feasibility.md`](../rfc/0001-notes/aws-feasibility.md) § 6.
- **Date:** 2026-06-23
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** [`docs/architecture/graphrag-aws-architecture/design.md`](../architecture/graphrag-aws-architecture/design.md) (D2 — the deliberation this records); [`docs/product/briefs/graphrag-aws-demo.md`](../product/briefs/graphrag-aws-demo.md) (Spec map, all slices); ADR-0001 (the hybrid orchestration that runs against these stores)

## Context

The product is a **clone-and-deploy** AWS reference: an architect deploys it into
their own account, demos it, and tears it down. Two non-obvious infrastructure
facts dominate the topology:

- **Amazon Neptune is VPC-only** — no public endpoint, so a laptop CLI cannot reach
  it directly; query/ingestion compute must run *inside* the VPC.
- **Neither Neptune nor OpenSearch truly scales to zero** — a cloned-and-forgotten
  demo accrues standing cost. This is a real footgun for the target user.

The top-ranked quality attributes are **cost/ephemerality** and
**reproducibility/operational simplicity** (after explainability); high
availability and scale are explicit non-goals. At the concept stage the maintainer
chose the ephemeral "Shape A" over a persistent environment or a single in-VPC
demo box.

## Decision

> We will deploy the demo as a single **ephemeral, teardown-first** stack: Neptune
> Serverless (min capacity) + a single-node OpenSearch domain with k-NN in a
> private VPC; on-demand Fargate for ingestion/sync and an in-VPC query Lambda
> behind an IAM-auth Function URL; a thin local CLI client; **no NAT gateway**
> (all egress via VPC endpoints); and one-command `deploy` **and** `destroy` IaC
> with an AWS Budgets alarm and a post-deploy smoke check.

The required VPC endpoints are part of the decision, not an implementation detail:
`bedrock-runtime`, `s3` (gateway, also the corpus-snapshot source), `ecr.api`,
`ecr.dkr`, `logs`, and `sts`.

## Decision drivers

- **Cost / ephemerality** — bounded idle cost; nothing billable survives `destroy`.
- **Reproducibility / operational simplicity** — one-command up/down on a clean
  account; no manual console steps.
- **Explainability** — the topology must keep ingest → retrieve → search legible.
- **Hard constraint** — Neptune VPC-only forces in-VPC compute.

## Consequences

**Positive:**
- Idle cost is bounded and `destroy` removes every billable resource (verified by a
  teardown check); scale-to-zero compute (Lambda + on-demand Fargate) keeps the
  idle floor to the two managed stores only.
- No NAT gateway removes a standing hourly cost; VPC endpoints carry all egress.
- State is fully reproducible from the S3 corpus snapshot, so rollback is
  `destroy` + redeploy with no data migration and no irreversible step.

**Negative:**
- VPC-attached Lambda cold starts (ENI + client init) can be multi-second —
  mitigated on stage by a pre-warm call or provisioned concurrency (cost tradeoff).
- In-VPC networking (security groups + the full endpoint set) is the most
  failure-prone wiring; a silent misconfig yields timeouts, not clear errors —
  mitigated by encoding it in IaC and a loud post-deploy smoke check.
- A live `git clone` of the corpus would need NAT and break the no-NAT posture, so
  the corpus source is constrained to an **S3 snapshot** (pinned commit).

**Neutral / to revisit:**
- Single-node OpenSearch vs. OpenSearch Serverless is pending a current-pricing
  check; if the AOSS minimum-OCU floor is now low, the choice flips to ops-vs-cost.
- Single-AZ / single-node means no HA — accepted, restated as a non-goal.

## Confirmation

- The post-deploy smoke check exercises every hop (laptop → Function URL → stores →
  Bedrock) and fails loudly with a diagnostic if any is unreachable.
- `destroy` leaves no billable resources (verified); the Budgets alarm is deployed
  and the running-cost estimate + destroy command print after deploy.

## Alternatives considered

- **OpenSearch Serverless instead of single-node.** Less ops surface. *Rejected
  (conditionally):* a per-OCU floor is the wrong cost shape for an ephemeral demo
  versus a low fixed-cost node — conditional on current AOSS pricing, which must be
  re-checked; if the floor has dropped enough this reverses.
- **Single in-VPC "demo box" (EC2/Fargate runs CLI + orchestration) — Shape B.**
  Simplest to reason about, cheapest when stopped. *Rejected:* hides the
  managed-services story the demo exists to teach, and the SSH-in console flow is
  less reproducible than a thin local CLI + IaC. Kept as the fallback if VPC-Lambda
  networking proves too fiddly.
- **Laptop CLI + VPN/bastion tunnel to Neptune.** *Rejected:* clunky per-user setup,
  not reproducible on a clean account.

## References

- Design doc D2 + by-construction pillar map: [`graphrag-aws-architecture/design.md`](../architecture/graphrag-aws-architecture/design.md)
