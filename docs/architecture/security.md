# Security posture

> The consolidated security view for the ini-002 Business Operations Knowledge
> Graph platform. Reflects the wave-3 shipped state (PRs #65–#83). Wave-4
> additions (API Gateway HTTP API, ADOT OTEL, git-ingestion EventBridge, MCP Lambda
> with `mcp_lambda_role`) are documented in
> [`biz-ops-knowledge-graph/design.md`](biz-ops-knowledge-graph/design.md)
> and will be added here when they ship. Decisions live in ADR-0011 (Neptune
> SPARQL / Text2SPARQL guard), ADR-0012 (named-graph partition isolation),
> ADR-0013 (multi-strategy routing), ADR-0014 (MCP tool server). The physical
> IAM and VPC topology is in `biz-ops-knowledge-graph/design.md`.

## Trust model

The query Lambda (`graphrag-query-lambda`, `query_role`) is the live ingress today.
The MCP Lambda (FastMCP + Mangum, `mcp_lambda_role`) is wave-4 in-flight.

| Path | Principal | Auth | Status |
|---|---|---|---|
| IAM-auth Function URL — query Lambda | Automation / CLI | SigV4 (IAM role) | **Shipped** — `query_role`; openCypher hybrid/governed path |
| IAM-auth Function URL — MCP Lambda | Automation / AI workflow / Bedrock AgentCore | SigV4 (IAM role) | **Wave-4 in-flight** — `mcp_lambda_role`; FastMCP+Mangum |
| API Gateway HTTP API | Human / AI IDE developer | API key (usage plan) | **Wave-4 in-flight** — not yet provisioned |

Both Lambdas run inside private VPC subnets with no NAT / no public IGW (ADR-0002
posture carried forward).

---

## Trust boundaries — query Lambda ingress (Function URL)

| Boundary | Control |
|---|---|
| Internet → Function URL | `AuthType=AWS_IAM` — every request must be SigV4-signed by a principal that holds `lambda:InvokeFunctionUrl` on the specific Function URL ARN. The invoke grant is **scoped to named principal ARNs** (automation role), never `Principal: *` or account-root. |
| Over-long question → Lambda | Questions exceeding ~8 KB are rejected before any retrieval runs — denial-of-wallet guard (OWASP LLM10). |
| Lambda errors → caller | On any unhandled failure the handler returns a **sanitized envelope** (a correlation id + a generic message); internal endpoint URLs, Neptune ARN, or stack text are **never** returned to the caller. Real detail is logged to CloudWatch only. |

---

## Trust boundaries — Text2SPARQL guard (ADR-0011)

The `query` and `ask` tools can invoke the LLM-authored SPARQL path. The threat
is a generated — or prompt-injected — **SPARQL Update mutation**
(`INSERT DATA`, `DELETE WHERE`, `DROP GRAPH`, etc.) reaching Neptune.

The guard is four layers (outermost first):

| Layer | Mechanism | What it stops |
|---|---|---|
| 1 — App-layer static denylist | `text2sparql._validator` rejects any SPARQL Update keyword (`INSERT`, `DELETE`, `DROP`, `CLEAR`, `LOAD`, `CREATE`) and any unbounded property-path quantifier (e.g. `biz:hasChunk*`); enforces a bounded `LIMIT`. Conservative: a forbidden keyword inside a string literal also rejects. | Most mutation attempts; runaway read patterns |
| 2 — Bounded self-heal | On validation failure or Neptune execution error, the error is fed back to the LLM for **at most N (default 1)** re-generation attempts, each re-validated. After the cap, the path returns a narratable refusal. The self-heal feedback rides Converse `messages` as **untrusted data** (never `system`). | Malformed or over-eager first generation |
| 3 — IAM read-only data-action scoping (the **write backstop**, the real guarantee) | The query Lambda's Neptune grant is **`ReadDataViaQuery` + `connect` only** — no `WriteDataViaQuery`, no `DeleteDataViaQuery`. Today this is `query_role` (the openCypher query Lambda); wave-4's `mcp_lambda_role` carries the same read-only scope. AWS IAM rejects a write *before the engine runs it*, independent of whether the app-layer validator caught it. (ADR-0011 explicitly carries this control forward from ADR-0004 to SPARQL.) | Every mutation the validator missed; novel mutation constructs; Unicode-escaped or backtick-quoted clause text |
| 4 — Neptune engine query timeout | A `neptune_query_timeout` cluster parameter kills runaway reads the `LIMIT` clause doesn't bound. | Runaway reads; denial-of-wallet on the read side |

The ingestion Fargate task and the Neptune smoke probe retain the full
read-write Neptune grant — both legitimately write.

---

## Trust boundaries — named-graph partition isolation (ADR-0012)

All knowledge lives in Neptune SPARQL. Named graphs are the isolation boundary:

| Named graph | Retrieval semantics | Guard |
|---|---|---|
| `urn:graph:normative` | Exhaustive recall (ALL matching items) — hard-fail if unavailable | `get_policies` only; no partial result returned; SPARQL query scoped to this graph by `FROM NAMED` — never touches `urn:graph:descriptive` |
| `urn:graph:descriptive` | Best top-k match — graceful degrade | `ask`, `search`, `search_graph` queries scoped to this graph; cannot leak into normative partition |
| `urn:graph:quarantine` | No retrieval | Documents failing SHACL or quality gates; written with `biz:quarantineReason`; never returned to callers |
| `urn:graph:taxonomy` | SPARQL lookup only | Domain/journey hierarchy + partition index |
| `urn:graph:ontology` | Read-only at query time | OWL schema; loaded once at startup |

The named-graph scope is a hard constraint on every SPARQL query, not a hint.
Mixing normative and descriptive retrieval paths is **architecturally blocked**:
vector search optimises for precision, not exhaustive recall — a policy worded
differently from a query could score below a content document and be silently
dropped, a compliance risk.

**Quarantine as a safety net.** Documents failing the SHACL validation gate or
PII/quality gates are never silently dropped — they are written to
`urn:graph:quarantine` with a structured `biz:quarantineReason` triple.

---

## Trust boundaries — PII handling

PII detection uses regex patterns (email, phone, SSN, credit card, national IDs)
supplemented by AWS Comprehend when the `comprehend` VPC endpoint is provisioned.

| Boundary | Control |
|---|---|
| PII-flagged document → retrieval | All retrieval paths default to filtering `biz:hasPII false`. A caller must explicitly opt in to receive PII-flagged results — fail-closed by default. |
| PII-flagged document → partition routing | PII sensitivity and knowledge kind are orthogonal. A PII-flagged SOP stays in `urn:graph:descriptive`; a PII-flagged policy stays in `urn:graph:normative`. Routing a PII-flagged document into the wrong partition would corrupt the retrieval contract. |
| PII-flagged result → MCP response | Every citation for a PII-flagged document carries `"pii_flagged": true` so the caller can decide handling. The platform surfaces; it does not redact (redaction destroys provenance). |

---

## Trust boundaries — prompt injection at the retrieval → LLM boundary

The `ask` and `get_policies` tools synthesise an answer using a Bedrock LLM call.
Retrieved document chunks reach that call as **data, never instructions**:

| Boundary | Control |
|---|---|
| Retrieved corpus text → LLM synthesizer (LLM01/LLM08) | Question + retrieved chunks ride Converse `messages` as data; the `system` block carries an explicit **defensive directive** that any instructions embedded in the data must not be followed. `maxTokens` is bounded. The synthesised answer is display-only (no caller evaluates it or feeds it into a tool call). |
| Text2SPARQL generation | See [Text2SPARQL guard](#trust-boundaries--text2sparql-guard-adr-0011) above. |

*Accepted residual:* the corpus is operator-supplied/trusted-origin and the
synthesised output is display-only. A successful injection can at worst produce
a misleading display string — it routes to a security review if the platform
ever ingests private untrusted data or wires synthesis output into a tool call.

---

## IAM roles — wave-3 shipped (least privilege, no wildcard Resource)

| Role | Neptune SPARQL | OpenSearch | Bedrock | S3 |
|---|---|---|---|---|
| `ingestion_task_role` | ReadDataViaQuery + **WriteDataViaQuery** + connect | `es:ESHttp*` (scoped to domain ARN) | embed (InvokeModel) + synthesise (Converse) — both scoped to model ARN | read + scoped PutObject: `manifest.json`, `schema_extraction_trace.txt`, `silver/*` |
| `query_role` | **ReadDataViaQuery + connect ONLY** | `es:ESHttp*` (scoped to domain ARN) | embed (InvokeModel) + synthesise (Converse) — both scoped to model ARN | — |
| `smoke_probe_role` | ReadDataViaQuery + WriteDataViaQuery + connect | — | — | — |
| `vector_probe_role` | — | `es:ESHttp*` (scoped to domain ARN) | embed (InvokeModel) scoped to Titan v2 ARN | — |

Wave-4 will introduce `mcp_lambda_role` (MCP Lambda — same read-only Neptune scope
as `query_role`, same Bedrock grants) and will rename `smoke_probe_role` to
`sparql_probe_role` to align with the SPARQL engine. The ingestion task role grant
set will expand when the git-delta SPARQL ingestion pipeline ships.

---

## VPC and network segmentation

The VPC topology (ADR-0002) carries forward unchanged: single-AZ private isolated
subnets, no NAT gateway, no Internet Gateway. VPC endpoints shipped (wave 3):

| Endpoint | Service |
|---|---|
| Gateway | S3 |
| Interface | `ecr.api`, `ecr.dkr`, `logs`, `sts`, `bedrock-runtime` |

Wave-4 additions (in-flight, not yet provisioned): `otlp`, `xray` (OTEL/ADOT);
`textract`, `comprehend` (scanned PDF OCR and PII detection — wave-5+ ingestion).

Each compute security group sets `allow_all_outbound=False` with explicit egress to
exactly the in-VPC stores (Neptune 8182, OpenSearch 443) and VPC endpoints (443).
No compute SG carries a `0.0.0.0/0` egress rule.

Neptune is VPC-resident (private subnet group, no public endpoint). OpenSearch is
VPC-resident in private isolated subnets — not public; encryption at rest +
node-to-node encryption + enforce-HTTPS.

---

## Cost as a security-adjacent control

Neptune Serverless and OpenSearch do not scale to zero. A cloned-and-forgotten
stack is a wallet-DoS footgun. Controls: one-command `terraform destroy`,
min-capacity stores (1 NCU Neptune floor, `t3.small.search` OpenSearch), and a
Budgets alarm. Note: the standing-cost floor — Neptune ~$110/mo + OpenSearch
~$26/mo + VPC endpoints ~$90/mo ≈ $226/mo idle — **exceeds 80% of a $250 alarm**,
so the Budgets threshold must be set above the floor to avoid a false alarm at idle
(tracked: `biz-ops-budgets-threshold-above-standing-floor`).

---

## Out of scope (named, not forgotten)

- **Production authorisation / real ACLs / multi-tenancy.** Visibility labels
  (`biz:visibility`, `biz:hasPII`) are labels and default query filters — not
  enforced access controls (ADR-0009). The platform makes it easy to add authz at
  the query path; it does not implement it.
- **Neptune CloudWatch audit-log export.** `enable_cloudwatch_logs_exports =
  ["audit"]` is not set. Accepted for the ephemeral teardown-first demo; revisit
  in a regulated deployment. (Backlog: `neptune-audit-log-export`.)
- **Live IAM backstop smoke test** for Text2SPARQL DROP GRAPH. The IAM
  `AccessDeniedException` on a `query_role`-credentialed SPARQL Update is tested
  offline (unit); the live confirmation (`pytest.mark.live_aws`) is deferred.
  (Backlog: `text2sparql-live-smoke-iam-backstop`.)
- **SAST/SCA scanners** — `pip-audit` and `detect-secrets` run in CI.
- **Function URL invoke grant principal scope** — the scoped-principal grant
  must enumerate authorized principal ARNs exactly, never `Principal: *`.
  Tracked: `biz-ops-functionurl-scoped-principal-restate`.
- **DROP GRAPH IAM action verification** — confirm SPARQL Update `DROP GRAPH`
  is gated by `DeleteDataViaQuery` so the query Lambda read-only grant actually
  blocks a partition-destroying mutation. Tracked:
  `neptune-sparql-dropgraph-iam-action-verify`.
