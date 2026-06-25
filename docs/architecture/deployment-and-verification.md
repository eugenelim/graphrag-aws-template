# Deployment & verification

> How the demo's infrastructure is deployed, torn down, and **verified end-to-end**
> against the real stores. Current-state architecture doc (for contributors).
> Binding decisions live in [ADR-0002](../adr/0002-ephemeral-vpc-store-topology.md)
> (topology) and [ADR-0003](../adr/0003-iac-tool-aws-cdk-python.md) (CDK); the
> rolled-up *what's provisioned + why-shaped-this-way* view is the
> [infrastructure lens](infrastructure.md); this file is the *how to deploy / verify*
> mechanics, including the live-deploy findings.

## Layout

`apps/infra/` is an AWS CDK (Python) app:

- `app.py` → `stacks/graphrag_stack.py` — the single `GraphragSlice1` stack
  (VPC + endpoints + Neptune + S3 + Fargate ingestion + Budgets + the smoke probe).
- `scripts/` — operational entrypoints: `deploy.sh`, `destroy.sh`, `status.sh`,
  and the shared `_aws-env.sh`.
- `tests/test_stack.py` — in-process synth assertions (no AWS account, no `cdk` CLI).

## The verification ladder

Four layers, cheapest first; each catches what the layer below can't:

| Layer | Proves | Against | Where |
| --- | --- | --- | --- |
| Unit / construction tests | parse → extract → resolve → multi-hop **insert+retrieve round-trip** | in-memory store | `packages/graphrag/tests` |
| Neptune adapter test | the adapter emits **parameterized** openCypher; responses parse | a **mock** | `test_store_neptune.py` |
| Synth assertions | topology + security posture + tags | `cdk synth` (no data) | `apps/infra/tests` |
| **Deploy-time graph probe** | the **real** openCypher works against the **real** cluster | live Neptune, in-VPC | the Neptune probe Lambda (below) |
| **Deploy-time vector probe** | **real** Titan v2 embed → **real** OpenSearch index → k-NN retrieve | live OpenSearch + Bedrock, in-VPC | the vector probe Lambda (below) |
| Credible-baseline eval | the vector baseline is fair (hit@5=1.0 + honest misses) | frozen real Titan v2 vectors | `test_vector_eval.py` |

The first two run offline; the synth layer runs in CI without an account; the probe
runs against a deployed stack. The mock layer is honest but cannot prove Neptune
accepts the queries — that is exactly the gap the probe closes.

## The in-VPC smoke probe (deploy-time verification)

Neptune is VPC-private (no public endpoint, no NAT — ADR-0002), so it is **not
reachable from a laptop or from CI directly**. The lightest secure way to verify
the live graph store is a **scale-to-zero Lambda inside the VPC**:

- **What it does:** `graphrag.smoke_lambda` upserts a unique node + edge into
  Neptune and reads them back through the **same `NeptuneGraphStore` the CLI uses**
  (`get_node` + a real one-hop `neighbors()` traversal), then cleans up its probe
  nodes. A green result (`{"ok": true, ...}`) proves the *actual* openCypher works,
  not a reimplementation.
- **Why a Lambda (not ECS exec / CodeBuild):** the no-NAT posture means a stock
  container can't `pip install` in-VPC, and building the ingestion image needs
  docker; a `Code.from_asset` Lambda over the pure-Python package needs neither.
  It is scale-to-zero (no standing cost, consistent with ADR-0002) and torn down
  with the stack.
- **Security:** private isolated subnets; a dedicated SG allowed into Neptune on
  8182 only; an execution role scoped to the cluster with the Neptune data-access
  actions; **no public function URL**; credentials from the role via the botocore
  chain; TLS verified.
- **How to run it** (control-plane invoke; the function executes in-VPC):
  ```bash
  aws lambda invoke --function-name "$(aws cloudformation describe-stacks \
    --stack-name GraphragSlice1 --query \
    "Stacks[0].Outputs[?OutputKey=='SmokeProbeName'].OutputValue" --output text)" \
    /dev/stdout
  ```

