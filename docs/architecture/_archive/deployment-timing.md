# Deployment, verification & teardown — indicative timing

> **A living doc.** Wall-clock timing for the full stand-up → verify → tear-down
> cycle, so an architect can plan a demo, a CI budget, or an iteration loop without
> being surprised. Times are **indicative**, not SLAs — they move with region,
> account warm-state, corpus size, and AWS-side variance. Each row is tagged
> **M** (measured on a real run, date in the row) or **E** (estimate, refine when
> next measured). Add rows as slices add steps; update M-values when a run gives a
> better number.

## What dominates, in one sentence

The two managed **stores** (Neptune Serverless + single-node OpenSearch) own the
critical path on both **deploy** and **teardown** — they provision and delete in
parallel and each takes the better part of ~10–25 min; everything else (VPC,
endpoints, Lambdas, Fargate task def, Budgets) is minutes or seconds around them.

## Environment these numbers assume

- `us-east-1`, single stack `GraphragSlice1`, **single-AZ / single-node** stores
  (ADR-0002 — deliberately not HA), Neptune Serverless min capacity.
- A **cold** account for the store rows (first create in the region); a warm CDK
  bootstrap. The locked fixture corpus (~hundreds of nodes/chunks), not a full clone.
- The laptop/CI host builds the Fargate image and drives `aws`/`cdk`; the in-VPC
  compute does the store I/O.

## Phase breakdown

### 1. Deploy (`scripts/deploy.sh` → `cdk deploy`)

| Sub-phase | Indicative | Tag | Notes |
| --- | --- | --- | --- |
| `cdk bootstrap` (warm) | ~5–15 s | E | "no changes" when already bootstrapped; one-time ~1–2 min on a fresh account. |
| Synth + asset publish (template + Lambda zips) | ~10–30 s | M (2026-06-25) | `Synthesis time: ~2 s` + S3 publish of `Code.from_asset` bundles. |
| VPC + subnets + route tables | ~1–2 min | E | No NAT (ADR-0002); cheap. |
| VPC interface endpoints ×6 (+ S3 gateway) | ~2–4 min | E | ENI provisioning per endpoint, in parallel. |
| **Neptune** Serverless cluster + instance + parameter group | ~8–15 min | E | On the critical path. Engine version is **pinned** (`1.3.5.0`/`neptune1.3`) so it must be a version the region offers — a bad pin fails the cluster create in **~1–2 min** and rolls the whole stack back (see K-0027). |
| **OpenSearch** single-node domain (k-NN) | ~15–25 min | E | Usually the **slowest single resource**; domain creation is long even single-node. |
| Lambdas (query + 2 smoke probes), Fargate task def, ECR repo, Budgets | ~1–3 min | E | Fast; run in parallel with the stores. |
| **Deploy total (wall clock)** | **~19 min** | M (2026-06-25, 18m43s) | First→last CREATE event, cold account, OpenSearch ∥ Neptune the critical path. |

> **Rollback cost:** a failed create (e.g. a bad Neptune engine pin, an OpenSearch
> SLR gap) triggers `ROLLBACK_IN_PROGRESS` then leaves `ROLLBACK_COMPLETE`, which
> **cannot be updated** — you must `delete-stack` (another ~5–10 min for the partial
> stores) before re-deploying. Budget a failed-deploy detour at **~10–20 min**.

### 2. Verification (dual-write + smoke + live query)

