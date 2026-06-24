# Plan: hybrid-orchestration

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done — offline build complete + reviewed; AC9 live smoke deferred (Docker) -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. Substantial changes get a dated
> changelog entry at the bottom.

## Approach

Build the hybrid mode **inside-out** on the two halves slices 1–2 already ship, adding the
smallest set of new library modules and wiring them into a CLI, an in-VPC Lambda, and the CDK
stack — exactly the shape slices 1 and 2 used.

The pipeline is **link + retrieve → seed → expand → merge → synthesize → trace**. Four new
library modules carry it, each a thin twin of an established pattern:

- `entity_link.py` — pure question→entity-ID linking over the controlled vocabulary, built on
  slice-1 `normalize`. No store, no network; trivially unit-tested.
- `synthesize.py` — a `Synthesizer` protocol with a real `BedrockClaudeSynthesizer` (boto3
  `bedrock-runtime` **Converse**) and an offline deterministic `TemplateSynthesizer`, mirroring
  the `Embedder` seam exactly (real + offline, injected everywhere).
- `query.py` (extend) — `expand_neighborhood`, a bounded multi-edge expansion over `neighbors()`,
  the graph-side twin of `traverse` but undirected-over-all-edge-kinds for the seed-and-expand
  neighborhood.
- `hybrid.py` — the orchestration: dual-seed (vector-owners ∪ question-links), seed cap, expand,
  merge, synthesize, and a `HybridResult` whose `.render()` is the narratable trace.
- `compare.py` — the three-mode runner over `vector_search` / a graph-only path / `hybrid_query`.

Then the surfaces: CLI verbs (`hybrid-query`, `compare`) offline-by-default with a thin SigV4
Function-URL client for live; `query_lambda.py` (the in-VPC handler, twin of the smoke lambdas);
the CDK additions (query Lambda + IAM-auth Function URL + scoped Bedrock-Claude grant); and the
consolidated showcase set + presenter script.

The load-bearing offline choice mirrors slice 2: **the offline embedder is non-semantic**, so
the hybrid/graph *wins* are asserted **structurally** (the entity-led query's owned-KEP set shows
up in the hybrid/graph trace and is absent from vector-only) rather than by similarity score. The
honest semantic win stays the slice-2 frozen-vector eval; the live path (AC9) proves the real
Bedrock-Claude round trip.

Riskiest parts, front-loaded: (1) the Bedrock **Converse** request/response shape and the
synthesis model id against a live account — de-risked by a mocked synthesizer + the AC9 live
invocation; (2) the **IAM-auth Function URL** + the in-VPC Lambda's three-hop networking
(Neptune, OpenSearch, Bedrock) — de-risked by synth assertions then the live deploy; (3) the
Bedrock IAM grant for the Claude model (on-demand vs. inference-profile ARNs) — pinned at deploy
and asserted in synth.

## Constraints

- **ADR-0001** — the hybrid mode *is* seed-and-expand: seed from both vector-owners and
  question entity-linking, expand 1–2 hops, merge, synthesize, return the seed/hop trace; bound
  over-expansion with a hop limit + seed cap; reuse the slice-1 resolver/alias table. The
  comparison runner still executes vector-only and graph-only independently.
- **ADR-0002** — the query Lambda is in private isolated subnets behind an **IAM-auth Function
  URL** (the only public ingress); reaches Neptune/OpenSearch/Bedrock VPC-internally (no NAT);
  scale-to-zero; removed by `destroy`.
- **ADR-0003** — IaC is AWS CDK (Python); additions land in the existing
  `apps/infra/stacks/graphrag_stack.py`, not a new stack.
- **Design doc D1/D2** — the seed-and-expand diagram is the contract for the orchestration; the
  thin-CLI / in-VPC-query-Lambda topology is the contract for the surface.
- **Charter principle 1** — every stage narratable; the seed/hop trace is the observability
  surface. **Principle 2** — the comparison must be honest (the runner gives each mode its real
  retrieval; no strawman).

## Construction tests

Most tests live per-task below. Cross-cutting:

- **Integration:** `compare` over the fixture corpus (offline embedder + offline synthesizer,
  in-memory stores) returns three traced answers; the entity-led exemplar shows the owned-KEP
  set in graph/hybrid and not in vector-only — `test_compare.py`.