This is the in-VPC realization of AC9's "active end-to-end smoke" and the
work-loop's infra/deploy verification mode.

## The in-VPC vector smoke probe (slice 2, deploy-time verification)

OpenSearch is VPC-private too, so the vector store gets the **same** probe pattern
(`graphrag.vector_smoke_lambda`):

- **What it does:** embeds a unique string with **Titan v2** (via the
  `bedrock-runtime` VPC endpoint), indexes it as a chunk into the **live** OpenSearch
  domain through the same `OpenSearchVectorStore` the CLI uses, **retrieves it back
  via k-NN**, asserts the ingested chunk is returned, and deletes it. A green result
  (`{"ok": true, "retrieved_id": "smoke-…"}`) proves the real Bedrock→OpenSearch
  round-trip — embeddings, the `es` SigV4 path, the `knn_vector` mapping, and
  response parsing — not a reimplementation.
- **Security:** private isolated subnets; a dedicated SG into OpenSearch on 443 only;
  an execution role scoped to the domain (`es:ESHttp*`) and the one Titan model
  (`bedrock:InvokeModel`); **no public function URL**; TLS verified; creds from the
  role via the botocore chain.
- **How to run it:**
  ```bash
  aws lambda invoke --function-name "$(aws cloudformation describe-stacks \
    --stack-name GraphragSlice1 --query \
    "Stacks[0].Outputs[?OutputKey=='VectorSmokeProbeName'].OutputValue" --output text)" \
    /dev/stdout
  ```

> **Live vector-probe status: PASS (2026-06-24).** Deployed to account `<redacted>`
> (`us-east-1`) and invoked end-to-end against the live single-node OpenSearch domain:
> ```json
> {"ok": true, "run": "14573ad2", "retrieved_id": "smoke-14573ad2", "hits": ["smoke-14573ad2"], "dims": 256}
> ```
> The probe embedded text with Titan v2 (256-dim) via the `bedrock-runtime` endpoint,
> indexed it as a chunk into OpenSearch, retrieved it back via k-NN through the real
> `OpenSearchVectorStore`, then cleaned up — confirming the `es` SigV4 path (no
> `AccessDenied`), the `bedrock:InvokeModel` grant, the `knn_vector` mapping, and
> correct response parsing. The stack was then torn down with `scripts/destroy.sh`.
> Offline layers (110 tests + synth, incl. the credible-baseline `vector-eval`) are
> green; this is the slice-2 live confirmation (AC7).

A note on scope: the probe proves a *synthetic* index→retrieve round-trip. Making
the *deployed corpus* queryable from a live `vector-query` needs the in-VPC query
Lambda, which is **slice 3**; the Fargate dual-write (AC9) writes the corpus to the
live domain, but a live corpus-backed `vector-query` is a manual check, not a
slice-2 acceptance criterion.

## Config / logic separation (the `scripts/` layout)

The `scripts/` hold **only logic**; every tunable lives in
[`scripts/config.env`](../../apps/infra/scripts/config.env) — the single declarative source
of build parameters and resource names (stack name, region, governance-tag defaults, the
SLR service list, cred-cache + venv + outputs knobs). Per-deployer values that must not be
committed (the required `BUDGET_EMAIL`; an optional account-specific `INVOKER_ROLE_ARN`) go
in a **gitignored** `scripts/config.local.env` (template: `config.local.env.example`).
`_aws-env.sh` sources `config.local.env` (absence-guarded) then `config.env` before the
credential logic, then re-`export`s the subprocess-consumed vars. Precedence is **explicit
env var > `config.local.env` > committed `config.env`** (both files use the assign-if-unset
`: "${VAR:=…}"` form), so the one-off `BUDGET_EMAIL=… scripts/deploy.sh` workflow is
unchanged. A `tools/hooks/pre-pr.py` guard fails closed on a real email / IAM-role-ARN in
any tracked `config*.env`. The deploy *behavior* is byte-unchanged — `cdk synth` is
identical to the pre-refactor template (spec
[`infra-config-separation`](../specs/infra-config-separation/spec.md)).

