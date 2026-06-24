# Spec: vector-rag-baseline

- **Status:** Approved
- **Shape:** mixed
- **Plan:** [`plan.md`](plan.md)
- **Brief:** [`docs/product/briefs/graphrag-aws-demo.md`](../../product/briefs/graphrag-aws-demo.md)
- **Constrained by:** [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (slice-3 seed-and-expand reads the chunk→entity metadata this slice writes), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (single-node OpenSearch + `bedrock-runtime` endpoint in the ephemeral VPC), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC tool), [design doc](../../architecture/graphrag-aws-architecture/design.md) (D2 store topology; D1 chunk-carries-entity-IDs)
- **Contract:** none (a CLI + internal Python interfaces; no repo-root `contracts/` API surface, as in slice 1)

> Slice 2 of the brief's Spec map. Builds the **vector half** of the demo on the
> stores, corpus, and ephemeral VPC stack that slice 1
> ([`graph-ingestion-resolution`](../graph-ingestion-resolution/spec.md)) stood up.
> `Depends on:` slice 1 (reuses its parse → `ParsedDoc` pipeline, fixture corpus,
> CDK stack, store-adapter + smoke-probe patterns).

## Objective

A solution architect evaluating GraphRAG needs to *see* the plain vector-RAG
baseline run on the same corpus the graph half runs on, and the comparison is only
honest if the baseline is **credible, not a strawman** (charter principle 2). This
slice ships that baseline end-to-end: it chunks the **prose-rich doc subset** of
the Kubernetes corpus (SIG `README.md` charters and KEP `README.md` bodies),
embeds each chunk with **Amazon Titan Text Embeddings v2** (Bedrock, 256-dim,
normalized), indexes the vectors into a **single-node Amazon OpenSearch domain with
k-NN**, and answers a semantic question through a **`graphrag vector-query` CLI**
that returns the top-k chunks with a **legible retrieval trace and source
provenance** (which chunk, from which document + heading, at what similarity score,
owning which entity). Fairness is a first-class, mechanical deliverable: a
**curated semantic-led query set** — drawn from the real pinned fixture corpus and
each labeled with its gold-relevant chunk — achieves **hit@5 = 1.0** against real
Titan v2 embeddings (reproducible in CI from committed frozen vectors), and the
same set **documents ≥2 entity-led queries the baseline honestly misses** — each
with a gold chunk that *exists* in the corpus but isn't retrieved (e.g. "all KEPs
the SIG `@thockin` tech-leads owns" — the scoping cases the slice-3 graph mode
wins) — so the baseline's strengths *and* its real limits are both visible. The
live path is proven, not asserted: an in-VPC **probe** embeds text via Titan v2,
indexes a chunk into the deployed OpenSearch domain, and **retrieves that ingested
chunk back via k-NN** through the same adapter the CLI uses, then cleans up.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule.

### Always do

- **Mirror slice-1's store-adapter shape.** The OpenSearch adapter follows
  `store/neptune.py` exactly: SigV4 over HTTPS via an **injectable** HTTP client,
  `https://`-enforced with TLS verification on, credentials from the default
  botocore provider chain (no plaintext-credential read at the call site), and a
  non-2xx response raises loudly with the body. Embeddings and retrieval requests
  carry values in the request **body/parameters**, never string-interpolated.
- **Keep the baseline credible.** Curate the query set so vector has genuine wins
  *and* at least one honest miss; the fairness bar is asserted by a test, not by
  prose. Query selection — not corpus structure — decides whether vector looks
  strong or weak (charter principle 4 / principle 2).
- **Make every stage narratable** (charter principle 1). `vector-ingest`,
  `vector-query`, and `vector-eval` each print an ordered, human-readable trace; no
  black-box hop.
- **Use `amazon.titan-embed-text-v2:0` at 256 dimensions with `normalize=true`**,
  and keep the chunk → owning-entity-ID metadata on every indexed chunk (slice-3
  seed-and-expand depends on it — ADR-0001).
