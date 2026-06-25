# Spec: permission-filtered-retrieval

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Shape:** mixed
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [charter](../../CHARTER.md) (principle 5 — *synthetic stays labeled synthetic*; Architecture-pattern 7 — *permission-filtered retrieval, applied during traversal on edges*), [design doc](../../architecture/graphrag-aws-architecture/design.md) (D1 — the *Permission-filtered retrieval* paragraph; D2 — the in-VPC query Lambda topology this reuses), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (the seed-and-expand path this filtering rides), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (the stores/Lambda topology), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python)
- **Brief:** [`docs/product/briefs/graphrag-aws-demo.md`](../../product/briefs/graphrag-aws-demo.md)
- **Contract:** none (internal Python interfaces + a `persona` field added to the existing in-VPC query Lambda's request body; no repo-root `contracts/` API surface, consistent with slices 1–3)

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> Slice 4 of the brief's Spec map — the first of the two enterprise-concern
> slices. It rides the **same stores and query path** the three-mode core ships
> ([`graph-ingestion-resolution`](../graph-ingestion-resolution/spec.md) slice 1,
> [`vector-rag-baseline`](../vector-rag-baseline/spec.md) slice 2,
> [`hybrid-orchestration`](../hybrid-orchestration/spec.md) slice 3) — no parallel
> pipeline. `Depends on:` slices 1–3 (reuses the `GraphStore`/`VectorStore` seams,
> `expand_neighborhood`/`neighbors_batch`, `vector_search`, `hybrid_query`,
> `run_modes`, the chunk→entity-ID metadata, the dual-write ingest path, and the
> in-VPC query Lambda behind the IAM-auth Function URL).

## Objective

A solution architect evaluating GraphRAG needs to *see* the enterprise concern that
quietly blocks real RAG — *who can see what* — answered on the same stores and the
same query path as the three retrieval modes, without a parallel pipeline. This slice
attaches **synthetic visibility labels** to the corpus at ingest, **propagates them to
both stores** (Neptune node *and* edge properties; OpenSearch chunk metadata), accepts
a **persona/clearance** on the query, and returns **permission-filtered retrieval across
all three modes** (vector, graph, hybrid) — each with the trace that names what was
filtered out, so the mechanism is narratable, never a black-box hop.

The load-bearing teaching point is *where* authorization rides the retrieval path: the
Neptune filter is applied **during traversal, on edges** — not as a post-filter on the
final node set. A post-filter leaks: a forbidden node still enters the frontier, still
appears in the hop trace, and can still bridge to a node that is *only* reachable
through it. Applying the filter on the edge during the hop means a forbidden node never
enters the frontier at all. A `public-reader` persona asking an entity-led question gets
an answer scoped to what it may see; a `maintainer` asking the same question sees the
restricted entities the reader could not — the divergence is visible side by side and
reproducible on a clean deploy. The labels are a **teaching stand-in for real ACLs**,
presented as such everywhere they surface — never dressed up as production authz.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule, even under time pressure.

### Always do

- **Apply the graph filter DURING traversal, on edges — never only on the final node
  set.** A forbidden node must never enter the frontier, never appear in the hop trace,
  and never serve as an intermediate hop to reach another node. This is the slice's
  load-bearing correctness property (charter pattern 7; design D1): the filter lives in
  the `neighbors`/`neighbors_batch` hop, so a node reachable *only* through a forbidden
  node is also unreachable for a persona who may not see the forbidden node.
- **Propagate labels to BOTH stores at ingest.** Visibility rides Neptune **node and
  edge** properties *and* OpenSearch chunk metadata, written from the **same** dual-write
  pass the corpus is already ingested through (charter pattern 2 — the stores never
  diverge). One label source feeds both, derived via the slice-1 entity IDs.
- **Compose visibility most-restrictive-wins (max).** A derived label is the
  most-restrictive of its inputs: an edge's visibility is the max of its two endpoint
  nodes' visibility; a chunk's visibility is the max of its owning entities' visibility.
  Anything unlabeled defaults to the least-restrictive tier (`public`). This is what
  makes "the edge is traversable" equivalent to "both endpoints are visible."
- **Filter all three modes by the persona's clearance.** Vector-only filters the k-NN
  result set by visibility (an OpenSearch metadata `filter` applied *during* the ANN
  search, and the in-memory equivalent); graph-only and hybrid filter seeds, traversal
  edges, and the merged node set. A persona may not see, in any mode, a chunk/node/edge
  above its clearance.
- **Keep the persona/clearance resolution PyYAML-free on the query path.** The
  persona→allowed-levels mapping and the tier ordering are pure-Python constants
  (importable by the in-VPC query Lambda); only label *assignment* (which reads the
  packaged `labels.yaml`) runs on the ingest path (Fargate / CLI), where PyYAML is
  available. The `test_query_lambda_import_graph_is_pyyaml_free` invariant stays green
  (`packages/graphrag/AGENTS.md`).
- **Keep the Neptune filter parameterized.** Allowed-label values ride the openCypher
  `parameters` map (`$allowed`), never string-interpolated into the query; the single
  `REL` relationship type and the `Entity` label stay fixed constants (slice-1 security
  posture; `store/neptune.py`). The `ruff` `S` ruleset stays enabled.
- **Label every surfacing of the construct "synthetic / not real authz."** The trace,
  the CLI output, the presenter script, and the docs all present the labels and personas
  as a teaching stand-in for ACLs — never as production IAM / multi-tenancy / data authz
  (charter principle 5; design D1/Non-goals).
- **Default to unrestricted when no persona is supplied — as a labeled *teaching*
  posture, not a silent fail-open.** With no `--persona` / no `persona` in the request
  body, retrieval is unfiltered (slice-1–3 behavior and tests unchanged; filtering is
  strictly opt-in). This "absent persona ⇒ full access" default is the textbook fail-open
  that a *real* ACL system must invert (default-deny) — it is safe **here only** because
  the labels are explicitly non-authz **and** the sole query ingress is the IAM-auth,
  scoped-principal Function URL (the caller is the trusted deploying/CLI role, not an
  authenticated end-user). The `visibility` module and the docs name this so the seam is
  never copied into a context where the persona is the security principal and the
  fail-open is inherited.
- **Keep teardown a feature** (charter principle 4): this slice adds **no** billable
  resource — the persona rides the existing query Lambda's per-request body and the only
  store change is the OpenSearch index mapping, applied at `create_index` on a fresh
  deploy. The Budgets value is unchanged.

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** This slice needs none (the
  filter is a WHERE clause + a terms filter over the existing adapters); reach for any
  new dependency only with sign-off and record it in `packages/graphrag/AGENTS.md`.
- **Changing the visibility tier set, the persona set, or the most-restrictive-wins
  composition rule.** The teaching model (3 tiers `public < internal < restricted`; 3
  personas `public-reader` / `member` / `maintainer`; max-composition) is the demo's
  pedagogy; changing its shape is a decision to surface.
- **Changing the query Lambda's request/response contract** (the new `persona` field, or
  the filtered-out trace schema) once a downstream (slice 5 delta) or the CLI consumes it.
