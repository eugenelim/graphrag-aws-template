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
