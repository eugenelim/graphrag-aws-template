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

## Trust boundaries (slice 2 — vector half)

| Boundary | Control |
| --- | --- |
| Compute → OpenSearch (k-NN) | In-VPC only; SigV4 + IAM-auth for service `es`; **body-parameterized** index/k-NN/delete (no value interpolated into path/query string); `https://` with TLS verification on. The domain's own **access policy** restricts to the task + probe role ARNs (resource-side IAM, not `AllPrincipals`). |
| Compute → Bedrock (Titan v2) | Via the `bedrock-runtime` VPC endpoint; default botocore-chain TLS client (no `verify=False`, no plaintext-HTTP `endpoint_url`). |
| Internet → OpenSearch | Domain is VPC-resident in the private isolated subnets, **not public**; encryption at rest + node-to-node encryption + enforce-HTTPS. |
| Retrieved corpus text → output | **Display-only** in this slice — chunk text is embedded and rendered, never fed to an LLM as instructions and no tool execution, so OWASP LLM01 is out of reach. It becomes control-bearing the moment slice 3 routes it into Claude synthesis (isolate-and-no-instruction there). |

## Least privilege

The Fargate **task role** and the **vector probe role** grant only: scoped `s3`
read on the corpus bucket (task only), `neptune-db:*` data actions on the specific
cluster (task only), **`es:ESHttp*` scoped to the one OpenSearch domain ARN**, and
**`bedrock:InvokeModel` scoped to the one Titan v2 model ARN** — **no wildcard
`Resource`** (asserted by `apps/infra/tests/test_stack.py`). The `es` IAM prefix and
the adapter's SigV4 signing service come from a single `"es"` constant so they can't
drift. The execution role's `ecr:GetAuthorizationToken` is the one legitimate `"*"`
(an AWS requirement) and is out of that assertion's scope.

## Cost as a security-adjacent control

Neptune/OpenSearch do not scale to zero, so a cloned-and-forgotten stack is a
wallet-DoS footgun. Controls: one-command `cdk destroy`, min-capacity stores, and a
Budgets alarm with a threshold + subscriber (charter principle 4).

## Out of scope this slice (named, not forgotten)

- **Production authorization.** Slice 4's synthetic visibility labels are a
  *teaching stand-in for ACLs*, never real authz (charter principle 5). Not built
  here.
- **Prompt injection from retrieved Markdown** (OWASP LLM01/08). Slice 2 embeds the
  corpus but keeps retrieved text **display-only** (no LLM instruction surface, no
  tools) — so the boundary is documented, not yet a control. It becomes a control in
  slice 3, where retrieved chunks reach Claude synthesis.
- **SAST/SCA scanners, cdk-nag, pip-audit.** Recommended as CI gates; ruff `S` +
  the explicit synth assertions cover the controls in the meantime. (Wiring
  `pip-audit`/Dependabot is the standing follow-up — see the security-review note in
  the `vector-rag-baseline` plan.)
- **Live IAM/SG evaluation.** Source/synth review only; the deployed-config review
  rides the deferred `graph-ingestion-resolution-live-deploy` backlog item.
- **Uniform least-privilege SG *egress*.** The OpenSearch SG sets
  `allow_all_outbound=False`, but the compute SGs (Fargate ingestion, both probes)
  default to allow-all egress. In the no-NAT, VPC-endpoint-only VPC there is no
  internet path, so this is **not exploitable** — accepted as defence-in-depth debt.
  The follow-up is a single uniform pass setting `allow_all_outbound=False` + explicit
  443 egress on every compute SG (do it across all SGs at once, not per-slice, to
  avoid asymmetry); it becomes load-bearing only if a NAT or public endpoint is ever
  added.
