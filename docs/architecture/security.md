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

## Trust boundaries (slice 3 — hybrid orchestration)

| Boundary | Control |
| --- | --- |
| Internet → query path | The in-VPC query Lambda's **only** public ingress is its **Function URL with `AuthType=AWS_IAM`** (never `NONE`) — every request must be SigV4-signed. The invoke permission is additionally **scoped to a named principal** (the `InvokerRoleArn` CfnParameter, the deploying/CLI role), never `Principal: *`/account-root: IAM auth gates *that a request is signed*, the scoped grant gates *who may invoke*. The Lambda sits in private isolated subnets and keeps the no-egress (VPC-endpoint-only, no NAT) guarantee. |
| Untrusted retrieved content → Claude (LLM01/LLM08) | The question and retrieved corpus Markdown are passed to Bedrock Claude (Converse) as **data, not instructions** — placed in the `messages` content, never concatenated into the `system` block; the `system` block carries an explicit **defensive directive** that any instructions embedded in the data must not be followed. The synthesized answer is **display-only** (no caller evaluates it, shells out on it, or feeds it into a tool call). A bounded `inferenceConfig.maxTokens` caps a runaway generation. |
| Compute → Bedrock (Claude synthesis) | Via the `bedrock-runtime` VPC endpoint; default botocore-chain TLS client (no `verify=False`, no plaintext `endpoint_url`); the synthesis model id rides the request body, never string-interpolated. |
| Public Function URL → error responses | On any failure the handler returns a **sanitized envelope** (a correlation id + a generic message; **no internal endpoint / ARN / stack text**) and logs the real detail to CloudWatch only — the loud-raise-with-body posture stays in-VPC (CLI/adapter side), never crossing the public ingress (information-disclosure boundary). An over-long question (> ~8 KB) is rejected before any orchestration runs. |

## Trust boundaries (slice 4 — permission-filtered retrieval)

> **These visibility labels are a *synthetic teaching stand-in* for access control — not
> real authorization** (charter principle 5; an explicit Non-goal below). They demonstrate
> *where* authorization rides a GraphRAG retrieval path; the controls below are about the
> *mechanism* being correct as a teaching artifact, not production-grade authz.

| Boundary | Control |
| --- | --- |
| Persona (untrusted input) → query filter | The `persona` is a query-time input. It resolves through `visibility.resolve_clearance` **fail-closed**: an unknown persona raises (CLI: non-zero exit; Lambda: a sanitized `unknown persona` envelope), never a silent fall-through to unrestricted. The resolved clearance's `allowed` tier set rides the openCypher `parameters` map (`$allowed`) and an OpenSearch `terms` filter — **never string-interpolated** (the `ruff S` ruleset stays on; injection-safe by construction). |
| Forbidden entity → traversal (the leak guard) | The graph filter is applied **DURING traversal, on edges** (`GraphStore.neighbors`/`neighbors_batch`: in-memory and a parameterized Neptune `WHERE r.visibility IN $allowed AND b.visibility IN $allowed`), **not** as a post-filter on the final node set. A forbidden node therefore never enters the frontier, never appears in the hop trace, and cannot bridge to a node reachable only through it. A redundant independent guard also drops any above-clearance node from the final merged set. |
| Default-when-no-persona = unrestricted | Omitting the persona yields unfiltered retrieval (slice-1–3 behavior). Read as an authz mechanism this is the textbook **fail-open default** a real ACL must invert (default-deny) — it is safe **here only** because the labels are non-authz **and** the query ingress is the IAM-auth, scoped-principal Function URL (the caller is the trusted deploying/CLI role, not an end-user). Named so the seam is never copied into a context where the persona is the security principal. |
| Filtered-out trace → caller | The trace names the *identity* of filtered items (an enumeration oracle in a real ACL system) as a **teaching observability aid**, explicitly labeled "a real ACL would not reveal this." This disclosure is **contained by the trusted-caller ingress**: the filtered-out trace crosses the IAM-auth, scoped-principal Function URL only to the trusted operator role — *never* to the persona as an authenticated end-user. Surfacing filtered IDs to a less-trusted caller (a multi-tenant fork, an end-user endpoint) is out of scope and would require re-deciding this boundary. |

The labels are written to **both** stores at ingest (Neptune node + edge properties;
OpenSearch chunk metadata) from the same dual-write, so the filter is consistent across
modes. The OpenSearch `visibility` keyword field lands only on a **fresh** index
(teardown-first rebuild; a re-deploy over a live domain does not migrate the mapping).

## Trust boundaries (opencypher-templates — the governed query path)

> The governed half of the governed-vs-risky pair. Its security property is **injection-safe
> and read-only by construction**, so — unlike the LLM-authored `text2opencypher-guarded`
> path — it does **not** rely on Neptune's read-replica enforcement (RFC-0001 §2). See the
> [governed-vs-risky explanation](../guides/explanation/governed-vs-risky-graph-queries.md).

