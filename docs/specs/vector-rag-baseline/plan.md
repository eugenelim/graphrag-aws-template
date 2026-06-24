# Plan: vector-rag-baseline

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. Substantial changes get a dated
> changelog entry at the bottom.

## Approach

Build inside-out, mirroring slice 1 so the vector half is the structural twin of
the graph half. The pipeline is **chunk → embed → index → query → eval**, with the
same two-backend store seam slice 1 uses for the graph: an in-memory implementation
(the offline / test / reproducible-demo backend) and a network adapter
(`OpenSearchVectorStore`, SigV4 over HTTPS, injectable HTTP client) that is the
behavioral twin of `store/neptune.py`. Embeddings sit behind an `Embedder` protocol
with a real Titan v2 implementation and a deterministic offline implementation, so
every unit test runs offline and the network surface is exercised only through
mocks (unit) and the live probe (integration).

The load-bearing reproducibility choice is **frozen real Titan v2 vectors**: the
credible-baseline confirmation (AC6) must reflect *real* embedding quality to be
honest, but CI cannot call Bedrock deterministically — so the curated query set's
chunk + query vectors are embedded once with real Titan v2 (256-dim, normalized)
and committed as a fixture. `vector-eval` runs cosine k-NN over those frozen
vectors and asserts the hit@5 bar offline; a documented `--bedrock` path
regenerates them and runs against live Titan. The live OpenSearch + Bedrock
round-trip itself is proven by the in-VPC probe (AC7), not by the eval.

The riskiest parts, front-loaded: (1) the OpenSearch adapter's request/response
shape and SigV4 service name (`es`) against a real domain — de-risked by mocking
the adapter and proving it live via the probe; (2) the IaC additions (a VPC
OpenSearch domain has a fiddly access policy + SG path) — de-risked by synth
assertions then the live deploy; (3) curating a query set that is genuinely fair —
addressed by drawing queries from the real pinned fixture and including a labeled
honest miss.

## Constraints

- **ADR-0002** — single-node OpenSearch + `bedrock-runtime` endpoint in the
  ephemeral, no-NAT, teardown-first VPC; everything billable removed by `destroy`.
- **ADR-0001** — every indexed chunk carries its owning entity ID(s) as metadata,
  because slice-3 seed-and-expand seeds graph entities from "the entities owning the
  top-k chunks". This slice writes that metadata; it does not read it.
- **ADR-0003** — IaC is AWS CDK (Python); additions land in the existing
  `apps/infra/stacks/graphrag_stack.py`, not a new stack.
- **Design doc D2** — OpenSearch is VPC-private; the CLI's offline path is the
  demoable one, the live path is in-VPC compute (probe now; query Lambda in slice 3).
- **Charter pattern 2** — single-parse dual-write: the Fargate task writes both
  stores from one parse.

## Construction tests

Most tests live per-task below. Cross-cutting:

- **Integration:** `vector-query` over the fixture (offline embedder) returns a
  ranked, provenance-bearing trace end-to-end (CLI → chunk → embed → in-memory
  knn → render) — `test_vector_cli.py`.
- **Live (manual, this environment):** `cdk deploy`, invoke the vector probe
  Lambda, assert `{"ok": true, ...}` (the ingested chunk is retrieved), `cdk
  destroy`. Recorded in `docs/architecture/deployment-and-verification.md`.

## Design (LLD)

Shape `mixed` → data & schema, interfaces & contracts, component decomposition,
failure & resilience, dependencies & integration. Stack derived from the
established repo (no `docs/architecture/reference.md` present): Python 3.11+,
`pyyaml`+`boto3`, AWS CDK (Python), mirroring slice-1 modules.

### Design decisions
*(Traces to: AC2, AC4, AC6 · no `contracts/` file — internal interfaces.)*

- **Two-backend vector store, like the graph store.** `VectorStore` ABC +
  `MemoryVectorStore` (pure-Python cosine k-NN) + `OpenSearchVectorStore` (SigV4
  HTTPS). Keeps the offline demo + CI fully offline and the live path a thin twin
  of `NeptuneGraphStore`. *Rejected:* `opensearch-py` client — it adds a runtime
  dependency for what SigV4+urllib already does (Ask-first boundary).
