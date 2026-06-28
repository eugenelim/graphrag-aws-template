# Plan: medallion-staging

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Land the change bottom-up so each task is independently testable offline before the
live wiring. First widen state (`IngestState` v2) and lift the graph mutation into a
pure `GraphDelta` — both are self-contained refactors with characterization tests
against today's behavior. Then add the `ArtifactStore` seam and the
content+config-addressed `materialize_silver` cache (the heart of the change, fully
offline-testable with `HashEmbedder`/`RuleTripleExtractor` and a Bedrock spy).
Finally wire the staged driver (`ingest_staged`) into `apps/ingestion/entrypoint.py`
and add the key-scoped Silver IAM grant in CDK, then run the live acceptance criteria
end-to-end. The riskiest part is the `GraphDelta` equivalence (it must reproduce
`_reconcile_graph` exactly) — pinned by a characterization test before the lift.

## Constraints

- **RFC-0003** — Bronze/Silver/Gold decoupling; D4 chose the single in-process driver
  (no orchestration); query-path is a non-goal.
- **ADR-0007** — Silver key = `content_hash ⊕ config_fingerprint`; grounding stays in
  Gold; artifacts are disposable.
- **ADR-0002** — teardown-first, single ephemeral Fargate task, no standing billable
  resource; Silver lives in the auto-emptied corpus bucket.
- **ADR-0006** — schema-guided extraction is additive, default-off, full/rebuild-only;
  Silver caches its *candidate* extraction only.

## Construction tests

**Integration tests:** `ingest_staged` over in-memory `GraphStore`/`VectorStore`: a
delta re-ingest reaches the same end state as a full rebuild while recomputing only
the changed set (spans T1–T4a).
**Manual verification:** the live ACs in T5 (deployed task), run at implementation.

## Design (LLD)

`Shape: mixed`. Stack: Python 3.11; `packages/graphrag/src/graphrag` (pure library,
offline seams) + `apps/ingestion/entrypoint.py` (Fargate driver) + `apps/infra`
(CDK Python). No reference architecture file present; stack detected from
`pyproject.toml` and the touched modules.

### Design decisions
- Silver split into two artifacts keyed independently (chunks by *embedder* fp,
  candidates by *extraction* fp) so an embedder change doesn't invalidate triples and
  vice-versa. Alternative — one combined key — rejected: over-invalidates. Traces to: AC2 · n/a.
- Silver caches **only Bedrock-expensive per-doc outputs** — chunks+vectors and ungrounded
  LLM candidates. Deterministic `extract()`/`resolve()` is **not** cached: it makes no Bedrock
  call and `resolve()` re-derives it in Gold (`resolve.py:33`), so a cached copy would be dead
  or divergent data. Traces to: AC1, AC5 · n/a.
- Grounding deferred to Gold (Silver holds ungrounded candidates). Alternative —
  ground per-doc in Silver — rejected: grounding needs the global graph (`schema_extract.py:101`). Traces to: AC1, AC8 · n/a.

### Data & schema
- `IngestState{version:2, docs:{id:DocState}, fingerprints, ingested_commit}`;
  `DocState{content_hash, silver_chunks?, silver_candidates?, stage}`. v1 envelope
  (`{version:1, docs:{id:hash}}`) upgrades in. Traces to: AC4 · n/a.
- `SilverArtifact{doc_id, chunks:[(Chunk,vector)], candidates:[CandidateTriple]}` — `candidates`
  holds **only** ungrounded schema-guided LLM triples; deterministic nodes/edges are **not**
  cached (Gold re-derives them via `resolve()`). Serialized to
  `silver/{fp}/{content_hash}/{chunks,candidates}.json`. Traces to: AC1, AC2, AC3 · n/a.
- `GraphDelta{upsert_nodes, upsert_edges, delete_nodes, delete_edges}`. Traces to: AC5 · n/a.

### Interfaces & contracts
- `ArtifactStore` Protocol (`has/load_silver/write_silver`) extending the existing
  `S3Client` Protocol pattern (`entrypoint.py:59`); an in-memory impl for tests, an
  S3 impl for the task. Traces to: AC1, AC7 · n/a.
- `Embedder.fingerprint()` (from `model_id`+`dimensions`) and a new
  `schema_fingerprint(EXTRACTION_SCHEMA)`. Traces to: AC2 · n/a.
- `ground_candidates(candidates, graph, schema, aliases)` carved from `extract_schema_guided`
  (`schema_extract.py:101`) so Gold grounds Silver's *cached* candidates with zero Bedrock.
  Traces to: AC1 · n/a.

### Failure, edge cases & resilience
- Partial Silver write: content-addressed keys + write-then-readable; a fingerprint
  bump sidesteps a poisoned generation. Traces to: AC1 · n/a.
