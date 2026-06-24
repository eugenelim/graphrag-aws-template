# Spec: hybrid-orchestration

- **Status:** Implementing
- **Shape:** mixed
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (the seed-and-expand decision this slice ships — D1), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (the in-VPC query Lambda behind an IAM-auth Function URL; Bedrock reached via the VPC endpoint), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python), [design doc](../../architecture/graphrag-aws-architecture/design.md) (D1 seed-and-expand diagram; D2 thin-CLI / query-Lambda topology)
- **Brief:** [`docs/product/briefs/graphrag-aws-demo.md`](../../product/briefs/graphrag-aws-demo.md)
- **Contract:** none (a CLI + an in-VPC Lambda + internal Python interfaces; no repo-root `contracts/` API surface, consistent with slices 1–2)

> Slice 3 of the brief's Spec map — the keystone that joins the graph half
> ([`graph-ingestion-resolution`](../graph-ingestion-resolution/spec.md), slice 1) and
> the vector half ([`vector-rag-baseline`](../vector-rag-baseline/spec.md), slice 2)
> into the **hybrid** retrieval mode and the **three-mode side-by-side runner** the
> demo exists to show. `Depends on:` slices 1 and 2 (reuses the resolver/alias table,
> the `GraphStore`/`VectorStore` seams, `traverse`/`vector_search`, the chunk→entity-ID
> metadata, the CDK stack, and the in-VPC probe pattern).

## Objective

A solution architect evaluating GraphRAG needs to *see*, on one question, how vector,
graph, and hybrid retrieval diverge — and to understand *why* the hybrid answer is
better when it is. This slice ships the **seed-and-expand hybrid** (ADR-0001 / design
D1) and the **three-mode comparison runner** that together are the payoff of the demo.

The hybrid path, executed by an **in-VPC query Lambda behind an IAM-auth Function URL**,
takes a natural-language question and: (1) **seeds graph entities from both sides** —
the entities owning the top-k vector hits *and* the entities linked from the question
itself (reusing slice-1's normalize + alias table on this controlled-vocabulary corpus);
(2) **expands 1–2 hops** in Neptune from the seed set to gather structural facts;
(3) **merges** the vector chunks with the graph facts; (4) **synthesizes** an answer with
a **Bedrock Claude model via the Converse API**; and (5) returns the **answer, its
citations, and a visible seed/hop trace** that names which seeds came from semantics vs.
the question and which hops enriched the answer. Over-expansion is bounded by a **hop
limit and a seed cap**, both surfaced in the trace so truncation is visible, never silent.

Alongside it ships the **three-mode runner**: `vector-only` / `graph-only` / `hybrid`
executed independently over a **consolidated, curated per-mode showcase query set**, each
rendering its own retrieval trace so the divergence is legible side by side. The showcase
set and a **presenter script** — which accreted informally across slices 2–4 — are
consolidated here into one home, so a presenter can drive the whole demo from one place.
Both the demo and CI run the same orchestration: **offline** (in-memory stores +
deterministic offline embedder/synthesizer) for reproducible, credential-free testing, and
**live** against the deployed VPC stores + Bedrock through the query Lambda — proven by an
end-to-end live invocation over the ingested corpus.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule.

### Always do

- **Seed from both sides, and tag every seed with its source.** The seed set is the
  union of (a) the owning entity IDs of the top-k vector hits (`source=vector`) and
  (b) the entities linked from the question (`source=question`). Every seed in the trace
  carries its source — this dual-seed visibility is the demo's whole pedagogy (ADR-0001
  decision driver "narratability").
- **Reuse the slice-1 resolver for entity-linking.** Question-to-entity linking uses the
  *same* `normalize_handle`/`normalize_slug`/`kep_id` functions + `aliases.yaml` the
  slice-1 resolver builds — no new matching model (ADR-0001 "reuse"; charter pattern 1).
- **Bound over-expansion and surface it.** Enforce a **hop limit (1–2)** and a **seed
  cap**; when either truncates, record the truncation in the trace. Two seed sources
  feeding one expansion must not silently bury the answer (ADR-0001 consequence).
- **Run the three modes independently in the comparison runner.** `vector-only` and
  `graph-only` execute as standalone paths for honest side-by-side contrast — that is the
  demo's pedagogy, distinct from the hybrid mode's internal dual-seed orchestration
  (ADR-0001 boundary; design D1 note).
