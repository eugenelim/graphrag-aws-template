# Plan: parent-child-retrieval

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Parent-child retrieval reuses the chunk pipeline and the embed/synthesis seams the
vector + permission slices built; the only genuinely new mechanism is a **nested
`knn_vector` index** and the orchestration that returns the parent body for synthesis.
The shape: a new **nested OpenSearch index** (`graphrag-parents`) whose documents are
*parents* (a corpus document) holding their *children* (the existing `chunk_corpus`
chunks) as a `nested` array, each child with its own `knn_vector`, plus the full parent
prose in an app-stored `body` field. A nested k-NN query matches a child vector **during**
the ANN scan (Lucene HNSW — the engine the flat index already uses, RFC-0001 §3/§4),
scores the parent by its best child (`score_mode: max`), and returns the parent document;
`inner_hits` surfaces *which* child matched, for the trace. A new **`ParentChildStore`**
seam (ABC + `OpenSearchParentChildStore` for live, `MemoryParentChildStore` for
CI/offline, backend-identical) owns the index + query. A pure **`group_into_parents`**
function turns embedded chunks + a parent-body map into `ParentDoc`s. An orchestrator
(`parentchild_query`) wires embed → nested search → synthesis **over the parent body** into
a `ParentChildResult` whose `.render()` is the trace. A CLI verb (`parentchild-query`) and
an additive `mode: "parentchild"` branch on the existing query Lambda expose it offline and
live. It is **additive**: the flat `graphrag-chunks` index and the four existing modes are
untouched, so the demo runs the same question through flat `vector` and `parentchild` and
shows the context difference.

The riskiest parts are (1) the **nested query shape** — getting the `nested`/`knn`/
`inner_hits`/`score_mode` body right so the parent is the returned unit (no `has_child`
join, no duplicate-parent dedup); mitigated by pinning the body in the adapter test against
a mock HTTP client and proving the in-memory backend returns the identical parent hit set;
and (2) the **embed-once invariant** — the parent-child index must reuse the chunk vectors
the flat dual-write already computed, never embed twice; mitigated by refactoring the
full-ingest dual-write to embed once and write both stores, asserted by a counting embedder.
Keeping the parent-child import graph **PyYAML-free** (so it bundles in the `Code.from_asset`
Lambda) is held by extending the existing `sys.modules` guard test. **No new infra or IAM** —
the nested index lands on the existing domain; the path reuses the granted Titan embed +
synthesis Converse + OpenSearch data-access and builds **no** Neptune store (AC7).

## Constraints

- **Charter coverage table** (*Parent-Child Retriever* row) — this slice ships that row;
  small child chunks for precise matching → the larger parent document body for
  context-complete synthesis.
- **RFC-0001 feasibility §3** — nested `knn_vector` child vectors that match while the parent
  doc is scored/returned is VERIFIED; the load-bearing caveat is that it is **nested-doc, not
  an Elasticsearch `has_child` cross-doc join** — the app stores/fetches the parent body. The
  parent document is the returned unit, so no duplicate-parent dedup is needed.
- **RFC-0001 feasibility §4** — the index runs on the **Lucene HNSW** engine (the metadata
  slice switched it there); the nested `knn` filter clauses apply **during** ANN.
- **ADR-0001** — reuse the Titan embedder + the `Synthesizer` seam + the retrieval-trace
  posture; parent-child is a vector-only mode (no graph store).
- **ADR-0002** — ride the existing in-VPC query Lambda + IAM-auth Function URL; the nested
  index lands on the existing OpenSearch domain at `create_index` (teardown-first); add no
  billable resource; Budgets unchanged at `150`.
- **ADR-0003** — IaC stays AWS CDK Python.
- **`packages/graphrag/AGENTS.md`** — runtime deps stay `pyyaml` + `boto3>=1.35`; the query
  import graph stays PyYAML-free; the nested filter is request-body DSL with the in-memory
  backend applying the identical predicate (backend-identical hit set).

## Construction tests

Most tests live under their task. Cross-cutting:

**Integration tests:**
- Offline end-to-end (`parentchild_query` over the fixture corpus with the in-memory nested
  store + `HashEmbedder` + offline synthesizer) on an exemplar question — asserts the matched
  child belongs to the expected parent, the synthesizer is called with the **parent body** (not
  the child text), and `.render()` emits question → matched child → returned parent (full body)
  → answer in order (T3).
- Backend-identical nested search: for a given query + clearance, the in-memory `search` and the
  OpenSearch adapter `search` (mock HTTP client) produce the **same sorted** parent hit set, and
  the OpenSearch body carries the nested `knn` over `children.vector` + `score_mode: max` +
  `inner_hits` + the composed visibility `bool.filter` (T2).
- PyYAML-free import-graph guard: blocks `import yaml`, then imports `parentchild` +
  `store.parentchild_opensearch` (extends `test_query_lambda.py`) (T6).

**Manual verification:** AC9 live deploy + parent-child smoke (run if live AWS is available;
otherwise deferred — see Rollout).

## Design (LLD)

Stack: Python 3.11+, `boto3` `bedrock-runtime` Converse (synthesis) + Titan embed,
`botocore` SigV4 to OpenSearch over signed HTTPS and to the Function URL, AWS CDK Python.
Conforms to the existing `packages/graphrag` module stereotypes (a pure logic module + an
injectable store seam with an in-memory + an OpenSearch backend + an orchestrator + a CLI
verb), mirroring `selfquery.py`/`vector.py`/`store/vector_*`.

### Design decisions
- **Additive new nested index, not a replacement of the flat index.** Parent-child is a new
  retrieval mode on a new `graphrag-parents` index; the flat `graphrag-chunks` index and the
  vector/hybrid/self-query/permission modes are untouched. *Rejected:* replacing the flat index
  with the nested one — far larger blast radius across four shipped slices, and it would lose the
  side-by-side teaching contrast (flat `vector` vs `parentchild` on the same question). Traces to:
  AC2, AC8, AC9.