- **Surfacing the *identity* of filtered-out items beyond the trusted ingress.** The
  teaching trace names filtered IDs (an enumeration oracle in a real ACL system) as an
  observability aid, with an explicit "a real ACL system would not reveal this" note. This
  is contained **only** because the filtered-out trace crosses the IAM-auth,
  scoped-principal Function URL to the trusted deploying/CLI role — *never* to the persona
  as an authenticated end-user. Broadening what the trace exposes, or wiring the
  filtered-ID trace to a less-trusted caller (a multi-tenant fork, an end-user-facing
  endpoint), needs sign-off; `docs/architecture/security.md` records the trusted-caller
  containment as the boundary, not just the synthetic-label framing.

### Never do

- **Never present the synthetic labels as real authorization.** No "permission-filtered
  retrieval" copy that implies production ACLs, IAM, multi-tenancy, or data authz
  (charter principle 5; charter Scope "does not"; design D1 Non-goals). It is a stand-in,
  always labeled as one.
- **Never post-filter the graph result as the *only* enforcement.** A final-node-set
  filter without the during-traversal edge filter is the leak this slice exists to
  prevent — it is a hard rule, not a perf trade.
- **Never let the offline non-semantic embedder/synthesizer back a quality claim.** As in
  slice 3, the offline path proves *structure* (the right items are present/absent for a
  persona); semantic quality is the live path / frozen-vector eval.
- **Never add a new top-level directory or module boundary beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces**
  (AGENTS.md: top-level directories need an RFC). New code lands as modules inside those.
