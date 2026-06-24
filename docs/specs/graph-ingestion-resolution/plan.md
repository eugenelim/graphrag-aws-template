# Plan: graph-ingestion-resolution

- **Plan status:** Executing
- **Spec:** [`spec.md`](spec.md)

## Strategy

Build inside-out: the **model** first, then **parse → extract → resolve** as a
pure-Python pipeline over a bundled fixture corpus, then the **graph store**
abstraction (in-memory first, Neptune adapter second), then **traversal + CLI**,
then the **resolver eval**, and finally the **IaC**. Every stage is deterministic
and offline-testable except the live AWS deploy, which ships as code + procedure
and defers its live verification (AC9).

The load-bearing design choice for reproducibility and narratability: **the
multi-hop traversal runs in the application layer over a `neighbors()` primitive
both stores implement**, rather than being pushed into openCypher. That keeps the
in-memory and Neptune backends behaviorally identical (so the demo trace is the
same offline and deployed) and keeps the whole graph half testable without a live
cluster. Pushing traversal into openCypher is a slice-3 optimization, explicitly
out of scope here (a Boundaries rail).

## Design (LLD)

### Design decisions

- **`neighbors()` as the cross-backend primitive** (above). Trace identity across
  backends is the reproducibility contract; openCypher pushdown is deferred.
- **Stable node IDs are the resolution mechanism.** A node's ID *is* its
  normalized key (`sig:<slug>`, `person:<handle>`, `kep:<number>`,
  `subproject:<sig-slug>/<name>`). Two mentions that normalize to the same ID are
  the same node — resolution is "upsert by normalized ID," not a post-hoc merge
  pass. This is why it stays narratable: the merge is visible as "these two source
  rows produced the same ID."
- **Alias table is data, not code** (`aliases.yaml`): `display-name → canonical
  handle`. Applied during Person normalization. Hand-authored, small, reviewed.
- **AWS CDK (Python) for IaC** — ADR-0003. Single language repo-wide; `deploy`/
  `destroy` map to `cdk deploy`/`cdk destroy`; constructs make the VPC-endpoint
  set legible.

### Data & schema (satisfies AC1–AC4)

- `graphrag.model`: `EntityKind`/`EdgeKind` enums; `Node(id, kind, props,
  sources)`; `Edge(src_id, dst_id, kind, props, sources)`; `Graph` (id-keyed node
  map + edge list, with `upsert_node`/`upsert_edge` that *union* `sources` and
  `props` on collision — that union is the resolution merge).
- Normalization: `normalize_handle("@Thockin") -> "thockin"`;
  `normalize_slug("SIG Network"/"sig-network") -> "sig-network"`. Alias lookup
  precedes handle normalization for prose names.

### Interfaces & contracts (satisfies AC6, AC7)

- `graphrag.store.base.GraphStore` (ABC): `upsert_node`, `upsert_edge`,
  `get_node`, `neighbors(node_id, edge_kind, direction) -> list[Node]`,
  `all_nodes`/`all_edges` (for ingest summary), `close`.
- `graphrag.store.memory.MemoryGraphStore` — wraps a `Graph`.
- `graphrag.store.neptune.NeptuneGraphStore` — openCypher over HTTPS with SigV4
  (botocore `SigV4Auth`); `upsert_*` via `MERGE`, `neighbors` via a
  **parameterized** `MATCH`. Endpoint + region injected; HTTP client injectable
  for the mock test.
- `graphrag.query.traverse(store, seed_ids, steps, max_hops) -> TraversalResult`
  where `steps` is a list of `(EdgeKind, direction)`; result carries the ordered
  `TraceEntry` list (seed list, per-hop frontier, resulting nodes) for printing.

### State & control flow (satisfies AC1, AC9)

- `graphrag.ingest.ingest(community_path, enhancements_path, store) ->
  IngestReport`: load → extract → upsert into store; returns counts +
  sample-merge list for the narration. Idempotent (upsert by ID); no delete pass
  (delta is slice 5).
- `apps/ingestion/entrypoint.py`: Fargate entry — resolve corpus snapshot from S3
  (env `CORPUS_BUCKET`/`CORPUS_PREFIX`) into a temp dir, build a
  `NeptuneGraphStore` from env (`NEPTUNE_ENDPOINT`), call `ingest`, print report.

### Failure, edge cases & resilience (satisfies AC1, AC4, AC7)

- Missing/garbled front-matter → skip the doc with a logged warning, never crash
  the run (a real corpus has messy docs — de-risk verdict noted pre-`kep.yaml`
  KEPs carry prose-only metadata).
- A KEP whose `owning-sig` slug has no matching SIG node → create the edge to a
  thin SIG node (forward reference), logged; resolution still single-nodes it when
  the SIG is seen.