- **Parent = document, child = the existing section/window chunk.** Children are sized for match
  precision (one `chunk_corpus` chunk = one vector); the parent is the whole document, returned in
  full for synthesis context. *Rejected:* a section-level parent (smaller context, less of the
  pattern's payoff) or a sliding-window parent (arbitrary boundaries). Traces to: AC1.
- **One nested document; the parent body is app-stored and read back.** The parent body is a
  top-level field on the same document that holds the nested children — not an Elasticsearch
  `has_child` cross-doc join (RFC-0001 §3 caveat). The parent *is* the returned unit, so a parent
  with several matching children still returns once (scored by its best child via `score_mode:
  max`), no dedup needed. *Rejected:* indexing children flat + a second fetch for the parent body
  (an extra round-trip and a join the verified mechanism avoids). Traces to: AC2, AC3.
- **Reuse the chunk embeddings — embed each child once.** The full-ingest dual-write embeds the
  chunks once and writes **both** the flat index and the parent-child index from the same vectors.
  *Rejected:* a second embed pass for the parent-child index (extra Bedrock cost + a drift risk
  between the two indexes' vectors). Traces to: AC4.
- **Synthesize over the parent body via the existing `Synthesizer` seam.** Each returned parent
  body is wrapped as the `VectorHit`/`Chunk` shape `synthesize` already reads, so Claude sees the
  full parent context with no synthesizer change. *Rejected:* a new synthesis entry point. Traces
  to: AC3, AC6.
- **Live path rides the existing Function URL via an additive `mode` value.** Back-compat (absent
  ⇒ `hybrid`); no new endpoint/IAM; no Neptune store on the branch (vector-only). Traces to: AC5,
  AC6, AC7.

### Data & schema
- `ChildVector(child_id: str, heading: str, text: str, vector: list[float])` — a child chunk + its
  embedding (the nested sub-document).
- `ParentDoc(parent_id: str, source: str, doc_path: str, heading: str, entity_ids: tuple[str, ...],
  visibility: str, body: str, children: tuple[ChildVector, ...])` — `parent_id` is the
  source-qualified `{source}/{doc_path}`; `body` is the document's full prose (app-stored);
  `visibility` is the document's single composed tier.
- `ParentHit(parent: ParentDoc, score: float, matched_child: ChildVector | None)` — the unit
  returned by `search`; `matched_child` is the `inner_hits` top child (the precise match), `score`
  is the parent's best-child score.
- `ParentChildResult(question, hits: list[ParentHit], answer: str, citations: list[str], clearance:
  Clearance | None)`.
- The new nested OpenSearch index `graphrag-parents`: `parent_id`/`source`/`doc_path`/`entity_ids`/
  `visibility` (keyword), `heading`/`body` (text), `children` (`type: nested`) with `child_id`/
  `heading` (keyword/text), `text` (text), `vector` (`knn_vector`, Lucene HNSW, `cosinesimil`, dim
  256 — same method block as the flat `_knn_mapping`). Traces to: AC1, AC2.

### Interfaces & contracts
- Internal Python seams only (no repo-root `contracts/`). New `ParentChildStore` ABC:
  `create_index()`, `index_parent(parent: ParentDoc)`, `search(vector, k, *, allowed_labels:
  frozenset[str] | None = None) -> list[ParentHit]`, `count()`, `clear()`. The Function URL request
  gains the `mode: "parentchild"` value (additive); the parent-child response envelope is
  `{hits (parent ids), matched_children, answer, citations, trace}` (no `mode` field — parent-child has a
  single retrieval shape, unlike self-query's vector/hybrid; consistent with the sibling serializers).
  Traces to: AC2, AC5, AC6.

### Component / module decomposition
- New: `store/parentchild_base.py` (`ChildVector`, `ParentDoc`, `ParentHit`, `ParentChildStore`
  ABC), `store/parentchild_opensearch.py` (`OpenSearchParentChildStore` — reuses the
  `_UrllibClient`/`HttpResponse`/`HttpClient` types + `OPENSEARCH_SERVICE` from `store.opensearch`;
  a thin SigV4 `_request` mirrors the flat adapter — ~15 lines — with a shared signer helper as the
  DRY alternative if review prefers it), `store/parentchild_memory.py` (`MemoryParentChildStore`),
  `parentchild.py` (`group_into_parents` + `parentchild_query` + `ParentChildResult`).
- Reused: `chunk.chunk_corpus` + the chunk `{source}/{doc_path}` key + `entity_ids`/`visibility`,
  `embed.HashEmbedder`/`BedrockTitanEmbedder`, `synthesize.*`, `visibility.Clearance`.
- Modified: `apps/ingestion/entrypoint.py` (`_vector_dual_write` embeds once, writes both stores;
  `run`/injection gain the parent-child store), `cli.py` (verb), `query_lambda.py` (mode dispatch +
  `_serialize_parentchild`). Traces to: AC1–AC7.

### State & control flow
`parentchild_query`: `embedder.embed([question])[0]` → `store.search(vector, k,
allowed_labels=clearance.allowed if clearance else None)` → for each `ParentHit`, wrap the parent
`body` as a `VectorHit` context → `synthesizer.synthesize(question, context, [])` → `ParentChildResult`.
`render()` order: question → matched child(ren) (id, parent, score) → returned parent(s) (id, doc_path,
body length/preview) → answer. Traces to: AC3.

### Behavior & rules
- Grouping: chunks group by `chunk.id.rsplit("#", 1)[0]` == `{source}/{doc_path}`; children ordered
  by ordinal; parent `entity_ids`/`visibility` taken from the group's chunks (identical across a
  document's chunks); parent `heading` = the first (ordinal-0) child's heading (a stable parent
  label); parent `body` from the `bodies` map (the document's full prose) — a parent key **absent**
  from `bodies` raises `ValueError` (fail-loud; never a silent empty body).
- Nested search composition: top-level `bool` with `must: [{nested: {path: "children", query: {knn:
  {"children.vector": {vector, k}}}, score_mode: "max", inner_hits: {...}}}]` and, when a clearance
  is applied, `filter: [{terms: {visibility: sorted(allowed)}}]`. `allowed_labels=None` ⇒ no
  visibility clause (unrestricted); an **empty** set ⇒ a `terms` clause matching nothing
  (fail-closed). Vector/`k`/filter values ride the body, never interpolated.
- In-memory `search`: per parent, the best child cosine = the parent score; filter by `visibility ∈
  allowed` (same `None`-vs-empty semantics); sort score-descending; top-`k`. Traces to: AC2.

### Failure, edge cases & resilience
- No hits (empty index / over-narrow clearance) ⇒ empty `hits`, the trace says "(no hits)", a
  graceful "no context" answer — not an error.
- A parent with no children never indexed (a doc with no prose body yields no chunks — `chunk_corpus`
  already skips it).
- Lambda: over-long question rejected pre-orchestration; any failure ⇒ generic sanitized envelope +
  correlation id; unknown `mode` ⇒ client error; unknown persona ⇒ client error. Traces to: AC3, AC6.

### Quality attributes (NFRs / security)
- Parameterization: every value in the request body (no interpolation) — pinned by the AC2 adapter
  test. `ruff` `S` stays enabled.
- Untrusted-input at the Claude boundary (synthesis): question + parent bodies as Converse `messages`
  data, defensive system directive, bounded `maxTokens`, default-TLS client, display-only answer
  (reuse `synthesize.py` posture). Traces to: AC3, AC6.
- Parent-child composes `AND` with clearance and only narrows — never widens past visibility. Traces
  to: AC2, AC3.
- PyYAML-free parent-child import graph (Lambda bundle). Traces to: AC6.

### Dependencies & integration
No new runtime dependency (nested search is request-body DSL over the existing SigV4/HTTPS plumbing;
Titan embed + Converse via existing `bedrock-runtime`; SigV4 via `botocore`). No new infra resource;
the nested index rides the existing domain; reuse the query Lambda's existing grants. Traces to: AC7.

## Tasks

### T1: Parent-child data model + `group_into_parents` (AC1)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/store/parentchild_base.py, packages/graphrag/src/graphrag/parentchild.py, packages/graphrag/tests/test_parentchild.py
**Tests:**
- `# STUB: AC1`: `group_into_parents(embedded_chunks, bodies)` groups chunks of one document into a
  single `ParentDoc` keyed by `{source}/{doc_path}`; children ordered by ordinal; parent body taken
  from `bodies`; parent `heading` == the ordinal-0 child's heading; `entity_ids`/`visibility`
  inherited from the group; two documents → two parents; a `{source}/{doc_path}` collision across the
  two sources stays distinct; a parent key **missing** from `bodies` raises `ValueError`;
  `import graphrag.parentchild` and `import graphrag.store.parentchild_base` pull in no `yaml`.
**Approach:**
- Define `ChildVector`, `ParentDoc`, `ParentHit`, and the `ParentChildStore` ABC in
  `store/parentchild_base.py` (pure dataclasses + abstract methods). Define `group_into_parents` in
  `parentchild.py` (pure; parent key = `chunk.id.rsplit("#", 1)[0]`). No yaml import.
**Done when:** `test_parentchild.py` model + grouping tests green; `ruff`/`mypy` clean.

### T2: Nested `knn_vector` index + `ParentChildStore` backends (AC2)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/store/parentchild_opensearch.py, packages/graphrag/src/graphrag/store/parentchild_memory.py, packages/graphrag/tests/test_store_parentchild.py
**Tests:**
- `# STUB: AC2` mapping: `_parentchild_mapping(dim)["mappings"]["properties"]["children"]["type"]
  == "nested"` and `children.properties.vector.method` has `engine == "lucene"`, `name == "hnsw"`,
  a Lucene-supported `space_type` (`cosinesimil`); `body`/`source`/`visibility` present.
- `# STUB: AC2` query body: OpenSearch `search(vector, k, allowed_labels=…)` issues a single `bool`
  whose `must` is a `nested` query over `children.vector` (a `knn` clause) with `score_mode: "max"`
  + `inner_hits`, a `filter:` carrying the visibility `terms` (asserted via the mock HTTP client;
  values in the body), and `_source` excludes `children.vector`.
- `# STUB: AC2` parse hit: a mock nested response → `ParentHit`s carrying the parent body + the
  `inner_hits` matched child.
- `# STUB: AC2` backend-identical: on the fixture-sized corpus (HNSW ANN ≈ exact cosine) the
  in-memory `search` returns the **same set of parent ids and the same matched child per parent** as
  the OpenSearch adapter (set + matched-child equality, not a float-sort-order guarantee across an
  approximate vs. exact scorer); visibility `None` ⇒ unrestricted, empty set ⇒ zero hits
  (fail-closed) on both backends.
**Approach:**
- `OpenSearchParentChildStore` (index `graphrag-parents`, dim default 256): `create_index` PUTs
  `_parentchild_mapping`; `index_parent` POSTs the parent doc (children as the nested array, body
  top-level); `search` builds the nested `bool` body and parses `_source` + `inner_hits`. Reuse
  `_UrllibClient`/`HttpResponse`/`HttpClient`/`OPENSEARCH_SERVICE` from `store.opensearch`; thin
  SigV4 `_request`.
- `MemoryParentChildStore`: dict of `ParentDoc`s; `search` = per-parent best-child cosine + the
  visibility predicate; sorted top-`k`.
**Done when:** mapping + query-body + parse + backend-identical tests green; gates clean.

### T3: Orchestration `parentchild_query` + trace (AC3)
**Depends on:** T1, T2
**Touches:** packages/graphrag/src/graphrag/parentchild.py, packages/graphrag/tests/test_parentchild.py
**Tests:**
- `# STUB: AC3`: `parentchild_query` over the fixture matches a child, returns its parent; the
  synthesizer is called with the **parent body** as the context chunk text (not the matched child
  text) — asserted via a spy synthesizer; `.render()` emits question → matched child → returned
  parent (full body) → answer in order.
- `# STUB: AC3` clearance: with a `clearance` excluding a doc's tier, that parent is absent; an
  empty `Clearance.allowed` ⇒ zero hits (fail-closed survives the orchestrator).
**Approach:**
- `parentchild_query(question, *, store, embedder, synthesizer, k=DEFAULT_K, clearance=None)`: embed
  → `store.search(vector, k, allowed_labels=clearance.allowed if clearance else None)` → wrap each
  returned parent as the `VectorHit`/`Chunk` shape `synthesize` reads — a synthetic `Chunk(id=parent_id,
  text=parent.body, source, doc_path, heading=parent.heading, entity_ids, visibility)` paired with the
  parent score — so the synthesizer reads the **full parent body** and the citation surface
  (`{source}:{doc_path}#{heading}`) resolves to the **parent**, not a child chunk → `ParentChildResult`.
  `.render()`.
**Done when:** orchestration + clearance + narratability tests green; gates clean.

### T4: Ingest dual-writes the parent-child index from one embed pass (AC4)
**Depends on:** T1, T2
**Touches:** apps/ingestion/entrypoint.py, apps/ingestion/tests/test_entrypoint.py
**Tests:**
- `# STUB: AC4`: the full-ingest dual-write, given an injected `MemoryVectorStore` +
  `MemoryParentChildStore` + a **counting** embedder, writes both stores and the embedder is invoked
  for the chunk texts **exactly once** (no second embed pass); the parent-child store holds one
  parent per document, each with the document's full body, ordered children (with vectors),
  `entity_ids`, and `visibility`.
- `# STUB: AC4`: absent both an injected parent-child store and `OPENSEARCH_ENDPOINT`, the
  parent-child write is a no-op (the flat-only / graph-only deploys are unchanged).
- `# STUB: AC4`: every grouped parent resolves a **non-empty** body from the `bodies` map — the
  `{source}/{path}`-from-`ParsedDoc` key is byte-identical to `group_into_parents`'s
  `chunk.id.rsplit("#",1)[0]` key (`chunk.doc_path == doc.path`), and a mismatch surfaces as the T1
  `ValueError`, never a silent empty body.
**Approach:**
- **This is a behavior-touching refactor of the shipped `_vector_dual_write` path, not a pure
  addition** (the one non-additive change in the slice — see Rollout): refactor it to embed the chunk
  texts **once** into a held `list[EmbeddedChunk]`, write the flat store from that list (behavior
  preserved for the flat write), then — when a parent-child store is injected or `OPENSEARCH_ENDPOINT`
  is set — build the `bodies` map from the parsed docs (`{source}/{path}` → `markdown.body`),
  `group_into_parents(embedded, bodies)`, and `index_parent` each. Thread an optional
  `parentchild_store` through `run`. Scope to MODE=full (delta/rebuild parent-child sync is out of
  scope — see Rollout).
- The counting/spy embedder asserts a **single** `embed(...)` call over the **exact** chunk-text list
  both stores consume — the only guard against a future edit re-embedding for the second index.
**Done when:** `test_entrypoint.py` dual-write tests green (single-embed over the shared chunk list
asserted; the existing flat-write behavior still green); gates clean.

### T5: CLI verb `parentchild-query` (AC5)
**Depends on:** T3
**Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_cli.py
**Tests:**
- `# STUB: AC5`: offline run builds the in-memory nested store from the fixture corpus + `HashEmbedder`
  + offline synthesizer, prints the trace + the non-semantic label; `--function-url` builds a SigV4
  POST whose body carries `mode: "parentchild"` (via `_function_url_query(..., mode="parentchild")`)
  and renders the returned trace; `--persona` rides the body.
**Approach:**
- Add `_cmd_parentchild_query` + parser (`--q`, corpus args, `--k`, `--bedrock`, `--function-url`,
  `--region`, `--persona`); offline default (in-memory nested store indexed from the fixture corpus +
  `HashEmbedder` + offline synthesizer), `--bedrock` ⇒ `BedrockTitanEmbedder` + `BedrockClaudeSynthesizer`.
  Reuse `_function_url_query(..., mode="parentchild")` (extend its docstring mode-list — a bundled
  same-area doc-fix). Add an offline-indexing helper for the nested store mirroring `_index_corpus`.
**Done when:** `test_cli.py` green; gates clean.

### T6: Query Lambda parent-child dispatch (AC6)
**Depends on:** T3
**Touches:** packages/graphrag/src/graphrag/query_lambda.py, packages/graphrag/tests/test_query_lambda.py
**Tests:**
- `# STUB: AC6`: `mode="parentchild"` with mocked store/embedder/synthesizer returns the trace
  envelope (matched child, parent ids, answer, citations, trace); unknown `mode` ⇒ client-error
  envelope; over-long question still rejected; unknown persona ⇒ client error; the PyYAML-free
  `sys.modules` guard now also imports `parentchild` + `store.parentchild_opensearch`; **no Neptune
  store** is constructed on this branch.
**Approach:**
- Extend `_extract_mode` docstring + the dispatch: on `parentchild` build `OpenSearchParentChildStore`
  + `BedrockTitanEmbedder` + `BedrockClaudeSynthesizer` (resolve the optional persona/clearance
  fail-closed, like selfquery), run `parentchild_query`, `_serialize_parentchild(result)`. No Neptune
  store on this branch.
**Done when:** `test_query_lambda.py` green; gates clean.

### T7: IaC unchanged — new index app-side, no new resource/grant, cost held (AC7)
**Depends on:** T6
**Touches:** apps/infra/tests/test_stack.py
**Tests:**
- `# STUB: AC7`: synth assertion — the parent-child path adds **no new resource and no new IAM
  statement** (specifically **no Neptune statement** — parent-child never touches the graph store);
  the query Lambda's Bedrock grant still scopes the synthesis model (`bedrock:Converse`) with no
  wildcard `Resource`; the Budgets value is the literal `150`.
**Approach:**
- Extend the existing infra test (no stack code change expected; the parent-child modules ride the
  existing `Code.from_asset` bundle; the nested index is created in `store/parentchild_opensearch.py`,
  not CDK). If `test_stack.py` already pins the no-wildcard-Bedrock-grant + Budgets-150 invariants,
  assert the *delta* — no new statement for this path.
**Done when:** CDK-env-gated synth test green; gates clean.

### T8: Parent-child showcase set + explanation doc (AC8)
**Depends on:** T1, T3
**Touches:** packages/graphrag/src/graphrag/showcase/queries.yaml, packages/graphrag/src/graphrag/showcase/__init__.py, packages/graphrag/tests/test_showcase.py, docs/guides/explanation/parent-child-retrieval.md
**Tests:**
- `# STUB: AC8`: `load_parentchild_showcase()` parses ≥3 entries; each names the expected matched
  child, the returned parent (a fixture doc), and the contrast vs flat `vector` mode; the test
  asserts each named parent doc / entity resolves in the fixture corpus.
**Approach:**
- Add `parentchild_queries` to `queries.yaml` (id, query, expected_matched_child, expected_parent,
  contrast, highlight) + `ParentChildShowcaseQuery` + `load_parentchild_showcase()`.
- Write the explanation doc: child sized for match precision, parent for synthesis context; the match
  runs during the nested ANN on Lucene; the parent body is one nested document's app-stored field, not
  a `has_child` join; exact `parentchild-query` CLI commands + the flat-`vector` contrast.
**Done when:** `test_showcase.py` green; doc renders; gates clean.

### T9: Live deploy + parent-child smoke (AC9) — run-or-defer
**Depends on:** T5, T6, T7
**Tests:**
- Manual/live: deploy, dual-write the corpus (nested index populated), a SigV4 `mode: parentchild`
  call matches a child live (nested ANN on Lucene), returns the trace (matched child + parent body) +
  a Claude answer from the parent body; a flat `mode: vector` call on the **same question** shows the
  smaller chunk context; a third call pairs `mode: parentchild` with a non-default `persona` and
  asserts the live parent hits exclude above-clearance docs (the visibility `bool.filter` composes
  AND with the child match, narrow-only);
  then `apps/infra/scripts/destroy.sh`.
**Approach:**
- If live AWS access is available, run the smoke end-to-end via `apps/infra/scripts/deploy.sh` →
  ingest → the three Function-URL calls, record it in `deployment-and-verification.md`, then tear
  down.
- **Otherwise defer, atomically:** in the *same* edit, create the `docs/backlog.md` heading
  `### parent-child-retrieval-live-smoke` (the metadata-filtering precedent) **and** set the spec's
  AC9 checkbox to `- [ ] AC9 … (deferred: parent-child-retrieval-live-smoke)` — token and target
  land together (CONVENTIONS § 4). The offline + mocked path proves the orchestration.
**Done when:** live smoke recorded **or** AC9 deferred with the backlog heading and token in the
same edit.

### T10: Spec metadata + drift closure (CONVENTIONS § 4) + architecture docs
**Depends on:** T1, T2, T3, T4, T5, T6, T7, T8
*(Not an AC — this task realizes the drift-closure metadata invariants: Status flip, AC checkbox
ticks, the deferral register entry if any, and the architecture-docs update. Finalization, not
scope creep.)*
**Touches:** packages/graphrag/AGENTS.md, docs/architecture/overview.md, docs/architecture/security.md, docs/specs/README.md, docs/specs/parent-child-retrieval/spec.md, docs/product/briefs/graphrag-pattern-catalog.md, docs/CHARTER.md
**Tests:**
- Goal-based: spec-status / coverage lint clean; the brief Spec-map row + the charter coverage table
  `Parent-Child Retriever` row reflect the shipped status; AC checkboxes reflect reality.
**Approach:**
- Update the `graphrag` AGENTS.md module map (`parentchild` + `store/parentchild_*`) + invariants
  (parent-child PyYAML-free; the nested-store backend-identical predicate; embed-once dual-write).
- Update `architecture/overview.md` (parent-child path) + `security.md` (parent-child posture:
  vector-only, parameterized nested query, composes-AND with clearance, no Neptune grant).
- Add the spec to `docs/specs/README.md`; tick met ACs; flip Status. The brief Spec-map row
  (`graphrag-pattern-catalog.md`) and the charter coverage table `Parent-Child Retriever` row are
  **auto-derived from the spec's `Status:`** — run the coverage/spec-status lint to re-derive them
  rather than hand-editing (the brief row's HTML comment marks it AUTO-DERIVED). **Verify** any AC9
  deferral token resolves to a real `docs/backlog.md` heading.
**Done when:** docs consistent; lints clean; every deferral token resolves to a real backlog heading.

## Rollout

- **Delivery:** additive at the seams (the CLI gains a verb; the Function URL gains the
  `mode: "parentchild"` value, absent ⇒ `hybrid`, unchanged; a new nested index `graphrag-parents` is
  created on the existing domain; no change to the flat index or the four existing modes) with **one
  internal, behavior-preserving refactor**: the full-ingest `_vector_dual_write` is restructured to
  embed once and write both indexes from the shared `EmbeddedChunk` list (the flat write's behavior is
  unchanged; the embed-once spy test in T4 is the regression guard). Rollback is reverting the PR (and
  dropping the nested index).
- **Infrastructure:** none new. The parent-child modules ride the existing query Lambda's
  `Code.from_asset` bundle; the nested index is on the existing OpenSearch domain; the path reuses the
  granted Titan embed + synthesis Converse + OpenSearch data-access. Budgets unchanged at `150` (AC7).
- **External-system integration:** Bedrock Titan (embed) + Claude (synthesis) and OpenSearch nested
  k-NN — all already wired and granted for the vector path.
- **Deployment sequencing:** none — a single PR. The nested index is populated by the full-ingest
  dual-write on a deploy of this branch. **Incremental delta re-ingest of the parent-child index is
  out of scope** — the flat index handles delta (slice 5); the parent-child index is (re)built on
  full ingest / `--rebuild`. Keeping a second index consistent under delta is a named future
  extension. The live smoke (AC9/T9) runs against a deploy of this branch if AWS is available, else
  defers.

## Risks

- **Nested query body wrong (returns children instead of parents, or duplicates).** Mitigation: pin
  the `nested`/`knn`/`score_mode: max`/`inner_hits` body in the adapter test against a mock HTTP
  client; prove the in-memory backend returns the identical parent hit set; the parent is the
  returned unit so no dedup is needed (T2); live re-proven by AC9.
- **Embed-twice regression.** Refactoring the dual-write could re-embed for the parent-child index.
  Mitigation: a counting/spy embedder asserts a single embed pass; both stores consume the same
  `EmbeddedChunk`s (T4).
- **PyYAML creeps into the parent-child import graph** (breaks the Lambda bundle). Mitigation: the
  extended `sys.modules` guard test (T6).
- **Two indexes drift under delta.** The parent-child index isn't maintained by delta re-ingest.
  Mitigation: scoped out explicitly (full-ingest / `--rebuild` only), named as a future extension in
  Rollout + the spec Assumptions — not silently divergent.
- **AC9 live access** may be unavailable. Mitigation: the run-or-defer rule (T9) with a backlog
  anchor, consistent with metadata-filtering.

## Changelog

- 2026-06-26: shipped. T1–T8 implemented offline (gates green); T9 AC9 verified live (deployed,
  dual-wrote incl. the new nested index — parent-child 6 parents from one embed pass, ran live
  `mode: parentchild` calls proving precise child match → parent body + the clearance compose, then
  destroyed); T10 drift closure (Status→Shipped, ACs ticked, charter coverage `◔ Planned`→`✅ Have`,
  brief row, AGENTS/architecture docs). Review-finding tweaks: AC2 offline-parity wording, response
  envelope `mode` drop, best-child `-inf` seed, trace label; `k`-clamp hardening deferred to backlog.
- 2026-06-25: initial plan. Parent-Child Retriever: a new nested `knn_vector` index
  (`graphrag-parents`) whose parents hold their children + an app-stored body; a `ParentChildStore`
  seam (OpenSearch nested + in-memory, backend-identical); `group_into_parents` + `parentchild_query`
  (synthesizes over the parent body); additive CLI verb + Function-URL `mode: parentchild`; the
  full-ingest dual-write embeds once and writes both indexes; no new infra/IAM; AC9 run-or-defer.