- **Integration:** `hybrid-query` CLI over the fixture (offline) prints a dual-seed trace +
  citations + answer end-to-end — `test_hybrid_cli.py`.
- **Live (manual, this environment):** `cdk deploy`, run the Fargate dual-write, SigV4-POST a
  curated entity-led question to the Function URL, assert an answer + citations + a seed/hop trace
  whose seeds include the question-linked entity, `cdk destroy`. Recorded in
  `docs/architecture/deployment-and-verification.md` (AC9).

## Design (LLD)

Shape `mixed` → design decisions, data & schema, interfaces & contracts, component decomposition,
failure & resilience, dependencies & integration. Stack derived from the established repo
(Python 3.11+, `pyyaml`+`boto3`, AWS CDK Python), mirroring slices 1–2.

### Design decisions
*(Traces to: AC1, AC2, AC4, AC6 · no `contracts/` file — internal interfaces + an IAM-auth Lambda.)*

- **Synthesizer seam mirrors the Embedder seam.** `Synthesizer` protocol +
  `BedrockClaudeSynthesizer` (real, Converse) + `TemplateSynthesizer` (offline deterministic).
  *Rejected:* the `anthropic` SDK — a new forever-dependency absent from the Lambda runtime
  (Ask-first rail); boto3 Converse already does it, exactly as Titan uses `bedrock-runtime`.
- **Entity-linking is pure + reuses slice-1 normalize; the graph confirms membership.**
  `link_question` returns *candidates*; `hybrid_query` keeps only those resolving to a real node
  (`get_node`), recording dropped candidates. *Rejected:* loading a full `all_nodes()` catalog
  per call — `get_node` membership is cheaper and the dropped-candidate record makes a misseed
  visible (ADR-0001 mitigation).
- **Expansion is undirected-over-all-edge-kinds, behind the `GraphStore.neighbors_batch` seam.**
  `expand_neighborhood` calls `neighbors_batch(frontier)` once per hop; the **default** fans out
  `EdgeKind` × `Direction` over `neighbors()` (in-memory) and Neptune **overrides** it with one
  batched openCypher query per direction. *(Revised post-live-deploy: the original per-edge-kind
  fan-out over `neighbors()` timed out against Neptune Serverless — backlog
  `hybrid-orchestration-live-deploy`; the override is safe because `expand_neighborhood` sorts the
  reached set + edge kinds, keeping the trace byte-identical across backends.)*
- **Three modes run independently in the runner; hybrid alone dual-seeds.** Matches ADR-0001's
  boundary (pedagogy vs. internal orchestration).
- **Configurable synthesis model id.** Resolves the design-doc open question as a *default*
  (`DEFAULT_SYNTHESIS_MODEL_ID = "us.anthropic.claude-sonnet-4-6"`, overridable via env/CLI/CDK
  constant), not a hard pin; the default drives the synth-asserted Bedrock IAM grant (AC8) and is
  confirmed against live model access at deploy (AC9).
- **Defense-in-depth at the public ingress.** The Function URL is IAM-auth *and* invoke-scoped to a
  named principal; the handler length-bounds the question and returns a sanitized error envelope;
  the synthesizer pins a `maxTokens` ceiling and a defensive system directive (untrusted content as
  data). Each control is a synth/unit AC, not prose (AC2/AC7/AC8).

### Data & schema
*(Traces to: AC1, AC3, AC4, AC5.)*

- `entity_link.Candidate`: `surface: str`, `entity_id: str`, `kind` (`person|sig|kep`),
  `via` (`handle|alias|slug|kep-number`).
- `hybrid.Seed`: `entity_id: str`, `source` (`vector|question`), `surface: str | None`.
- `query.NeighborhoodTrace` / `NeighborhoodResult`: per-hop `frontier_in`, `reached`,
  `edge_kinds`, `truncated`; `result_ids`.
- `hybrid.HybridResult`: `question`, `seeds: list[Seed]`, `dropped_candidates: list[Candidate]`,
  `hop_trace`, `chunks: list[VectorHit]`, `graph_nodes: list[Node]`, `answer: str`,
  `citations: list[str]`, plus caps applied (`seed_cap`, `max_hops`) + truncation flags.
  `.render()` → seeds-by-source → hops → citations → answer.
