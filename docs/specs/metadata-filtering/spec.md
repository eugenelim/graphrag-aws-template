# Spec: metadata-filtering

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [Charter — Pattern coverage table, *Metadata Filtering / Self-Query* row](../../CHARTER.md#pattern-coverage-against-the-graphragcom-catalog) (the coverage contract this slice ships), [RFC-0001 feasibility note §4](../../rfc/0001-notes/aws-feasibility.md) (efficient filtered k-NN **during** ANN search VERIFIED on **Lucene/Faiss HNSW**, *not* the currently-pinned `nmslib` engine — so this slice switches the k-NN method to Lucene HNSW), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (the vector leg of the seed-and-expand hybrid the self-query filter also threads through), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (rides the existing in-VPC query Lambda behind the IAM-auth Function URL; the engine switch lands on a fresh index, teardown-first; adds no billable resource), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python)
- **Brief:** [`docs/product/briefs/graphrag-pattern-catalog.md`](../../product/briefs/graphrag-pattern-catalog.md)
- **Contract:** none (new internal Python interfaces + an additive `mode` value on the existing in-VPC Function URL; no repo-root `contracts/` API surface, consistent with the sibling pattern slices)
- **Shape:** mixed

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> The **Metadata Filtering / Self-Query** pattern from the
> [graphrag.com](https://graphrag.com) catalog, implemented on OpenSearch — the LLM
> reads structured **filters** out of a natural-language question and the vector
> search applies them **during** the ANN scan. It is the *question-derived*
> generalization of the fixed visibility filter the
> [`permission-filtered-retrieval`](../permission-filtered-retrieval/spec.md) slice
> ships: that slice carries an allowed-tier `terms` filter on the same k-NN seam
> *ad hoc* (and, on the `nmslib` engine it inherited, as a **post-filter** with a
> recall caveat); this slice formalizes the seam into a real **during-ANN** filter
> on the Lucene engine and lets Bedrock populate it from the question. `Depends on:`
> the vector + hybrid slices ([`vector-rag-baseline`](../vector-rag-baseline/spec.md),
> [`hybrid-orchestration`](../hybrid-orchestration/spec.md)) and the permission slice
> — it reuses `vector_search`, the `VectorStore.knn` filter seam, `hybrid_query`/
> `run_modes`, the chunk `source`/`entity_ids` metadata, the slice-1
> `link_question`/`normalize` resolvers, the `BedrockClaudeSynthesizer`/`Bedrock…Selector`
> Converse posture, and the in-VPC query Lambda + IAM-auth Function URL.

## Objective

A solution architect evaluating GraphRAG needs to *see* the **self-query** pattern:
a user asks a natural-language question that carries an implicit structured
constraint — *"in the **enhancements** repo, which KEPs does **SIG Node** own?"* —
and the system extracts that constraint into a **structured metadata filter** and
applies it to vector retrieval, so the k-NN search ranks only over the qualifying
subset rather than the whole corpus. This slice delivers it on the same stores and
query path as the three retrieval modes, with the filter applied **during** the ANN
search — not as a post-filter over an already-ranked top-`k`.

The load-bearing engineering point is *where* the filter runs. A post-filter ranks
the whole corpus, takes the top `k`, and *then* drops the non-matching hits — so a
constrained question can return **fewer than `k`** relevant chunks (or none), and
recall silently degrades. An efficient filter applied during the ANN scan
restricts the candidate set first and still returns `k` hits *from the qualifying
subset*. AWS verifies this efficient-during-ANN behavior on the **Lucene/Faiss
HNSW** engines, not on the `nmslib` engine the index was first built on (RFC-0001
§4); so the k-NN index method **is Lucene HNSW** — which also closes the recall
caveat the permission slice carries for its own visibility `terms` filter on the
same seam.

The LLM's authority is **bounded by construction**: it may only emit filters over a
**fixed, declared field schema** (`source` — the cross-source repo; `entity_ids` —
a SIG / KEP / person), and every value is **validated deterministically** before it
touches OpenSearch — `source` against the closed enum of the two corpus repos
(`community`, `enhancements` — the `sources.COMMUNITY`/`ENHANCEMENTS` values the chunk
carries),
entity values resolved through the slice-1 resolvers to a *normalized* graph-node id.
An undeclared field, or a value that does not resolve, is **dropped and recorded**,
never passed through; the model never authors raw query DSL. The self-query filter
**composes** with the permission filter (both must pass — it only narrows, never
widens past a persona's clearance), threads through **vector mode and the vector leg
of the hybrid mode**, and the result carries a **trace** that names the extracted
filter, how each value was validated, and what was dropped — so the mechanism is
narratable, never a black-box hop. The whole path runs **offline by default** (a
deterministic, non-semantic rule extractor + in-memory store) for credential-free CI
and a laptop demo, and **live** against the deployed stores + Bedrock through the
existing query Lambda.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule, even under time pressure.

### Always do

- **Apply the self-query filter DURING the ANN search, never as a post-filter.**
  The k-NN index method uses the **Lucene HNSW** engine (RFC-0001 §4), which applies
  a `filter` during the scan and guarantees `k` hits from the qualifying subset; the
  filter rides the request body as a parameterized `bool`/`filter` (terms), exactly
  as the visibility `terms` filter already does — never interpolated into a path or
  query string. This is the slice's load-bearing correctness property.
- **Bound extraction to the fixed, declared field schema.** The extractor may only
  produce filters over the declared fields — `source` (validated against the closed
  enum `{community, enhancements}`) and `entity_ids` (each
  value run as a surface string through the slice-1 `link_question`/`normalize`
  resolvers — which are **pure**, no store/network — to a *normalized* graph-node id). An
  **undeclared field**, or a value whose surface matches **no declared-entity pattern**
  (KEP number / SIG name / `@handle` / alias — `link_question` returns `[]`), is **dropped
  and recorded** — never forwarded to OpenSearch, never bound as free-form model text.
- **Compose the self-query filter AND the permission (visibility) filter.** Both
  predicates apply together on the same k-NN call (a chunk must satisfy the
  self-query filter **and** be within the persona's clearance); the self-query filter
  can only *narrow* the candidate set, never widen access past clearance.
- **Pair the OpenSearch filter with an in-memory equivalent.** The in-memory
  `VectorStore` applies the **identical** structured-filter predicate so the offline
  backend returns the same filtered hit set — the slice-4 backend-identical invariant
  (`packages/graphrag/AGENTS.md`).
- **Treat the question as untrusted data at the Claude boundary.** Reuse the
  `BedrockTemplateSelector`/`BedrockClaudeSynthesizer` posture: the question rides
  Converse `messages` **as data** (never the `system` block), the `system` block
  carries the defensive untrusted-data directive (OWASP LLM01/LLM08), `maxTokens` is
  bounded, the client is the default botocore-chain client over TLS, and the answer is
  display-only.
- **Thread the filter through vector mode and the vector leg of hybrid.**
  `vector_search` and the vector retrieval inside `hybrid_query`/`run_modes` both
  accept and apply the validated metadata filter, consistent with how `clearance`
  already threads through both.
- **Reuse the existing query Lambda + Function URL for the live path.** The
  self-query path is dispatched by an **additive, backward-compatible** `mode` value
  on the existing IAM-auth Function URL (`"hybrid"` default | … | `"selfquery"`) — no
  new endpoint, no new ingress — and the self-query import graph stays **PyYAML-free**
  so it bundles in the `Code.from_asset` Lambda.
- **Keep teardown a feature** (charter principle 4): the engine switch lands at
  `create_index` on a **fresh** index (teardown-first rebuild, no live-domain mapping
  migration); the slice adds **no** billable resource and **no** standing cost.

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** Extraction uses the
  existing `bedrock-runtime` Converse client and the filter is request-body DSL over
  the existing adapter — no new dependency; reach for any other LLM/HTTP client only
  with sign-off, recorded in `packages/graphrag/AGENTS.md`.
- **Changing the declared filterable-field schema** (`source`, `entity_ids`) — adding
  or removing a self-query field is a teaching-surface decision, not an
  implementation detail.
- **Pinning or changing the extractor model id away from the synthesis-model
  default.** Extraction reuses the already-granted synthesis Claude model, so the IAM
  grant is unchanged today; a *different* model would widen the grant (AC8).
- **Changing the k-NN engine to anything other than Lucene HNSW** (e.g. Faiss HNSW,
  also efficient-filter-capable) — Lucene is the chosen default for this template; a
  different engine is a decision to surface.
- **Changing the Function-URL request/response contract beyond the additive `mode`
  value, or the self-query result/trace schema once a consumer depends on it.**

### Never do

- **Never let the LLM author a raw OpenSearch query, an undeclared field, or an
  un-validated `field:value`.** The extractor's output is a constrained, validated
  filter set over the declared schema only; anything else is dropped and recorded.
- **Never string-interpolate a filter value into a query** — always the request-body
  `terms` filter (the slice-4 / `neptune.py` parameterization posture; `ruff` `S`
  ruleset stays enabled).
- **Never ship the post-filter as the production path.** The `nmslib` post-filter
  (which can return fewer than `k` qualifying hits) is precisely what this slice
  replaces; the Lucene engine + during-ANN filter is the contract.
- **Never let the self-query filter widen visibility.** It composes `AND` with the
  persona's clearance and can only narrow — a self-query filter never re-admits a
  chunk above clearance.
- **Never add a new top-level directory or module boundary** beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces
  (AGENTS.md: top-level directories need an RFC). New code lands as modules/docs
  inside those.
- **Never let the offline non-semantic rule extractor back a quality claim** — it is
  labeled non-semantic in the output; semantic extraction is the live path.
- **Never let the self-query import graph `import yaml` at Lambda module load** — the
  existing `sys.modules` guard test is extended to the self-query modules.
- **Never expose a public, unauthenticated endpoint or weaken the Function URL below
  IAM-auth.**

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1 — TDD.** Pure functions/constants over the declared field schema: the field
  set, kinds, the `source` enum, and the `MetadataFilter` value type are
  deterministic and trivially unit-tested; no store, no network.
- **AC2 — TDD.** Deterministic validation over the fixture corpus + aliases: a valid
  `source`, an entity value resolving to a confirmed graph-node id, an unconfirmed
  entity dropped+recorded, and an undeclared field dropped+recorded; no free-form
  value ever survives.
- **AC3 — TDD + goal-based mapping check.** A static check asserts `_knn_mapping`
  declares the k-NN method `engine: "lucene"` (HNSW) with a Lucene-supported `space_type`;
  a `knn` test asserts the
  adapter composes the metadata filter **and** the visibility filter into a single
  request-body `bool`/`filter` (parameterized — asserted via the adapter's mock HTTP
  client), and the in-memory store applies the identical predicate (backend-identical
  filtered hit set).
- **AC4 — TDD with mock.** The Bedrock extractor against a **mock** Converse client
  returns a validated filter; an undeclared field / unresolvable value / malformed
  JSON is dropped (never raised to OpenSearch); the Converse request carries the
  defensive `system` directive, the question in `messages` (not `system`), a bounded
  `maxTokens`, and the default-TLS client (no `verify=False`). The `RuleMetadataExtractor`
  (offline, deterministic, **non-semantic**, labeled) extracts structurally for CI.
  The `ruff` `S` ruleset stays enabled.
- **AC5 — TDD + narratability check.** Over the fixture, `vector_search(...,
  metadata_filter)` and `hybrid_query(..., metadata_filter)`/`run_modes` exclude every
  non-matching chunk from the vector hits **and** the vector leg of hybrid, the trace
  renders an `extracted filter:` line (each field/value + how it was validated, and
  what was dropped); a no-filter question leaves retrieval unfiltered. The orchestrator
  render order is asserted.
- **AC6 — TDD.** `graphrag selfquery-query` runs offline (in-memory + rule extractor +
  offline synthesizer) and prints the ordered trace + the non-semantic label;
  `--bedrock` switches to the Bedrock extractor + Claude synthesis; `--function-url`
  builds a SigV4 POST whose **body** carries `mode: "selfquery"`.
- **AC7 — TDD with mock.** With the extractor, store, and synthesizer mocked,
  `lambda_handler` with `mode="selfquery"` runs the path end-to-end and returns the
  trace envelope; an unknown `mode` is a client error; the over-long-question guard and
  the generic sanitized error envelope apply as for hybrid; a `sys.modules` assertion
  proves the self-query import graph stays PyYAML-free.
- **AC8 — goal-based (`cdk synth` + `aws_cdk.assertions.Template`), CDK-env-gated.**
  The engine switch is an **app-side** mapping change, not CDK: the synthesized stack
  adds **no** new resource and **no** new IAM statement, the query Lambda's Bedrock
  grant still scopes the synthesis model (Converse) with no wildcard `Resource`, and
  the Budgets value is asserted **unchanged at the literal `150`**.
- **AC9 — live deploy + self-query smoke (active end-to-end).** Against the deployed
  stack (corpus dual-written on a fresh Lucene-engine index), a SigV4-signed
  `mode: selfquery` call extracts a filter from a constrained question, runs filtered
  k-NN **live on OpenSearch with the filter applied during ANN**, returns the trace
  (extracted + validated filter, filtered hits) and a Claude answer; a contrasting
  no-filter question runs unfiltered; then the stack is destroyed (teardown-first).
- **AC10 — goal-based (self-query showcase set + explanation doc).** A
  `selfquery_queries` section holds **≥4** queries spanning **vector mode and hybrid
  mode**, each labeled with the expected extracted filter (field/value) and the gold
  visible/excluded chunk split; a loader/test asserts it parses and every named entity
  resolves in the fixture corpus. A doc under `docs/guides/` walks the self-query path
  and states the contrast (question-derived self-query filter vs. the fixed permission
  filter; the engine switch closing the post-filter recall caveat).

Gates: `ruff` (lint+format, `S` security ruleset), `mypy` (typecheck), `pytest`
(tests). Already wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [x] **AC1 — Declared filterable-field schema + structured-filter model (pure,
  PyYAML-free).** A `graphrag.selfquery` module declares the **fixed** set of
  filterable fields: `source` (kind `enum`, the closed set `{community,
  enhancements}` — the `sources.COMMUNITY`/`ENHANCEMENTS` values) and `entity_ids` (kind `entity`, resolved to a graph-node
  id). A `MetadataFilter` value type carries the validated `{field: [values]}` map and
  is empty when nothing was extracted; its match semantics are **OR within a field**
  (a chunk matches if its field value intersects the filter's value set) and **AND
  across fields** (every present field must match). The module imports **no `yaml`**
  (importable by the query Lambda). *(TDD)*
- [x] **AC2 — Deterministic filter validation (the governance boundary).**
  `validate_filter(raw, *, aliases)` turns a raw extracted map into a `FilterExtraction`
  (the validated `MetadataFilter` + the dropped entries): a `source` value is kept only
  if it is in the closed enum; an `entity_ids` value (a surface string) is resolved
  through the slice-1 `link_question`/`normalize` resolvers (**pure** — no store, no
  network) to a **normalized** graph-node id (a value whose surface matches no
  declared-entity pattern — `link_question` returns `[]` — is **dropped and
  recorded**); an **undeclared field** is **dropped and recorded**; an empty result is
  the no-filter case. No free-form model value is ever bound. (Resolution is
  controlled-vocabulary *normalization*, not corpus-existence confirmation — a resolved
  id that matches no chunk simply filters to zero hits, a correct self-query outcome — so
  this path needs **no graph store and no Neptune grant**.) *(TDD)*
- [x] **AC3 — k-NN engine is Lucene HNSW; filter applied DURING ANN.** `_knn_mapping`
  declares the `knn_vector` method with `engine: "lucene"` (HNSW), keeping a
  Lucene-supported `space_type` (`cosinesimil`, supported on the Lucene engine in
  OpenSearch 2.11), and
  `VectorStore.knn(vector, k, *, allowed_labels, metadata_filter)` composes the
  metadata filter **and** the visibility `terms` filter into a **single** request-body
  `bool`/`filter` so both are applied **during** the ANN scan (efficient filtering,
  RFC-0001 §4 — returns `k` from the qualifying subset, not a post-filter over the top
  `k`). Filter values ride the request body, never interpolated. The in-memory store
  applies the **identical** predicate; for a given filter both backends return the
  **same** filtered hit set (sorted; backend-identical). `metadata_filter=None` and the
  empty filter leave the candidate set unfiltered. **Composition preserves the permission
  slice's fail-closed `Clearance` semantics:** the metadata `terms` and the visibility
  `terms` are **independent** clauses on the same `bool`/`filter`, so a `None` clearance
  contributes **no** visibility clause (unrestricted) while a `Clearance` with an **empty**
  `allowed` set still contributes a visibility clause that **matches nothing** (zero hits) —
  regardless of `metadata_filter`. An empty metadata filter (unfiltered) can never cause the
  clearance clause to be dropped, and a self-query filter can only narrow, never re-admit a
  chunk above clearance. Pinned at the composed `knn` seam for both backends. *(TDD +
  goal-based mapping check)*
- [x] **AC4 — Bedrock self-query extractor (Converse), validated, with an offline
  deterministic counterpart.** `BedrockMetadataExtractor.extract(question, *, aliases)`
  issues a well-formed Converse request — a configurable `modelId` (default
  `DEFAULT_SYNTHESIS_MODEL_ID`); a `system` block instructing extraction of a JSON filter
  over **only** the declared fields, plus the defensive directive that the question is
  untrusted data (LLM01/LLM08); the question in `messages` **as data**; a bounded
  `maxTokens` — parses the JSON, then runs it through `validate_filter` (AC2) and
  **returns the validated `FilterExtraction`**, so an undeclared field / unresolvable
  value / malformed response yields a **dropped-and-recorded** entry or the empty filter,
  never a raised raw value — all verifiable at the extractor seam against a **mock** (no
  live call); the client is the default botocore-chain client over TLS. A
  `RuleMetadataExtractor` (offline, deterministic, **non-semantic**, labeled) extracts
  via keyword + `link_question` candidate rules and returns the same validated
  `FilterExtraction` for CI/offline. (`validate_filter` is the single pure validation
  chokepoint both extractors call.) *(TDD with mock)*
- [x] **AC5 — Self-query orchestration with a trace; threads vector AND hybrid's vector
  leg.** `vector_search(..., metadata_filter)` and `hybrid_query(..., metadata_filter)`/
  `run_modes(..., metadata_filter)` thread the validated filter into the vector k-NN
  call (composed with `clearance`), so a constrained question's vector hits **and** the
  vector leg of its hybrid result exclude every non-matching chunk. When a `clearance` is
  **also** supplied, the two compose AND (a chunk must match the filter **and** be within
  clearance) and the fail-closed clearance semantics of AC3 hold through `hybrid_query`/
  `run_modes` — a self-query filter never re-admits an above-clearance chunk. A `selfquery_query`
  orchestrator (`extractor.extract` → validated `FilterExtraction` → filtered search →
  synthesize) returns a result
  whose `.render()` narrates, in order, **question → extracted filter → validated filter
  (+ what was dropped) → filtered hits → answer**; the trace adds an `extracted filter:`
  line naming each field/value and how it was validated. A **no-filter** question
  (nothing extractable) leaves retrieval unfiltered and says so in the trace. *(TDD +
  narratability check)*
- [x] **AC6 — CLI verb `selfquery-query`, offline by default, live via SigV4.**
  `graphrag selfquery-query --q "<text>"` runs **offline** (in-memory store from the
  fixture corpus + `RuleMetadataExtractor` + offline synthesizer) and prints the ordered
  trace, labeling the extractor **non-semantic**. `--bedrock` switches to
  `BedrockMetadataExtractor` + Bedrock Claude synthesis. `--function-url <url>` switches
  to the **thin live client** — a SigV4-signed (`service=lambda`) HTTPS POST of
  `{"question": …, "mode": "selfquery"}` whose **signature covers the body** — and renders
  the returned trace; a non-2xx raises with the body. *(TDD)*
- [x] **AC7 — In-VPC query Lambda self-query dispatch, PyYAML-free, sanitized.**
  `lambda_handler` reads an optional `mode` and on `"selfquery"` builds the live
  OpenSearch store + `BedrockMetadataExtractor` (the same Converse model) +
  `BedrockClaudeSynthesizer` from the execution role, runs `selfquery_query`, and returns
  the trace envelope (extracted + validated filter, filtered hits, answer, citations,
  trace). An **unknown mode** is a client error; the **over-long-question** guard and the
  **generic sanitized error envelope** (correlation id, no internal endpoint/ARN/stack
  detail) apply exactly as for hybrid. The self-query import graph stays **PyYAML-free**
  (the existing `sys.modules` guard is extended to the self-query modules). Exercised with
  the extractor, store, and synthesizer **mocked** (no network); reuses the **same**
  `selfquery_query` the CLI uses. *(TDD with mock; live in AC9)*
- [x] **AC8 — IaC unchanged: engine switch is app-side, no new resource, no widened
  grant, cost held.** The k-NN engine change lives in `store/opensearch.py`
  (`_knn_mapping`), applied at `create_index` on a **fresh** index — not CDK. The
  self-query Lambda path uses the **same grants as the hybrid path** — the
  already-granted synthesis-model `bedrock:Converse` action and the existing OpenSearch
  data-access — and **adds no Neptune statement** (entity validation is pure
  controlled-vocab resolution, AC2, so this path never touches the graph store). A synth
  assertion confirms `cdk synth` adds **no** new resource and **no** new IAM statement for
  the self-query path: the query Lambda's Bedrock grant still scopes the synthesis model
  with **no wildcard `Resource`**, and the Budgets value is asserted **unchanged at the
  literal `150`**. Per ADR-0002. *(goal-based synth, CDK-env-gated)*
- [ ] **AC9 — Live deploy + self-query smoke (in-VPC).** Against the deployed stack with
  the corpus dual-written on a fresh Lucene-engine index, a SigV4-signed `mode: selfquery`
  call extracts a structured filter from a constrained question (a `source` and/or an
  `entity_ids` constraint), runs filtered k-NN **live on OpenSearch with the filter applied
  during ANN**, and returns the trace (extracted + validated filter, the filtered hits) and
  a Bedrock Claude answer; a contrasting no-filter question runs unfiltered. A third call pairs
  an extracted filter with a **non-default persona/clearance** and asserts the live filtered
  hits exclude **both** above-clearance chunks **and** non-matching chunks — proving the two
  `terms` clauses compose AND **during ANN on the Lucene engine** (the one place engine-level
  composition runs for real). Then the stack is destroyed (teardown-first). If live AWS access
  is unavailable in the build environment,
  this criterion ships deferred against a backlog anchor, with the offline + mocked path
  proving the orchestration. *(live smoke)*
- [x] **AC10 — Self-query showcase set + the self-query teaching framing.** A
  `selfquery_queries` section in the showcase `queries.yaml` holds **≥4** queries spanning
  **vector mode and hybrid mode**, each labeled with the expected extracted filter
  (field/value) and the gold visible/excluded chunk split; a loader/test asserts it parses
  and every named entity's **normalized id matches at least one fixture chunk** (a gold-data
  check — distinct from `validate_filter`, which is corpus-blind per AC2). A doc under
  `docs/guides/` walks the self-query path with the exact CLI commands and **states the
  contrast** — the self-query filter is *question-derived* (the LLM reads the constraint
  out of the question) where the permission filter is *fixed* (the persona's clearance);
  the filter runs **during** ANN on the Lucene engine, closing the post-filter recall
  caveat — so a watcher can state when self-query metadata filtering helps. *(goal-based)*

## Assumptions

- Technical: runtime is Python 3.11+; runtime deps stay `pyyaml` + `boto3>=1.35`,
  infra extra is `aws-cdk-lib`, dev is `pytest`/`ruff` (with the `S` ruleset)/`mypy`;
  this slice adds **no** runtime dependency (source: `pyproject.toml`;
  `packages/graphrag/AGENTS.md`).
- Technical: the deployed OpenSearch domain is engine **2.11** but the k-NN mapping
  currently pins the **`nmslib`** engine, where a `bool`+`filter` behaves as a
  **post-filter** over the `k` ANN candidates (and can return fewer than `k` qualifying
  hits); AWS verifies efficient filtering **during** ANN only on **Lucene/Faiss HNSW**
  — so this slice switches the method `engine` to **`lucene`** (HNSW) (source:
  `apps/infra/stacks/graphrag_stack.py:487`; `store/opensearch.py:89,181-188`; RFC-0001
  §4; user confirmation 2026-06-25).
- Technical: the filterable chunk fields already mapped as `keyword` are `source` (the
  cross-source repo: `community` vs `enhancements`, per `sources.COMMUNITY`/`ENHANCEMENTS`) and
  `entity_ids`; the self-query schema exposes exactly these two — `visibility` stays the
  *permission* filter, not a self-query field (source: `store/opensearch.py:_knn_mapping`;
  user confirmation 2026-06-25).
- Technical: the self-query filter composes with the existing visibility `terms` filter
  on the same `VectorStore.knn` seam, both riding the request body parameterized; the
  in-memory store applies the identical predicate (the slice-4 backend-identical
  invariant) (source: `store/opensearch.py:174`; `vector.py:46`; `packages/graphrag/AGENTS.md`).
- Technical: the extractor reuses the established `BedrockTemplateSelector` Converse
  posture — `system` block defensive directive, question in `messages` as data, bounded
  `maxTokens`, default-TLS client, JSON output validated against a fixed schema (source:
  `select.py:97-134`).
- Technical: entity filter values resolve through the slice-1 `link_question`/`normalize`
  functions on the controlled vocabulary — which are **pure** (no store, no network;
  `link_question` returns candidates, the graph confirmation is a separate hybrid-layer
  step the self-query path does not need) — so a bound entity value is byte-identical to its
  resolved graph-node id and the self-query path needs **no graph store / no Neptune grant**
  (source: `entity_link.py:9` "pure (no store, no network)", `:71` `link_question`; `normalize.py`).
- Technical: the live self-query path **reuses the existing in-VPC query Lambda + the
  IAM-auth Function URL**, dispatched by the additive back-compat `mode` field; the
  Lambda's IAM already grants `bedrock:Converse` on the synthesis model and OpenSearch
  data-access, so the slice adds **no new infra resource or IAM statement** (source:
  `query_lambda.py:98-128`; `apps/infra/stacks/graphrag_stack.py`).
- Technical: the engine switch is an app-side mapping change applied at `create_index`
  on a fresh deploy (teardown-first rebuild, no live-domain migration); no test pins the
  `nmslib` engine string, and the frozen-vector eval is in-memory cosine
  (engine-independent), so the switch is contained to `_knn_mapping` + its tests (source:
  `store/opensearch.py:81`; `vector_eval.py`; repo grep for `nmslib` 2026-06-25).
- Product: the audience is a solution architect evaluating the *self-query* pattern; the
  slice ends at extraction + validated filter + during-ANN filtered k-NN (vector + the
  vector leg of hybrid) + the trace + the contrast framing; the self-query filter is
  question-derived where the permission filter is fixed (source: charter coverage table;
  brief Scope; user confirmation 2026-06-25).
- Product: the self-query showcase covers **both** vector mode and hybrid mode (source:
  user confirmation 2026-06-25).
- Process: no new ADR — the engine choice is verified by RFC-0001 §4 and the topology is
  pinned by ADR-0002; the engine switch + field-schema are slice-level LLD in `plan.md`,
  folded in with the permission-slice recall-caveat fix (source: `docs/rfc/0001-notes/aws-feasibility.md`
  §4; user confirmation 2026-06-25).
- Process: full work-loop mode — security boundary (an untrusted question routed to an
  LLM extractor; OpenSearch network I/O; an IAM-auth public Function URL) and structural
  (the k-NN index-method engine change + new modules + a Function-URL `mode` extension);
  constrained by the charter coverage table + RFC-0001 §4 + ADR-0001/0002/0003 (source:
  `docs/CONVENTIONS.md` risk triggers; brief Spec map row `metadata-filtering`).
- Process: the live AC (AC9) is run when AWS access is available (live deploy is available
  in this environment), else deferred with a backlog anchor created atomically (source:
  user auto-memory `live-deploy-available`; the opencypher-templates AC9 precedent).

## Changelog

- 2026-06-25 — Spec authored. Metadata Filtering / Self-Query pattern: Bedrock extracts a
  structured filter (over the fixed `source`/`entity_ids` schema) from the question →
  OpenSearch filtered k-NN with the filter applied **during** ANN on the **Lucene HNSW**
  engine (switched from `nmslib`, closing the permission slice's post-filter recall
  caveat); deterministic validation bounds the LLM's authority; the filter composes with
  the permission filter and threads through vector + hybrid; rides the existing query
  Lambda via an additive `mode: selfquery` (no new infra); offline runs via a non-semantic
  rule extractor + the in-memory backend-identical predicate.
