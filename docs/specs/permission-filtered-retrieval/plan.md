# Plan: permission-filtered-retrieval

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done — offline build complete + reviewed; AC9 live two-persona smoke deferred (needs AWS creds) -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. Substantial changes get a dated
> changelog entry at the bottom.

## Approach

Thread one new concept — **visibility** — through the pipeline slices 1–3 already ship,
adding the smallest set of code and no new runtime dependency or infra resource. The
change has two halves that meet at a value object:

1. **Write side (ingest):** a labeling pass stamps `visibility` onto graph nodes, graph
   edges (`max` of endpoints), and chunks (`max` of owning entities), from a packaged
   `labels.yaml`, on the **same** dual-write the corpus is already ingested through.
2. **Read side (query):** a pure-Python `Clearance` (resolved from a `persona`) threads
   through the `VectorStore`/`GraphStore` seams as an optional `clearance`/`allowed_labels`
   that defaults to *unrestricted*, so the filter is opt-in and slices 1–3 stay green.

The **riskiest and load-bearing** part — front-loaded — is the **during-traversal edge
filter** (AC3): the filter must live in the `neighbors`/`neighbors_batch` hop so a
forbidden node never enters the frontier and can't bridge to a node reachable only
through it. The in-memory fan-out filters edges/neighbors in Python; the Neptune override
adds a parameterized `WHERE … IN $allowed` on the edge **and** the neighbor. A fixture
with a *restricted intermediate* is the test that proves it's an edge filter, not a
post-filter. Everything else (vector terms-filter, the orchestration thread-through, the
CLI/Lambda persona, the trace lines) is mechanical once the seam carries `clearance`.

The load-bearing offline choice mirrors slice 3: the offline embedder/synthesizer are
**non-semantic**, so the persona contrast is asserted **structurally** (the right items
are present/absent for a persona), never by score; semantic honesty stays the live path /
frozen-vector eval.

## Constraints

- **Charter principle 5** — synthetic labels are presented as a stand-in for ACLs
  everywhere they surface (trace, CLI, docs); **never** real authz.
- **Charter Architecture-pattern 7 / design doc D1 (permission paragraph)** — labels are
  carried as Neptune node/edge properties *and* OpenSearch metadata filters, applied
  **during traversal on edges**, not only on final nodes; the query takes a
  persona/clearance and the trace shows what was filtered.
- **ADR-0001** — filtering rides the **same** seed-and-expand path; the comparison runner
  still executes the three modes independently. **ADR-0002** — same stores + the in-VPC
  query Lambda behind the IAM-auth Function URL; no new ingress. **ADR-0003** — IaC stays
  AWS CDK Python; any change lands in `apps/infra/stacks/graphrag_stack.py`.
- **`packages/graphrag/AGENTS.md`** — the query import graph stays PyYAML-free; the
  `neighbors_batch` seam keeps a backend-identical (sorted) trace.
- **Charter principle 1** — every filtered hop is narratable; the filtered-out trace is
  the observability surface.

## Construction tests

Most tests live per-task below. Cross-cutting:

- **Integration:** `compare --persona public-reader` vs `--persona maintainer` over the
  fixture (offline) returns divergent three-mode results — the restricted item absent for
  the reader, present for the maintainer — `test_compare.py` (extend).
- **Integration / leak guard:** the AC3 restricted-intermediate fixture asserted through
  `expand_neighborhood` *and* through `hybrid_query`, so the during-traversal guarantee is
  checked end-to-end, not only at the seam — `test_query.py` / `test_hybrid.py`.
- **Invariant:** `test_query_lambda_import_graph_is_pyyaml_free` stays green after the
  `visibility` module + persona wiring land.

## Design (LLD)

Shape `mixed` → design decisions, data & schema, interfaces & contracts, component
decomposition, failure & resilience, dependencies & integration. Stack is the established
repo (Python 3.11+, `pyyaml`+`boto3`, AWS CDK Python), mirroring slices 1–3.