| Boundary | Control |
| --- | --- |
| Untrusted question → LLM template selector (LLM01/LLM08) | The Bedrock Claude (Converse) selector receives the template catalog + the question as **data** in `messages` (never `system`); the `system` block carries the defensive untrusted-data directive; `maxTokens` is bounded. Its output is **validated against the fixed template set** — an id outside the library (or malformed/empty JSON) resolves to a governed *no-match*, never a fabricated query. The LLM selects an id only; it never authors query text. |
| Selected template → Neptune (the executable surface) | The executable surface is a **fixed, reviewed, read-only library** (`templates.py`): a static check asserts every template is read-only (no mutating clause / `CALL`) and binds every value through a declared `$param` (no value-interpolation token). Read-only is guaranteed by **review + lint**, not by endpoint enforcement — the teaching contrast with text2cypher. |
| Question → bound parameters (the governance boundary) | Parameter **values** are extracted **deterministically** (`params.py`), never taken as free LLM text: entity slots resolve through the slice-1 normalizers and are **confirmed against the store** (an unconfirmed candidate is dropped, not bound); enum slots are checked against a declared set; int slots are parsed + bounded. A bad required slot → governed no-match (no query runs). Values ride the openCypher `parameters` map (`NeptuneGraphStore.run_template_query`) — **never string-interpolated** (`ruff S` stays on). |
| Untrusted rows → Claude synthesis | Same posture as the hybrid path: returned rows ride Converse `messages` as data, the answer is display-only, the client is the default-TLS botocore chain. |
| Live ingress + IAM | The governed path rides the **existing** IAM-auth, scoped-principal Function URL via an additive `mode` field; it adds **no new resource and no new IAM statement** — selection reuses the already-granted `bedrock:Converse` on the synthesis model and the existing Neptune data-access (a *different* selection model would be the only thing that widens the grant). |

## Least privilege

The Fargate **task role** and the **vector probe role** grant only: scoped `s3`
read on the corpus bucket (task only), `neptune-db:*` data actions on the specific
cluster (task only), **`es:ESHttp*` scoped to the one OpenSearch domain ARN**, and
**`bedrock:InvokeModel` scoped to the one Titan v2 model ARN** — **no wildcard
`Resource`** (asserted by `apps/infra/tests/test_stack.py`). The `es` IAM prefix and
the adapter's SigV4 signing service come from a single `"es"` constant so they can't
drift. The execution role's `ecr:GetAuthorizationToken` is the one legitimate `"*"`
(an AWS requirement) and is out of that assertion's scope.

The slice-3 **query Lambda role** carries the same scoped grants (Neptune-data on the
cluster, `es:ESHttp*` on the domain, Titan `bedrock:InvokeModel`) **plus** the
synthesis Claude grant: `bedrock:InvokeModel` + `bedrock:Converse` scoped to **both**
the cross-region inference-profile ARN (`inference-profile/us.anthropic.claude-sonnet-4-6`,
account+region-qualified) **and** each underlying regional foundation-model ARN
(`foundation-model/anthropic.claude-sonnet-4-6` in the profile's routing regions) — a
cross-region inference profile needs both, with **no wildcard `Resource`** (asserted by
`test_stack.py`). The CDK `_SYNTHESIS_MODEL_ID` is asserted equal to the library
`DEFAULT_SYNTHESIS_MODEL_ID` so the grant scope can't drift from the runtime default.

## Cost as a security-adjacent control

Neptune/OpenSearch do not scale to zero, so a cloned-and-forgotten stack is a
wallet-DoS footgun. Controls: one-command `cdk destroy`, min-capacity stores, and a
Budgets alarm with a threshold + subscriber (charter principle 4).

## Out of scope this slice (named, not forgotten)

- **Production authorization.** Slice 4 ships the synthetic visibility labels + the
  permission filter (see the slice-4 trust-boundary table above), but those are a
  *teaching stand-in for ACLs*, never real IAM / multi-tenancy / data authz (charter
  principle 5). Real authorization is **not** built here.
- **Prompt injection from retrieved Markdown** (OWASP LLM01/08). **Now a control as
  of slice 3** (see the slice-3 trust-boundary table): retrieved chunks reach Claude as
  data-not-instruction with a defensive system directive, the answer is display-only,
  and `maxTokens` is bounded. *Accepted residual:* the corpus is public/benign and the
  output is display-only, so a successful injection can at worst produce a misleading
  display string — it routes to `security-reviewer` if the demo ever ingests private
  data or wires the output into a tool.
- **SAST/SCA scanners, cdk-nag, pip-audit.** Recommended as CI gates; ruff `S` +
  the explicit synth assertions cover the controls in the meantime. (Wiring
  `pip-audit`/Dependabot is the standing follow-up — see the security-review note in
  the `vector-rag-baseline` plan.)
- **Live IAM/SG evaluation.** Source/synth review only; the deployed-config review
  rides the deferred `graph-ingestion-resolution-live-deploy` backlog item.
- **Uniform least-privilege SG *egress*.** The OpenSearch SG and the slice-3 query
  Lambda SG set `allow_all_outbound=False`, but the other compute SGs (Fargate
  ingestion, both smoke probes) default to allow-all egress. In the no-NAT,
  VPC-endpoint-only VPC there is no internet path, so this is **not exploitable** —
  accepted as defence-in-depth debt.
  The follow-up is a single uniform pass setting `allow_all_outbound=False` + explicit
  443 egress on every compute SG (do it across all SGs at once, not per-slice, to
  avoid asymmetry); it becomes load-bearing only if a NAT or public endpoint is ever
  added.