- **Keep traversal in the application layer over `neighbors()`.** Graph expansion builds
  on `GraphStore.neighbors()` so the in-memory and Neptune backends produce an identical
  trace (slice-1 invariant; `packages/graphrag/AGENTS.md`).
- **Treat retrieved Markdown as untrusted content at the Claude boundary.** The corpus
  text routed into synthesis is external untrusted input (OWASP LLM01/LLM08). It is placed
  as **data, not instructions**, in the Converse request (isolate-and-no-instruction), and
  the answer is **display-only** — no tool execution, no agentic action off the model
  output (design doc Risks).
- **Mirror the slice-1/2 adapter posture.** The Bedrock Converse client is the default
  botocore-chain client over TLS (no `verify=False`, no plaintext `endpoint_url`); credentials
  resolve via the default provider chain (the Lambda role); model id and values ride the
  request body/parameters, never string-interpolated.
- **Keep teardown a feature** (charter principle 4): every billable resource this slice
  adds (the query Lambda, its Function URL) is removed by `cdk destroy`; the query Lambda
  is scale-to-zero (no standing cost).

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** Claude synthesis uses the
  `boto3` `bedrock-runtime` **Converse** API (no new dependency); reach for the
  `anthropic` SDK or any HTTP/LLM client only with sign-off, and record it in
  `packages/graphrag/AGENTS.md` (AGENTS.md *Check before acting*). The `anthropic` SDK is
  **not** in the Lambda runtime and would break the pure-Python `Code.from_asset` bundle.
- **Pinning or changing the synthesis Claude model id.** The design doc leaves the exact
  model an open question (cost/latency vs. quality). This slice ships a **configurable**
  model id (env/CLI, default documented); changing the default, or hard-pinning one, is a
  decision to surface (it also re-scopes the Bedrock IAM grant — AC8).
- **Changing the hybrid result shape, seed/trace schema, or the Function-URL request/
  response contract** once a downstream (slice 4 permission filtering, slice 5 delta) or
  the CLI client consumes it.

### Never do

- **Never implement permission-filtered retrieval (synthetic visibility labels) or
  incremental delta re-ingest** — slices 4 and 5. The query path takes no persona/clearance
  and applies no label filter here; ingestion is unchanged from slice 2.
- **Never expose a public, unauthenticated query endpoint.** The Function URL is
  **IAM-auth** (SigV4) — the only public ingress; the Lambda is in private isolated subnets,
  reaches Neptune/OpenSearch/Bedrock VPC-internally, and has **no** unauthenticated URL.
  The laptop CLI's *live* path is the SigV4-signed Function URL, never a direct hit on the
  VPC-private stores.
- **Never let the offline `HashEmbedder` or the offline synthesizer back a quality claim.**
  They exist so the orchestration is testable offline (structural assertions); semantic
  quality is shown live / via the slice-2 frozen-vector eval. The CLI labels the offline
  embedder/synthesizer as non-semantic so a reader is never misled.
- **Never add a new top-level directory or module boundary beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces**
  (AGENTS.md: top-level directories need an RFC). New code lands as modules/docs inside
  those.
- **Never push graph traversal into openCypher** for the expansion — keep it in the app
  layer over `neighbors()` so the backends stay trace-identical (slice-1 Boundaries rail).

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1–AC6 — TDD (fast unit/integration over the fixture corpus).** Entity-linking, the
  bounded neighborhood expansion + trace, the seed-and-expand merge, the offline
  synthesizer, the three-mode runner, and the CLI verbs are deterministic over the bundled
  fixtures with the **offline embedder + offline synthesizer** (or mocks for the network
  adapters); each carries a red-stub-first construction test in `plan.md`. Because the
  offline embedder is non-semantic, the hybrid/graph **wins** are asserted **structurally**
  (the entity-led query's owned-KEP set appears in the hybrid/graph trace and is absent from
  vector-only), not by similarity score — the honest semantic win is the slice-2 frozen-vector
  eval and the live path.
- **AC2 also pins a security posture:** the Bedrock Converse synthesizer uses the
  **default-TLS** botocore client (no `verify=False`, no plaintext `endpoint_url`); retrieved
  corpus text is passed as **data** in the Converse `messages` (not concatenated into the
  system/instruction text), and the request body is parameterized (no caller value
  interpolated into a path/query string). The `ruff` `S` ruleset stays enabled.