### Design decisions
*(Traces to: AC1, AC3, AC4 · no `contracts/` file — internal interfaces + a persona field on the existing Lambda.)*

- **Visibility is one ordered tier scale; composition is `max` (most-restrictive-wins).**
  An edge is as sensitive as its more-sensitive endpoint; a chunk as its most-sensitive
  owner. *Why:* makes "edge traversable" ≡ "both endpoints visible," so the edge filter
  *is* the node guarantee — one rule, no second mechanism. *Rejected:* independent
  per-edge labels (a second label source to keep consistent; no teaching gain here).
- **Clearance threads as an optional seam parameter defaulting to unrestricted.**
  `knn(..., allowed_labels=None)`, `neighbors(..., allowed_labels=None)`,
  `neighbors_batch(..., allowed_labels=None)`, `expand_neighborhood(..., clearance=None)`.
  *Why:* opt-in keeps slices 1–3 behavior/tests byte-unchanged. *Rejected:* a required
  parameter (churns every slice-1–3 call site and test for no benefit).
- **`visibility` lives in `Node.props`/`Edge.props` (graph) and a new `Chunk.visibility`
  field (vector).** *Why:* `props` is already a generic scalar bag round-tripped by the
  Neptune adapter (`_scalar_props`); no `model.py` schema change for the graph. The chunk
  is a flat dataclass written to a flat OpenSearch doc, so an explicit field is cleanest.
- **Persona→clearance is pure-Python constants; label assignment reads `labels.yaml`.**
  *Why:* the query Lambda is PyYAML-free; clearance must resolve there, labels are already
  baked into the stores at ingest, so the read path needs no YAML. *Rejected:* a
  `personas.yaml` loaded at query time (would pull YAML into the Lambda bundle).
- **The edge filter is server-side on Neptune (a `WHERE … IN $allowed`), never a Python
  post-filter.** *Why:* the leak guard (AC3) + keeps the DB from returning forbidden rows;
  `$allowed` rides the parameters map (security posture preserved).

### Data & schema
*(Traces to: AC1, AC2.)*

- `graphrag.visibility.Visibility` (ordered): `PUBLIC` < `INTERNAL` < `RESTRICTED`, with a
  rank map; `compose(*labels) -> Visibility` returns the max (empty → `PUBLIC`).
- `graphrag.visibility.Clearance`: `persona: str`, `allowed: frozenset[str]`;
  `allows(label) -> bool`. `PERSONAS: dict[str, frozenset]` constant;
  `resolve_clearance(persona) -> Clearance` (raises **`ValueError`** on unknown — one type,
  so the CLI and Lambda catch a known exception).
- `Chunk.visibility: str = "public"` (new field; flows into the OpenSearch doc +
  `_knn_mapping` `visibility: keyword`).
- `Node`/`Edge`: `props["visibility"]` (string tier). Edge value = `compose(src, dst)`.
- Packaged `labels.yaml`: `{ "<entity-id>": "<tier>" }` + a top-level default; loaded by
  `graphrag.labels.load_labels()` (ingest-path only, like `load_aliases`).

### Interfaces & contracts
*(Traces to: AC1, AC3–AC7.)*

- `graphrag.visibility`: `Visibility`, `Clearance`, `PERSONAS`, `resolve_clearance(str)`,
  `compose(*str)`. PyYAML-free.
- `graphrag.labels`: `load_labels() -> dict[str,str]`; `label_graph(graph, labels)`;
  `label_chunks(chunks, labels) -> None` (sets `chunk.visibility`).
- `graphrag.store.vector_base.VectorStore.knn(vector, k, *, allowed_labels=None)`;
  `MemoryVectorStore` + `OpenSearchVectorStore` honor it.
- `graphrag.store.base.GraphStore.neighbors(..., *, allowed_labels=None)` /
  `neighbors_batch(node_ids, *, allowed_labels=None)`; memory + Neptune honor it.