- **Never break the backend-identical trace.** The during-traversal filter is added to
  the `neighbors`/`neighbors_batch` seam such that the in-memory and Neptune backends
  produce an **identical** filtered trace (the slice-1/3 invariant — sort the reached
  set, don't rely on store order).
- **Never implement incremental delta re-ingest** — that is slice 5. Ingestion here is a
  full, idempotent dual-write with labels added; the delta/orphan-removal path is
  unchanged from slice 3.

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per behavior:

- **AC1 (label model + persona resolution) — TDD.** Pure functions over constants:
  the tier ordering, `resolve_clearance(persona)`, `allows(level)`, and the
  most-restrictive-wins `compose(*levels)` are deterministic and trivially unit-tested;
  no store, no network.
- **AC2 (label assignment at ingest, both stores) — TDD.** Over the fixture corpus, the
  labeling pass stamps node `visibility`, edge `visibility = max(endpoints)`, and chunk
  `visibility = max(owning entities)` from the packaged `labels.yaml` (default `public`);
  asserted against in-memory stores and the chunk list (no network).
- **AC3 (during-traversal edge filter — the leak guard) — TDD.** The load-bearing
  correctness AC. Over a fixture graph with a restricted intermediate node,
  `expand_neighborhood(..., clearance=public_reader)` **never** reaches the restricted
  node at any hop, **and** never reaches a node that is reachable *only* through it,
  while `clearance=maintainer` reaches both — proving the filter is on the edge during
  the hop, not a post-filter. Backend-identical: the in-memory fan-out and the Neptune
  WHERE-clause override produce the same filtered reached set (the Neptune path asserted
  via the adapter's mock HTTP client, checking `$allowed` rides the parameters map).
- **AC4 (vector filter during k-NN) — TDD.** The in-memory vector store filters hits by
  allowed levels; the OpenSearch adapter issues a `bool` query with a `filter: terms
  visibility` alongside the `knn` clause (asserted via the adapter's mock HTTP client —
  the filter is in the request body, parameterized).
- **AC5 (three modes filtered + filtered-out trace) — TDD.** `hybrid_query` and
  `run_modes` accept a `clearance`; over the fixture, a `public-reader` persona's
  vector/graph/hybrid results exclude every restricted item and the trace renders a
  `clearance:` line + a `filtered (visibility):` line; a `maintainer` persona's results
  include them. The seed-source attribution and the bounded-trace invariants from slice 3
  still hold under filtering.
- **AC6 (CLI `--persona`) — TDD.** `graphrag compare --persona public-reader` /
  `hybrid-query --persona …` (offline) print the persona, the clearance, and the
  filtered-out trace; an unknown persona fails loudly; **no `--persona` = unrestricted**
  (slice-3 output unchanged). The synthetic-stand-in label appears in the output.
- **AC7 (query Lambda persona) — TDD with mock.** `lambda_handler` reads an optional
  `persona` from the request body, resolves it via the pure-Python constants (no YAML
  import), runs the filtered `hybrid_query`, and returns the filtered result + trace;
  an unknown persona returns a sanitized 4xx-shaped envelope; the PyYAML-free import test
  stays green.
- **AC8 (OpenSearch mapping + no new infra) — goal-based (`cdk synth` + the existing
  stack test).** The OpenSearch k-NN mapping carries a `visibility` keyword field; the
  stack synthesizes **no new resource** for this slice and the Budgets value is unchanged
  (the persona rides the existing Function URL; IAM data actions already cover read/write).
- **AC9 (live two-persona smoke) — active end-to-end, deferred to the supervisor.**
  Against the deployed stack with the labeled corpus dual-written, a SigV4 call with
  `persona=public-reader` and one with `persona=maintainer` return divergent
  permission-filtered answers over the same entity-led question (the restricted entity
  absent for the reader, present for the maintainer), then the stack is destroyed.
- **AC10 (showcase + presenter narration) — goal-based.** The showcase set gains
  permission-filtered queries (a persona + the expected filtered/visible split); a
  loader/test asserts they parse and every gold id resolves in the fixture; the presenter
  script walks the two-persona contrast with the exact CLI commands and the
  synthetic-stand-in framing.

Gates: `ruff` (lint+format, `S` security ruleset), `mypy` (typecheck), `pytest` (tests).
Already wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [x] **AC1 — Visibility model + persona/clearance resolution (pure, PyYAML-free).** A
  `graphrag.visibility` module defines an **ordered** tier set `public < internal <
  restricted`; `compose(*labels)` returns the **most-restrictive** (max) of its inputs
  (empty → `public`); a `Clearance` carries a persona name + the set of allowed tiers;
  `resolve_clearance(persona)` maps each of the three personas — `public-reader` (sees
  `public`), `member` (sees `public`,`internal`), `maintainer` (sees all) — to its
  `Clearance`, and an unknown persona raises **`ValueError`** (one type, so the CLI and
  Lambda catch a known exception). `Clearance.allowed` is **downward-closed** (a persona
  sees every tier at or below its level) and `Clearance.allows(label)` is true iff the
  label is within it. **The `None`-vs-empty distinction is fail-closed:** only a literal
  `clearance=None` means *unrestricted* (the opt-out default); a `Clearance` with an
  **empty** `allowed` set filters **everything** (sees nothing) and never falls through to
  unrestricted — so a degenerate clearance can only narrow access, never widen it. The
  module imports **no `yaml`** (importable by the query Lambda). *(TDD)*
- [x] **AC2 — Labels assigned at ingest and propagated to BOTH stores.** A labeling pass
  (reading the packaged `labels.yaml`, an entity-id→tier map, default `public`) stamps,
  from the **same** dual-write the corpus is ingested through: every Neptune **node** with
  a `visibility` property; every Neptune **edge** with `visibility = compose(src, dst)`;
  every OpenSearch **chunk** with `visibility = compose(owning entity ids)`. The fixture
  corpus resolves with at least one `restricted` and one `internal` entity so the
  filtering bites. Verified against in-memory stores + the chunk list. *(TDD)*
- [x] **AC3 — Graph filter applied DURING traversal, on edges (the leak guard).**
  `expand_neighborhood(store, seeds, *, max_hops, frontier_cap, clearance)` and the
  `GraphStore.neighbors`/`neighbors_batch` seam exclude, **during each hop**, any edge
  whose `visibility` is outside the clearance — so a forbidden node **never enters the
  frontier**, never appears in the hop trace, and **never bridges** to a node reachable
  only through it. Both backends apply the **same** predicate — `edge.visibility ∈
  allowed` **and** `neighbor.visibility ∈ allowed` — where the edge is in scope (the
  in-memory store inspects the edge it is already iterating; the Neptune override adds a
  parameterized `WHERE r.visibility IN $allowed AND b.visibility IN $allowed`). Because an
  edge's visibility is `compose(src, dst) = max(src, dst)` and `allowed` is
  downward-closed, `edge.visibility ∈ allowed` holds iff **both** endpoints are within
  clearance — so the edge predicate *is* the node guarantee, and the two backends compute
  an identical filtered reached set (sorted; backend-identical). The `neighbor.visibility ∈
  allowed` conjunct is therefore logically redundant given correctly-composed edge labels;
  it is kept as a deliberate defensive guard against a stale edge label (e.g. a re-ingest
  whose `Graph.upsert_edge` `setdefault` merge kept a pre-existing `visibility` prop), not
  as a requirement of the equivalence. Over a fixture with a
  **restricted intermediate** R (seed → R → B, where B is reachable only via R):
  `public-reader` reaches **neither** R nor B at any hop — a result a final-node-set
  post-filter would get wrong (it would still surface B) — while `maintainer` reaches
  both. A forbidden **seed** is dropped and recorded (distinct from slice-3's
  unconfirmed-candidate drop). The leak-correctness check runs against the **in-memory**
  store (the only backend where traversal logic executes locally); the **Neptune**
  assertion is a *parameterization/shape* check — `$allowed` rides the openCypher
  parameters map (never interpolated) and the `WHERE` is present — verified via the
  adapter's mock HTTP client, and is explicitly **not** a substitute for the in-memory
  leak proof. `clearance=None` = unrestricted (slice-3 behavior unchanged). *(TDD)*
- [x] **AC4 — Vector filter applied during k-NN.** `vector_search(..., clearance)` and the
  `VectorStore.knn(vector, k, *, allowed_labels)` seam return only chunks within
  clearance: the in-memory store filters by `chunk.visibility`; the **OpenSearch** adapter
  issues a `bool` query pairing the `knn` clause with a `filter: {terms: {visibility:
  [...allowed]}}` so the filter is applied **during** the ANN search (parameterized in the
  request body — asserted via the adapter's mock HTTP client). `clearance=None` =
  unfiltered. *(TDD)*
- [x] **AC5 — All three modes permission-filtered, with a filtered-out trace.**
  `hybrid_query(..., clearance)` and `run_modes(..., clearance)` thread the clearance into
  **each** mode — vector search (AC4), seed selection, traversal (AC3), **and** the final
  merged node set — so a persona's vector-only / graph-only / hybrid results **each**
  exclude every item above its clearance (vector-only must filter too, or it leaks
  restricted chunks the other two modes drop — a per-mode divergence this AC forbids). The
  final `HybridResult.graph_nodes` is asserted to contain **no** node above clearance,
  independent of the seed/edge filters (so a node re-materialized by id in the merge can't
  reintroduce a restricted node). The rendered trace gains a `clearance:` line (persona +
  allowed tiers) and a `filtered (visibility):` line naming the filtered seeds/chunks **as
  a teaching observability aid, explicitly noted as something a real ACL system would not
  reveal**. Over the entity-led exemplar, a `public-reader` and a `maintainer` produce
  **divergent** result sets; the slice-3 dual-seed/bounded-trace invariants still hold.
  *(TDD)*
- [x] **AC6 — CLI `--persona` across the query verbs, offline by default.** `graphrag
  hybrid-query`/`compare`/`vector-query`/`graph-query` accept `--persona <name>`; offline
  runs print the persona, the resolved clearance, and the filtered-out trace, labeling the
  construct a synthetic stand-in for ACLs (not real authz); an unknown persona exits
  non-zero with a clear message; **no `--persona` leaves output byte-identical to slice
  3** — the offline corpus is labeled (so `--persona` can filter), but visibility is
  **inert** (never rendered, never filtered) without a persona, asserted by a no-persona
  regression test. *(TDD)*
- [x] **AC7 — Query Lambda accepts a persona, stays PyYAML-free.**
  `graphrag.query_lambda.lambda_handler` reads an optional `persona` from the request body,
  resolves it via the pure-Python `visibility` constants, runs the **filtered**
  `hybrid_query`, and returns the filtered `{answer, citations, trace, seeds, hops}`
  (the trace carrying the clearance + filtered-out line); an **unknown persona** returns a
  generic sanitized envelope (correlation id, no internal detail); **no persona** =
  unrestricted. Exercised with the embedder, both stores, and the synthesizer **mocked**;
  the `test_query_lambda_import_graph_is_pyyaml_free` invariant stays green. *(TDD with
  mock; live in AC9)*
- [x] **AC8 — OpenSearch mapping carries `visibility`; no new infra; cost unchanged.** The
  k-NN index mapping (`store/opensearch.py` `_knn_mapping`) declares `visibility` as a
  `keyword` field (so the terms filter is exact-match), created at `create_index` on a
  **fresh** index. `cdk synth` adds **no new resource** for this slice and the Budgets
  value is **unchanged** (the persona rides the existing query Lambda; Neptune/OpenSearch
  IAM data actions already cover read/write). The `visibility` field lands only on a fresh
  index (teardown-first rebuild — `create_index` tolerates already-exists and does **not**
  migrate a live domain's mapping); re-deploy over a non-destroyed index without a
  re-create is explicitly out of scope (matches the slice-5 delta boundary). *(goal-based
  synth, CDK-env-gated)*
- [x] **AC9 — Live two-persona permission-filtered smoke (in-VPC).** Against the deployed
  stack with the labeled corpus dual-written, a SigV4-signed Function-URL call with
  `persona=public-reader` and one with `persona=maintainer` return **divergent**
  permission-filtered answers over the same entity-led question — the restricted entity
  **absent** for the reader and **present** for the maintainer, each with its filtered-out
  trace — then the stack is destroyed (teardown-first). **Verified live (2026-06-24):** for
  *"What KEPs does SIG Node own?"*, `public-reader` reached `kep-2086` but **not** the
  restricted `kep-1287` (its `OWNS` edge filtered during the hop, so its approvers were
  unreachable too), answer citing only KEP-0009; `maintainer` reached `kep-1287` + `kep-1880`
  and its hop-2 approvers, answer citing KEP-1287 — full trace in
  `deployment-and-verification.md`; stack torn down. The live run surfaced + fixed one
  packaging bug (`labels.yaml` missing from `[tool.setuptools.package-data]`, now declared +
  regression-tested). *(live smoke)*
- [x] **AC10 — Showcase + presenter narration for the two-persona contrast.** The
  consolidated showcase set gains permission-filtered queries, each labeled with a persona
  and the expected visible/filtered split; a loader/test asserts they parse and every gold
  id resolves in the fixture corpus. The presenter script (`docs/guides/`) walks the
  `public-reader` vs `maintainer` contrast with the exact CLI commands and the explicit
  "synthetic stand-in for ACLs, not real authz" framing. *(goal-based)*

## Assumptions

- Technical: runtime is Python 3.11+; runtime deps stay `pyyaml` + `boto3>=1.35`; gates
  are `ruff` (`S` ruleset) / `mypy` / `pytest`; this slice adds **no** runtime dependency
  (source: `pyproject.toml`; `packages/graphrag/AGENTS.md`).
- Technical: labels propagate to both stores from the existing dual-write — graph via
  `ingest()`/`apps/ingestion/entrypoint.py`, vector via `_vector_dual_write` — which
  derive entity IDs identically, so one `labels.yaml` (entity-id→tier) feeds both
  (source: `apps/ingestion/entrypoint.py`; `packages/graphrag/src/graphrag/ingest.py`).
- Technical: the OpenSearch k-NN mapping is created by app code (`_knn_mapping` in
  `store/opensearch.py`), not CDK, so the `visibility` keyword field is an app change
  applied at `create_index` on a fresh deploy — teardown-first rebuild means no migration
  (source: `store/opensearch.py:143`; design doc D2 rollout).
- Technical: no new CDK/infra resource is needed — the persona rides the existing query
  Lambda's per-request body, and Neptune/OpenSearch IAM data actions already grant
  read/write (source: `apps/infra/stacks/graphrag_stack.py`).
- Technical: the query path (`hybrid`/`compare`/`query_lambda`) is PyYAML-free and a test
  enforces it, so persona→clearance resolution is a pure-Python constant module and label
  *assignment* (reads `labels.yaml`) runs only on the ingest path (source:
  `tests/test_query_lambda.py:188`; `packages/graphrag/AGENTS.md`).
- Technical: the Neptune adapter is parameterized-openCypher-only with a single `REL`
  type, so the during-traversal filter is a `WHERE … IN $allowed` with `$allowed` on the
  parameters map (source: `store/neptune.py`).
- Product: the teaching model is 3 ordered tiers (`public < internal < restricted`) and 3
  personas (`public-reader` / `member` / `maintainer`) with most-restrictive-wins
  composition; the labels are a stand-in for ACLs, never real authz (source: charter
  principle 5 + pattern 7; design D1; task brief 2026-06-24).
- Product: the trace surfaces filtered-out item IDs as a teaching observability aid,
  explicitly noted as something a real ACL system would not reveal to the requester
  (source: design D1 "the trace shows what was filtered out"; task brief 2026-06-24).
- Process: no new ADR — the load-bearing decision (filter on edges during traversal;
  labels on node/edge props + OpenSearch metadata) is already pinned by charter pattern 7
  + design D1; the tier/persona/composition model is slice-level LLD in `plan.md` (source:
  `docs/CHARTER.md:159-164`; `design.md:208-213`; task brief 2026-06-24).
- Process: scope is the **fixed** permission filter (a visibility terms filter during
  ANN); the LLM **self-query** metadata-filtering is the separate `metadata-filtering`
  example in the pattern-catalog brief — out of scope here (source: charter coverage
  table; `docs/product/briefs/graphrag-pattern-catalog.md`).
- Process: full work-loop mode — security boundary (permission filtering; network I/O;
  untrusted retrieved content → Claude) + structural (new module) + constrained by the
  charter + design doc; derived from brief `graphrag-aws-demo.md` (source:
  `docs/CONVENTIONS.md` risk triggers; brief Spec map row 4).
- Process: the live two-persona AC (AC9) is the supervisor's step; the offline build is
  the work-loop deliverable, with AC9 deferred to `permission-filtered-retrieval-live-deploy`
  if AWS creds are unavailable (mirrors slice-3 T10) (source: slice-3 plan; `docs/backlog.md`).

## Changelog

- 2026-06-24 — Spec authored (slice 4). Synthetic visibility labels → both stores
  (Neptune node + edge props, OpenSearch chunk metadata) → persona/clearance on the query
  → permission-filtered retrieval across all three modes, with the Neptune filter applied
  **during traversal on edges** (the leak guard) and the trace naming what was filtered.
  No new runtime dependency, no new infra resource; labels are a teaching stand-in for
  ACLs throughout.