- `synthesize.SynthesisResult`: `answer: str`, `citations: list[str]`.
- `compare.ModeResult` (mode name, retrieval trace, answer, citations) + `ComparisonResult`
  (`vector`, `graph`, `hybrid`); `.render()` side-by-side.
- Showcase set: `packages/graphrag/src/graphrag/showcase/queries.yaml` (packaged) — per query:
  `id`, `query`, `wins` (`vector|graph|hybrid`), `gold` (entity-id(s) and/or chunk-id(s) the
  trace should surface), `highlight` (the one-line narration point).

### Interfaces & contracts
*(Traces to: AC1–AC7.)*

- `graphrag.entity_link`: `link_question(question: str, aliases: dict[str,str]) -> list[Candidate]`.
- `graphrag.synthesize`: `Synthesizer` (Protocol) `synthesize(question, context_chunks,
  graph_facts) -> SynthesisResult` + `model_id`; `BedrockClaudeSynthesizer(model_id=…, region=…,
  client=…)`; `TemplateSynthesizer()`.
- `graphrag.query`: `expand_neighborhood(store, seed_ids, *, max_hops=2, frontier_cap=50) ->
  NeighborhoodResult`.
- `graphrag.hybrid`: `hybrid_query(question, *, vector_store, graph_store, embedder, synthesizer,
  aliases, k=5, max_hops=2, seed_cap=8) -> HybridResult`.
- `graphrag.compare`: `run_modes(question, *, vector_store, graph_store, embedder, synthesizer,
  aliases, **bounds) -> ComparisonResult`.
- `graphrag.showcase`: `load_showcase() -> list[ShowcaseQuery]` (packaged resource).
- `graphrag.query_lambda.lambda_handler(event, context) -> dict` (returns
  `{answer, citations, trace, seeds, hops}`).

### Component / module decomposition
*(New modules under the existing `packages/graphrag/src/graphrag/`:)*

- `entity_link.py`, `synthesize.py`, `hybrid.py`, `compare.py`, `query_lambda.py`,
  `showcase/__init__.py` + `showcase/queries.yaml`. Extends `query.py` (expansion), `cli.py`
  (verbs). Reuses `normalize.py`, `resolve.load_aliases`, `vector.vector_search`, `chunk`,
  `embed`, `store/*`, `model` unchanged. IaC extends `apps/infra/stacks/graphrag_stack.py`.
  Presenter script: `docs/guides/tutorials/three-mode-demo.md`.

### Failure, edge cases & resilience
*(Traces to: AC3, AC4, AC7.)*

- No question-linked entity and no vector hits → empty seed set → expansion is a no-op; the
  synthesizer answers from the (possibly empty) context, trace shows `seeds: (none)`.
- Seed set over `seed_cap` / hop frontier over `frontier_cap` → truncate, record in trace.
- A linked candidate not present in the graph → dropped, recorded (`dropped_candidates`).
- Bedrock Converse throttling / non-2xx on the live path → surfaced loudly; not a unit concern
  (mocked). Retrieved Markdown is untrusted → placed as Converse `messages` data, never as
  instruction text (LLM01/LLM08; display-only output).
- Function-URL non-2xx in the CLI live client → raise with the body (loud, like the adapters).
- Query Lambda handler (the public ingress) → an over-long question is rejected before
  orchestration; any internal failure returns a sanitized error envelope (correlation id, no
  endpoint/ARN/stack detail) with the detail logged to CloudWatch — the loud raise stays in-VPC,
  never crosses the Function URL (info-disclosure boundary).

### Dependencies & integration
*(Traces to: AC2, AC7, AC8, AC9.)*

- Bedrock `bedrock-runtime` Converse (Claude synthesis) + Titan v2 (query embedding) via the
  existing VPC endpoint; Neptune (`neptune-db`) + OpenSearch (`es`) via SigV4; the Function URL
  signed `service=lambda` from the CLI. No new Python runtime dependency (boto3 Converse +
  urllib SigV4) — but `client.converse()` first shipped in **boto3 ≥ 1.35**, so T2 bumps the
  `pyproject.toml` floor from `boto3>=1.34` to `boto3>=1.35` (a version-floor bump, not a new
  dependency; recorded in `packages/graphrag/AGENTS.md`). The Lambda runtime ships a current boto3.
  All grants least-privilege-scoped (AC8).