- **`Embedder` protocol, injected.** `BedrockTitanEmbedder` (real) +
  `HashEmbedder` (deterministic offline). *Rejected:* hard-coding a boto3 Bedrock
  client at call sites — untestable offline.
- **Frozen real Titan v2 vectors for the eval.** Honest (real model) *and*
  CI-reproducible. *Rejected:* (a) live-only eval — not reproducible per charter
  principle 3; (b) eval over the `HashEmbedder` — no semantic structure, can't
  honestly show vector "wins".
- **256-dim, normalized.** Cost-first (97% of 1024's accuracy per AWS), and with
  normalized vectors cosine == dot product, simplifying both backends.

### Data & schema
*(Traces to: AC1, AC3, AC4.)*

- `graphrag.chunk.Chunk`: `id` (`<doc-path>#<ordinal>`), `text`, `source`
  (`community`|`enhancements`), `doc_path`, `heading` (nearest preceding heading),
  `entity_ids` (`list[str]`, **derived via `normalize.sig_id`/`kep_id`** so they
  match the graph node IDs exactly — `["sig:<slug>"]`, or `["kep-<n>",
  "sig:<slug>"]`; note KEP uses `kep-` (hyphen) per slice-1 `normalize.kep_id`,
  SIG uses `sig:` (colon) per `sig_id`).
- `graphrag.vector.EmbeddedChunk`: a `Chunk` + `vector: list[float]` (256).
- OpenSearch index mapping: `vector` → `knn_vector` (dimension 256, cosine /
  `space_type: cosinesimil`), plus keyword fields for `source`, `doc_path`,
  `heading`, `entity_ids`, and `text`. Index settings `index.knn: true`.
- Frozen-vectors fixture: `tests/fixtures/vector/query_set.yaml` (queries + gold
  chunk ids + the labeled entity-led miss) and `frozen_embeddings.json`
  (`chunk_id`/`query_id` → 256-float vector, real Titan v2).

### Interfaces & contracts
*(Traces to: AC2, AC3, AC4, AC5.)*

- `graphrag.embed`: `Embedder` (Protocol) with `embed(texts: list[str]) ->
  list[list[float]]`; `BedrockTitanEmbedder(model, dimensions=256, normalize=True,
  client=…)`; `HashEmbedder(dimensions=256)`.
- `graphrag.store.vector_base.VectorStore` (ABC): `create_index()`,
  `index_chunk(EmbeddedChunk)`, `knn(vector, k, *, filter=None) -> list[VectorHit]`,
  `delete(ids)`, `count()`. (No `filter` use this slice — the param exists for
  slice-4 metadata filtering but is unused/optional here.)
- `graphrag.store.vector_memory.MemoryVectorStore`, `…store.opensearch.OpenSearchVectorStore`.
- `graphrag.vector.vector_search(store, embedder, query, k) -> VectorQueryResult`
  (carries the ordered trace + hits with provenance; `.render()` like
  `TraversalResult`).

### Component / module decomposition
*(New modules under the existing `packages/graphrag/src/graphrag/`:)*

- `chunk.py`, `embed.py`, `vector.py`, `vector_eval.py`,
  `store/vector_base.py`, `store/vector_memory.py`, `store/opensearch.py`,
  `vector_smoke_lambda.py`. Reuses `sources.py`, `parse.py`, `normalize.py`,
  `model.py` unchanged. CLI verbs added to `cli.py`. Fargate dual-write extends
  `apps/ingestion/entrypoint.py`. IaC extends `apps/infra/stacks/graphrag_stack.py`.

### Failure, edge cases & resilience
*(Traces to: AC1, AC4.)*

- A prose doc with no body / only headings → no chunks (skip, no empty chunk).
- A chunk longer than the Titan token limit → split at the chunk-size bound before
  embedding (chunk size is set well under 8,192 tokens).
- OpenSearch non-2xx → raise with the response body (loud, per ADR-0002), as
  Neptune does; no retry logic this slice.
- Bedrock throttling on the live/`--bedrock` path → surfaced loudly; batch sizes
  kept small. Not a unit-test concern (mocked).

### Dependencies & integration
*(Traces to: AC4, AC7, AC8, AC9.)*

- Bedrock `bedrock-runtime` (Titan v2) via the new VPC interface endpoint;
  OpenSearch Service domain (k-NN) via SigV4 (`es`). Both least-privilege-scoped.
- No new Python runtime dependency (SigV4+urllib). `boto3` already present.

## Tasks

> Tests come before Approach in each task (tests drive the build). TDD tasks carry
> a red **stub** marked `# STUB: AC<n>`. File paths are under
> `packages/graphrag/src/graphrag/` and `packages/graphrag/tests/` unless noted.

### T1 — Chunking the prose-rich subset
- **Depends on:** none
- **Touches:** packages/graphrag/src/graphrag/chunk.py, packages/graphrag/tests/test_chunk.py
- **Tests:** `test_chunk.py` — over the fixture corpus, `chunk_corpus(docs)` emits
  chunks only from `sig_readme` + `kep_readme` docs (none from `sigs_index`/
  `kep_yaml`); each chunk carries `source`, `doc_path`, nearest `heading`, and
  `entity_ids` (SIG charter → `[sig_id(slug)]` == `["sig:<slug>"]`; KEP README →
  `[kep_id(n), sig_id(slug)]` == `["kep-<n>", "sig:<slug>"]`), and a test asserts a
  chunk's KEP `entity_id` is **byte-equal to the graph KEP node ID** the slice-1
  extractor produces for the same KEP (regression guard against the colon/hyphen
  drift); a heading-only/empty body yields zero chunks; chunk size + overlap bounds
  hold. `# STUB: AC1`, `stub: true`.
- **Approach:** `graphrag.chunk` — `Chunk` dataclass + `chunk_corpus(list[ParsedDoc])
  -> list[Chunk]`; heading-aware splitter over `ParsedMarkdown.body` with a
  char-budget bound + overlap; entity IDs from the `ParsedDoc.payload`
  (`slug` / `owning_sig_dir` + `dir_number`) via `normalize` (`sig_id`, `kep_id`).
- **Done when:** `test_chunk.py` green; chunks carry provenance + entity IDs (AC1).

### T2 — Embedder protocol + offline + Titan v2
- **Depends on:** none
- **Touches:** packages/graphrag/src/graphrag/embed.py, packages/graphrag/tests/test_embed.py
- **Tests:** `test_embed.py` — `HashEmbedder(256)` returns stable, unit-norm
  256-vectors (same text → same vector; different text → different); a mocked
  `BedrockTitanEmbedder` issues `invoke_model` with body
  `{"inputText": …, "dimensions": 256, "normalize": true}` against model
  `amazon.titan-embed-text-v2:0` and parses `embedding` from the response; no
  network call in either test. `# STUB: AC2`, `stub: true`.
- **Approach:** `graphrag.embed` — `Embedder` Protocol; `HashEmbedder`
  (seeded hash → float vector, L2-normalized); `BedrockTitanEmbedder` (injectable
  `bedrock-runtime` client; default from `boto3`), batched `embed`.
- **Done when:** `test_embed.py` green (AC2).

### T3 — Vector store interface + in-memory k-NN
- **Depends on:** T1
- **Touches:** packages/graphrag/src/graphrag/store/vector_base.py, store/vector_memory.py, tests/test_vector_store_memory.py
- **Tests:** `test_vector_store_memory.py` — index N `EmbeddedChunk`s; `knn(v, k)`
  returns top-k by cosine, ordered desc by score, bounded to k; empty store → `[]`;
  k > N → all; `count()` correct. `# STUB: AC3`, `stub: true`.
- **Approach:** `graphrag.vector.EmbeddedChunk` + `VectorHit`;
  `store.vector_base.VectorStore` (ABC); `store.vector_memory.MemoryVectorStore`
  (cosine via dot product on normalized vectors).
- **Done when:** `test_vector_store_memory.py` green (AC3).

### T4 — OpenSearch adapter (mock-tested)
- **Depends on:** T3
- **Touches:** packages/graphrag/src/graphrag/store/opensearch.py, tests/test_store_opensearch.py
- **Tests:** `test_store_opensearch.py` — with a mocked HTTPS client:
  `create_index` PUTs a `knn_vector` mapping of dimension 256; `index_chunk` issues
  a document index request with the vector + metadata in the **body**; `knn` issues
  a `knn` query with the query vector + `k` in the **body** (no value interpolated
  into the path/query string); the endpoint is `https://` with TLS verify on;
  requests are SigV4-signed for service **`es`** via the botocore chain (no
  `AWS_SECRET_ACCESS_KEY` read at the call site); a non-2xx **raises with the body**.
  `# STUB: AC4`, `stub: true`.
- **Approach:** `graphrag.store.opensearch.OpenSearchVectorStore` — twin of
  `NeptuneGraphStore`: `SigV4Auth` over a module constant
  `OPENSEARCH_SERVICE = "es"` (mirrors `NEPTUNE_SERVICE`), injectable `HttpClient`,
  `verify=True`, `https://`-guard; `_request(method, path, body)` helper; responses
  parsed into `VectorHit`. The same `"es"` constant is the single source for the
  SigV4 signing service **and** the IAM action prefix (`es:ESHttp*`) the IaC scopes
  (T10) — so the sign service and the policy can't drift apart.
- **Done when:** `test_store_opensearch.py` green (AC4).

### T5 — Retrieval + trace
- **Depends on:** T2, T3
- **Touches:** packages/graphrag/src/graphrag/vector.py, tests/test_vector.py
- **Tests:** `test_vector.py` — `vector_search(store, embedder, "risks of in-place
  pod resize", k=5)` over the fixture (offline embedder + in-memory store loaded
  from frozen vectors for the exemplar) returns hits; `VectorQueryResult.render()`
  names, in order, the query, the model+dims, then per hit rank/score/source/
  doc_path/heading/entity_ids. `# STUB: AC5`, `# STUB: AC10`, `stub: true`.
- **Approach:** `graphrag.vector` — `VectorQueryResult`/`VectorHit` +
  `vector_search`; `.render()` mirrors `TraversalResult.render()`.
- **Done when:** `test_vector.py` green (AC5, AC10 for query).

### T6 — Curated query set + frozen vectors + vector-eval (credible-baseline)
- **Depends on:** T1, T3, T5 (T2 only for the out-of-CI `--bedrock` regen step)
- **Touches:** packages/graphrag/src/graphrag/vector_eval.py, tests/fixtures/vector/*, tests/test_vector_eval.py
- **Tests:** `test_vector_eval.py` — `evaluate(query_set, frozen_embeddings)`
  computes hit@k + MRR; **asserts hit@5 == 1.0 across the semantic-led queries**;
  for each labeled **entity-led miss**, asserts its gold chunk **exists in the
  corpus** (the chunk id resolves to a real fixture chunk) **and** is **not** in
  top-5 (`hit@5 == 0` for it); and a curation guard asserts no semantic-led query
  string shares a verbatim substring of **≥ 25 characters** (case-folded) with its
  gold chunk text, so the win is semantic, not lexical. `# STUB: AC6`, `stub: true`.
- **Approach:** hand-author `tests/fixtures/vector/query_set.yaml` — **≥5
  semantic-led** queries + **≥2 entity-led** queries, each phrased as a natural
  architect question, each with `gold_chunk_id`(s), a `gold_source_span` (file +
  heading the gold chunk came from, for audit), and `expect_miss: true|false`;
  generate `frozen_embeddings.json` with **real Titan v2** (the `--bedrock`
  regenerate path, run once in this environment); `graphrag.vector_eval` —
  `evaluate` (cosine knn over frozen vectors) + `VectorEvalResult` (hit@k, MRR,
  per-query rank). Document the regenerate command.
- **Done when:** `test_vector_eval.py` green with real frozen vectors; the
  entity-led misses are gold-present-but-unretrieved (AC6).
- **Pre-check first:** before authoring the frozen vectors, confirm the fixture
  corpus actually yields ≥2 realizable entity-led honest misses (the
  fixture-prose-density risk in Risks); if it doesn't, extend the pinned fixture
  prose rather than contriving a miss.

### T7 — CLI verbs: vector-ingest, vector-query, vector-eval
- **Depends on:** T4, T5, T6
- **Touches:** packages/graphrag/src/graphrag/cli.py, tests/test_vector_cli.py
- **Tests:** `test_vector_cli.py` — `vector-query --q …` over the fixture (offline
  embedder, in-memory store) prints the ranked provenance trace; `vector-ingest`
  prints chunk counts by source + dims; `vector-eval --query-set …` prints
  per-query rank → hit@k/MRR → PASS/FAIL and **exits non-zero on FAIL** (asserted,
  mirroring `resolve-eval` in `cli.py`); all three satisfy the narratability
  assertion. `# STUB: AC10`, `# STUB: AC5`, `stub: true`.
- **Approach:** extend `cli.py` — `vector-ingest`, `vector-query`, `vector-eval`
  subparsers; `--opensearch-endpoint`/`--region`/`--bedrock` flags select the live
  vs offline backend+embedder (offline default), mirroring `_target_store`.
- **Done when:** `test_vector_cli.py` green (AC5, AC10).

### T8 — OpenSearch vector smoke probe (Lambda handler)
- **Depends on:** T2, T4
- **Touches:** packages/graphrag/src/graphrag/vector_smoke_lambda.py, tests/test_vector_smoke_lambda.py
- **Tests:** `test_vector_smoke_lambda.py` — with the embedder + OpenSearch store
  mocked, `lambda_handler` embeds text, indexes a unique chunk, retrieves it via
  `knn`, returns `{"ok": true, "retrieved_id": …}`, and calls cleanup (`delete`).
  *(goal-based)*
- **Approach:** `graphrag.vector_smoke_lambda` — twin of `smoke_lambda.py`:
  reads `OPENSEARCH_ENDPOINT`/`AWS_REGION`, embeds via `BedrockTitanEmbedder`,
  `index_chunk` a unique chunk, `knn` it back, assert returned, `finally` delete.
- **Done when:** the handler round-trips through mocks (AC7 offline guard; live in T11).

### T9 — Fargate dual-write
- **Depends on:** T1, T2, T4
- **Touches:** apps/ingestion/entrypoint.py, apps/ingestion/tests/test_entrypoint.py
- **Tests:** `test_entrypoint.py` (extend) — with S3 download, Bedrock, Neptune,
  and OpenSearch all mocked, the entrypoint runs one parse and invokes **both** the
  graph write (`ingest`) and the vector write (chunk→embed→index). *(goal-based)*
- **Approach:** extend `entrypoint.py` — after the graph `ingest`, build the
  `OpenSearchVectorStore` + `BedrockTitanEmbedder` from env
  (`OPENSEARCH_ENDPOINT`), `chunk_corpus` + embed + `index_chunk`. One parse, two
  writes.
- **Done when:** `test_entrypoint.py` green (AC9).

### T10 — IaC: OpenSearch domain + bedrock-runtime endpoint + vector probe + roles
- **Depends on:** none (parallel-eligible; disjoint from the Python lib)
- **Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py
- **Tests:** `test_stack.py` (extend) — synth asserts: a **single-node** OpenSearch
  domain (1 data node, no dedicated masters) with **encryption-at-rest +
  node-to-node encryption + enforce-HTTPS**, in the **private isolated** subnets,
  **no public access**, with a **domain access policy scoped to the task + probe
  role ARNs** (resource-side IAM enforcement, not `AllPrincipals`); the
  **`bedrock-runtime`** interface VPC endpoint added to
  the set; the vector probe Lambda + the Fargate task role each carry
  `es:ESHttp*` scoped to the **domain ARN** and `bedrock:InvokeModel` scoped to the
  **Titan model ARN**, with **no wildcard `Resource`**; the `es:ESHttp*` action
  prefix uses the **same `"es"` constant** the adapter signs with (T4); SG path
  Fargate/probe to OpenSearch 443; descriptions use the EC2 ASCII charset; and the
  Budgets limit/threshold equals the re-evaluated value (Rollout). `# STUB: AC8`,
  `stub: true`.
- **Approach:** extend `graphrag_stack.py` — add `bedrock-runtime` to
  `_INTERFACE_ENDPOINTS`; `_opensearch(vpc)` (single-node `opensearchservice.Domain`
  or `CfnDomain`, VPC + SG, the three encryption flags, IAM access policy scoped to
  the task/probe roles); a scoped `_bedrock_invoke` + `_opensearch_data_access`
  policy statement helper (no wildcard); `_vector_smoke_lambda(...)`; grant the
  Fargate task role both; add `OPENSEARCH_ENDPOINT` to the task + probe env;
  `CfnOutput` the domain endpoint + probe name.
- **Done when:** `test_stack.py` green; `cdk synth` clean (AC8).

### T11 — Live deploy + retrieve probe (this environment)
- **Depends on:** T8, T10
- **Tests:** live — `scripts/deploy.sh`, invoke the vector probe Lambda, assert
  `{"ok": true, "retrieved_id": …}` (the ingested chunk comes back via k-NN),
  `scripts/destroy.sh`. *(live smoke — the AC7 active end-to-end check)*
- **Approach:** deploy the updated stack; build/push the ingestion image if the
  dual-write path is exercised; invoke `aws lambda invoke` on the vector probe;
  record the JSON result + teardown in `deployment-and-verification.md`. Also record
  there a **manual** live corpus-backed retrieval note (run the dual-write, confirm
  a known corpus chunk is queryable) — this is documentation, **not** a slice-2 AC
  (live `vector-query` is the slice-3 query-Lambda path; see Rollout).
- **Done when:** the live probe returns `ok: true` and the stack is destroyed (AC7).

### T12 — Docs + capture-learnings + spec tick
- **Depends on:** T1-T11
- **Tests:** n/a (docs).
- **Approach:** update `docs/architecture/overview.md` (new modules + the vector
  half landed); add `docs/architecture/infrastructure.md` (the **living
  infrastructure lens** — topology + inventory + idle cost + cross-cutting infra
  patterns + a per-slice evolution log, grown each infra slice);
  `docs/architecture/deployment-and-verification.md` (the vector
  probe + live result + verification-ladder row), `docs/architecture/security.md`
  (OpenSearch/Bedrock boundaries, least-privilege, display-only-no-injection-control
  note), `docs/specs/README.md` (status), `docs/product/changelog.md`; add
  knowledge entries to `docs/knowledge/patterns.jsonl`; tick the spec's met ACs and
  set Status. Record any new dependency decision (expected: none) in
  `packages/graphrag/AGENTS.md`. Stop carrying the SCA gap across slices — wire
  `pip-audit` (or Dependabot) into CI and drop it from `security.md`'s "out of
  scope" list (not a spec AC; security-review nit #5).
- **Done when:** docs match the code; spec ACs ticked; gates green.

## Rollout

Per the design doc's phased rollout, slice 2 extends the **same** IaC stack:

- **Provisions (added to the slice-1 stack):** a single-node OpenSearch domain
  (k-NN, encrypted, enforce-HTTPS, VPC-private), the `bedrock-runtime` interface
  VPC endpoint, and the vector probe Lambda; the Fargate task role + probe role gain
  scoped OpenSearch-write + Bedrock-invoke permissions.
- **Standing cost:** OpenSearch single-node is a **new standing** cost (does not
  scale to zero — ADR-0002), and the `bedrock-runtime` interface endpoint bills
  hourly per AZ — both land on top of standing Neptune Serverless. T10 re-evaluates
  the existing `$50` monthly Budgets limit / 80% threshold
  (`graphrag_stack.py` `_budget_alarm`) against the new idle floor and either
  confirms it holds (with the arithmetic) or raises it and asserts the new value in
  the synth test. The README gets **both** the Neptune *and* the OpenSearch idle
  `$/hr` figures written out (the current README carries neither — add both, don't
  append "alongside" an absent note). All removed by `destroy`.
- **Live corpus-backed query is out of slice-2 scope.** AC7's probe proves a
  *synthetic* index→retrieve round-trip against the live domain; making the
  *deployed corpus* queryable end-to-end from a live `vector-query` needs the in-VPC
  query Lambda, which is **slice 3**. The dual-write (T9/AC9) runs the corpus into
  the live domain, but a live corpus-backed `vector-query` is recorded as a manual
  step in `deployment-and-verification.md`, not a slice-2 AC.
- **Deploy:** `cdk deploy` provisions; `cdk run-task` (or the deploy script) runs
  the dual-write ingestion once; the vector probe verifies live retrieval.
  **Destroy:** `cdk destroy` removes every billable resource (incl. the domain).
- **Rollback:** `destroy` + redeploy; state reproducible from the S3 snapshot — no
  migration, no irreversible step (ADR-0002).
- **Deployment sequencing:** OpenSearch domain + endpoint before the probe/ingestion
  that use them (CDK dependency order handles this).

## Risks

- **OpenSearch SigV4 service name / request shape.** Wrong service name (`es` vs
  `aoss`) or body shape fails only against a live domain. *Mitigation:* mock-tested
  adapter + the live probe (T11) before declaring done; `es` is the provisioned-domain
  service.
- **VPC OpenSearch access policy + SG path.** A silent misconfig yields timeouts,
  not errors (same 3am risk as Neptune). *Mitigation:* synth assertions for the
  SG/role scope + the loud live probe.
- **Frozen vectors drift from live Titan over time.** *Mitigation:* documented as a
  snapshot (like the pinned corpus excerpts); the `--bedrock` path regenerates, and
  the live probe always exercises the current model.
- **Query-set fairness is a judgment call.** *Mitigation:* queries drawn from the
  real pinned fixture; the labeled honest miss keeps it from being a strawman; the
  adversarial reviewer checks the curation.
- **Standing OpenSearch cost** (cloned-and-forgotten footgun). *Mitigation:* the
  existing Budgets alarm; README cost note; `destroy` removes it.

## Notes / declined patterns

- **Declined:** `opensearch-py` runtime dependency — SigV4 + urllib already does it
  (the Neptune adapter proves the pattern), and "dependencies are forever".
- **Declined:** a live-only credible-baseline eval — fails the reproducibility
  principle; frozen real vectors give both honesty and CI-determinism.
- **Declined:** pushing chunking/embedding into a separate app — it's library code
  in `packages/graphrag`, consumed by both the CLI and the Fargate task.
- **Surfaced assumption:** the fixture corpus prose is *representative* of the real
  repos' KEP/SIG README shape — the hit@5 bar is honest only if the fixture mirrors
  real prose density; the fixture is built from real, pinned README excerpts.

## Changelog

- 2026-06-24 — Initial plan (slice 2). Inside-out build mirroring slice 1; frozen
  real Titan v2 vectors for the credible-baseline eval; live probe in scope.
- 2026-06-24 (EXECUTE) — the credible-baseline eval uses a **dedicated vector eval
  corpus** under `tests/fixtures/vector/corpus/` (real pinned excerpts, a superset
  of the graph fixture's prose plus extra KEP READMEs). Rationale: the shared
  graph fixture is only ~6 prose docs (~13 chunks), too few for a selective hit@5
  with realizable honest misses; and growing the *shared* corpus would break
  slice-1's fixed node/merge counts (`test_entrypoint.py` asserts `nodes == 22`).
  The eval corpus is decoupled, so slice 1 stays green and the bar is honest.