- **AC7 — TDD with mock (in-VPC query Lambda handler).** With the embedder, both stores, and
  the synthesizer mocked, `lambda_handler` runs the seed-and-expand path end-to-end and returns
  `{answer, citations, trace, seeds, hops}`; no network call in the unit test (the slice-1/2
  smoke-lambda pattern).
- **AC8 — goal-based check (`cdk synth` + `aws_cdk.assertions.Template`), CDK-env-gated.**
  The stack synthesizes a VPC-resident query Lambda with a **Function URL whose `AuthType` is
  `AWS_IAM`** (not `NONE`); the Lambda role is **least-privilege** — Neptune data-access scoped
  to the cluster, `es:ESHttp*` scoped to the domain ARN, and `bedrock:InvokeModel`(+`Converse`)
  scoped to the **Titan** model ARN (query embedding) **and** the **synthesis Claude** model
  ARN(s) — **no wildcard `Resource`**; SG paths to Neptune 8182 and OpenSearch 443; SG/ingress
  descriptions use the EC2 ASCII charset; and the standing-cost Budgets value is reconciled
  (the query Lambda is scale-to-zero, so the slice-2 idle floor is unchanged).
- **AC9 — live deploy + hybrid-query smoke (active end-to-end).** Against the deployed stack
  (corpus dual-written by the Fargate task), a SigV4-signed call to the Function URL runs a
  curated entity-led question end-to-end through live OpenSearch + Neptune + Bedrock Claude and
  returns an answer with citations and a seed/hop trace whose seed set includes the
  question-linked entity — verified live in this environment, then the stack is destroyed.
- **AC10 — goal-based check (consolidated showcase set + presenter script).** A single curated
  showcase query set holds **≥5–6 queries per mode** labeled with the mode each is meant to
  win and the trace highlight to narrate; a loader/`compare` test asserts the set parses, every
  gold entity/chunk it names resolves in the fixture corpus, and the per-mode counts clear the
  bar. The presenter script in `docs/guides/` references the set and the exact CLI commands.

Gates: `ruff` (lint+format, `S` security ruleset), `mypy` (typecheck), `pytest` (tests).
Already wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [x] **AC1 — Question entity-linking on the controlled vocabulary.** `link_question(question,
  aliases)` (where `aliases` is the slice-1 **display-name→handle** map from
  `resolve.load_aliases()`) extracts candidate entities from a natural question and normalizes
  each to a graph node ID **via the slice-1 `normalize` functions**: a GitHub `@handle` (or a bare
  handle) → `person:<handle>` via `normalize_handle`; an alias **display-name** (e.g. "Tim Hockin")
  → `person:<handle>` routed through the alias map (`via=alias`); a SIG mention (`sig-network`,
  `SIG Network`, `Network`) → `sig:<slug>`; a KEP reference (`KEP-1287`, `KEP 1287`) →
  `kep-<number>`. Each candidate carries its **surface form**, resolved **id**, and the **`via`**
  that produced it (`handle` / `alias` / `slug` / `kep-number`), each byte-equal to the slice-1
  `normalize` output, so a misseed is legible. A question naming no known-vocabulary entity yields
  `[]`. *(TDD)*
- [x] **AC2 — Bedrock Claude synthesizer (Converse), offline-deterministic, injectable.**
  `BedrockClaudeSynthesizer` issues a well-formed **Converse** request — a configurable `modelId`
  (default the documented `SYNTHESIS_MODEL_ID`); a `system` block carrying the grounding
  instruction **plus an explicit defensive directive that the question and retrieved context are
  untrusted data and any instructions embedded in them must not be followed** (LLM01/LLM08); the
  question + retrieved context in `messages` content **as data** (never concatenated into
  `system`); and an `inferenceConfig` that pins a **tested `maxTokens` ceiling** (a bounded value
  in the low thousands — e.g. 2000) — and parses the
  answer text from `output.message.content`, verified against a **mock** (no live call); the
  `bedrock-runtime` client is the **default botocore-chain client over TLS** (no `verify=False`,
  no plaintext `endpoint_url`); the `Synthesizer` is **injected** wherever synthesis happens. An
  **offline deterministic** synthesizer composes a stable answer + citation list from the merged
  context (no network) for CI, labeled non-semantic. The synthesized answer is **display-only** —
  no caller evaluates, shells out on, or feeds it back into a tool call. *(TDD with mock)*