## Tasks

> Tests come before Approach in each task. TDD tasks carry a red **stub** marked
> `# STUB: AC<n>`. Paths are under `packages/graphrag/src/graphrag/` and
> `packages/graphrag/tests/` unless noted.

### T1 — Question entity-linking
- **Depends on:** none
- **Touches:** packages/graphrag/src/graphrag/entity_link.py, packages/graphrag/tests/test_entity_link.py
- **Tests:** `test_entity_link.py` — `link_question("the KEPs @thockin tech-leads owns", aliases)`
  yields a `person:thockin` candidate via `handle`; `"what does SIG Network own"` →
  `sig:sig-network` via `slug`; `"risks in KEP-1287"` / `"KEP 1287"` → `kep-1287` via
  `kep-number`; an alias display-name resolves via `alias`; a question with no known vocabulary
  → `[]`; every candidate's `entity_id` is **byte-equal** to the slice-1 `normalize` output.
  `# STUB: AC1`, `stub: true`.
- **Approach:** `graphrag.entity_link` — `Candidate` dataclass; regexes for `@handle`, `KEP[-\s]N`,
  and SIG mentions; normalize via `normalize_handle`/`normalize_slug`/`kep_id`; `aliases` is the
  slice-1 **display-name→handle** map (`resolve.load_aliases()`), routed through
  `normalize_handle(surface, aliases)` so a display name resolves (`via=alias`).
- **Done when:** `test_entity_link.py` green (AC1).

### T2 — Synthesizer protocol + offline + Bedrock Converse
- **Depends on:** none
- **Touches:** packages/graphrag/src/graphrag/synthesize.py, packages/graphrag/tests/test_synthesize.py
- **Tests:** `test_synthesize.py` — `TemplateSynthesizer().synthesize(q, chunks, facts)` returns a
  stable answer + a citation list derived deterministically from the context (same input → same
  output); a **mocked** `BedrockClaudeSynthesizer` issues `converse(modelId=…, system=[{"text":…}],
  messages=[{"role":"user","content":[{"text":…}]}], inferenceConfig={"maxTokens": …})` and parses
  `output.message.content[0].text`; the retrieved corpus text appears in the `messages` **content
  (data)**, not in `system`; the `system` block carries an explicit **untrusted-data defensive
  directive**; `inferenceConfig` pins a **tested `maxTokens` ceiling**; no network call in either
  test; the client is the default botocore-chain client (no `verify=False`). `# STUB: AC2`,
  `stub: true`.
- **Approach:** `graphrag.synthesize` — `Synthesizer` Protocol; `SynthesisResult`;
  `TemplateSynthesizer` (compose answer + citations from provenance, labeled non-semantic);
  `BedrockClaudeSynthesizer` (injectable `bedrock-runtime` client; `modelId` configurable, default
  the module constant `DEFAULT_SYNTHESIS_MODEL_ID = "us.anthropic.claude-sonnet-4-6"`; grounding +
  defensive system prompt; untrusted content as `messages` data; bounded `inferenceConfig`
  `maxTokens`). Bump the `pyproject.toml` runtime floor `boto3>=1.34` → `boto3>=1.35` (the floor at
  which `bedrock-runtime.converse` exists) and record it in `packages/graphrag/AGENTS.md`.
- **Done when:** `test_synthesize.py` green (AC2); `pyproject.toml` floor is `boto3>=1.35`.

### T3 — Bounded neighborhood expansion + trace
- **Depends on:** none
- **Touches:** packages/graphrag/src/graphrag/query.py, packages/graphrag/tests/test_query.py
- **Tests:** `test_query.py` (extend) — `expand_neighborhood(store, ["sig:sig-network"],
  max_hops=1)` over the fixture graph reaches the SIG's owned KEPs + leadership in one hop, with a
  trace naming the contributing edge kinds; **from `["person:thockin"]`, `max_hops=2` reaches
  `sig:sig-network` at hop 1 (`TECH_LEADS`) and the SIG's owned KEPs at hop 2 (`OWNS`)** — the
  exemplar path the graph-win depends on, which `max_hops=1` does *not* reach; a `frontier_cap`
  smaller than the true frontier **truncates and records it**; empty seeds → empty result; the
  in-memory result matches what `neighbors()` yields (app-layer, backend-identical).
  `# STUB: AC3`, `stub: true`.
