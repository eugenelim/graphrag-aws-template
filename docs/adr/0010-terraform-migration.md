# ADR-0010: Migrate infrastructure from AWS CDK (Python) to Terraform (HCL)

- **Status:** Accepted
- **Date:** 2026-07-22
- **Decision-makers:** eugenelim
- **Supersedes:** [ADR-0003](0003-iac-tool-aws-cdk-python.md) (IaC tool is AWS CDK Python)
- **Related:** [ADR-0002](0002-ephemeral-vpc-store-topology.md) (the ephemeral VPC topology — unchanged); spec [`infra-terraform-scaffold`](../specs/infra-terraform-scaffold/spec.md) (first implementation spec of this ADR)

## Context

ADR-0003 chose AWS CDK (Python) for the initial implementation, citing:
single-language repo, in-process synth assertions (`Template.from_stack()`), and no
remote-state bootstrap friction. Those reasons held at slice 1.

The rationale to migrate now:

1. **Terraform is the dominant IaC standard.** The repo's audience — solution
   architects evaluating graph vs vector retrieval — predominantly operate in
   environments that standardize on Terraform. Showing CDK limits the pattern's
   portability signal. ADR-0003 itself noted "Terraform remains a reasonable swap;
   the topology is tool-independent."
2. **HCL is more readable for the teaching goal.** A resource block per AWS resource
   is unambiguous to an architect reading the wiring for the first time; CDK
   constructs hide the resource boundary (one L2 construct ≠ one CloudFormation
   resource). The repo's job is to *teach* the topology, not to be the most
   concise expression of it.
3. **`terraform test` + plan-JSON assertions are now a credible replacement for
   CDK synth tests.** Terraform ≥ 1.6 ships a built-in test framework; plan JSON
   (`terraform show -json`) is parseable in pytest with the same assertion depth as
   `aws_cdk.assertions.Template`. The security-posture gate (`no wildcard IAM`,
   `closed egress`, `read-only Neptune`) survives the migration.
4. **State bootstrap is one-time and scriptable.** The S3 backend + native state
   locking (Terraform ≥ 1.11, no DynamoDB needed) can be bootstrapped from
   `terraform init --backend-config`. The `deploy.sh` / `destroy.sh` scripts absorb
   the one-time bootstrap; the clone-and-deploy experience degrades by one step and
   is recovered by documentation.
5. **Provider-level security scanning.** `trivy config .` on HCL catches known
   misconfigurations before plan; no equivalent runs on CDK-synthesized CloudFormation
   without the generated template on disk.

## Decision

> We will migrate the demo's infrastructure from the CDK Python app (`apps/infra/`)
> to a Terraform root module (`apps/infra-tf/`). The CDK app is **kept in place**
> (not deleted) until all Terraform slices pass their live-deploy ACs, at which point
> the CDK app is archived. `terraform apply` and `terraform destroy` replace `cdk
> deploy` and `cdk destroy`. The topology (ADR-0002), security posture (closed
> egress, IAM least-privilege, ADR-0004 read-only Neptune backstop), and the
> governance tag requirement are unchanged. All 40+ CDK synth assertions are
> translated to an equivalent pytest plan-assertion suite on the Terraform plan JSON.

## Decision drivers

- **Terraform-first audience.** Solution architects on multi-cloud or CDK-unfamiliar
  stacks can now clone, read, and adapt without learning CDK constructs.
- **HCL narratability.** Each `aws_neptune_cluster` / `aws_vpc_endpoint` / `aws_lambda_function`
  block names the resource explicitly; the endpoint-set design intent is as legible as CDK's
  `_INTERFACE_ENDPOINTS` dict but without the CDK abstraction layer.
- **Plan-JSON assertion parity.** `terraform show -json tfplan | python -m pytest apps/infra-tf/tests/`
  replaces `Template.from_stack()` with equivalent coverage; the IAM and SG
  egress-set assertions that proved ADR-0004 and the closed-egress posture are preserved.
- **No `aws_cdk` dependency on the critical path.** The Lambda zip, ECS task image, and
  Fargate role no longer require `jsii` / Node to test.

## Consequences

**Positive:**
- Terraform-native audience alignment; pattern portability claim is credible.
- `trivy config .` security scan is native to Terraform HCL, no template-on-disk step.
- Plan JSON is a durable, versionable artifact; diffs between plans are reviewable.
- Native state locking (≥ 1.11) removes DynamoDB bootstrap overhead.

**Negative:**
- S3 backend must be bootstrapped before the first `apply` on a clean account (one-time
  step, absorbed by `apps/infra-tf/scripts/bootstrap.sh`).
- CDK's `auto_delete_objects=True` on S3 has no direct Terraform equivalent — replaced by
  `force_destroy = true` on `aws_s3_bucket`, which empties the bucket on destroy (same
  observable behavior; different mechanism).
- Lambda code packaging: `aws_archive_file` data source zips `packages/graphrag/src/` at
  plan time; the CDK `Code.from_asset()` did this at synth time. Functionally identical.
- CDK's `cdk-nag` hard gate is replaced by `trivy config .` + OPA/Conftest on the plan JSON;
  `cdk-nag` AwsSolutions findings are addressed by the resource configuration, not suppressed.

**Neutral / to revisit:**
- AWS CDK remains documented as the alternative. A team standardizing on CDK can reverse
  this migration by pointing at `apps/infra/` (the CDK app is kept intact until archive).

## Confirmation

- `apps/infra-tf/tests/test_plan.py` asserts the Terraform plan JSON has the same
  topology and security posture as the CDK synth assertions in
  `apps/infra/tests/test_stack.py` (no wildcard IAM, closed egress exact sets,
  Neptune read-only on the query role, OpenSearch access policy no AllPrincipals,
  governance tags on key resources, budget $150/80%).
- `trivy config apps/infra-tf/` exits 0 with no HIGH/CRITICAL findings.
- Live AC (deploy → smoke → destroy) confirms the topology behaves identically.

## Alternatives considered

- **Keep CDK; add a Terraform mirror.** Dual-maintain two IaC implementations.
  *Rejected:* doubles drift surface; the repo would teach two tools simultaneously,
  diluting the retrieval-pattern message.
- **CDK for AWS + Terraform for other clouds.** Per-cloud tool split.
  *Rejected:* the demo is AWS-only (ADR-0002); the split buys nothing.
- **Pulumi (Python).** Keeps Python, adds explicit resource model.
  *Rejected:* smaller operator audience than Terraform; the Terraform plan/apply
  ergonomics are the ecosystem standard the audience already knows.
- **Raw CloudFormation.** Direct resource authoring, no framework.
  *Rejected:* verbosity and no plan-before-apply oracle; inferior narratability.

## References

- ADR-0002 (topology); ADR-0003 (superseded CDK decision); ADR-0004 (read-only Neptune
  backstop — preserved unchanged); generate-iac skill (`SKILL.md`) — the skill used to
  author the Terraform implementation.