- `graphrag.query.expand_neighborhood(store, seeds, *, max_hops, frontier_cap, clearance=None)`.
- `graphrag.vector.vector_search(store, embedder, query, k, *, clearance=None)`.
- `graphrag.hybrid.hybrid_query(..., clearance: Clearance | None = None)`.
- `graphrag.compare.run_modes(..., clearance: Clearance | None = None)`.
- `graphrag.query_lambda.lambda_handler` — reads optional `persona` from the body.

### Component / module decomposition
*(New modules under `packages/graphrag/src/graphrag/`.)*

- **New:** `visibility.py` (pure), `labels.py` (ingest-path; uses yaml),
  `src/graphrag/labels.yaml` (packaged data).
- **Extend:** `model.py`? no (props bag). `chunk.py` (add `visibility` field),
  `store/vector_base.py` + `store/vector_memory.py` + `store/opensearch.py` (knn filter +
  mapping + doc field), `store/base.py` + `store/memory.py` + `store/neptune.py`
  (neighbors filter), `query.py` (expand_neighborhood clearance + seed filter), `vector.py`
  (clearance), `hybrid.py` + `compare.py` (clearance thread-through + trace lines),
  `cli.py` (`--persona`), `query_lambda.py` (persona), `ingest.py` (label the graph),
  `apps/ingestion/entrypoint.py` (label chunks in the dual-write),
  `showcase/queries.yaml` + `showcase/__init__.py` (persona field).
- **Reused unchanged:** `normalize`, `resolve`, `entity_link`, `embed`, `synthesize`.

### Failure, edge cases & resilience
*(Traces to: AC1, AC3, AC5, AC7.)*

- Unknown persona → CLI exits non-zero with a message; Lambda returns a sanitized
  envelope (no internal detail). Resolution failure never silently widens access.
- Unlabeled entity/edge/chunk → `public` (least-restrictive default), explicit in
  `compose([]) == PUBLIC`.
- `clearance=None` → no filtering (slice-1–3 path). Empty `allowed` (a persona that sees
  nothing — not in the shipped set) → everything filtered; still correct, never errors.
- A forbidden **seed** → dropped + recorded (distinct trace bucket from slice-3's
  unconfirmed-candidate drop), so a filtered seed is visible, not silent.
- Filtered-out trace surfaces IDs as a teaching aid with an explicit "a real ACL system
  would not reveal this" note (charter principle 5).

### Dependencies & integration
*(Traces to: AC2, AC4, AC8.)*

- No new Python runtime dependency. OpenSearch terms-filter + Neptune WHERE use the
  existing SigV4+urllib adapters. The `visibility` keyword field rides `create_index` on a
  fresh deploy (idempotent; teardown-first rebuild — no migration). No new infra resource;
  Budgets unchanged.

## Tasks

> Tests come before Approach in each task. TDD tasks carry a red **stub** marked
> `# STUB: AC<n>`. Paths are under `packages/graphrag/src/graphrag/` and
> `packages/graphrag/tests/` unless noted.

### T1 — Visibility model + persona/clearance resolution (pure)
- **Depends on:** none
- **Touches:** packages/graphrag/src/graphrag/visibility.py, packages/graphrag/tests/test_visibility.py
- **Tests:** `test_visibility.py` — tier ordering (`PUBLIC < INTERNAL < RESTRICTED`);
  `compose("public","restricted") == RESTRICTED`, `compose() == PUBLIC`;
  `resolve_clearance("public-reader").allowed == {public}`, `"member"` → `{public,
  internal}`, `"maintainer"` → all three; `Clearance.allowed` is downward-closed and
  `Clearance.allows` true within / false above; unknown persona raises **`ValueError`**
  (one type); a `Clearance` with **empty** `allowed` filters everything (fail-closed — the
  None-vs-empty distinction, asserted at the filter call sites in T3/T4/T5 too);
  **`import graphrag.visibility` pulls in no `yaml`** (assert against `sys.modules` like
  the query-lambda guard). `# STUB: AC1`, `stub: true`.