- **Approach:** `graphrag.query.expand_neighborhood` — per hop, for each frontier node, union
  `neighbors(node, kind, dir)` over all `EdgeKind` × `Direction`; dedupe; cap; record a
  `NeighborhoodTrace` entry; return `NeighborhoodResult`.
- **Done when:** `test_query.py` green (AC3).

### T4 — Seed-and-expand orchestration
- **Depends on:** T1, T2, T3
- **Touches:** packages/graphrag/src/graphrag/hybrid.py, packages/graphrag/tests/test_hybrid.py
- **Tests:** `test_hybrid.py` — over the fixture corpus (in-memory vector+graph stores, offline
  embedder + `TemplateSynthesizer`): seeds are the **union** of top-k chunk owners (`source=vector`)
  and confirmed question links (`source=question`), each tagged; a question link absent from the
  graph is in `dropped_candidates`; a seed set beyond `seed_cap` truncates (recorded); expansion
  respects `max_hops`; the merge dedupes chunks + nodes; `HybridResult.render()` names
  seeds-by-source → hops → citations → answer; on the entity-led exemplar, `@thockin` is a
  `source=question` seed resolving to **`person:thockin`** (a handle, not the SIG), and the 2-hop
  expansion `person:thockin → sig:sig-network → owned KEPs` reaches the owned KEPs — the seed-by-
  source split is asserted against what the resolver actually produces. `# STUB: AC4`,
  `stub: true`.
- **Approach:** `graphrag.hybrid` — `Seed`, `HybridResult`, `hybrid_query`: `vector_search` →
  owners; `link_question` → confirm via `get_node` → seeds; cap; `expand_neighborhood`; merge;
  `synthesize`; assemble + `render()`.
- **Done when:** `test_hybrid.py` green (AC4).

### T5 — Three-mode comparison runner
- **Depends on:** T4
- **Touches:** packages/graphrag/src/graphrag/compare.py, packages/graphrag/tests/test_compare.py
- **Tests:** `test_compare.py` — `run_modes(entity_led_q, …)` returns three `ModeResult`s; from
  the `person:thockin` question seed, `graph` and `hybrid` expand 2 hops (`TECH_LEADS`/`OWNS`) so
  their result sets enumerate the KEPs owned by `sig:sig-network` while `vector` does not
  (structural graph-wins); `ComparisonResult.render()` shows all three traces; a semantic-led query
  produces a vector result with chunks. `# STUB: AC5`, `stub: true`.
- **Approach:** `graphrag.compare` — `run_modes` calls `vector_search` (vector), entity-link →
  `expand_neighborhood` → synthesize (graph), `hybrid_query` (hybrid); `ModeResult` /
  `ComparisonResult` with `.render()`.
- **Done when:** `test_compare.py` green (AC5).

### T6 — CLI verbs: hybrid-query + compare (offline + live Function-URL client)
- **Depends on:** T4, T5
- **Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_hybrid_cli.py
- **Tests:** `test_hybrid_cli.py` — `hybrid-query --q …` and `compare --q …` over the fixture
  (offline) print the trace + citations + answer (`compare`: three modes), labeling the offline
  embedder/synthesizer as non-semantic; with a mocked HTTP client, `hybrid-query --function-url
  https://… --q …` issues a **SigV4-signed (`service=lambda`) POST** with the question in the body,
  the signature **covering the body** (the request carries an `X-Amz-Content-SHA256` payload hash,
  not `UNSIGNED-PAYLOAD`), and renders the returned answer/trace; a non-2xx raises with the body.
  `# STUB: AC6`, `stub: true`.
- **Approach:** extend `cli.py` — `hybrid-query`/`compare` subparsers reusing `_vector_store`/
  `_populated_store`/`_embedder`; a `_synthesizer(args)` (offline default, `--bedrock` →
  `BedrockClaudeSynthesizer`); a thin `_function_url_client` (urllib + `SigV4Auth` service
  `lambda`) for the live path.
- **Done when:** `test_hybrid_cli.py` green (AC6).