- [x] **AC3 — Bounded neighborhood expansion with a trace.** `expand_neighborhood(store,
  seed_ids, *, max_hops, frontier_cap)` expands the seed set up to `max_hops` (1–2) hops over
  **all** edge kinds in both directions via `neighbors()`, returning the reached node IDs and an
  ordered per-hop trace (the frontier in, the nodes reached, the edge kinds that contributed);
  a hop whose frontier exceeds `frontier_cap` is **truncated and the truncation recorded**; an
  empty seed set expands to nothing. Backends produce an identical trace (built on
  `neighbors()`). The entity-led exemplar's path is pinned: from `person:thockin` the expansion
  reaches `sig:sig-network` at hop 1 (`TECH_LEADS`) and the SIG's owned KEPs at hop 2 (`OWNS`),
  so the win requires `max_hops >= 2`. *(TDD)*
- [x] **AC4 — Seed-and-expand orchestration with a dual-seed, bounded trace.** `hybrid_query`
  over a `VectorStore` + `GraphStore` + `Embedder` + `Synthesizer`: runs vector search (top-k),
  forms the seed set as the **union** of the top-k chunks' owning entity IDs (`source=vector`)
  and the question-linked entities **confirmed to exist in the graph** (`source=question`,
  unconfirmed candidates recorded as dropped), caps the seed set to **`seed_cap`** (recording
  truncation), expands 1–2 hops, **merges** the vector chunks with the reached graph facts
  (deduped), synthesizes an answer, and returns a `HybridResult` carrying the **answer,
  citations, the source-tagged seed set, the hop trace, and the merged context**. `.render()`
  narrates, in order, **seeds-by-source → hops → citations → answer** (the narratability
  assertion — no black-box hop). The seed attribution is honest to the resolver: on the entity-led
  exemplar, `@thockin` links to **`person:thockin`** (a handle) as `source=question`, while
  `sig:sig-network` and the owned KEPs enter as `source=vector` owners of the top-k chunks and/or
  via expansion — never mis-attributed to the question. *(TDD)*