- Re-index idempotency: delete-by-doc before re-index (today's `ingest_delta`
  behavior preserved). Traces to: AC8 · n/a.
- A raising extractor leaves the deterministic graph intact (ADR-0006 additive
  resilience preserved). Traces to: AC1 · n/a.

### Dependencies & integration
- S3 `ArtifactStore` rides the task's existing boto3 `S3Client`; CDK adds one
  key-scoped `grant_put(SILVER_PREFIX)` beside the manifest/trace grants
  (`graphrag_stack.py:355-359`). Traces to: AC6, AC7 · n/a.

## Tasks

### T1: `IngestState` v2 round-trips and upgrades from v1

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/state.py, packages/graphrag/tests/test_state.py

**Tests:**
- JSON round-trip is identity for a v2 state (TDD). [AC4]
- A v1 envelope (`{version:1,docs:{id:hash}}`) parses to v2 with each doc
  `stage=bronze`, Silver keys `None` (TDD). [AC4]
- `as_manifest()` reproduces the exact v1 `{id:hash}` dict (TDD). [AC4]
- `diff_manifests(prev.as_manifest(), new.as_manifest())` classifies a move correctly —
  the projection is exercised **through** the diff, not just dict-compared (TDD). [AC3, AC4]

**Approach:**
- Add `state.py` with `Stage`, `DocState`, `IngestState` (dataclasses) + `to_json`/`from_json`
  mirroring `delta.py:manifest_to_json/from_json`, version-bumped to 2 with v1 fallback.

**Done when:** `test_state.py` green; `as_manifest()` feeds `diff_manifests` unchanged.

### T2: content+config-addressed Silver cache skips Bedrock on a hit

**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/silver.py, packages/graphrag/src/graphrag/embed.py, packages/graphrag/src/graphrag/extract_llm.py, packages/graphrag/tests/test_silver.py

**Tests:**
- Cache **hit** (artifact present at `{fp}/{hash}`) makes zero embed and zero
  extractor calls — asserted with a spy (TDD). [AC1]
- Cache **miss** computes once and writes both artifacts (TDD). [AC1]
- An embedder-fp bump makes **every** doc's chunks artifact a miss while candidates stay
  hits; a schema-fp bump is the mirror; a moved doc (same hash+fp) is a hit (TDD, multi-doc). [AC2, AC3]

**Approach:**
- Add `Embedder.fingerprint()` and `schema_fingerprint(schema)`; add `ArtifactStore`
  Protocol + in-memory impl; add `materialize_silver(doc, artifacts, embedder, extractor,
  schema, embedder_fp, extraction_fp)` that caches **chunks+vectors** (`chunk_corpus([doc])`
  → `embedder.embed(...)`) and **ungrounded LLM candidates** (`extractor.extract(doc, schema)`).
  Deterministic `extract()`/`resolve()` is **not** cached (no Bedrock; Gold re-derives it).
  No grounding here.

**Done when:** `test_silver.py` green incl. the zero-Bedrock-on-hit spy.

### T2b: carve a `ground_candidates` seam (ground cached candidates, zero Bedrock)

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/schema_extract.py, packages/graphrag/tests/test_schema_extract.py

**Tests:**
- Characterization: `ground_candidates(candidates, graph, schema, aliases)` over the candidates
  `extractor.extract` would produce yields edges byte-identical to today's `extract_schema_guided`
  for the same inputs — and invokes the extractor **zero** times (TDD). [AC1]