### T7 — In-VPC query Lambda handler
- **Depends on:** T4
- **Touches:** packages/graphrag/src/graphrag/query_lambda.py, packages/graphrag/tests/test_query_lambda.py
- **Tests:** `test_query_lambda.py` — with the embedder, both stores, and the synthesizer mocked,
  `lambda_handler` parses the question from a **Function-URL event** (`body`, base64-aware) and a
  bare `{"question": …}` event, runs `hybrid_query`, and returns `{answer, citations, trace, seeds,
  hops}`; reads endpoints + model id from env; an **over-long question is rejected** (a 4xx-shaped
  envelope, no orchestration run); a forced internal failure returns a **generic error envelope**
  (a correlation id, `error` message, **no `NEPTUNE_ENDPOINT`/`OPENSEARCH_ENDPOINT`/ARN/stack
  text**) — asserted by checking the endpoint strings are absent from the response; no network in
  the unit test. `# STUB: AC7`, `stub: true`.
- **Approach:** `graphrag.query_lambda` — twin of `vector_smoke_lambda`: read
  `NEPTUNE_ENDPOINT`/`OPENSEARCH_ENDPOINT`/`AWS_REGION`/`SYNTHESIS_MODEL_ID`; build
  `NeptuneGraphStore` + `OpenSearchVectorStore` + `BedrockTitanEmbedder` +
  `BedrockClaudeSynthesizer`; parse + length-bound the question from the event/Function-URL body;
  run `hybrid_query`; serialize `HybridResult`; wrap the body in `try/except` that logs detail to
  CloudWatch and returns a sanitized envelope (the loud-raise stays on the CLI/adapter side).
- **Done when:** the handler round-trips through mocks (AC7; live in T10).

### T8 — Showcase consolidation + loader + presenter script
- **Depends on:** T5
- **Touches:** packages/graphrag/src/graphrag/showcase/__init__.py, showcase/queries.yaml, packages/graphrag/tests/test_showcase.py, docs/guides/tutorials/three-mode-demo.md
- **Tests:** `test_showcase.py` — `load_showcase()` parses the packaged set; **≥5–6 queries per
  mode** (`wins ∈ {vector, graph, hybrid}`); every `gold` entity-id/chunk-id named resolves in the
  fixture corpus (graph node exists / chunk id is produced by `chunk_corpus`); each query has a
  non-empty `highlight`. `# STUB: AC10`, `stub: true`.
- **Approach:** author `showcase/queries.yaml` consolidating the curated demo queries (drawing on
  the slice-2 `query_set.yaml` semantic + entity-led labels and adding graph/hybrid wins);
  `graphrag.showcase.load_showcase` (packaged-resource loader, like `load_aliases`); write
  `docs/guides/tutorials/three-mode-demo.md` — the presenter script walking vector → graph →
  hybrid with the exact `graphrag compare`/`hybrid-query` commands and the trace highlight per
  query.
- **Done when:** `test_showcase.py` green; the presenter script references the set + commands (AC10).