> **Live three-slice re-verification: PASS (2026-06-24).** The refactored, config-separated
> scripts were exercised end-to-end on account `<redacted>` (`us-east-1`), with
> `config.local.env` **absent** (so the committed-defaults path ran): `scripts/deploy.sh` →
> `CREATE_COMPLETE` (18 min); `scripts/status.sh` → `CREATE_COMPLETE`; **slice-1** Neptune
> smoke probe `{"ok": true, …}`; **slice-2** vector smoke probe `{"ok": true, …, "dims":
> 256}`; **slice-3** SigV4 hybrid query via the IAM-auth Function URL returned a real Bedrock
> Claude answer (KEP-1880 / KEP-2086 as @thockin/SIG-Network-owned) with citations and the
> dual-seed trace (`question: person:thockin` + a 2-hop live Neptune `TECH_LEADS`/`OWNS`
> expansion); `scripts/destroy.sh` → `DOES_NOT_EXIST`. Confirms the config/logic split drives
> the full deploy unchanged. Two notes, neither a refactor regression: (1) the Fargate
> *vector* dual-write hit a pre-existing `opensearch.create_index` idempotency bug (the
> urllib client raises `HTTPError` on a 4xx, so the documented already-exists tolerance never
> fires) — surfaced by running the slice-2 probe before ingestion, which leaves the index;
> backlog `opensearch-create-index-idempotency`. (2) `destroy.sh` left two CDK
> custom-resource provider Lambda log groups (created by those providers running *during*
> `cdk destroy`, after the pre-destroy name capture) — a sweep-timing gap **now fixed**:
> `destroy.sh` adds an `/aws/lambda/<STACK>-` prefix scan *after* destroy (alongside the
> pre-destroy name capture) so the provider log groups are swept too.

## Deploy / destroy operations

`scripts/` bake in two operational lessons (see below):

- **Credentials are cached once** (`_aws-env.sh`) into a mode-600 session file, so a
  per-command credential/SSO provider isn't re-resolved on every call (which
  rate-limits the auth provider).
- **No status polling** — `deploy.sh`/`destroy.sh` let `cdk` block until the stack
  settles; `status.sh` is a single ad-hoc check, never a loop.
- **Teardown leaves nothing behind** — `destroy.sh` removes the stack (incl. the
  probe) and then sweeps the auto-created `/aws/lambda/<fn>` log groups CDK doesn't
  manage. `deploy.sh` fills the `User` governance tag from the caller identity.

## Live-deploy findings (what synth could not catch)

These surfaced only by actually deploying; each now has a synth-level guard or a
fix so it can't regress silently:

1. **Security-group descriptions must use EC2's restricted charset.** A non-ASCII
   em-dash in `GroupDescription` was rejected; an ASCII `>` in an ingress-rule
   description was *also* rejected (the rule set excludes `>`). Guarded by
   `test_security_group_descriptions_use_ec2_charset`. *(K-0008)*
2. **A Neptune DB subnet group requires subnets in ≥2 AZs** or `cdk deploy` fails —
   so the VPC spans 2 AZs even though a single serverless instance runs (an API
   requirement, not HA; subnets are free). Guarded by the ≥2-subnet assertion.
   *(K-0006)*
3. **`AWS_REGION` is a reserved ECS variable** — not set in the task-def env; the
   Fargate agent injects it. *(K-0007)*
4. **Neptune IAM-auth needs data-plane actions** (`ReadDataViaQuery`,
   `WriteDataViaQuery`, `DeleteDataViaQuery`) — `neptune-db:connect` alone cannot
   read/write via openCypher. Both the ingestion task role and the probe role use
   the scoped data-access statement. *(found via the probe path)*