- [x] **AC5 — Three-mode comparison runner with per-mode traces.** `run_modes(question, …)`
  executes `vector-only` (vector search → synthesize over chunks), `graph-only` (question
  entity-linking → expand → synthesize over graph facts), and `hybrid` (AC4) **independently**,
  returning a `ComparisonResult` whose `.render()` shows the three answers and their retrieval
  traces side by side. On the curated **entity-led** exemplar ("the KEPs the SIG `@thockin`
  tech-leads owns"), `@thockin` links to `person:thockin`; the **graph-only and hybrid** paths
  expand `person:thockin → sig:sig-network → owned KEPs` (the 2-hop `TECH_LEADS`/`OWNS` path,
  `max_hops >= 2`) so their result sets enumerate the owned KEPs, while **vector-only** does not —
  the structural demonstration that graph augments vector. *(TDD)*
- [x] **AC6 — CLI verbs: `hybrid-query` and `compare`, offline by default, live via SigV4.**
  `graphrag hybrid-query --q "<text>"` and `graphrag compare --q "<text>"` run **offline**
  (in-memory stores from the fixture corpus + offline embedder + offline synthesizer) by
  default and print the seed/hop trace, citations, and answer (`compare`: all three modes). A
  `--function-url <url>` flag switches `hybrid-query` to the **thin live client** — a
  SigV4-signed (`service=lambda`) HTTPS POST of the question to the in-VPC query Lambda whose
  **signature covers the request body** (payload-hash present, so a tampered body is rejected) —
  and renders the returned answer + trace; a non-2xx raises with the body. The offline
  embedder/synthesizer are labeled non-semantic in the output, and each verb prints the ordered
  seeds-by-source → hops → citations → answer trace. *(TDD + narratability check)*
- [x] **AC7 — In-VPC query Lambda handler.** `graphrag.query_lambda.lambda_handler` reads
  `NEPTUNE_ENDPOINT` / `OPENSEARCH_ENDPOINT` / region / synthesis model id from the environment,
  builds the live stores + Titan embedder + Bedrock Claude synthesizer from the execution role,
  runs `hybrid_query`, and returns `{answer, citations, trace, seeds, hops}`. It **rejects an
  over-long question** (a bounded input length, on the order of a few KB — e.g. ≤ 8 KB) and, on any
  failure, returns a **generic
  client-facing error envelope** (a correlation id, no internal endpoint / ARN / stack detail)
  while logging the detail to CloudWatch — the loud-raise-with-body posture stays on the CLI side,
  not across the public Function URL. Exercised with the embedder, both stores, and the
  synthesizer **mocked** (no network in the unit test); reuses the **same** `hybrid_query` the CLI
  uses, so a green live result proves the real path, not a reimplementation. *(TDD with mock; live
  in AC9)*
- [x] **AC8 — IaC synthesizes the query Lambda + IAM-auth Function URL, securely.** The CDK app
  synthesizes a VPC-resident (private isolated subnets, **not public**) query Lambda with a
  **Function URL whose `AuthType` is `AWS_IAM`** **and whose invoke permission is scoped to a
  named principal** — an `InvokerRoleArn` `CfnParameter` (the demo's deploying/CLI role), asserted
  as the grant's `Principal`, never `Principal: *` or account-root; IAM auth alone gates *that a
  request is signed*, the scoped grant gates *who may invoke* (the same identity signs the AC9 live
  call); an SG path to Neptune
  **8182** and OpenSearch **443**; an execution role that is **least-privilege** — Neptune
  data-access scoped to the cluster, `es:ESHttp*` scoped to the domain ARN, and `bedrock:InvokeModel`
  (and `bedrock:Converse`) scoped to the **Titan** model ARN **and** the **synthesis Claude** model
  resource — **when the configured model is a cross-region inference profile, the grant scopes the
  account-and-region-qualified `inference-profile` ARN AND each underlying regional
  `foundation-model` ARN the profile routes to** — all with **no wildcard `Resource`**; the query
  Lambda SG keeps the established **no-egress-path** guarantee (no NAT, VPC-endpoint-only) now that
  it sits behind a public ingress; a stack-managed log group removed on destroy; and the Budgets value
  asserted **unchanged at the literal `150`** (no new standing cost — the Lambda is scale-to-zero).
  Per ADR-0002. *(goal-based synth, CDK-env-gated)*
- [ ] **AC9 — Live deploy + hybrid-query smoke (in-VPC).** (deferred: hybrid-orchestration-live-deploy)
  Against the deployed stack with the corpus dual-written, a **SigV4-signed call to the Function
  URL** runs a curated entity-led question through live OpenSearch + Neptune + Bedrock Claude and
  returns an answer with citations and a seed/hop trace whose seeds include the question-linked
  entity; recorded in `deployment-and-verification.md`, then the stack is destroyed. The IaC is
  **`cdk synth`-validated** (the real template carries the `AWS_IAM` Function URL, the named-principal
  invoke grant, and the Bedrock grant scoped to the `inference-profile` + `foundation-model` ARNs),
  and Bedrock access to `us.anthropic.claude-sonnet-4-6` is confirmed in the target account; the
  corpus-backed live run is **blocked only on building the Fargate ingestion image, which needs a
  Docker daemon not available in this environment** — so it is deferred to the maintainer's
  deploy/verify/destroy window. *(live smoke)*
- [x] **AC10 — Consolidated showcase set + presenter script (every stage narratable).** One
  curated showcase query set (`≥5–6 per mode`, each labeled with its intended winning mode and
  the trace highlight) is the single home for the demo's queries; a loader/test asserts it parses
  and every gold entity/chunk it names resolves in the fixture corpus. A presenter script under
  `docs/guides/` walks vector → graph → hybrid for the showcase queries with the exact CLI
  commands and what to point at in each trace. `hybrid-query` and `compare` each print an ordered,
  human-readable trace — no black-box hop (charter principle 1). *(goal-based)*

## Assumptions

- Technical: runtime is Python 3.11+; runtime deps stay `pyyaml` + `boto3`, infra extra is
  `aws-cdk-lib`/`constructs`, dev is `pytest`/`ruff`/`mypy` with the `S` ruleset (source:
  `pyproject.toml`; `packages/graphrag/AGENTS.md`).
- Technical: Claude synthesis uses the `boto3` `bedrock-runtime` **Converse** API
  (`client.converse(modelId=…, system=[…], messages=[…], inferenceConfig=…)`), **not** the
  `anthropic` SDK — the SDK would be a new runtime dependency and is absent from the Lambda
  runtime / pure-Python `Code.from_asset` bundle (source: existing `BedrockTitanEmbedder` uses
  the same `bedrock-runtime` client; `claude-api` skill — Bedrock uses boto3 Converse, not the
  Anthropic SDK; AGENTS.md "dependencies are forever").
- Technical: the synthesis model id is **configurable** (env `SYNTHESIS_MODEL_ID` / CLI flag /
  CDK constant) with a **documented default of the cross-region inference profile
  `us.anthropic.claude-sonnet-4-6`** — a cost/latency-balanced Claude for demo-scale grounded
  summarization, overridable to a higher-quality model (e.g. an Opus profile) per the deployer's
  call. The design doc leaves the exact model an open question (cost/latency vs. quality), so this
  slice resolves it as a configurable default rather than a hard pin (source: design doc Open
  Questions; ADR-0001 "synthesis model not pinned"; `claude-api` skill — Bedrock Claude is
  invoked via boto3 Converse with `anthropic.`/`us.anthropic.`-prefixed ids). The synth test
  (AC8) asserts the grant is scoped to ARNs derived from this constant with no wildcard; the exact
  string + model access + on-demand-vs-inference-profile shape are confirmed against current
  Bedrock access in the target account/region at deploy time (AC9).
- Technical: entity-linking reuses slice-1 `normalize_handle`/`normalize_slug`/`kep_id` +
  `aliases.yaml` on the controlled-vocabulary corpus (SIG slugs, `@handles`, KEP numbers); a
  linked candidate becomes a seed only if its ID resolves to a real graph node, so a misseed is
  filtered and recorded, not silently expanded (source: ADR-0001; `normalize.py`; `resolve.py`).
- Technical: graph expansion stays in the application layer over `GraphStore.neighbors()` (new
  `expand_neighborhood` reusing the slice-1 traversal seam), so the in-memory and Neptune
  backends produce an identical trace (source: `query.py`; `packages/graphrag/AGENTS.md`
  invariant).
- Technical: the live query path is the **in-VPC Lambda behind an IAM-auth Function URL**; the
  VPC-private Neptune/OpenSearch are unreachable from a laptop, so the CLI's live mode is a thin
  SigV4 client to the Function URL, mirroring the slice-1/2 in-VPC-compute posture (source:
  design doc D2; `apps/infra/stacks/graphrag_stack.py`).
- Technical/Process: AWS credentials, CDK bootstrap, and Bedrock access to
  `us.anthropic.claude-sonnet-4-6` **are available in this environment**, and the IaC
  **`cdk synth`-validates** to a real template with the correct security posture — but the
  corpus-backed live hybrid query (AC9) needs the Fargate **ingestion image**, whose build
  requires a **Docker daemon that is not available here**, so AC9's live smoke is **deferred** to
  the maintainer's deploy/verify/destroy window (backlog anchor
  `hybrid-orchestration-live-deploy`), matching the slice-1 AC9 deferral pattern. Everything
  testable without a live corpus is met offline (source: `cdk synth` output; `docker info`
  unavailable; slice-1 backlog precedent; user direction 2026-06-24).
- Process: this spec is full work-loop mode — security boundary (Bedrock + Neptune + OpenSearch
  network I/O; an IAM-auth public Function URL; untrusted retrieved content → Claude), structural
  (new modules + new infra), constrained by ADR-0001/0002/0003 + the design doc; derived from
  brief `graphrag-aws-demo.md` (source: `docs/CONVENTIONS.md` risk triggers; brief Spec map row 3).
- Product: the audience is a solution architect comparing retrieval modes; this slice ends at the
  hybrid mode + the three-mode runner + the consolidated showcase, with permission filtering
  (slice 4) and delta re-ingest (slice 5) out of scope (source: charter scope; brief Spec map).

## Changelog

- 2026-06-24 — Spec authored (slice 3). Assumptions surfaced; seed-and-expand hybrid in the
  in-VPC query Lambda + three-mode runner + consolidated showcase; Bedrock Claude synthesis via
  boto3 Converse (no new dependency); over-expansion bounded by hop limit + seed cap with the
  seed set surfaced in the trace; live corpus-backed hybrid query in scope (the slice-2 deferred
  manual step becomes this slice's automated live AC).