- **Keep teardown a feature** (charter principle 4): every billable resource this
  slice adds (OpenSearch domain, `bedrock-runtime` endpoint, probe Lambda) is
  removed by `cdk destroy`; the OpenSearch domain is single-node and the smallest
  demoable instance.

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** The OpenSearch and
  Bedrock calls are designed to need **no** new runtime dependency (SigV4 + urllib,
  as Neptune does); reach for `opensearch-py`/`requests` only with sign-off, and
  record it in `packages/graphrag/AGENTS.md` (AGENTS.md *Check before acting*).
- **Changing the chunk record or its entity-ID metadata** once slice 3 reads it
  (it becomes a published internal interface the moment seed-and-expand consumes
  it).
- **Changing the embedding model, dimension, or `normalize` setting** — it changes
  the frozen-embeddings fixture and the OpenSearch `knn_vector` mapping together.

### Never do

- **Never implement the hybrid orchestration, the three-mode comparison runner, or
  graph↔vector merge here** — that is slice 3 (`hybrid-orchestration`). This slice
  ships the vector mode standalone.
- **Never implement permission-filtered retrieval (synthetic visibility labels) or
  incremental delta re-ingest** — slices 4 and 5. Vector ingest is a full,
  idempotent (re)index only.
- **Never add a new top-level directory or a new module boundary beyond the
  existing `packages/graphrag/`, `apps/ingestion/`, `apps/infra/` surfaces**
  (AGENTS.md: top-level directories need an RFC). New code lands as modules inside
  those.