- **Approach:** `graphrag.visibility` — `Visibility(StrEnum)` + a `_RANK` map; `compose`;
  frozen `Clearance` dataclass (`allowed: frozenset[str]`, downward-closed) + `allows`;
  `PERSONAS` constant; `resolve_clearance` (raises `ValueError` on unknown). No imports
  beyond stdlib.
- **Done when:** `test_visibility.py` green (AC1).

### T2 — Label source + ingest labeling pass wired into the dual-write (both stores)
- **Depends on:** T1
- **Touches:** packages/graphrag/src/graphrag/labels.py, packages/graphrag/src/graphrag/labels.yaml, packages/graphrag/src/graphrag/chunk.py, packages/graphrag/src/graphrag/ingest.py, apps/ingestion/entrypoint.py, packages/graphrag/tests/test_labels.py, packages/graphrag/tests/test_ingest.py (or test_store_memory), apps/ingestion/tests/test_entrypoint.py
- **Tests:** `test_labels.py` — `load_labels()` parses the packaged map + default;
  `label_graph(graph, labels)` sets each node's `props["visibility"]` and each edge's
  `props["visibility"] == compose(src_vis, dst_vis)`; `label_chunks(chunks, labels)` sets
  `chunk.visibility == compose(owning entity_ids)`; an unlabeled entity defaults `public`;
  the fixture `labels.yaml` marks ≥1 `restricted` + ≥1 `internal` entity (a test asserts
  they resolve to real fixture nodes). Ingest-seam tests: `ingest(..., labels=…)` upserts
  nodes/edges that already carry `visibility` (the label pass runs **after** `resolve()`,
  **before** the `upsert_*` loop); the Fargate `_vector_dual_write` labels chunks
  **before** `index_chunk`. `# STUB: AC2`, `stub: true`.
