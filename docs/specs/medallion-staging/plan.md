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
  ground per-doc in Silver — rejected: grounding needs the global graph (`schema_extract.py:101`). Traces to: AC1, AC9 · n/a.

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
  S3 impl for the task. The S3 impl builds the key as
  `silver/{config_fingerprint}/{content_hash}/{chunks,candidates}.json` from a
  `silver_key(fingerprint, content_hash, artifact)` helper that **only** accepts the
  server-computed `content_hash` (sha256 hex) + server-derived `config_fingerprint`
  (hex) and raises on a component outside `^[a-f0-9]+$` — `doc_id`/path/span/model text
  never reach the key (the trace-key CWE-23 confinement pattern, `entrypoint.py:47-50`).
  Traces to: AC1, AC6, AC7 · n/a.
- `Embedder.fingerprint()` and `schema_fingerprint(EXTRACTION_SCHEMA)` both return a
  short **hex** digest over a *canonical* form (sorted/explicit fields — embedder:
  `model_id`+`dimensions`; schema: each edge's `kind.value`/`src_kind`/`dst_kind`
  only, **not** the free-text `description`), so the fingerprint is stable across runs
  and changes iff a load-bearing field changes. Traces to: AC2 · n/a.
- `ground_candidates(candidates, graph, *, schema, aliases) -> (entries, edges)` carved from
  `extract_schema_guided` (`schema_extract.py:101`) so Gold grounds Silver's *cached*
  candidates with zero Bedrock. It **preserves input order** (no internal sort), so
  `extract_schema_guided` — which gathers candidates per-doc in extractor order, then delegates —
  produces `entries`/`render()` byte-identical to today (trace artifact unchanged). The graph
  is unaffected by order regardless: edges are reconciled by `(src, kind, dst)` key, so
  set-equality is what the store needs. For determinism the **staged Gold path** loads cached
  candidates in sorted doc-id order before calling `ground_candidates`, so the staged path's
  edge **set** equals a full-corpus `extract_schema_guided` pass. Traces to: AC1 · n/a.

### Staged-driver scope (`ingest_staged`, T4a/T4b)
`ingest_staged(prev_state, community_root, enhancements_root, graph_store, vector_store,
artifacts, embedder, *, extractor=None, schema=EXTRACTION_SCHEMA, aliases=None,
labels=None) -> tuple[DeltaReport, IngestState]` is the staged engine for **all three
modes** (full = `prev_state=None`; rebuild = clear-then-full; delta = incremental). It
**subsumes**: Bronze (parse + build new state + `diff_manifests(prev.as_manifest(), new)`);
Silver (`materialize_silver` per added/changed/moved-to/stale doc — chunks+vectors always,
LLM candidates **only when `extractor` is supplied**); Gold (`resolve()` re-derives
deterministic nodes/edges + `label_graph` + — when `extractor` supplied — `ground_candidates`
over the cached candidates of every surviving doc + `plan_graph_delta`/`apply_graph_delta`
reconcile; vector `delete_by_doc` + re-index of cached vectors + `label_chunks`). It returns
the new v2 `IngestState` and, when grounding ran, the `ExtractionResult` (carried on the
`DeltaReport`) so the entrypoint persists the trace.