**Approach:**
- Split `extract_schema_guided` (`schema_extract.py:101`) into its two halves: the per-doc
  Bedrock `extractor.extract` (now owned by Silver's `materialize_silver`, T2) and a pure
  `ground_candidates(candidates, graph, schema, aliases)` that runs `validate_triple` + `ground`
  over **pre-supplied** candidates. `extract_schema_guided` is retained as the composition of the
  two (back-compat for the full/rebuild path).

**Done when:** the characterization test is green; `extract_schema_guided` output unchanged.

### T3: `GraphDelta` plan/apply reproduces `_reconcile_graph`

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/graphdelta.py, packages/graphrag/src/graphrag/ingest.py, packages/graphrag/tests/test_graphdelta.py

**Tests:**
- Characterization: for representative `(store, scratch, removed_ids)`, `apply(plan(...))`
  yields a store state byte-identical to today's `_reconcile_graph` (TDD). [AC5]
- `plan_graph_delta` performs no store mutation (a read-only store spy asserts zero writes) (TDD). [AC5]

**Approach:**
- Extract the reconciliation in `ingest.py:159-203` into `plan_graph_delta` (pure,
  returns `GraphDelta`) + `apply_graph_delta` (the only mutating step); `_reconcile_graph`
  becomes `apply(plan(...))`.

**Done when:** equivalence + purity tests green; existing delta tests unchanged.

### T4a: `ingest_staged` driver reaches full-rebuild parity on a delta

**Depends on:** T1, T2, T2b, T3
**Touches:** packages/graphrag/src/graphrag/ingest.py, packages/graphrag/tests/test_ingest_staged.py

**Tests:**
- Integration (in-memory stores): a delta re-ingest recomputes only the changed Silver set
  and reaches the same node/edge/vector end state as a full rebuild — and a content-only change
  recomputes nothing beyond the changed docs (goal-based/integration). [AC1, AC2, AC8]

**Approach:**
- Add `ingest_staged(prev_state, …)` orchestrating Bronze (parse+manifest) → Silver
  (`materialize_silver` per changed/stale doc) → Gold (`resolve()` re-derives deterministic +
  `ground_candidates` (T2b) over the cached candidates + `apply_graph_delta` + vector
  delete/re-index), returning `(DeltaReport, IngestState)`. Unchanged docs hit Silver (zero Bedrock).

**Done when:** the integration test is green and `ingest_staged` returns a v2 `IngestState`.

### T4b: Fargate entrypoint reads/writes `IngestState`; query Lambda stays clean

**Depends on:** T4a
**Touches:** apps/ingestion/entrypoint.py, apps/ingestion/tests/test_entrypoint.py

**Tests:**
- `entrypoint.py` round-trips `IngestState` to/from S3, a v1 object upgrading in (goal-based). [AC4]
- Import check: the query Lambda bundle imports neither `delta.py` nor `silver.py` (goal-based). [AC9]
- Diff check: the change touches no query-path/retrieval code — `git diff --name-only` against
  the base lists none of `hybrid.py`, `query.py`, or the query-Lambda module (goal-based). [AC9]

**Approach:**
- Wire `ingest_staged` into the task; read the prior `IngestState` (v1-compatible), write the
  new one **last** (preserving slice-5 ordering); back the `ArtifactStore` S3 impl with the
  existing `S3Client` seam (`entrypoint.py:59-109`).

**Done when:** entrypoint round-trip + import check green.

### T4c: key-scoped Silver IAM grant

**Depends on:** T4b
**Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py

**Tests:**
- CDK assertion: the synthesized template grants `PutObject` scoped to the Silver prefix and
  no broader bucket write (goal-based). [AC6]

**Approach:**
- Add `SILVER_PREFIX` + `bucket.grant_put(task_role, SILVER_PREFIX + "*")` beside the existing
  manifest/trace grants (`graphrag_stack.py:355-359`).

**Done when:** the CDK assertion test is green; least privilege preserved.

### T5: live acceptance — cache-skip, fingerprint-bump, retrieval, teardown

**Depends on:** T4c
**Touches:** docs/specs/medallion-staging/spec.md (check AC boxes), docs/backlog.md (only if a live AC must defer)

**Tests:**
- Deploy; ingest twice unchanged → second run zero Bedrock embed/extract (live). [AC1]
- Bump embedder/schema fingerprint → affected artifacts recompute; a vector query and a
  graph traversal reflect the change through the **unmodified** query path (live, read-only). [AC2, AC8]
- `destroy` → zero residual Silver objects in the bucket (live). [AC7]

**Approach:**
- Run the live ACs against the deployed task per the run-or-defer convention; check the
  boxes in `spec.md`; only defer with a `docs/backlog.md` anchor if a live AC genuinely can't run.

**Done when:** the live ACs above pass (or are recorded deferred with an anchor); spec AC boxes updated.

## Rollout

- **Delivery:** big-bang within the ingest path; reversible via `--rebuild` (ground-truth
  reset) and the v1 state still readable. No published external contract.
- **Infrastructure:** one new S3 prefix (`silver/`) in the existing corpus bucket + one
  key-scoped `grant_put`; no new resource type, no standing service (ADR-0002).
- **External-system integration:** none beyond the existing Bedrock/Neptune/OpenSearch the
  task already uses.
- **Deployment sequencing:** ship state+cache+delta code (T1–T4) and the IAM grant together;
  the first deployed run warms Silver (one full ingest's cost), then steady-state is incremental.

## Risks

- The `GraphDelta` lift could subtly change reconciliation order (nodes-before-edges); the
  characterization test (T3) pins it before the refactor.
- Fingerprint granularity too coarse → over-invalidation; acceptable direction, but tune in T2.
- First post-deploy run recomputes all Silver (expected warm-up); narrate it in the report so
  it isn't mistaken for a regression.

## Changelog

- 2026-06-28: initial plan (follows RFC-0003 / ADR-0007).