### T9 — IaC: query Lambda + IAM-auth Function URL + scoped Bedrock-Claude grant
- **Depends on:** none (parallel-eligible; disjoint from the Python lib)
- **Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py
- **Tests:** `test_stack.py` (extend) — synth asserts a VPC-resident query Lambda (private
  isolated subnets, not public) with a **Function URL `AuthType == AWS_IAM`** **and an invoke
  permission scoped to a named principal** (the deploying/CLI role ARN, not `Principal: *`, with the
  `lambda:FunctionUrlAuthType == AWS_IAM` condition; the principal comes from a `CfnParameter`
  `InvokerRoleArn`, and the synth test asserts the grant's `Principal` equals it — not account-root);
  an SG path to Neptune 8182 + OpenSearch 443;
  the Lambda role carries Neptune data-access scoped to the cluster, `es:ESHttp*` scoped to the
  domain ARN, and `bedrock:InvokeModel`(+`Converse`) scoped to the **Titan** model ARN **and** the
  **synthesis Claude** model resource — **when the configured model is a cross-region inference
  profile, both the `inference-profile` ARN and the underlying regional `foundation-model` ARN(s)**
  — all with **no wildcard `Resource`**; a stack-managed log group; SG/ingress descriptions in the
  EC2 ASCII charset; the Budgets limit asserted **unchanged at the literal `150`**. `# STUB: AC8`,
  `stub: true`.
- **Approach:** extend `graphrag_stack.py` — `_query_lambda(vpc, cluster, neptune_sg, domain,
  opensearch_sg)`; a `_bedrock_synthesis_invoke()` statement scoped to the configured Claude model
  ARN(s) — for an inference-profile id, both the `inference-profile` ARN and the regional
  `foundation-model` ARN(s) (alongside the existing Titan `_bedrock_invoke`);
  `fn.add_function_url(auth_type=FunctionUrlAuthType.AWS_IAM)` + `fn.grant_invoke_url(
  iam.ArnPrincipal(invoker_role_arn))` where `invoker_role_arn` is a new `CfnParameter`
  (`InvokerRoleArn`) — never a `*`/account-root grant; `SYNTHESIS_MODEL_ID` env defaults to the
  CDK module constant `_SYNTHESIS_MODEL_ID`, which **must equal** the library's
  `DEFAULT_SYNTHESIS_MODEL_ID` (a synth test asserts equality so the grant scope and the runtime
  default can't drift); the synthesis-model ARN(s) are built account-and-region qualified for the
  `inference-profile` plus the underlying regional `foundation-model` ARNs; `CfnOutput` the
  Function URL.
- **Done when:** `test_stack.py` green; `cdk synth` clean (AC8).

### T10 — Live deploy + hybrid-query smoke (this environment)
- **Depends on:** T7, T9
- **Tests:** live — `scripts/deploy.sh`; run the Fargate dual-write; SigV4-POST a curated
  entity-led question to the Function URL; assert an answer + citations + a seed/hop trace whose
  seeds include the question-linked entity; `scripts/destroy.sh`. *(live smoke — AC9)*
- **Approach:** deploy the updated stack; build/push the ingestion image; run the dual-write; sign
  and POST to the Function URL (or `aws lambda invoke`); record the JSON result + teardown in
  `deployment-and-verification.md` (a new verification-ladder row).
- **Done when:** the live hybrid query returns a grounded answer + dual-seed trace and the stack is
  destroyed (AC9).

### T11 — Docs + capture-learnings + spec tick
- **Depends on:** T1-T10
- **Tests:** n/a (docs).
- **Approach:** update `docs/architecture/overview.md` (new modules + the hybrid mode landed),
  `docs/architecture/infrastructure.md` (query Lambda + Function URL in the inventory + evolution
  log), `docs/architecture/deployment-and-verification.md` (the hybrid live row + result),
  `docs/architecture/security.md` (the IAM-auth Function URL boundary; untrusted-content→Claude
  control as data-not-instruction; the Bedrock-Claude grant); `docs/specs/README.md` (status);
  `docs/product/changelog.md`; add knowledge entries to `docs/knowledge/patterns.jsonl`; tick the
  spec's met ACs and set Status `Shipped`. Record the no-new-dependency outcome (boto3 Converse) in
  `packages/graphrag/AGENTS.md` and add `synthesize`/`hybrid`/`compare`/`entity_link`/`query_lambda`
  to its module map.
- **Done when:** docs match the code; spec ACs ticked; gates green.

## Rollout

Per the design doc's phased rollout, slice 3 extends the **same** IaC stack:

- **Provisions (added to the slice-1/2 stack):** the in-VPC query Lambda + an **IAM-auth Function
  URL**; the Lambda role gains scoped Neptune-data + OpenSearch-data + Bedrock-invoke (Titan +
  Claude) permissions; a stack-managed log group; an SG path to Neptune 8182 + OpenSearch 443.
- **Standing cost:** **none new** — the query Lambda is scale-to-zero and the Function URL has no
  hourly charge; Bedrock Claude is per-invocation. The slice-2 Budgets value (`$150`) holds; T9
  confirms with the arithmetic in the synth test rather than raising it.
- **Live corpus-backed hybrid query is now in scope** (the slice-2 plan recorded it as a future
  manual step): deploy → dual-write → SigV4-POST to the Function URL → grounded answer + trace,
  then `destroy`.
- **Deploy:** `cdk deploy`; `cdk run-task` (the dual-write); the Function URL serves the hybrid
  query. **Destroy:** `cdk destroy` removes every billable resource (incl. the query Lambda).
- **Rollback:** `destroy` + redeploy; state reproducible from the S3 snapshot — no migration, no
  irreversible step (ADR-0002).
- **Deployment sequencing:** Neptune + OpenSearch + Bedrock endpoint (slices 1–2) before the query
  Lambda that uses them (CDK dependency order handles this); the dual-write before a corpus-backed
  query.

## Risks

- **Bedrock Converse shape / synthesis model availability.** Wrong Converse body or an
  inaccessible model id fails only live. *Mitigation:* mocked synthesizer + the AC9 live invocation;
  configurable model id; the grant pinned to the resolved ARN(s) in T9.
- **Bedrock Claude IAM scoping (on-demand vs. inference profile).** A cross-region inference profile
  needs the profile ARN *and* the underlying foundation-model ARNs. *Mitigation:* resolve at deploy;
  assert the scoped ARNs in synth; no wildcard.
- **IAM-auth Function URL + in-VPC three-hop networking.** A silent SG/endpoint misconfig yields
  timeouts, not errors (the slice-1/2 3am risk). *Mitigation:* synth assertions for the SG/role
  scope + the loud live SigV4 invocation.
- **Untrusted retrieved content → Claude (LLM01/LLM08).** *Accepted, boundary named:* corpus is
  public/benign, output is display-only (no tool execution), content is passed as Converse data not
  instructions; routes to `security-reviewer` if the demo ever ingests private data.
- **Offline non-semantic embedder can't show a semantic win.** *Mitigation:* hybrid/graph wins
  asserted structurally offline; semantic honesty stays the slice-2 frozen-vector eval + the live
  path.
- **Showcase fairness is a judgment call.** *Mitigation:* queries drawn from the real pinned
  fixture; the per-mode labels + gold-resolves test keep it honest; the adversarial reviewer checks
  the curation.

## Notes / declined patterns

- **Declined:** the `anthropic` SDK — boto3 `bedrock-runtime` Converse already does synthesis, and
  the SDK is absent from the Lambda runtime / pure-Python bundle ("dependencies are forever").
- **Reversed post-live-deploy (was "Declined"):** a batched openCypher expansion. Originally
  declined to keep the backends trace-identical, but the per-edge-kind fan-out over `neighbors()`
  timed out against Neptune Serverless at demo time (backlog `hybrid-orchestration-live-deploy`).
  Resolved by `GraphStore.neighbors_batch` (default fan-out + Neptune-batched override) with
  `expand_neighborhood` **sorting** the reached set, so the trace stays byte-identical — the
  invariant is preserved by sort, not by forbidding the override.
- **Declined:** a public unauthenticated Function URL for demo convenience — IAM auth is the only
  acceptable ingress (ADR-0002).
- **Surfaced assumption:** the exact Bedrock Claude model/inference-profile string + model access
  are account/region-dependent — configurable, resolved at deploy, asserted scoped in synth.

## Changelog

- 2026-06-24 — Initial plan (slice 3). Inside-out build on slices 1–2: entity-linking +
  Synthesizer (boto3 Converse) + neighborhood expansion + seed-and-expand hybrid + three-mode
  runner + CLI + in-VPC query Lambda behind an IAM-auth Function URL + consolidated showcase;
  live corpus-backed hybrid query in scope.
- 2026-06-24 — T1–T9 + T11 implemented; all four gates green (ruff lint+format, mypy, pytest
  151 passed / 1 skipped). Deviations: (a) T6 added a `_make_http_client` seam on the CLI so the
  SigV4 Function-URL signing path is unit-testable with a mock HTTP client; (b) the AC3 `expand_
  neighborhood` exemplar test asserts the hop-2 OWNS-from-SIG path (not the absence of the owned
  KEPs at hop 1), because `@thockin` also *approves* those KEPs directly, so all-edge expansion
  reaches them at hop 1 too — the 2-hop *via-SIG OWNS* path is the load-bearing claim and is the
  one pinned. The pure-Python-Lambda aliases decision is option (b): the live handler entity-links
  with `aliases={}` (mechanical normalizers only), keeping the bundle PyYAML-free. T10 (live
  deploy + hybrid smoke, AC9) is the supervisor's step — left unticked.
