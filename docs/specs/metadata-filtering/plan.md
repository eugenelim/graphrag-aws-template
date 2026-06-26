# Plan: metadata-filtering

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

The self-query path reuses every retrieval seam the vector + hybrid + permission
slices built; the only genuinely new mechanism is **structured-filter extraction**
and a **k-NN engine switch**. The shape: a new **pure-Python self-query module**
(`selfquery.py`) declares the fixed filterable-field schema (`source` enum,
`entity_ids` entity) and a `MetadataFilter` value type, plus `validate_filter` —
the deterministic governance boundary that turns a raw extracted map into a
validated filter (a `source` checked against the closed enum; an `entity_ids` value
resolved through the slice-1 `link_question`/`normalize` resolvers to a confirmed
graph-node id; an undeclared field or unresolvable value dropped and recorded). An
**extractor** seam (`BedrockMetadataExtractor` for the live path, `RuleMetadataExtractor`
for CI/offline) reads the question into a raw map, but its output is always run
through `validate_filter`, so the model can only ever produce a vetted filter. The
**`VectorStore.knn` seam grows a `metadata_filter` parameter** that composes with the
existing `allowed_labels` (visibility) filter into a single request-body `bool`/`filter`;
critically, the OpenSearch `_knn_mapping` k-NN method **switches from `nmslib` to
`lucene` (HNSW)** so that filter is applied *during* the ANN scan (RFC-0001 §4), not
as a post-filter — which also closes the recall caveat the permission slice flagged.
`vector_search`, `hybrid_query`, and `run_modes` thread the filter through vector mode
and the vector leg of hybrid. An orchestrator (`selfquery_query`) wires
extract → validate → filtered search → synthesis into a result whose `.render()` is the
trace. A CLI verb (`selfquery-query`) and an additive `mode: "selfquery"` branch on the
existing query Lambda expose it offline and live.

The riskiest parts are (1) the **engine switch** — it changes the index method for
*all* vector retrieval and must land on a fresh index (teardown-first), with the
existing permission/vector tests still green; mitigated by no test pinning the
`nmslib` string and the frozen-vector eval being engine-independent (in-memory
cosine), so the blast radius is `_knn_mapping` + the adapter `knn` body + their tests;
and (2) keeping the self-query import graph **PyYAML-free** so it bundles in the
`Code.from_asset` Lambda, mitigated by making `selfquery.py` import only yaml-free
modules and extending the existing `sys.modules` guard test. **No new infra or IAM** —
extraction reuses the already-granted synthesis-model `bedrock:Converse` action and the
existing OpenSearch data-access (AC8).

## Constraints

- **Charter coverage table** (*Metadata Filtering / Self-Query* row) — this slice ships
  that row; it is the question-derived generalization of the fixed permission filter.
- **RFC-0001 feasibility §4** — efficient filtering **during** ANN is VERIFIED on
  **Lucene HNSW (2.4+) / Faiss HNSW (2.9+)**, *not* the pinned `nmslib` engine — so the
  k-NN method switches to Lucene HNSW; the filter rides the request body, parameterized.
- **ADR-0001** — reuse the `Synthesizer` seam + `link_question`; the self-query filter
  threads through the vector leg of the seed-and-expand hybrid; no new matching model.
- **ADR-0002** — ride the existing in-VPC query Lambda + IAM-auth Function URL; the
  engine switch lands on a fresh index at `create_index` (teardown-first, no migration);
  add no billable resource; Budgets unchanged at `150`.
- **ADR-0003** — IaC stays AWS CDK Python.
- **`packages/graphrag/AGENTS.md`** — runtime deps stay `pyyaml` + `boto3>=1.35`; the
  query import graph stays PyYAML-free; the vector filter is request-body DSL with the
  in-memory backend applying the identical predicate (backend-identical hit set).

## Construction tests

Most tests live under their task. Cross-cutting:

**Integration tests:**
- Offline end-to-end (`selfquery_query` over the fixture corpus with
  `RuleMetadataExtractor` + in-memory store + offline synthesizer) on a constrained
  exemplar (e.g. *"in the enhancements repo, which KEPs does SIG Node own?"*) — asserts
  the extracted+validated filter (`source=kubernetes/enhancements`, `entity_ids=[sig:sig-node]`),
  the filtered hits exclude the community-repo chunks, and `.render()` emits
  question → extracted filter → validated filter → filtered hits → answer in order (T5).
- Backend-identical filter: for a given `MetadataFilter`, the in-memory `knn` and the
  OpenSearch adapter `knn` (mock HTTP client) produce the **same sorted** filtered hit
  set, and the OpenSearch body carries the composed `bool`/`filter` (T3).
- PyYAML-free import-graph guard: blocks `import yaml`, then imports `selfquery` (extends
  `test_query_lambda.py`) (T7).

**Manual verification:** AC9 live deploy + self-query smoke (run if live AWS is
available; otherwise deferred — see Rollout).

## Design (LLD)

Stack: Python 3.11+, `boto3` `bedrock-runtime` Converse, `botocore` SigV4 to the
Function URL, OpenSearch k-NN over `botocore`-signed HTTPS, AWS CDK Python. Conforms to
the existing `packages/graphrag` module stereotypes (a pure logic module + an injectable
adapter + an orchestrator + a CLI verb), mirroring `select.py`/`governed.py`/`vector.py`.