- **Approach:** add `Chunk.visibility: str = "public"`; `graphrag.labels` —
  `load_labels()` (packaged-resource loader, like `load_aliases`), `label_graph(graph,
  labels)` (sets node + `compose`-derived edge props), `label_chunks(chunks, labels)`
  (using `visibility.compose`); author `labels.yaml` with the demo's restricted/internal
  picks (chosen with T3's leak fixture). **Wire the seam:** `ingest()` gains a
  `labels: dict[str,str] | None = None` param (default `load_labels()`), calls
  `label_graph(graph, labels)` between `resolve()` and the upsert loop; `entrypoint.py`'s
  `_vector_dual_write` calls `label_chunks(chunks, load_labels())` before indexing. Both
  are on the PyYAML-available ingest path (never imported by the Lambda).
- **Done when:** `test_labels.py` + the ingest-seam tests green; labeled nodes/edges/chunks
  are written by the dual-write, not bolted on after (AC2).

### T3 — During-traversal edge filter at the GraphStore seam (the leak guard)
- **Depends on:** T1
- **Touches:** packages/graphrag/src/graphrag/store/base.py, packages/graphrag/src/graphrag/store/memory.py, packages/graphrag/src/graphrag/store/neptune.py, packages/graphrag/src/graphrag/query.py, packages/graphrag/tests/test_query.py, packages/graphrag/tests/test_store_neptune.py
- **Tests (leak-correctness — in-memory):** `test_query.py` (extend) — over a fixture
  graph with a **restricted intermediate** R (seed → R → B where B is reachable *only* via
  R): `expand_neighborhood(store, [seed], max_hops=2, clearance=public_reader)` reaches
  **neither** R **nor** B at any hop (a final-node-set post-filter would still surface B —
  the regression the fixture catches); `clearance=maintainer` reaches both; `clearance=None`
  unchanged from slice 3. This runs on `MemoryGraphStore`, the only backend where traversal
  logic executes locally. (Seed-visibility filtering — dropping a forbidden seed before
  expansion — needs `get_node` and so lives in the orchestration layer, asserted in T5; an
  *allowed* seed whose only edges lead to forbidden nodes already reaches nothing here via
  the edge filter, which is the leak the fixture proves.)
- **Tests (parameterization/shape — Neptune):** `test_store_neptune.py` (extend) —
  `neighbors_batch(ids, allowed_labels={...})` emits an openCypher `WHERE` filtering
  `r.visibility` **and** `b.visibility` with the allowed list on the **`$allowed`
  parameters map** (asserted via the mock HTTP client; never interpolated). This is a
  *shape* check (the WHERE is present, the value is parameterized) — explicitly **not** a
  leak-correctness proof (a mock returns author-supplied rows, so it can't prove
  server-side exclusion); the leak proof is the in-memory test above. Have the mock return
  a restricted row to confirm the override *requested* it be filtered (the WHERE names it).
  Assert the in-memory and Neptune query *predicates* are the same (`edge.visibility ∈
  allowed AND neighbor.visibility ∈ allowed`). `# STUB: AC3`, `stub: true`.
- **Approach:** add `allowed_labels: frozenset[str] | None = None` to
  `GraphStore.neighbors`/`neighbors_batch`. The predicate is **one rule, both backends**:
  exclude an edge unless `edge.visibility ∈ allowed` **and** `neighbor.visibility ∈
  allowed`. Because `edge.visibility = compose(src,dst) = max(src,dst)` and `allowed` is
  downward-closed, this reduces to "both endpoints visible" — the node guarantee falls out
  of the edge check. `MemoryGraphStore.neighbors` already **iterates the edge objects**, so
  it inspects `edge.props["visibility"]` and the target node's `props["visibility"]`
  directly (the default `neighbors_batch` fan-out over `neighbors()` inherits the filter —
  no edge needs to be *returned*, only *applied* where it is in scope). `NeptuneGraphStore`
  appends `WHERE r.visibility IN $allowed AND b.visibility IN $allowed` to both `neighbors`
  and the batched override, `$allowed` riding the parameters map; the override need not
  *return* `r.visibility`/`b.visibility` (it filters server-side). `expand_neighborhood`
  gains `clearance`, filters seeds up front (recording dropped) and passes
  `clearance.allowed` into `neighbors_batch`. Keep the reached-set sort so the trace stays
  backend-identical.
- **Done when:** the in-memory leak fixture (post-filter would fail it) + the Neptune
  shape/parameterization test are green, computing the same predicate (AC3).

### T4 — Vector filter during k-NN
- **Depends on:** T1, T2
- **Touches:** packages/graphrag/src/graphrag/store/vector_base.py, packages/graphrag/src/graphrag/store/vector_memory.py, packages/graphrag/src/graphrag/store/opensearch.py, packages/graphrag/src/graphrag/vector.py, packages/graphrag/tests/test_vector_store_memory.py, packages/graphrag/tests/test_store_opensearch.py, packages/graphrag/tests/test_vector.py
- **Tests:** `test_vector_store_memory.py` — `knn(vec, k, allowed_labels={public})` drops a
  `restricted` chunk; `None` returns all. `test_store_opensearch.py` (extend) — `knn(...,
  allowed_labels=[...])` issues a `bool` query with the `knn` clause **and** a `filter:
  {terms: {visibility: [...]}}` in the request body (mock HTTP client); `_knn_mapping`
  carries `visibility: {type: keyword}`. `test_vector.py` — `vector_search(..., clearance)`
  threads `allowed_labels`. `# STUB: AC4`, `stub: true`.
- **Approach:** add `allowed_labels` to `VectorStore.knn`; memory filters hits by
  `chunk.visibility`; OpenSearch wraps the `knn` in `bool: {must:[knn], filter:[terms]}`
  when `allowed_labels` is set and adds `visibility` to `_knn_mapping` + the indexed doc +
  `_hit`; `vector_search` passes `clearance.allowed`.
- **Done when:** the three vector tests green (AC4).

### T5 — Thread clearance through the three modes + filtered-out trace
- **Depends on:** T3, T4
- **Touches:** packages/graphrag/src/graphrag/hybrid.py, packages/graphrag/src/graphrag/compare.py, packages/graphrag/tests/test_hybrid.py, packages/graphrag/tests/test_compare.py
- **Tests:** `test_hybrid.py` (extend) — `hybrid_query(..., clearance=public_reader)` over
  the fixture: vector seeds, question seeds, and reached nodes all exclude restricted items;
  a forbidden question seed is recorded as filtered (distinct from unconfirmed-dropped);
  **the final merged `HybridResult.graph_nodes` contains no node above clearance** (asserted
  independently of the seed/edge filters — a node re-materialized by id in the merge cannot
  reintroduce a restricted node); `HybridResult.render()` shows a `clearance:` line + a
  `filtered (visibility):` line with the synthetic-stand-in note; the slice-3
  dual-seed/bounded invariants still hold; `clearance=None` identical to slice 3.
  `test_compare.py` (extend) — `run_modes(..., clearance)` makes `public-reader` and
  `maintainer` results diverge across **all three** modes, **including vector-only** (which
  must filter its own chunk set, or it leaks restricted chunks the other two modes drop);
  the leak fixture (T3) holds through hybrid too. `# STUB: AC5`, `stub: true`.
- **Approach:** add `clearance: Clearance | None = None` to `hybrid_query`/`run_modes` and
  to the three private `compare` helpers (`_vector_only`/`_graph_only`/`_hybrid`); pass into
  `vector_search` + `expand_neighborhood`; filter question seeds by
  `clearance.allows(node.visibility)` (recording filtered); **after the merge, drop any
  resolved node above clearance** as the final guard; add `clearance` + a
  `filtered_seeds`/`filtered_chunks` record to `HybridResult` and the two trace lines in
  `render()`. `_vector_only` passes `clearance` into `vector_search` so vector-only filters
  too. `run_modes` threads clearance into each mode.
- **Done when:** `test_hybrid.py` + `test_compare.py` green, incl. the merged-set guard and
  the per-mode (vector-only included) divergence (AC5).

### T6 — CLI `--persona` across query verbs
- **Depends on:** T5
- **Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_hybrid_cli.py, packages/graphrag/tests/test_vector_cli.py, packages/graphrag/tests/test_cli.py
- **Tests:** `test_hybrid_cli.py` (extend) — `hybrid-query`/`compare --persona
  public-reader` (offline) print the persona + clearance + filtered-out trace, labeled a
  synthetic stand-in; an unknown persona exits non-zero; **no `--persona` = output
  byte-identical to slice 3** (a regression test pins this — the offline corpus is now
  labeled, but visibility must be inert: never rendered, never filtered, without a
  persona). `test_vector_cli.py`/`test_cli.py` — `vector-query`/`graph-query --persona …`
  filter likewise. `# STUB: AC6`, `stub: true`.
- **Approach:** add `--persona` to the four query subparsers; a `_clearance(args)` helper
  (`resolve_clearance` or `None` when absent); pass into the verb calls; label the construct
  in the offline-label/output **only when a persona is set**; also label the offline corpus
  in `_index_corpus`/`_populated_store` so the offline CLI demo can filter
  (`label_graph`/`label_chunks`) — but the trace lines + filtering are gated on a non-None
  clearance, so no-persona output stays byte-identical to slice 3.
- **Done when:** the CLI tests green, incl. the byte-identical no-persona regression (AC6).

### T7 — Query Lambda persona (PyYAML-free)
- **Depends on:** T5
- **Touches:** packages/graphrag/src/graphrag/query_lambda.py, packages/graphrag/tests/test_query_lambda.py
- **Tests:** `test_query_lambda.py` (extend) — with everything mocked, a `persona` in the
  body runs the filtered `hybrid_query` and returns the filtered result + trace; an unknown
  persona returns a sanitized envelope (correlation id, no internal detail); **no persona =
  unrestricted**; `test_query_lambda_import_graph_is_pyyaml_free` still green after the
  `visibility` import lands, **and** an added assertion that `graphrag.labels` (the
  yaml-loading module) is **absent** from `sys.modules` after importing `query_lambda` — so
  threading `visibility` never transitively drags `labels` (and PyYAML) onto the query path.
  `# STUB: AC7`, `stub: true`.
- **Approach:** parse optional `persona` in `_extract_question`'s sibling (or inline);
  `resolve_clearance` (caught → sanitized envelope on unknown); pass `clearance` into
  `hybrid_query`; `_serialize` already renders the trace.
- **Done when:** `test_query_lambda.py` green incl. the PyYAML-free guard (AC7).

### T8 — OpenSearch mapping + no-new-infra synth assertion
- **Depends on:** T4
- **Touches:** apps/infra/tests/test_stack.py (the `visibility` field is in T4's `_knn_mapping`)
- **Tests:** `test_stack.py` (extend) — `cdk synth` adds **no new resource** vs. slice 3
  (resource-count/Budgets assertion unchanged); the Budgets value stays the literal it was.
  (The `visibility` keyword mapping is unit-asserted in T4's `test_store_opensearch.py`.)
  `# STUB: AC8`, `stub: true`.
- **Approach:** confirm the stack is untouched (persona rides the existing Lambda; IAM data
  actions already cover read/write); add/extend the synth assertion that the slice adds no
  resource and the Budgets value is unchanged. Note in the test/Rollout that the
  `visibility` keyword field lands only on a **fresh** index (`create_index` tolerates
  already-exists and does not migrate a live domain's mapping) — re-deploy over a
  non-destroyed index is out of scope (teardown-first rebuild; slice-5 delta boundary).
- **Done when:** `test_stack.py` green; `cdk synth` clean (AC8).

### T9 — Showcase permission queries + presenter narration
- **Depends on:** T5
- **Touches:** packages/graphrag/src/graphrag/showcase/queries.yaml, packages/graphrag/src/graphrag/showcase/__init__.py, packages/graphrag/tests/test_showcase.py, docs/guides/tutorials/three-mode-demo.md
- **Tests:** `test_showcase.py` (extend) — the new permission-filtered queries parse,
  carry a `persona` + an expected visible/filtered split, and every gold id resolves in the
  fixture; the loader exposes the persona field. `# STUB: AC10`, `stub: true`.
- **Approach:** add permission queries to `queries.yaml` (a `persona` + `filtered`/visible
  gold); extend `load_showcase`/the `ShowcaseQuery` shape; add a two-persona section to the
  presenter script with the exact `compare --persona …` commands and the
  "synthetic-stand-in-for-ACLs, not real authz" framing.
- **Done when:** `test_showcase.py` green; the presenter script walks the contrast (AC10).

### T10 — Live two-persona deploy smoke (supervisor's step)
- **Depends on:** T2, T7, T8
- **Tests:** live — `scripts/deploy.sh`; run the labeled Fargate dual-write; SigV4-POST the
  same entity-led question with `persona=public-reader` then `persona=maintainer`; assert
  the restricted entity is absent for the reader and present for the maintainer, each with
  its filtered-out trace; `scripts/destroy.sh`. *(live smoke — AC9)*
- **Approach:** deploy the updated stack; dual-write with labels; sign + POST both personas;
  record the two JSON results + teardown in `deployment-and-verification.md`. Deferred to
  `permission-filtered-retrieval-live-deploy` if AWS creds are unavailable in-loop.
- **Done when:** the two live calls diverge as specified and the stack is destroyed (AC9).

### T11 — Docs + capture-learnings + spec tick
- **Depends on:** T1-T10
- **Tests:** n/a (docs).
- **Approach:** update `docs/architecture/overview.md` (new `visibility`/`labels` modules +
  the permission filter), `docs/architecture/security.md` (the synthetic-label boundary;
  the edge-during-traversal leak guard; the teaching-stand-in posture; **the
  trusted-scoped-principal Function-URL ingress as the boundary that contains the
  filtered-out-ID trace disclosure and makes the default-unrestricted posture safe — not
  just the synthetic-label framing**),
  `docs/architecture/infrastructure.md` (the `visibility` mapping field; no new resource),
  `docs/architecture/deployment-and-verification.md` (the two-persona live row),
  `docs/specs/README.md` (status), `docs/product/changelog.md`; add knowledge entries to
  `docs/knowledge/patterns.jsonl` (the post-filter-leak antipattern; the
  most-restrictive-wins composition); record the no-new-dependency/no-new-infra outcome +
  the `visibility`/`labels` modules in `packages/graphrag/AGENTS.md`; tick the spec's met
  ACs and set Status `Shipped` — **valid only with AC9's `(deferred: …)` marker live and the
  `permission-filtered-retrieval-live-deploy` anchor present in `docs/backlog.md`** (added
  in this PR; CONVENTIONS § 4 metadata contract).
- **Done when:** docs match the code; spec ACs ticked (AC9 deferral marker + backlog anchor
  in place); gates green.

## Rollout

Per the design doc's phased rollout, slice 4 extends the **same** IaC stack with **no new
resource**:

- **Provisions:** nothing new — the persona rides the existing query Lambda's request body;
  the only store change is the OpenSearch `visibility` keyword field, applied at
  `create_index` on a fresh deploy.
- **Standing cost:** **none new** — the Budgets value is unchanged (T8 asserts it).
- **Deploy:** `cdk deploy`; run the labeled Fargate dual-write; the Function URL serves the
  permission-filtered query (persona in the body). **Destroy:** `cdk destroy` removes every
  billable resource (unchanged from slice 3).
- **Rollback:** `destroy` + redeploy; state reproducible from the S3 snapshot + the packaged
  `labels.yaml` — no migration, no irreversible step (ADR-0002).
- **Deployment sequencing:** the labeled dual-write must run before a persona-scoped query
  (the labels must be in the stores); CDK dependency order is otherwise unchanged.

## Risks

- **Edge filter implemented as a post-filter by mistake (the leak).** The whole slice fails
  its teaching point if the filter slips to the final node set. *Mitigation:* the AC3
  restricted-intermediate fixture asserts a node reachable *only* via a forbidden node is
  unreachable — a post-filter would surface it, so the test catches the regression.
- **Backend trace divergence under filtering.** The Neptune WHERE override could return a
  different filtered set/order than the in-memory fan-out. *Mitigation:* keep the
  reached-set sort; assert in-memory ≡ Neptune filtered set in `test_store_neptune.py`.
- **A stray `import yaml` on the query path** (e.g. importing `labels` from `hybrid`).
  *Mitigation:* `labels` is ingest-path only; `visibility` is pure; the PyYAML-free guard
  test stays green (T1/T7).
- **Slices 1–3 regressions from the new seam parameters.** *Mitigation:* every new
  parameter defaults to `None`/unrestricted; existing tests must stay green unchanged.
- **Showing filtered IDs reads as a leak.** *Mitigation:* the trace explicitly labels the
  filtered-out line a teaching aid a real ACL system would not expose (charter principle 5);
  named in the CLI/docs.
- **Label fairness/teaching realism is a judgment call.** *Mitigation:* labels are explicit
  data in `labels.yaml`, drawn from the real fixture; the adversarial reviewer checks the
  picks; the two-persona contrast is asserted structurally.

## Changelog

- 2026-06-24 — Initial plan (slice 4). Thread `visibility` through ingest (label both
  stores) and query (a `Clearance` from a `persona`, opt-in, defaulting unrestricted); the
  load-bearing AC3 during-traversal edge filter front-loaded with a restricted-intermediate
  leak fixture; no new runtime dependency, no new infra resource; labels a teaching
  stand-in for ACLs throughout.