- Neptune adapter: non-2xx → raise with the response body in the message (loud,
  per ADR-0002's "fail loudly, not silent timeouts"); retries are out of scope.

### Quality attributes (NFRs) (satisfies AC10)

- Narratability is a tested NFR (AC10), not a nicety: each CLI verb prints a
  structured, human-readable trace. The traversal trace is the observability
  surface the design doc calls out.

### Dependencies & integration (satisfies AC8)

- Runtime: `pyyaml` (parse), `boto3`/`botocore` (SigV4 for Neptune; already a
  transitive AWS need). Recorded in `packages/graphrag/AGENTS.md`.
- `infra` extra: `aws-cdk-lib`, `constructs`. Dev: `pytest`, `ruff`, `mypy`.
- Integration points: S3 (corpus snapshot), Neptune (graph store), ECR (Fargate
  image), CloudWatch logs — all via VPC endpoints (no NAT), per ADR-0002.

## Rollout

Per the design doc's phased rollout, this slice deploys the **slice-1 subset** of
the ADR-0002 stack behind the same IaC app:

- **Provisions:** VPC (private subnets, no NAT) + endpoints (`s3`, `ecr.api`,
  `ecr.dkr`, `logs`, `sts`) + Neptune Serverless (min capacity) + S3 snapshot
  bucket + Fargate ingestion task def + Budgets alarm.
- **Deliberately deferred to later slices:** `bedrock-runtime` endpoint,
  OpenSearch, the query Lambda + Function URL (slices 2–3 add them to the same
  stack).
- **Deploy:** `cdk deploy` provisions, uploads the corpus snapshot, runs the
  ingestion task once. **Destroy:** `cdk destroy` removes every billable resource.
  Live verification deferred (AC9 → backlog).
- **Rollback:** `destroy` + redeploy; state is reproducible from the S3 snapshot —
  no migration, no irreversible step (ADR-0002).

## Tasks

> Tests come before Approach in each task (tests drive the build). TDD tasks
> carry a red **stub** marked `# STUB: AC<n>`.

### T1 — Project scaffold + model + normalization
- **Depends on:** none
- **Tests:** `test_model.py` — `Graph.upsert_node` unions `sources`/`props` on
  ID collision (the resolution-merge primitive); `normalize_handle` /
  `normalize_slug` cases. `# STUB: AC4`, `stub: true`.
- **Approach:** root `pyproject.toml` (package `graphrag`, src layout under
  `packages/graphrag/src`, ruff/mypy/pytest config, `infra`/`dev` extras);
  `graphrag.model`; `graphrag.normalize`.

### T2 — Source loading + parsing
- **Depends on:** T1
- **Tests:** `test_parse.py` — over the fixture, `sigs.yaml` and a SIG `README.md`
  parse into typed records with provenance; a doc with broken front-matter is
  skipped with a warning, not a crash; **a fixture containing a `!!python/object`
  tag parses inert** (no object construction) via `yaml.safe_load` (security
  negative test). `# STUB: AC1`, `stub: true`.
- **Approach:** `graphrag.sources` (locate community/enhancements files);
  `graphrag.parse` (YAML via `yaml.safe_load` + Markdown front-matter/headings).
  Build the fixture corpus under `packages/graphrag/tests/fixtures/corpus/` from
  real, pinned repo excerpts (record source URLs + fetch date in a fixtures
  `README.md`).

### T3 — Entity + edge extraction
- **Depends on:** T2
- **Tests:** `test_extract.py` — expected SIG/Person/KEP/Subproject entities and
  CHAIRS/TECH_LEADS/OWNS/AUTHORS/APPROVES/HAS_SUBPROJECT edges from the fixture.
  (`Person→Subproject` ownership is *not* modeled — sigs.yaml subproject `owners`
  are OWNERS-file URLs, not inline handles; fabricating it was declined.)
  `# STUB: AC2`, `# STUB: AC3`, `stub: true`.
- **Approach:** `graphrag.extract` — map parsed records to `Node`/`Edge`, IDs via
  `normalize`.

### T4 — Cross-source resolution + alias table
- **Depends on:** T3
- **Tests:** `test_resolve.py` — a SIG slug and a handle each present in both
  sources single-node; `@thockin`/`thockin`/`@SergeyKanzhelev` normalization cases
  merge correctly; the alias table merges a prose-name↔handle case. **Negatives:**
  two distinct handles do *not* merge; a display name absent from the alias table
  stays split (no false merge). No duplicate nodes for any shared entity.
  `# STUB: AC4`, `stub: true`.
- **Approach:** `graphrag.resolve` (upsert-by-normalized-ID into a `Graph`;
  alias applied in Person normalization); `aliases.yaml`.

### T5 — Resolver eval harness (open confirmation)
- **Depends on:** T4
- **Tests:** `test_eval.py` — `evaluate(labeled_sample)` returns
  precision/recall; **asserts both ≥ 0.80**. `# STUB: AC5`, `stub: true`.
- **Approach:** `graphrag.eval` (compare resolver merge decisions to gold labels;
  TP/FP/FN over shared-entity pairs incl. negatives); hand-label
  `tests/fixtures/labeled_sample.yaml` from the real, pinned excerpts. Add the
  opt-in `resolve-eval --corpus <full-clone>` path for the deferred full-corpus
  follow-on (`graph-ingestion-resolution-full-corpus-eval`).

### T6 — Graph store: interface + in-memory
- **Depends on:** T1
- **Tests:** `test_store_memory.py` — upsert/get/`neighbors` by edge kind +
  direction. `# STUB: AC7`, `stub: true`.
- **Approach:** `graphrag.store.base.GraphStore`, `graphrag.store.memory`.
  (Files disjoint from T2–T5, so eligible to build alongside them.)

### T7 — Multi-hop traversal + trace
- **Depends on:** T6
- **Tests:** `test_query.py` — entity-led exemplar (`@thockin` → TECH_LEADS → SIG
  → OWNS → KEPs) returns the correctly-scoped KEP set; the `TraversalResult`
  trace names every seed, hop, and result; hop cap enforced. `# STUB: AC6`,
  `# STUB: AC10`, `stub: true`.
- **Approach:** `graphrag.query.traverse` + `TraceEntry`/`TraversalResult`.

### T8 — Neptune openCypher adapter
- **Depends on:** T6
- **Tests:** `test_store_neptune.py` — with a mocked HTTPS/SigV4 client,
  `upsert_*` emits `MERGE` and `neighbors` emits a **parameterized** `MATCH` (values
  in the parameter map, never interpolated into the query string); responses parse
  into the same `Node` shape as memory; the endpoint is `https://` with TLS verify
  on; credentials resolve via the default botocore provider chain (no
  `AWS_SECRET_ACCESS_KEY` env read at the call site); non-2xx raises loudly with the
  body. `# STUB: AC7`, `stub: true`.
- **Approach:** `graphrag.store.neptune.NeptuneGraphStore` (botocore `SigV4Auth`
  over a `botocore.session` credential chain; injectable HTTP client; `verify=True`).

### T9 — CLI + ingest orchestration
- **Depends on:** T4, T7
- **Tests:** `test_cli.py` — `ingest` over the fixture prints parsed counts +
  resolved merges; `graph-query` prints the trace; `resolve-eval` prints the
  metrics; all three satisfy the narratability assertion. `# STUB: AC10`,
  `# STUB: AC6`, `stub: true`.
- **Approach:** `graphrag.ingest.ingest` + `IngestReport`; `graphrag.cli`
  (argparse: `ingest`, `graph-query`, `resolve-eval`); `graphrag` console script.

### T10 — Fargate ingestion app
- **Depends on:** T9
- **Tests:** `test_entrypoint.py` — entrypoint resolves env config and calls
  `ingest` (S3 download + store construction mocked). *(goal-based)*
- **Approach:** `apps/ingestion/entrypoint.py` + `Dockerfile`.

### T11 — IaC (AWS CDK, Python)
- **Depends on:** none (parallel-eligible; disjoint files from the Python lib).
  ADR-0003 is already written and accepted.
- **Tests:** `apps/infra/tests/test_stack.py` — `aws_cdk.assertions.Template`
  asserts: VPC (no NAT gateway), the five VPC endpoints, Neptune Serverless cluster
  **with no public endpoint**, S3 bucket **with public access blocked + encryption**,
  Fargate task def with a **least-privilege task role** (no wildcard `Resource`;
  scoped `s3`/`neptune-db`/`logs`), and Budgets alarm **with a threshold +
  notification subscriber**. Skipped when `aws-cdk-lib` absent. `# STUB: AC8`,
  `stub: true`.
- **Approach:** `apps/infra/app.py`, `apps/infra/stacks/graphrag_stack.py`,
  `cdk.json`, `requirements.txt`.

### T12 — Docs + capture-learnings + gate wiring
- **Depends on:** T1–T11
- **Tests:** n/a (docs).
- **Approach:** add `docs/architecture/security.md` (trust boundaries, no-NAT/
  private-subnet posture, least-privilege role intent, synthetic-labels-≠-authz
  disclaimer — charter principle 7); update `docs/architecture/overview.md` (real
  apps/packages), `docs/specs/README.md` (active spec row),
  `docs/product/changelog.md` (backlog deferral anchors already added); wire
  commands (incl. `ruff` with the `S` ruleset) into `tools/hooks/pre-pr.py`; add
  knowledge entries to `docs/knowledge/patterns.jsonl`; tick the spec's met ACs and
  flip Status → Implementing/Shipped as appropriate.

## Notes / declined patterns

- **Declined:** pushing traversal into openCypher now (would diverge backends).
- **Declined:** a `requirements.txt`-per-module sprawl — one root `pyproject.toml`
  with extras; infra keeps its own (CDK toolchain is separate).
- **Surfaced assumption:** the fixture corpus is *representative* of the real
  repos' shape (controlled-vocab slugs, stable handles) — the eval bar is honest
  only if the fixture mirrors the real overlap the de-risk verdict found. The
  fixture is hand-built from the real `sigs.yaml`/`kep.yaml` schemas.