- **Never expose a public OpenSearch endpoint or a public probe URL**, and never
  reach OpenSearch from the laptop CLI directly — the domain is VPC-private (the
  CLI's live path is in-VPC compute, exactly as Neptune is in slice 1).
- **Never read embeddings off the network in a unit test** — unit tests use the
  injectable offline embedder / a mock; real Titan calls are the live probe and the
  opt-in `--bedrock` path only.

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1–AC5, AC10 — TDD (fast unit/integration over the fixture corpus).**
  Chunking, the in-memory cosine k-NN, the embedder request shape, the OpenSearch
  adapter, the retrieval-trace structure, and the CLI verbs are deterministic over
  the bundled fixture with the **offline embedder** (or a mock for the network
  adapters); each carries a red-stub-first construction test in `plan.md`. The
  OpenSearch adapter is tested against a **mocked HTTPS/SigV4 endpoint** (no live
  domain), asserting the request is `https://`, SigV4-signed for service `es`,
  body-parameterized, and that a non-2xx raises with the body.
- **AC2 also pins a security posture:** the embedder/adapter never interpolate
  caller values into a URL or query string; the `ruff` `S` ruleset stays enabled
  (catches an unsafe-request regression). The corpus text embedded is untrusted
  external input, but it is **display-only** in this slice (no tool execution), so
  the prompt-injection boundary is documented, not a control here (design doc
  Risks).
- **AC6 — goal-based check, framed as a pytest (the "credible-baseline open
  confirmation").** `vector-eval` loads the curated query set + the committed
  **frozen real Titan v2 vectors**, runs cosine k-NN, and the test **asserts
  hit@5 = 1.0 on the semantic-led queries** and that the labeled entity-led
  query is **not** retrieved in top-5 (the honest miss). Frozen real vectors keep
  the bar honest (real model output) *and* reproducible in CI (charter principle
  3); a documented `--bedrock` path regenerates them and runs against live Titan.
- **AC7 — live deploy + retrieve probe (active end-to-end smoke).** A scale-to-zero
  in-VPC probe Lambda embeds text via Titan v2, indexes a unique chunk into the
  live OpenSearch domain, retrieves it via k-NN through the **same**
  `OpenSearchVectorStore` the CLI uses, asserts the ingested chunk comes back, and
  cleans up — invoked end-to-end against the deployed stack in this environment
  (the slice-1 `smoke_lambda` pattern; live verification is available here, so it
  is **not** deferred).
- **AC8 — goal-based check (`cdk synth` + `aws_cdk.assertions.Template`).** The
  stack is synthesized in-process and the test asserts: a single-node OpenSearch
  domain with **encryption at rest + node-to-node encryption + enforce-HTTPS**,
  VPC-resident in the private isolated subnets, **not** public; the
  `bedrock-runtime` interface VPC endpoint; and the probe + Fargate roles
  **least-privilege** (`es:ESHttp*` scoped to the domain ARN, `bedrock:InvokeModel`
  scoped to the Titan model ARN — no wildcard `Resource`). CDK-env-gated.
- **AC9 — goal-based check.** The Fargate entrypoint, over one parse, writes both
  stores (graph + vector); tested with the S3 download, Bedrock, and both stores
  mocked, asserting both write paths are invoked.

Gates: `ruff` (lint+format, `S` security ruleset), `mypy` (typecheck), `pytest`
(tests). Already wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [ ] **AC1 — Chunk the prose-rich subset, with provenance + entity IDs.** Over the
  fixture corpus, chunking yields chunks from **SIG `README.md` charters and KEP
  `README.md` bodies only** (never `sigs.yaml`/`kep.yaml` structured data),
  heading-aware with a bounded chunk size and overlap; every chunk carries its
  provenance (source repo, doc path, nearest heading) and its **owning entity
  ID(s)**, **derived via slice-1's `normalize` functions so they are byte-identical
  to the graph node IDs** (`sig_id` → `sig:<slug>` for a SIG charter; `kep_id` →
  `kep-<number>` plus the owning `sig:<slug>` for a KEP README). Note the form
  asymmetry is slice-1's, not new: SIG/Person use `:`, KEP uses `-`. A doc with no
  prose body yields no chunks (no empty chunks). *(TDD)*
- [ ] **AC2 — Titan v2 embedder: correct request, offline-deterministic, injectable.**
  `BedrockTitanEmbedder` issues a well-formed Titan v2 request (model
  `amazon.titan-embed-text-v2:0`, `dimensions=256`, `normalize=true`) and parses
  the returned vector, verified against a **mock** (no live call); the Bedrock
  client is the **default botocore-chain client over TLS** (no `verify=False`, no
  plaintext-HTTP `endpoint_url` override); an **offline deterministic embedder**
  returns stable 256-dim vectors for CI; the `Embedder` is injected wherever
  embeddings are produced (no hard-coded Bedrock client at a call site). Retrieved
  corpus text is **display-only** in this slice (no LLM instruction surface, no tool
  execution — LLM01 is out of reach); it becomes control-bearing untrusted input
  the moment slice 3 routes it into Claude synthesis (isolate-and-no-instruction
  there). *(TDD with mock)*
- [ ] **AC3 — In-memory vector store k-NN.** `MemoryVectorStore.knn(vector, k)`
  returns the top-`k` chunks by **cosine similarity**, correctly ordered by score,
  bounded to `k`; an empty store returns `[]`; `k` larger than the corpus returns
  all chunks. *(TDD)*
- [ ] **AC4 — OpenSearch adapter is injection-safe and IAM-mediated.** The adapter
  indexes a chunk (vector + metadata) and runs a **k-NN query whose vector and
  values ride the request body, never string-interpolated**; it targets `https://`
  with TLS verification on, **SigV4-signs for service `es`** via the default
  botocore provider chain (no plaintext-credential read at the call site), creates
  the index with a `knn_vector` mapping of **dimension 256**, and a non-2xx
  response **raises loudly with the body**. Exercised (index + knn) against a
  mocked SigV4/HTTPS endpoint. *(TDD with mock)*
- [ ] **AC5 — CLI semantic query with a retrieval trace + provenance.** `graphrag
  vector-query --q "<text>"` returns the top-`k` chunks ranked by score, and its
  stdout names, in order: the query, the **embedding model + dimensions**, then per
  hit the **rank, similarity score, source repo + doc path + heading (provenance),
  and owning entity ID(s)** — legible enough to narrate. Verified on the
  semantic-led exemplar: "risks of in-place pod resize" → the top hit is a chunk of
  the **KEP-1287** (`sig-node`) README. Rendered text/provenance is the
  pinned, trusted-by-review fixture corpus for this slice (terminal control-char
  hardening is revisited if arbitrary corpora become renderable). *(TDD + narratability check)*
- [ ] **AC6 — Credible (fair) baseline: curated query set clears the bar.** The
  curated query set holds **≥5 semantic-led queries** and **≥2 entity-led queries**,
  each authored as a **natural architect-style question** (not a paraphrase of its
  gold chunk's wording — the win must be semantic, not lexical-overlap), and each
  labeled with its gold-relevant chunk **plus the exact source span that chunk was
  drawn from**, so the curation is auditable. The **semantic-led** queries achieve
  **hit@5 = 1.0** against **real Titan v2 256-dim embeddings** (reproducible in CI
  from committed frozen vectors); the **entity-led** queries are **honest misses** —
  each one's gold chunk **exists in the corpus** (asserted) but is **not** in top-5
  (the scoping cases slice-3 graph wins, e.g. "all KEPs the SIG `@thockin`
  tech-leads owns"), so the baseline's strengths *and* real limits are both shown —
  credible, not a strawman in either direction (charter principle 2). `vector-eval`
  asserts the semantic bar AND each labeled miss (including gold-present). *(goal-based pytest)*
- [ ] **AC7 — Live deploy + retrieve probe (in-VPC).** A scale-to-zero in-VPC probe
  embeds text via Titan v2, indexes a unique chunk into the **live** OpenSearch
  domain, retrieves it via k-NN through the same `OpenSearchVectorStore` the CLI
  uses, asserts the ingested chunk is returned, and cleans up — returning
  `{"ok": true, ...}`. Verified live against the deployed stack. *(live smoke)*
- [ ] **AC8 — IaC synthesizes the slice-2 additions, securely.** The CDK app
  synthesizes: a **single-node** OpenSearch domain with **encryption at rest +
  node-to-node encryption + enforce-HTTPS**, VPC-resident in private isolated
  subnets and **not public**, whose **domain access policy restricts to the specific
  task + probe role ARNs** (the resource-side control — not `AllPrincipals` — so IAM
  auth is actually enforced, not merely network-gated); the **`bedrock-runtime`**
  interface VPC endpoint; and a vector probe Lambda whose role — and the Fargate
  task role — are **least-privilege** (`es:ESHttp*` scoped to the domain ARN,
  `bedrock:InvokeModel` scoped to the Titan model ARN; **no wildcard `Resource`**) —
  per ADR-0002. *(goal-based synth, CDK-env-gated)*
- [ ] **AC9 — Single-parse dual-write ingestion.** The Fargate entrypoint, over one
  parse of the corpus, writes **both** stores — the graph (slice 1) and the vector
  index (chunk → embed → index) — so the two never diverge (charter pattern 2).
  *(goal-based)*
- [ ] **AC10 — Every stage is narratable (ordered trace).** `vector-ingest` prints
  chunk counts by source + the embedding dims; `vector-query` prints query →
  model → ranked hits-with-provenance in order; `vector-eval` prints per-query
  hit-rank → hit@k/MRR → PASS/FAIL **and exits non-zero on FAIL** (a usable CI
  gate, mirroring slice-1 `resolve-eval`). No black-box hop (charter principle 1).
  *(goal-based)*

## Assumptions

- Technical: runtime is Python 3.11+; runtime deps are `pyyaml` + `boto3`, infra
  extra is `aws-cdk-lib`/`constructs`, dev is `pytest`/`ruff`/`mypy` with the `S`
  ruleset (source: `pyproject.toml`).
- Technical: the vector store seam mirrors slice 1 — a `VectorStore` ABC with an
  in-memory impl (offline/test/demo) + an OpenSearch adapter (SigV4 over HTTPS via
  urllib, injectable HTTP client, `https://`-enforced, TLS-verified), so **no new
  runtime dependency** is needed (source: `packages/graphrag/src/graphrag/store/base.py`,
  `store/neptune.py`).
- Technical: embeddings use Titan v2 `amazon.titan-embed-text-v2:0` via
  `bedrock-runtime`, output dims selectable 256/512/1024 (default 1024) with a
  `normalize` param, ≤8192 input tokens; this slice pins **256 dims, normalized**
  (97% of 1024's retrieval accuracy per AWS, cheaper k-NN storage — fits the
  cost-first posture) (source: `docs/rfc/0001-notes/aws-feasibility.md` §5;
  https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-titan-text-embeddings-v2.html).
- Technical: this slice adds a single-node OpenSearch domain (k-NN) + the
  `bedrock-runtime` interface VPC endpoint to the **existing** CDK stack — both were
  explicitly deferred out of slice 1 to here (source:
  `apps/infra/stacks/graphrag_stack.py` `_INTERFACE_ENDPOINTS` comment; ADR-0002;
  design.md D2).
- Technical: the prose-rich doc subset is SIG `README.md` charters + KEP `README.md`
  bodies (already parsed by `sources.py` into `ParsedDoc.markdown`), not the
  structured YAML; each chunk carries owning-entity IDs which slice-3 hybrid
  seed-and-expand reads (source: charter pattern 2; design.md D1 step 1).
- Technical: the credible-baseline eval runs against **frozen real Titan v2 vectors**
  committed as a fixture (honest *and* CI-reproducible), with a documented
  `--bedrock` regeneration/opt-in path; the live OpenSearch+Bedrock round-trip is
  proven by the in-VPC probe (source: user confirmation 2026-06-24; slice-1
  `smoke_lambda.py` + `eval.py` patterns).
- Technical/Process: live AWS deploy + retrieve verification **is available in this
  environment**, so the probe runs live as part of this slice and AC7 is **not**
  deferred to the backlog (contrast slice-1 AC9) (source: user confirmation
  2026-06-24).
- Process: this spec is full work-loop mode (security boundary — Bedrock +
  OpenSearch network I/O; structural — new modules + new infra), constrained by
  ADR-0001/0002/0003 + the design doc, derived from brief `graphrag-aws-demo.md`
  (source: `docs/CONVENTIONS.md` risk triggers; brief Spec map row 2).
- Product: the audience is a solution architect comparing retrieval modes; this
  slice ends at the standalone vector mode + its credible-baseline confirmation —
  the side-by-side comparison is slice 3 (source: user confirmation 2026-06-24;
  charter scope).

## Changelog

- 2026-06-24 — Spec authored (slice 2). Assumptions surfaced and confirmed; live
  probe in scope (not deferred) per the environment supporting live deploy; fair
  baseline mechanized as a curated-query-set hit@5 bar against frozen real Titan v2
  vectors with a documented honest miss.
- 2026-06-24 — `Approved` after spec-stage adversarial + security review.
  Adversarial: fixed the KEP entity-ID form (`kep-<n>`, derived via `normalize`, so
  the chunk→graph join slice 3 depends on stays byte-identical); hardened the
  fairness bar (natural-question queries + audit span + ≥2 gold-present honest
  misses); pinned `vector-eval` non-zero exit; reconciled the standing-cost budget.
  Security: pinned the OpenSearch **domain access policy** scoping (AC8), the Bedrock
  **default-TLS client** (AC2), the display-only→slice-3-control forward reference,
  and the trusted-fixture rendering note.