### Design decisions
- **The schema is a fixed Python declaration, not data the LLM can extend.** The two
  filterable fields (`source` enum, `entity_ids` entity) and their validators are reviewed
  code → the LLM's authority is bounded and the module is Lambda-safe (no yaml). *Rejected:*
  letting the extractor name arbitrary fields or emit raw OpenSearch DSL (would make the
  filter model-authored — the exact thing this slice's governance boundary prevents).
  Traces to: AC1, AC2, AC4.
- **The LLM extracts; validation is deterministic.** The extractor returns a raw map; every
  value is re-derived/validated against the schema (enum membership; entity resolved to a
  confirmed graph-node id). This minimizes and bounds the LLM's authority — the same
  selector-vs-extraction split the governed slice uses. *Rejected:* binding the model's raw
  values directly (free-form value into a query). Traces to: AC2, AC4.
- **Engine switch `nmslib` → `lucene` (HNSW) to get during-ANN filtering.** The pinned
  `nmslib` engine post-filters (the permission slice's recall caveat); Lucene HNSW applies
  the filter during the scan and returns `k` from the qualifying subset (RFC-0001 §4). The
  switch is contained to `_knn_mapping` and lands on a fresh index. *Rejected:* Faiss HNSW
  (also efficient-filter-capable but a different posture — Lucene is the simpler default;
  recorded as the *Ask first* alternative); keeping `nmslib` + post-filter (fails the
  slice's headline contract). Traces to: AC3.
- **One `knn` filter clause composes self-query AND visibility.** Both predicates become a
  single request-body `bool`/`filter` (`terms` per field), so they apply together during ANN
  and the self-query filter can only narrow. *Rejected:* a second post-filter pass (defeats
  the engine switch). Traces to: AC3, AC5.
- **Live path rides the existing Function URL via an additive `mode` value.** Back-compat
  (absent ⇒ `hybrid`); no new endpoint/IAM. Traces to: AC6, AC7, AC8.

### Data & schema
- `FieldSpec(name: str, kind: Literal["enum", "entity"], choices: tuple[str, ...] | None,
  entity_kind: EntityKind | None)`; `FIELDS: tuple[FieldSpec, ...]` declaring `source`
  (enum, choices = the two corpus repos) and `entity_ids` (entity); `FIELD_BY_NAME`.
- `MetadataFilter(terms: Mapping[str, tuple[str, ...]])` — frozen; `.is_empty`,
  `.as_filter_clauses()` (the request-body `terms` list), `.matches(chunk)` (the in-memory
  predicate; semantics: **OR within a field** — `chunk.field ∩ filter.values ≠ ∅` — and
  **AND across fields**). Values are normalized graph-node ids — SIG `sig:<slug>`, KEP
  `kep-<num>` (hyphen, no colon — `normalize.kep_id`), person `person:<handle>`. Empty ⇒
  unfiltered.
- `FilterExtraction(filter: MetadataFilter, dropped: tuple[DroppedFilter, ...])` where
  `DroppedFilter(field, value, reason)` records each undeclared-field / unresolvable-value
  drop for the trace. `validate_filter(raw, *, aliases) -> FilterExtraction` is the pure
  validation chokepoint (no store param — entity values resolve via the pure
  `link_question`/`normalize`, so no graph store / Neptune grant is needed).
- `SelfQueryResult(question, extraction: FilterExtraction, hits: list[VectorHit] |
  HybridResult, answer: str, citations: list[str], mode: Literal["vector", "hybrid"])`.
- The OpenSearch chunk fields used: `source` (keyword), `entity_ids` (keyword) — already
  mapped; no new field. Traces to: AC1, AC2, AC3, AC5.

### Interfaces & contracts
- Internal Python seams only (no repo-root `contracts/`). `VectorStore.knn` gains an optional
  `metadata_filter: MetadataFilter | None` (additive, default `None` = unfiltered, so slices
  2–4 callers are unchanged). The Function URL request gains the `mode: "selfquery"` value
  (additive); the self-query response envelope adds `extracted_filter`, `dropped`, `mode` to
  the existing `{answer, citations, trace, ...}` shape. Traces to: AC3, AC6, AC7.

### Component / module decomposition
- New: `selfquery.py` (field schema + `MetadataFilter` + `validate_filter` + extractor seam
  `BedrockMetadataExtractor`/`RuleMetadataExtractor` + `selfquery_query` orchestrator).
  Reused: `entity_link.link_question`, `normalize.*`, `synthesize.*`, `store/*`,
  `vector.vector_search`, `hybrid.hybrid_query`/`run_modes`. Modified: `store/vector_base.py`
  (the `knn` ABC) + `store/opensearch.py` (`_knn_mapping` engine → `lucene`; `knn` body) +
  `store/vector_memory.py` (`knn` gains `metadata_filter`), `vector.py`/`hybrid.py` (thread
  `metadata_filter`),
  `cli.py` (verb), `query_lambda.py` (mode dispatch). Traces to: AC1–AC7.

### State & control flow
`selfquery_query`: `extractor.extract(question, aliases=…)` (Bedrock/rule parses to a raw
map then calls `validate_filter`) → `FilterExtraction` (filter + dropped) → filtered
`vector_search` or `hybrid_query` (thread `metadata_filter` + any `clearance`) →
`synthesize` over hits → `SelfQueryResult`.
`render()` order: question → extracted filter → validated filter (+ dropped) → filtered
hits → answer. Empty filter ⇒ unfiltered retrieval, trace says "no filter extracted".
Traces to: AC5.

### Behavior & rules
- Schema-bound extraction: a raw key not in `FIELD_BY_NAME` ⇒ dropped+recorded; a `source`
  value not in the enum ⇒ dropped+recorded; an `entity_ids` surface value that matches no
  declared-entity pattern via the pure `link_question`/`normalize` (`link_question` returns
  `[]`; no `get_node` confirmation — a resolved-but-absent id simply filters to zero hits) ⇒
  dropped+recorded.
- Composition: the `knn` request body pairs `must: [knn_clause]` with `filter:` = the
  visibility `terms` (if a clearance) **and** each self-query field's `terms` (if present) —
  one `bool`. The in-memory `knn` applies `chunk.visibility ∈ allowed` **and**
  `metadata_filter.matches(chunk)`.
- No-interpolation: filter values ride the `terms` list in the body, never a path/string.
  Traces to: AC2, AC3.

### Failure, edge cases & resilience
- Nothing extractable / all dropped ⇒ empty `MetadataFilter` ⇒ unfiltered retrieval (a
  valid, narrated outcome, not an error).
- Malformed Converse JSON ⇒ empty filter (recorded), never a raised raw value.
- Lambda: over-long question rejected pre-orchestration; any failure ⇒ generic sanitized
  envelope + correlation id; unknown `mode` ⇒ client error. Traces to: AC4, AC5, AC7.

### Quality attributes (NFRs / security)
- Parameterization: every filter value in the request-body `terms` (no interpolation) —
  pinned by the AC3 adapter test and the slice-4 posture. `ruff` `S` stays enabled.
- Untrusted-input at the Claude boundary (extraction + synthesis): question/hits as Converse
  `messages` data, defensive system directive, bounded `maxTokens`, default-TLS client,
  display-only answer (reuse `select.py`/`synthesize.py` posture). Traces to: AC4.
- The self-query filter composes `AND` with clearance and only narrows — never widens past a
  persona's visibility. Traces to: AC3, AC5.
- PyYAML-free self-query import graph (Lambda bundle). Traces to: AC7.

### Dependencies & integration
No new runtime dependency (Converse via existing `bedrock-runtime`; filter is request-body
DSL over the existing adapter; SigV4 via `botocore`). No new infra resource; reuse the query
Lambda's existing grants. Traces to: AC8.

## Tasks

### T1: Self-query field schema + `MetadataFilter` model (AC1)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/selfquery.py, packages/graphrag/tests/test_selfquery.py
**Tests:**
- `# STUB: AC1`: `FIELDS` declares exactly `source` (enum, choices = the two repos) and
  `entity_ids` (entity); `FIELD_BY_NAME` round-trips; `MetadataFilter({})` `.is_empty`;
  `.as_filter_clauses()` returns one `terms` clause per field; `.matches(chunk)` is true iff
  every present field's value set intersects the chunk's field (**OR within / AND across**) —
  incl. a **multi-value** `entity_ids` case (`[sig:sig-node, kep-1880]` matches a chunk
  carrying either id; note KEP ids are hyphen-form `kep-1880`, not `kep:…`) and a two-field
  case (must match both); `import graphrag.selfquery` pulls in no `yaml`.
**Approach:**
- Define `FieldSpec`, `FIELDS`, `FIELD_BY_NAME`, `MetadataFilter` (frozen dataclass) with
  `is_empty`, `as_filter_clauses`, `matches`. Pure Python; no yaml import.
**Done when:** `test_selfquery.py` schema tests green; `ruff`/`mypy` clean.

### T2: Deterministic filter validation (AC2)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/selfquery.py, packages/graphrag/tests/test_selfquery.py
**Tests:**
- `# STUB: AC2`: `validate_filter` keeps a valid `source`; resolves an `entity_ids` surface
  value (`"SIG Node"`→`sig:sig-node` via the SIG slug normalizer; `"KEP-1880"`→`kep-1880`);
  drops+records a value whose surface matches no declared-entity pattern (`link_question`
  returns `[]`); drops+records an undeclared field; an all-dropped/empty input ⇒ empty
  `MetadataFilter`; no free-form value survives; **no store argument** is taken.
**Approach:**
- `validate_filter(raw: Mapping[str, list[str]], *, aliases) -> FilterExtraction`: per field,
  branch on `kind` — enum membership for `source`; for `entity_ids`, run each surface value
  through the **pure** `link_question`/`normalize` to a normalized id (no `get_node`; an empty
  `link_question` result is the drop); record each drop with a reason.
**Done when:** `test_selfquery.py` validation tests green; gates clean.

### T3: k-NN engine → Lucene HNSW + `metadata_filter` on the knn seam (AC3)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/store/vector_base.py, packages/graphrag/src/graphrag/store/opensearch.py, packages/graphrag/src/graphrag/store/vector_memory.py, packages/graphrag/tests/test_store_opensearch.py, packages/graphrag/tests/test_vector_store_memory.py
**Tests:**
- `# STUB: AC3` mapping: `_knn_mapping(...)["mappings"]["properties"]["vector"]["method"]`
  has `engine == "lucene"`, `name == "hnsw"`, and a Lucene-supported `space_type`
  (`cosinesimil` — supported on the Lucene engine in OpenSearch 2.11).
- `# STUB: AC3` compose: OpenSearch `knn(vector, k, allowed_labels=…, metadata_filter=…)` issues
  a single `bool` with `must:[knn]` and a `filter:` carrying both the visibility `terms` and the
  self-query field `terms` (asserted via the mock HTTP client; values in the body, not a path).
- `# STUB: AC3` backend-identical: in-memory `knn` with the same filter returns the same sorted
  hit set; `metadata_filter=None`/empty ⇒ unfiltered (slice-2–4 behavior unchanged).
**Approach:**
- Switch `_knn_mapping` method `engine` `nmslib`→`lucene`, keeping `space_type:
  "cosinesimil"` (Lucene-supported on 2.11 — verify against the OpenSearch 2.11 k-NN docs
  at implementation; no embedding re-normalization needed since the space is unchanged).
- Add `metadata_filter: MetadataFilter | None = None` to the `VectorStore.knn` ABC and both
  impls (`VectorStore` ABC in `vector_base.py`; `MemoryVectorStore` in `vector_memory.py`;
  `OpenSearchVectorStore` in `opensearch.py`); OpenSearch composes the existing visibility `filter`
  with `metadata_filter.as_filter_clauses()` into one `bool`; in-memory ANDs
  `metadata_filter.matches(chunk)` into the existing predicate.
**Done when:** mapping + compose + backend-identical tests green; existing vector/permission
tests still green; gates clean.

### T4: Extractor seam — Bedrock + offline rule extractor (AC4)
**Depends on:** T2
**Touches:** packages/graphrag/src/graphrag/selfquery.py, packages/graphrag/tests/test_selfquery.py
**Tests:**
- `# STUB: AC4`: `BedrockMetadataExtractor.extract(question, aliases=…)` against a **mock**
  Converse client returns a validated `FilterExtraction`; an undeclared field / unresolvable
  value / malformed JSON ⇒ dropped or empty (never raised) — assertable at the seam since the
  extractor calls `validate_filter` internally; the Converse request has the defensive `system`
  directive, the question in `messages` (not `system`), bounded `maxTokens`, default-TLS client
  (no `verify=False`). `RuleMetadataExtractor` extracts the exemplar's `source` + `entity_ids`
  structurally and returns the same validated `FilterExtraction`; labeled non-semantic.
**Approach:**
- `MetadataExtractor` protocol `extract(question, *, aliases) -> FilterExtraction`: each impl
  parses to a raw map then calls the single pure `validate_filter` (so validation is
  single-sourced as a function, invoked by the extractor — the drop contract is testable at the
  seam, not deferred to the orchestrator).
- `BedrockMetadataExtractor` (configurable `modelId=DEFAULT_SYNTHESIS_MODEL_ID`, injectable
  client): build the declared-field schema prompt, JSON-instructed Converse, parse to a raw map,
  `validate_filter`.
- `RuleMetadataExtractor`: keyword (repo names) + `link_question` candidate rules → raw map →
  `validate_filter`; non-semantic.
**Done when:** `test_selfquery.py` extractor tests green; gates clean.

### T5: Orchestration `selfquery_query` + thread vector AND hybrid (AC5)
**Depends on:** T2, T3, T4
**Touches:** packages/graphrag/src/graphrag/selfquery.py, packages/graphrag/src/graphrag/vector.py, packages/graphrag/src/graphrag/hybrid.py, packages/graphrag/tests/test_selfquery.py, packages/graphrag/tests/test_hybrid.py
**Tests:**
- `# STUB: AC5` vector: `vector_search(..., metadata_filter)` excludes non-matching chunks;
  `selfquery_query(..., mode="vector")` on the exemplar selects+validates the filter, returns
  filtered hits, and `.render()` emits question → extracted → validated (+dropped) → hits →
  answer in order.
- `# STUB: AC5` hybrid: `hybrid_query(..., metadata_filter)`/`run_modes` apply the filter to the
  vector leg; the hybrid result's vector-sourced chunks exclude non-matching ones.
- `# STUB: AC5` no-filter: a question with nothing extractable leaves retrieval unfiltered and the
  trace says so.
**Approach:**
- Thread `metadata_filter: MetadataFilter | None` through `vector_search` and the vector retrieval
  inside `hybrid_query`/`run_modes` (mirroring how `clearance` already threads).
- `selfquery_query(question, *, store, extractor, synthesizer, aliases, mode, clearance=None, …)`:
  `extractor.extract(question, aliases=…)` (→ validated `FilterExtraction`) → filtered search →
  `synthesize` → `SelfQueryResult`; `.render()`.
**Done when:** vector + hybrid + no-filter tests green; slice-3/4 invariants still hold; gates clean.

### T6: CLI verb `selfquery-query` (AC6)
**Depends on:** T5
**Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_cli.py
**Tests:**
- `# STUB: AC6`: offline run prints the trace + non-semantic label; `--mode hybrid|vector`
  switches the leg; `--function-url` builds a SigV4 POST whose body carries `mode: "selfquery"`
  (via `_function_url_query(..., mode="selfquery")`); live render path.
**Approach:**
- Add `_cmd_selfquery_query` + parser (`--q`, corpus args, `--mode` default `vector`,
  `--bedrock`, `--function-url`, `--region`, `--persona`); offline default (in-memory store +
  `RuleMetadataExtractor` + offline synthesizer), `--bedrock` ⇒ `BedrockMetadataExtractor` +
  `BedrockClaudeSynthesizer`. Call `_function_url_query(..., mode="selfquery")` — the helper
  already accepts a `mode` and serializes any non-default into the body (`cli.py:176,202`), as
  governed/text2cypher already do; **no edit to the helper** beyond extending its docstring
  mode-list to include `selfquery` (a bundled same-area doc-fix).
**Done when:** `test_cli.py` green; gates clean.

### T7: Query Lambda self-query dispatch (AC7)
**Depends on:** T5
**Touches:** packages/graphrag/src/graphrag/query_lambda.py, packages/graphrag/tests/test_query_lambda.py
**Tests:**
- `# STUB: AC7`: `mode="selfquery"` with mocked extractor/store/synthesizer returns the trace
  envelope (extracted+validated filter, dropped, filtered hits, answer); unknown `mode` ⇒
  client-error envelope; over-long question still rejected; the PyYAML-free `sys.modules` guard
  now also imports `selfquery`.
**Approach:**
- Extend `_extract_mode` handling: on `selfquery` build live OpenSearch store +
  `BedrockMetadataExtractor` (same model) + `BedrockClaudeSynthesizer`, run `selfquery_query`,
  `_serialize_selfquery(result)`; reject unknown mode. **No Neptune graph store** is built on
  this branch (entity validation is pure controlled-vocab resolution — AC2), so the path holds
  the same grants as `hybrid`. Extend the `_extract_mode`/handler docstring mode-list to include
  `selfquery` (a bundled same-area doc-fix).
**Done when:** `test_query_lambda.py` green; gates clean.

### T8: IaC unchanged — engine switch is app-side, no new resource/grant, cost held (AC8)
**Depends on:** T7
**Touches:** apps/infra/tests/test_stack.py
**Tests:**
- `# STUB: AC8`: synth assertion — the self-query path adds **no new resource and no new IAM
  statement** (specifically **no Neptune statement** — entity validation is pure, so the path
  never touches the graph store); the query Lambda's Bedrock grant still scopes the synthesis
  model (`bedrock:Converse`) with no wildcard `Resource`; the Budgets value is the literal `150`.
**Approach:**
- Extend the existing infra test (no stack code change expected; the self-query modules ride the
  existing `Code.from_asset` bundle; the engine switch is in `store/opensearch.py`, not CDK).
  If the existing `test_stack.py` already pins the no-wildcard-Bedrock-grant + Budgets-150
  invariants (the governed/text2cypher slices added them), the self-query addition asserts the
  *delta* — no new statement for this path — rather than re-pinning a held invariant.
**Done when:** CDK-env-gated synth test green; gates clean.

### T9: Self-query showcase set + explanation doc (AC10)
**Depends on:** T1, T5
**Touches:** packages/graphrag/src/graphrag/showcase/queries.yaml, packages/graphrag/src/graphrag/showcase/__init__.py, packages/graphrag/tests/test_showcase.py, docs/guides/explanation/metadata-self-query-filtering.md
**Tests:**
- `# STUB: AC10`: `load_selfquery_showcase()` parses ≥4 entries spanning `vector` and `hybrid`
  modes; each names the expected extracted filter (field/value) and gold visible/excluded chunk
  ids; the test asserts each named entity's normalized id matches ≥1 fixture chunk (a gold-data
  check, **not** part of `validate_filter`, which is corpus-blind per AC2).
**Approach:**
- Add `selfquery_queries` to `queries.yaml` (id, query, mode, expected_filter, visible, excluded,
  highlight) + `SelfQueryShowcaseQuery` + `load_selfquery_showcase()`.
- Write the explanation doc: self-query (question-derived filter, extracted by the LLM, validated
  deterministically, applied during ANN on the Lucene engine) vs. the fixed permission filter;
  note the engine switch closing the post-filter recall caveat; exact `selfquery-query` CLI
  commands for both modes.
**Done when:** `test_showcase.py` green; doc renders; gates clean.

### T10: Live deploy + self-query smoke (AC9) — run-or-defer
**Depends on:** T6, T7, T8
**Tests:**
- Manual/live: deploy, dual-write the corpus on a fresh Lucene-engine index, SigV4 `mode:
  selfquery` call extracts a filter, runs filtered k-NN live on OpenSearch (filter during ANN),
  returns the trace + a Claude answer; a no-filter question runs unfiltered; then `cdk destroy`.
**Approach:**
- If live AWS access (creds, CDK bootstrap, Bedrock model access) is available, run the smoke
  end-to-end and record it in `deployment-and-verification.md`; then tear down.
- **Otherwise defer, atomically:** in the *same* edit, create the `docs/backlog.md` heading
  `### metadata-filtering-live-smoke` (the opencypher-templates precedent) **and** set the spec's
  AC9 checkbox to `- [ ] AC9 … (deferred: metadata-filtering-live-smoke)` — token and target land
  together (CONVENTIONS § 4). The offline + mocked path proves the orchestration.
**Done when:** live smoke recorded **or** AC9 deferred with the backlog heading and token in the
same edit.

### T11: Spec metadata + drift closure (CONVENTIONS § 4) + architecture docs
**Depends on:** T1, T2, T3, T4, T5, T6, T7, T8, T9
*(Not an AC — this task realizes the drift-closure metadata invariants: Status flip, AC checkbox
ticks, the deferral register entry if any, and the architecture-docs update. Finalization, not
scope creep.)*
**Touches:** packages/graphrag/AGENTS.md, docs/architecture/overview.md, docs/architecture/security.md, docs/specs/README.md, docs/specs/metadata-filtering/spec.md, docs/product/briefs/graphrag-pattern-catalog.md, docs/CHARTER.md
**Tests:**
- Goal-based: spec-status / coverage lint clean; the brief Spec-map row + the charter coverage
  table `Metadata Filtering / Self-Query` row reflect the shipped status; AC checkboxes reflect
  reality.
**Approach:**
- Update the `graphrag` AGENTS.md module map (`selfquery`) + invariants (self-query PyYAML-free;
  the knn `metadata_filter` backend-identical predicate; Lucene engine).
- Update `architecture/overview.md` (self-query path) + `security.md` (self-query posture:
  schema-bound extraction, deterministic validation, parameterized terms filter, composes-AND
  with clearance).
- Add the spec to `docs/specs/README.md`; tick met ACs; flip Status. Update the charter coverage
  table row from `◔ Planned` to its shipped glyph (a coverage-status flip is the documented
  exception the table calls out as auto-derived; verify the coverage lint rather than hand-editing
  if a lint owns the cell).  **Verify** any AC9 deferral token (created by T10) resolves to a real
  `docs/backlog.md` heading.
**Done when:** docs consistent; lints clean; every deferral token resolves to a real backlog
heading.

## Rollout

- **Delivery:** additive. The CLI gains a verb; the Function URL gains the `mode: "selfquery"`
  value (absent ⇒ `hybrid`, unchanged); `VectorStore.knn` gains an optional `metadata_filter`
  (default `None`, so existing callers are unchanged). The **engine switch is the one
  non-additive change** — it changes the k-NN index method, so it takes effect on a **fresh
  index** (a clean deploy / teardown-first rebuild); re-deploy over a non-destroyed index without
  a re-create is out of scope (matches the slice-4 mapping-change boundary). Rollback is reverting
  the PR (and re-creating the index on the prior engine).
- **Infrastructure:** none new. The self-query modules ride the existing query Lambda's
  `Code.from_asset` bundle; extraction reuses the granted synthesis-model Converse action and the
  OpenSearch data-access. Budgets unchanged at `150` (AC8).
- **External-system integration:** Bedrock Claude (extraction + synthesis) and OpenSearch k-NN —
  both already wired and granted for the vector/hybrid path.
- **Deployment sequencing:** none — a single PR. The live smoke (AC9/T10) runs against a deploy of
  this branch (fresh index) if AWS is available, else defers.

## Risks

- **Engine switch regresses existing retrieval.** Moving `nmslib`→`lucene` changes the index
  method for all vector search. Mitigation: no test pins the `nmslib` string; the frozen-vector
  eval is engine-independent (in-memory cosine); the blast radius is `_knn_mapping` + the adapter
  `knn` body; existing vector/permission tests must stay green (T3); live re-proven by AC9.
- **Extractor over-trusts the LLM.** Mitigation: the raw map is always run through
  `validate_filter` — an undeclared field/unresolvable value is dropped+recorded, never bound;
  a bad extraction is a visible wrong/empty filter in the trace, never an injected query.
- **PyYAML creeps into the self-query import graph** (breaks the Lambda bundle). Mitigation: the
  extended `sys.modules` guard test (T7).
- **AC9 live access** may be unavailable. Mitigation: the run-or-defer rule (T10) with a backlog
  anchor, consistent with opencypher-templates.

## Changelog

- 2026-06-25: initial plan. Self-query module (`selfquery.py`): fixed `source`/`entity_ids`
  field schema + `MetadataFilter` + deterministic `validate_filter`; Bedrock/rule extractor
  seam (validated output); k-NN engine switch `nmslib`→`lucene` so the composed self-query +
  visibility filter applies during ANN (RFC-0001 §4); threaded through vector + hybrid's vector
  leg; additive Function-URL `mode: selfquery`; no new infra/IAM; AC9 run-or-defer.