5. **Lambda auto-creates `/aws/lambda/<fn>` log groups that survive `cdk destroy`** —
   the probe uses a stack-managed log group and `destroy.sh` sweeps the rest.
6. **Cred-cache + no-poll** operational pattern (above). *(K-0009)*

> **Live probe status: PASS (2026-06-24).** Deployed to account `<redacted>`
> (`us-east-1`) and invoked end-to-end against the live Neptune Serverless cluster:
> ```json
> {"ok": true, "run": "b85735ec", "retrieved_node": "person:smoke-b85735ec", "neighbors": ["sig:smoke-b85735ec"]}
> ```
> The probe upserted a node + edge and read them back via the real adapter, then
> cleaned up — confirming the Neptune IAM data-access actions (no `AccessDenied`),
> the SG path on 8182, valid openCypher, and correct response parsing. The stack was
> then torn down with `scripts/destroy.sh`. Offline layers (65 tests + synth) are
> green; this is the deferred AC9 live confirmation, now satisfied for the graph
> store round-trip.

## Verification ladder — slice 3 (hybrid live query)

| Rung | What it proves | Status |
| --- | --- | --- |
| Offline (`pytest` + synth) | seed-and-expand, the three-mode runner, the CLI verbs, the query-Lambda handler (mocked), and the IaC shape (query Lambda + IAM-auth Function URL + scoped Claude grant) | **green** (this PR) |
| `cdk synth` (real template) | the synthesized `GraphragSlice1` template carries the `AuthType: AWS_IAM` Function URL, the named-principal (`InvokerRoleArn`) invoke grant, and the Bedrock grant scoped to the `inference-profile/us.anthropic.claude-sonnet-4-6` **and** `foundation-model/anthropic.claude-sonnet-4-6` ARNs (no wildcard) | **green** (this PR — `cdk synth` clean) |
| Live deploy + dual-write | `cdk deploy` stands up the full stack (`CREATE_COMPLETE`); the Fargate ingestion task runs the **single-parse dual-write** end-to-end | **PASS (2026-06-24)** — graph: 22 nodes / 28 edges / 6 cross-source merges (incl. `person:thockin`, `sig:sig-network`); vector: 13 chunks indexed via **live Bedrock Titan**; Neptune smoke probe `ok:true` |
| Live hybrid query (AC9) | a SigV4-signed POST to the Function URL runs a curated entity-led question through live OpenSearch + Neptune + **Bedrock Claude**, returning an answer + citations + a seed/hop trace whose seeds include the question-linked entity | **PASS (2026-06-24)** — completes in **22.7 s** with a real Claude answer; trace below |

