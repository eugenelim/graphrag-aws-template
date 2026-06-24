# Security posture

> The consolidated security view for the demo stack. Living doc; updated as
> slices land. Slice 1 (`graph-ingestion-resolution`) stands up the first VPC,
> IAM roles, and data store, so this doc starts here. Decisions live in
> [ADR-0002](../adr/0002-ephemeral-vpc-store-topology.md) /
> [ADR-0003](../adr/0003-iac-tool-aws-cdk-python.md); the design doc's Risks
> section is the threat narrative.

## Trust boundaries (slice 1)

| Boundary | Control |
| --- | --- |
| Untrusted corpus files → parser | `yaml.safe_load` only (no `yaml.load`); a `!!python/object` tag parses inert (CWE-502). Enforced by ruff `S506`. Malformed front matter is skipped, not executed. |
| Ingestion compute → Neptune | In-VPC only; SigV4 + IAM-auth; **parameterized** openCypher (no value/relationship-type interpolation); `https://` with TLS verification on. |
| Compute → AWS APIs | All egress via VPC endpoints (`s3`, `ecr.api`, `ecr.dkr`, `logs`, `sts`) — **no NAT**, no public egress. |
| Credentials | Resolved via the default botocore provider chain (the Fargate task role) — never read from env/argv at a call site. |
| Internet → data stores | Neptune VPC-resident (private subnet group, no public endpoint); S3 bucket public-access-blocked, encrypted, TLS-only. |

## Least privilege

The Fargate **task role** grants only scoped `s3` read on the corpus bucket and
`neptune-db:connect` on the specific cluster — **no wildcard `Resource`** (asserted
by `apps/infra/tests/test_stack.py`). The execution role's
`ecr:GetAuthorizationToken` is the one legitimate `"*"` (an AWS requirement) and is
out of that assertion's scope.

## Cost as a security-adjacent control

Neptune/OpenSearch do not scale to zero, so a cloned-and-forgotten stack is a
wallet-DoS footgun. Controls: one-command `cdk destroy`, min-capacity stores, and a
Budgets alarm with a threshold + subscriber (charter principle 4).

## Out of scope this slice (named, not forgotten)

- **Production authorization.** Slice 4's synthetic visibility labels are a
  *teaching stand-in for ACLs*, never real authz (charter principle 5). Not built
  here.
- **Prompt injection from retrieved Markdown** (OWASP LLM01/08). No Bedrock /
  synthesis in slice 1; the boundary is named in the design doc and re-reviewed when
  slice 2 introduces embeddings/synthesis.
- **SAST/SCA scanners, cdk-nag, pip-audit.** Recommended as CI gates; ruff `S` +
  the explicit synth assertions cover the slice-1 controls in the meantime.
- **Live IAM/SG evaluation.** Source/synth review only; the deployed-config review
  rides the deferred `graph-ingestion-resolution-live-deploy` backlog item.
