# ADR-0003: Infrastructure-as-code tool is AWS CDK (Python)

- **Status:** Accepted
- **Date:** 2026-06-23
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** [ADR-0002](0002-ephemeral-vpc-store-topology.md) (the ephemeral
  VPC topology this IaC provisions); [design doc](../architecture/graphrag-aws-architecture/design.md)
  (§ Rollout — "One stack (CDK or Terraform) with `deploy` and `destroy`", the
  choice this ADR resolves at slice time); spec
  [`graph-ingestion-resolution`](../specs/graph-ingestion-resolution/spec.md)
  (the first slice to author IaC).

## Context

ADR-0002 fixed the *topology* (ephemeral, teardown-first, VPC-resident, one-command
`deploy`/`destroy`) but the design doc deliberately deferred the **tool** —
"CDK or Terraform" — to slice time. Slice 1 is the first to author IaC, so the
choice must be made now. The decision drivers come straight from the charter and
ADR-0002: reproducibility by construction, one-command up/down, narratability (the
topology must read legibly — an architect clones this to *learn* the wiring), and
single-language simplicity (the rest of the repo is Python: parsing, resolution,
CLI, the Fargate ingestion task).

## Decision

> We will author the demo's infrastructure as an **AWS CDK app written in
> Python**. `cdk deploy` and `cdk destroy` are the one-command up/down the charter
> requires; the stack is synthesized and asserted in-process via
> `aws_cdk.assertions.Template` (no live account, no `cdk` CLI needed for the
> test), which is how the slice verifies the topology without deploying.

## Decision drivers

- **Single language repo-wide** — Python already carries parsing, resolution, the
  CLI, and the Fargate task; CDK-Python keeps one toolchain and lets the IaC import
  the same constants (bucket names, env keys) the app uses.
- **Narratability** — programmatic constructs make the VPC-endpoint *set* explicit
  and legible (`InterfaceVpcEndpoint` per service), which an HCL resource block
  also does but a `for_each` over a list obscures; the demo's job is to *teach* the
  wiring.
- **Testable without an account** — `Template.from_stack()` synthesizes in-process,
  so the topology is unit-assertable in CI (the slice's AC8) with no AWS
  credentials and no `cdk` binary.
- **One-command teardown** — `cdk destroy` removes the stack; reproducible state
  lives in the S3 snapshot (ADR-0002), so there is no IaC state-store to manage
  (unlike Terraform's remote-state bootstrap, which adds a chicken-and-egg step to
  a clone-and-deploy demo).

## Consequences

**Positive:**
- One language, one test harness; the synth assertions are the topology's
  executable spec.
- No remote-state backend to bootstrap on a clean account — `cdk` keeps state in
  CloudFormation, which `destroy` also tears down.

**Negative:**
- Adds the `aws-cdk-lib` + `constructs` dependency (isolated to the `infra` extra;
  the runtime app does not import them) and the Node-based `cdk` CLI for actual
  deploys (the *test* path needs only the Python lib).
- CDK's generated CloudFormation is less transparent than hand-written templates —
  mitigated by the synth-assertion tests pinning the resources that matter.

**Neutral / to revisit:**
- Terraform remains a reasonable swap for a team that standardizes on it; the
  topology (ADR-0002) is tool-independent. Recorded as the alternative, not a
  foreclosed door.

## Confirmation

- The slice-1 synth test (`apps/infra/tests/test_stack.py`) asserts the stack
  contains the ADR-0002 topology subset and the security posture (no NAT, no public
  Neptune endpoint, S3 public-access blocked + encrypted, least-privilege task
  role, Budgets alarm with a threshold + subscriber).

## Alternatives considered

- **Terraform (HCL).** Ubiquitous, tool-agnostic, strong plan/apply ergonomics.
  *Rejected (for this repo):* a second language alongside the Python app, and a
  remote-state backend must be bootstrapped before the first `apply` on a clean
  account — friction against the clone-and-deploy, teardown-first goal. Kept as the
  documented swap.
- **AWS CDK (TypeScript).** The most common CDK surface. *Rejected:* introduces
  TypeScript/Node as a second language for no benefit here, since the app is Python.
- **Raw CloudFormation / SAM.** *Rejected:* verbose and hard to keep narratable at
  the VPC-endpoint-set altitude; no in-process unit-assertion story as clean as
  `assertions.Template`.

## References

- ADR-0002 (topology); design doc § Rollout (the deferred choice).