> **Live hybrid-query smoke: PASS (2026-06-24).** Deployed to account `<redacted>`
> (`us-east-1`), corpus dual-written, then a **SigV4-signed POST to the IAM-auth Function
> URL** (via `graphrag hybrid-query --function-url …`) for *"Which KEPs does the SIG
> @thockin tech-leads own?"* returned in **22.7 s** with the dual-seed seed-and-expand
> trace:
> ```text
> seeds:
>   vector: kep-9, sig:sig-node, sig:sig-network, kep-2086
>   question: person:thockin          # the @thockin handle linked from the question
> hops:
>   hop 1: via APPROVES, AUTHORS, CHAIRS, HAS_SUBPROJECT, OWNS, TECH_LEADS
>          -> kep-1287, kep-1880, …(people + subprojects)
>   hop 2: via APPROVES, AUTHORS -> person:lavalamp, person:tallclair, person:vinaykul
> citations: KEP-0009 / sig-node / sig-network READMEs + entity ids …
> answer: <real Bedrock Claude synthesis>
> ```
> This exercised the full live path — Titan embed (Bedrock) → OpenSearch k-NN →
> question entity-linking → 2-hop Neptune expansion → Bedrock **Claude Converse**
> synthesis — and satisfies AC9 (answer + citations + a seed/hop trace whose `question`
> seed is `person:thockin`). The stack was then torn down with `scripts/destroy.sh`
> (teardown-first); no billable resource remains.
>
> **Three findings surfaced that only a live run could** — the first two are fixed in
> this PR, the third is a quality follow-up:
>
> 1. **`deploy.sh` did not pass the new `InvokerRoleArn` CfnParameter.** The documented
>    deploy would fail on a missing parameter. **Fixed:** `deploy.sh` derives the
>    caller's role ARN (override via `INVOKER_ROLE_ARN`) and passes it.
> 2. **The query Lambda hung to its 120s timeout** — the *actual* blocker, masked behind
>    the timeout across two diagnoses. (a) The first-order cause: `QuerySg` was created
>    `allow_all_outbound=False` (the store-SG pattern), so the in-VPC compute could not
>    initiate outbound — its first Bedrock Titan-embed call (boto3 60s connect × retries)
>    hung to the budget, never reaching the graph. **Fixed:** `QuerySg` allows outbound
>    like the other compute SGs (no-NAT = no internet path anyway); guarded by
>    `test_query_lambda_sg_allows_outbound`. (b) A real second-order cost:
>    `expand_neighborhood` issued `O(frontier × 6 edge-kinds × 2 directions)` sequential
>    `neighbors()` openCypher round-trips per hop — instant in-memory, slow against
>    Neptune Serverless. **Fixed:** a batched `GraphStore.neighbors_batch` (one query per
>    direction per hop; default app-layer fan-out keeps the trace identical) + a longer,
>    configurable Function-URL client timeout.
> 3. **Synthesis context under-specifies the typed edges (quality follow-up,**
>    **backlog `hybrid-orchestration-synthesis-edges`).** The expansion correctly *reaches*
>    the owned KEPs (they are in the trace), but the merged context handed to Claude lists
>    graph *nodes* without the typed `OWNS` / `TECH_LEADS` *edges*, so Claude hedged ("the
>    graph facts do not include explicit owns edges") instead of stating the ownership
>    chain. The trace + structural win are correct; enriching the synthesis context with
>    the relationships is the next quality step.

## Verification ladder — slice 4 (permission-filtered retrieval)

Slice 4 adds **no new infrastructure** (the persona rides the existing query Lambda's
request body; the only store change is the OpenSearch `visibility` mapping field). The
offline build proves the filter structurally — the during-traversal edge filter (the leak
guard), the vector terms-filter, and the two-persona divergence are all asserted over the
fixture corpus (`test_query.py`, `test_store_neptune.py`, `test_hybrid.py`,
`test_compare.py`, `test_query_lambda.py`).

| Rung | What it proves | Status |
| --- | --- | --- |
| Offline leak guard (AC3) | a restricted intermediate is unreachable for a low-clearance persona — incl. a node reachable *only* through it (a post-filter would leak it) | **PASS** (unit, in-memory) |
| Neptune filter shape (AC3) | the `WHERE r.visibility IN $allowed AND b.visibility IN $allowed` is present and parameterized (`$allowed` on the params map, never interpolated) | **PASS** (mock HTTP) |
| Three-mode + Lambda persona (AC5/AC7) | vector/graph/hybrid each filter by clearance; the query Lambda accepts a `persona`, fails closed on unknown, stays PyYAML-free | **PASS** (unit) |
| Live two-persona smoke (AC9) | deploy → labeled Fargate dual-write → SigV4 Function-URL query as `public-reader` **and** `maintainer` over one entity-led question; the restricted entity absent for the reader, present for the maintainer; then destroy | **DEFERRED** — `permission-filtered-retrieval-live-deploy` (needs AWS creds + a deploy window) |

> The live two-persona run is the supervisor's step (it costs a deploy cycle). When run,
> record the two JSON results + teardown here as a new ladder row, mirroring the slice-3
> hybrid-live entry above.