It deliberately **leaves to the entrypoint** (unchanged from today): community detection
(`_community_writeback`, full/rebuild only — ADR-0005, never on delta); the trace-artifact
S3 write (server-side key); and MODE routing. **Schema-guided extraction stays full/rebuild-
only and default-off** (ADR-0006): the entrypoint passes `extractor` to `ingest_staged`
**only** on `MODE in {full, rebuild}` **and** `SCHEMA_EXTRACTION` set, so `MODE=delta` never
grounds (today's behavior preserved) while the Silver candidate cache still makes a
full/rebuild re-run zero-Bedrock. Additive resilience is preserved: a raising extractor
leaves the deterministic graph intact. Traces to: AC1, AC2, AC9 · n/a.

### Failure, edge cases & resilience
- Partial Silver write: content-addressed keys + write-then-readable; a fingerprint
  bump sidesteps a poisoned generation. Traces to: AC1 · n/a.
- Re-index idempotency: delete-by-doc before re-index (today's `ingest_delta`
  behavior preserved). Traces to: AC9 · n/a.
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
- Content-only change (multi-doc spy): the changed doc is a miss on both artifacts while
  **sibling** docs stay hits on both — per-doc embed/extract call counts asserted (TDD). [AC2]
- `SilverArtifact` round-trips through the **serialized JSON text** (not the in-memory
  dict): `load(write(art))` restores `Chunk` provenance fields, the float vectors, and
  `CandidateTriple` fields exactly (TDD). [AC1]
- `silver_key` is built only from hex `content_hash`+`config_fingerprint`; a `doc_id`
  containing `../` passed anywhere near the store cannot change the resolved key, and a
  non-hex component raises (TDD, confinement). [AC6]
- `schema_fingerprint`/`Embedder.fingerprint` are stable across two calls and change iff a
  load-bearing field changes (a `description`-only schema edit does **not** bump the fp) (TDD). [AC2]

**Approach:**
- Add `Embedder.fingerprint()` and `schema_fingerprint(schema)` (canonical hex digests per
  the Design `Interfaces` note); add `ArtifactStore` Protocol + in-memory impl + the
  `silver_key()` confinement helper; add `materialize_silver(doc, artifacts, embedder,
  extractor, schema, embedder_fp, extraction_fp)` that caches **chunks+vectors**
  (`chunk_corpus([doc])` → `embedder.embed(...)`) and **ungrounded LLM candidates**
  (`extractor.extract(doc, schema)`, only when an extractor is supplied).
  Deterministic `extract()`/`resolve()` is **not** cached (no Bedrock; Gold re-derives it) —
  the authoritative reading; the RFC sketch's `extract([doc]) +` is illustrative only.
  No grounding here.

**Done when:** `test_silver.py` green incl. the zero-Bedrock-on-hit spy, the serialized
round-trip, and the key-confinement test.

### T2b: carve a `ground_candidates` seam (ground cached candidates, zero Bedrock)

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/schema_extract.py, packages/graphrag/tests/test_schema_extract.py

**Tests:**
- Characterization: `ground_candidates(candidates, graph, schema, aliases)` over the candidates
  `extractor.extract` would produce yields edges byte-identical to today's `extract_schema_guided`
  for the same inputs — and invokes the extractor **zero** times (TDD). [AC1]
- Edge-set stability: candidates supplied in two different orders ground to the same edge
  **set** (the property the staged path relies on, since Gold reloads candidates per-doc, not in
  one `extract` pass; the store reconciles by `(src, kind, dst)` key) (TDD). [AC1]
- Trace byte-identity: `extract_schema_guided`'s `entries`/`render()` for a multi-candidate doc
  is unchanged by the refactor — `ground_candidates` preserves input order, so the doc-then-
  extractor entry order is identical (TDD). [AC1]
- Existing `test_schema_extract.py` suite stays green (the retained `extract_schema_guided`
  composition is unchanged in output and per-doc call count).

**Approach:**
- Split `extract_schema_guided` (`schema_extract.py:101`) into its two halves: the per-doc
  Bedrock `extractor.extract` (now owned by Silver's `materialize_silver`, T2) and a pure
  `ground_candidates(candidates, graph, *, schema, aliases)` that runs `validate_triple` +
  `ground` over the candidates **in input order** (no internal sort), returning
  `(entries, edges)`. `extract_schema_guided` is retained as the composition: gather candidates
  via `extractor.extract` per doc (one call/doc), then delegate to `ground_candidates`
  (back-compat for the full/rebuild path) — output and trace byte-identical to today.
- The `CandidateTriple` contract is frozen by T1's data-schema decision, so T2↔T2b have no
  ordering hazard (both touch the extractor contract but it is fixed); `Depends on: none` holds.

**Done when:** the edge-set-stability + trace-byte-identity tests are green; `extract_schema_guided`
output and per-doc call count unchanged.

### T3: `GraphDelta` plan/apply reproduces `_reconcile_graph`

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/graphdelta.py, packages/graphrag/src/graphrag/ingest.py, packages/graphrag/tests/test_graphdelta.py

**Tests:**
- Characterization: for representative `(store, scratch, removed_ids)`, `apply(plan(...))`
  yields a store state byte-identical to today's `_reconcile_graph` (TDD). [AC5]
- Call-count parity: a spy store records every `replace_node`/`replace_edge`/`delete_*`;
  `apply(plan(...))` makes the **same set** of mutating calls as `_reconcile_graph` — an
  **unchanged** row triggers **no** `replace_*` (the no-op optimization is preserved, not just
  the final state) (TDD). [AC5]
- `plan_graph_delta` performs no store mutation (a read-only store spy asserts zero writes) (TDD). [AC5]
- Existing `test_ingest_delta.py` suite stays green (the refactor is behavior-preserving).

**Approach:**
- Extract the reconciliation in `ingest.py:159-203` into `plan_graph_delta` (pure, returns a
  `GraphDelta{upsert_nodes, upsert_edges, delete_nodes, delete_edges}`) + `apply_graph_delta`
  (the only mutating step). `plan_graph_delta` puts a node/edge in the upsert list **only when
  it actually changed** (`store_node is None or not _node_unchanged(...)`), so `apply` re-writes
  exactly the rows `_reconcile_graph` re-wrote — unchanged rows stay untouched. `_reconcile_graph`
  becomes `apply(plan(...))` and returns the orphan count (delete-list length) for back-compat.

**Done when:** equivalence + call-count-parity + purity tests green; existing delta tests unchanged.

### T4a: `ingest_staged` driver reaches full-rebuild parity on a delta

**Depends on:** T1, T2, T2b, T3
**Touches:** packages/graphrag/src/graphrag/ingest.py, packages/graphrag/tests/test_ingest_staged.py

**Tests:**
- Integration (in-memory stores): a delta re-ingest recomputes only the changed Silver set
  and reaches the same node/edge/vector end state as a full rebuild — per-doc embed/extract
  call counts asserted, so a content-only change recomputes nothing beyond the changed docs
  (goal-based/integration). [AC1, AC2, AC8]
- With an `extractor` supplied, the staged path's schema-guided edges match a full
  `extract_schema_guided` over the same corpus (the cached-candidates → `ground_candidates`
  Gold path is edge-equivalent to the one-pass path) (integration). [AC9]
- Preserved passes: `label_graph`/`label_chunks` still stamp visibility on the staged output
  (a node/chunk carries its `visibility` prop after `ingest_staged`) (integration). [AC9]

**Approach:**
- Add `ingest_staged(...)` per the **Staged-driver scope** in Design (LLD) — Bronze → Silver
  (`materialize_silver` per changed/stale doc) → Gold (`resolve()` + `label_graph` +
  `ground_candidates` over cached candidates when an extractor is supplied + `apply_graph_delta`
  + vector delete/re-index + `label_chunks`), returning `(DeltaReport, IngestState)` with the
  `ExtractionResult` carried on the report when grounding ran. Unchanged docs hit Silver (zero
  Bedrock). Community detection and the trace write stay in the entrypoint (T4b), not here.

**Done when:** the integration tests are green and `ingest_staged` returns a v2 `IngestState`.

### T4b: Fargate entrypoint reads/writes `IngestState`; query Lambda stays clean

**Depends on:** T4a
**Touches:** apps/ingestion/entrypoint.py, apps/ingestion/tests/test_entrypoint.py

**Tests:**
- `entrypoint.py` round-trips `IngestState` to/from S3, a v1 object upgrading in (goal-based). [AC4]
- Import check: the query Lambda bundle imports neither `delta.py` nor `silver.py` (goal-based). [AC10]
- Diff check: the change touches no query-path/retrieval code — `git diff --name-only` against
  the base lists none of `hybrid.py`, `query.py`, or the query-Lambda module (goal-based). [AC10]

**Approach:**
- Wire `ingest_staged` into the task as the engine for all three MODEs; read the prior
  `IngestState` (v1-compatible), write the new one **last** (preserving slice-5 ordering); back
  the `ArtifactStore` S3 impl with the existing `S3Client` seam (`entrypoint.py:59-109`). The
  entrypoint threads `load_aliases()` (`entrypoint.py:325`) as `aliases` into `ingest_staged`'s
  Gold step. The entrypoint passes `extractor` to `ingest_staged` **only** when `MODE in {full, rebuild}` and
  `SCHEMA_EXTRACTION` is set (ADR-0006 default-off, full/rebuild-only — preserved); it keeps
  `_community_writeback` (full/rebuild only) and writes the returned `ExtractionResult` trace to
  the server-side key, replacing `_schema_extraction_writeback`'s extract+ground with the staged
  cached path while preserving its default-off / trace-write / additive-resilience contract.

**Done when:** entrypoint round-trip + import check green; the existing entrypoint test suite
(`test_entrypoint.py`) stays green (default-off and full/delta/rebuild behavior preserved).

### T4c: key-scoped Silver IAM grant

**Depends on:** T4b
**Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py

**Tests:**
- CDK assertion (new): the synthesized template has a `PutObject` statement whose Resource is
  prefix-bounded to `silver/*` (not the bucket root, not `*`) (goal-based). [AC7]
- Update the **existing** `test_ingestion_task_can_write_manifest_scoped_to_manifest_key`
  (`test_stack.py:242`): add the Silver prefix to its `_allowed_keys` tuple so the new PutObject
  statement passes the per-statement scope assertion. [AC7]
- Update the **existing** `test_schema_extraction_trace_putobject_grant_is_key_scoped`
  (`test_stack.py:810`): refine its `bucket_wide` heuristic so a `…/silver/*` **prefix** ARN is
  distinguished from a bare bucket-root `…/*` (the current `endswith("/*")` test conflates them) —
  bucket-wide means the segment after the bucket ARN is exactly `/*`, a prefix grant has an
  intervening path. Empirically confirm via `cdk synth` output which form CDK emits. [AC7]

**Approach:**
- Add `SILVER_PREFIX = "silver/"` + `bucket.grant_put(task_role, SILVER_PREFIX + "*")` beside the
  existing manifest/trace grants (`graphrag_stack.py:355-359`). Keep both existing grants; this
  is additive. Run the stack test suite and fix the two existing assertions above as needed.

**Done when:** the new + both updated CDK assertion tests are green; least privilege preserved
(no bucket-root/`*` PutObject; no other role gains write).

### T5: live acceptance — cache-skip, fingerprint-bump, retrieval, teardown

**Depends on:** T4c
**Touches:** docs/specs/medallion-staging/spec.md (check AC boxes), docs/backlog.md (only if a live AC must defer)

**Tests:**
- Deploy; ingest twice unchanged → second run zero Bedrock embed/extract (live). [AC1]
- Bump embedder/schema fingerprint → affected artifacts recompute; a vector query and a
  graph traversal reflect the change through the **unmodified** query path (live, read-only). [AC2, AC9]
- `destroy` → zero residual Silver objects in the bucket (live). [AC8]

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
- 2026-06-28: pre-EXECUTE review amendments — pinned `ingest_staged` scope (subsumes
  Bronze/Silver/Gold incl. labels; leaves community detection + trace write + MODE routing to
  the entrypoint; schema-guided stays full/rebuild-only + default-off); `plan_graph_delta`
  excludes unchanged rows (call-count-parity test) to preserve the no-op; added Silver-key
  CWE-23 confinement helper + AC; canonical hex fingerprints + stability test; candidate
  ordering contract for staged-path edge byte-identity; serialized round-trip + content-only
  multi-doc spy tests; T4c now refines the two existing `test_stack.py` PutObject assertions
  and asserts a prefix-bounded `silver/*` Resource.