| Step | Indicative | Tag | Notes |
| --- | --- | --- | --- |
| Build Fargate ingestion image (`docker build --platform linux/amd64`) | ~1–3 min | M (2026-06-25) | Cold layer cache; faster on rebuild. |
| ECR login + push image | ~15 s | M (2026-06-25, 13s) | Size- and uplink-bound. |
| Upload fixture corpus to S3 (`aws s3 cp --recursive`) | ~10 s | M (2026-06-25, 8s) | 10 objects (the fixture corpus). |
| Fargate ingestion task (single-parse **dual-write**, `MODE=full`) | ~90 s | M (2026-06-25, 88s) | Task cold start (image pull + ENI) + parse → Neptune + OpenSearch writes (22 nodes/28 edges/13 chunks); ends and scales to zero. |
| In-VPC smoke probes (graph + vector), each | ~3–10 s | E | Scale-to-zero Lambda; VPC cold start dominates. |
| **Live query latency, per mode** (SigV4 → Function URL → stores → Bedrock) | see below | — | First call also pays a VPC-Lambda cold start (multi-second). |
| &nbsp;&nbsp;• hybrid (`mode:hybrid`) | ~20–25 s | M (2026-06-24, 22.7 s) | Vector + multi-hop + Bedrock Claude synthesis. |
| &nbsp;&nbsp;• governed templates (`mode:governed`) | ~10 s/query | M (2026-06-25, 9.9 s) | Bedrock select + bound openCypher + Claude answer. |
| &nbsp;&nbsp;• text2cypher (`mode:text2cypher`) | ~8–11 s | M (2026-06-25, 11 s cold / 8 s warm) | Bedrock generate + validate + Neptune read + Claude answer. |
| &nbsp;&nbsp;• self-query (`mode:selfquery`) | ~15 s cold | M (2026-06-26, 15.2 s cold) | Bedrock filter-extraction + OpenSearch filtered k-NN (Lucene engine, **during-ANN**) + Claude answer; warm persona/no-filter calls faster. |

### 3. Teardown (`scripts/destroy.sh` → `cdk destroy`)

| Sub-phase | Indicative | Tag | Notes |
| --- | --- | --- | --- |
| Lambdas, Fargate task def, ECR, endpoints, Budgets | ~1–3 min | E | Fast. |
| **OpenSearch** domain delete | ~10–20 min | E | Slow, like its create. |
| **Neptune** cluster + instance delete | ~5–12 min | E | On the critical path. |
| S3 corpus bucket (auto-delete objects) + VPC | ~1–3 min | E | `RemovalPolicy.DESTROY`; auto-empties. |
| **Teardown total (wall clock)** | **~15–25 min** | E | Dominated by the two stores in parallel. Verify **no billable resource remains** (teardown-first, charter principle 4). |

## Planning rules of thumb

- **Full cold cycle** (deploy → dual-write → a few live queries → destroy):
  budget **~50–75 min** end-to-end, most of it unattended waiting on the stores.
- **Idle cost while up** is the real footgun, not the deploy time: neither store
  scales to zero, so a stack left standing accrues cost until `destroy`. The Budgets
  alarm (`BudgetLimit 150`) is the guardrail; tear down when done.
- **Iterating on code, not infra?** Don't redeploy the stack per change — keep one
  stack up, push a new Fargate image / re-invoke the query Lambda, and destroy once.
- **The first live query of a session pays a VPC-Lambda cold start** (ENI + client
  init, multi-second) on top of the per-mode latency above; subsequent calls are warmer.

## How to measure (so M-values stay honest)

- **Deploy/teardown wall clock:** the `deploy.sh` / `destroy.sh` run duration, or
  diff the first/last CloudFormation `Timestamp` in the stack events
  (`aws cloudformation describe-stack-events --stack-name GraphragSlice1`).
- **Per-resource:** the gap between a resource's `CREATE_IN_PROGRESS` and
  `CREATE_COMPLETE` events (same `describe-stack-events`).
- **Live query latency:** wall-clock of the `graphrag <mode>-query --function-url …`
  call (the existing records cite the end-to-end seconds).

## Changelog

- 2026-06-25 — Initial doc. Phase breakdown for deploy / verify / teardown with the
  first M-values (synth ~2 s, image build ~1–3 min, hybrid 22.7 s, governed 9.9 s);
  store provisioning/teardown left as E pending a clean measured run. Added the
  rollback-detour note (K-0027: a bad Neptune engine pin fails fast but forces a
  delete-before-redeploy). text2cypher live latency TBD on the AC10 run.
- 2026-06-26 — metadata-filtering (self-query) live run: added the **self-query**
  per-mode latency M-value (15.2 s cold). Deploy wall clock measured again at **~17m24s**
  (cdk total 1069.87 s; consistent with the ~19 min row — OpenSearch ∥ Neptune still the
  critical path). Dual-write again ~90 s (22 nodes / 28 edges / 13 chunks). The k-NN engine
  switch (`nmslib`→`lucene`) did not change deploy time (it's an app-side `create_index`
  mapping, not a CDK resource).
