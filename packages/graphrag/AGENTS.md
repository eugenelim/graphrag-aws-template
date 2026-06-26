# `graphrag` package — agent context

The graph half of the GraphRAG-on-AWS demo (slice 1): parse → extract → resolve →
query, plus a backend-abstracted graph store. See
[`docs/specs/graph-ingestion-resolution/spec.md`](../../docs/specs/graph-ingestion-resolution/spec.md)
for the contract and the module map.

## Module map

| Module | Role |
| --- | --- |
| `normalize.py` | Stable-ID functions — a node's ID *is* its normalized key (the merge key). |
| `model.py` | `Node`/`Edge`/`Graph`; `upsert_*` unions sources/props on ID collision (the resolution merge). |
| `parse.py` | YAML (`safe_load` only) + Markdown front-matter parsing. |
| `sources.py` | Load the `community` + `enhancements` files into `ParsedDoc`s. |
| `extract.py` | `ParsedDoc` → entities/edges (IDs via `normalize`). |
| `resolve.py` | `extract` + `upsert` into a `Graph`; loads the `aliases.yaml` table. |
| `eval.py` | Pairwise precision/recall of the resolver vs. a labeled sample (the open confirmation). |
| `store/` | `GraphStore` ABC + `MemoryGraphStore` + `NeptuneGraphStore`. |
| `query.py` | Bounded multi-hop traversal over `neighbors()`, with a trace. |
| `ingest.py` | Orchestration + the narratable `IngestReport`. (slice 5) `ingest_delta` (provenance-set orphan reconciliation across both stores; `prev_manifest=None` → full-ingest fallback) + `rebuild` (clear + full) + `DeltaReport`. |
| `delta.py` | (slice 5) **Ingest-path only** (imports `sources`, uses yaml): content hash, the ingest `Manifest` (`{source}/{path}` doc id → sha256), and `diff_manifests` → add/change/delete/**move** (move = same hash, new path). The no-NAT detection source; **never imported by the query Lambda**. |
| `chunk.py` | (slice 2) Chunk the prose-rich subset (SIG/KEP READMEs) → `Chunk` with provenance + owning-entity IDs (== graph node IDs). |
| `embed.py` | (slice 2) `Embedder` protocol — `BedrockTitanEmbedder` (Titan v2, 256-dim) + offline `HashEmbedder`. |
| `vector.py` | (slice 2) `vector_search` + the retrieval trace (`VectorQueryResult`). |
| `vector_eval.py` | (slice 2) Curated-query-set hit@k over frozen real Titan v2 vectors (credible-baseline confirmation). |
| `store/` | `GraphStore` (+ memory/Neptune) **and** `VectorStore` ABC + `MemoryVectorStore` + `OpenSearchVectorStore` (slice 2). (slice 5) both ABCs gain delete/clear + exact-set `replace_*` (graph) / `delete_by_doc` (vector, keyed on source+doc_path together) for delta reconciliation; Neptune round-trips `doc_paths` as a JSON-string property. |
| `vector_smoke_lambda.py` | (slice 2) In-VPC probe: embed → index → k-NN-retrieve an ingested chunk → cleanup. |
| `entity_link.py` | (slice 3) Pure question→entity-ID linking over the controlled vocabulary, built on `normalize` (`@handles`/slugs/KEP numbers + the display-name alias table). |
| `synthesize.py` | (slice 3) `Synthesizer` seam — `BedrockClaudeSynthesizer` (Bedrock Claude via the Converse API) + offline deterministic `TemplateSynthesizer`; `DEFAULT_SYNTHESIS_MODEL_ID`. |
| `query.py` | Bounded multi-hop `traverse` (over `neighbors()`) **and** (slice 3) `expand_neighborhood` (undirected-over-all-edge-kinds neighborhood expansion for seed-and-expand) over the `neighbors_batch` seam (default fan-out; Neptune-batched override), with a sorted, backend-identical trace. |
| `hybrid.py` | (slice 3) The seed-and-expand orchestration: dual-seed (vector-owners ∪ question-links) → cap → expand → merge → synthesize → `HybridResult.render()` trace. |
| `compare.py` | (slice 3) The three-mode runner — `vector-only` / `graph-only` / `hybrid` independently, with a side-by-side `ComparisonResult.render()`. |
| `query_lambda.py` | (slice 3) In-VPC query Lambda handler behind an IAM-auth Function URL; reuses the same `hybrid_query`; PyYAML-free import graph; entity-links with `aliases={}` (mechanical normalizers only — no display-name table in the bundle). (opencypher-templates) additive `mode` dispatch (`hybrid` default \| `governed` \| `text2cypher`); `governed` runs `governed_query`; (text2opencypher-guarded) `text2cypher` runs `text2cypher_query` and returns a **sanitized** audit envelope (no raw Neptune error / ARN crosses the URL); (metadata-filtering) `selfquery` runs `selfquery_query` (OpenSearch + extractor + synthesizer; **builds no Neptune store** — entity validation is pure) and returns the extracted-filter envelope; (parent-child-retrieval) `parentchild` runs `parentchild_query` (nested OpenSearch store + Titan embed + synthesizer; **builds no Neptune store** — vector-only) and returns the matched-child + returned-parent envelope; unknown mode → client error. |
| `validate.py` | (text2opencypher-guarded) Read-only **static validator** for LLM-authored openCypher — the flexible path's layer 1: rejects any mutating clause / **any `CALL`** / multi-statement / `RETURN`-less / **unbounded variable-length path**, and bounds the `LIMIT`. Conservative (a forbidden keyword even inside a string literal rejects). **Not** the guarantee — the IAM read-only scope (writes) + the Neptune engine query timeout (runaway reads) back it (ADR-0004). Pure-Python, PyYAML-free. |
| `generate.py` | (text2opencypher-guarded) Text2openCypher **generation** seam — `BedrockText2CypherGenerator` (Converse; schema+question+self-heal feedback ride `messages` as untrusted data, never `system`; default model = `DEFAULT_SYNTHESIS_MODEL_ID` so no widened grant) + offline non-semantic `RuleText2CypherGenerator` (emits within the offline subset). The LLM **writes** the query (contrast with `select.py`). Holds `GRAPH_SCHEMA_DESCRIPTION`. PyYAML-free. |
| `cypher_eval.py` | (text2opencypher-guarded) The **bounded read-subset evaluator** — runs a model-authored query offline over the `GraphStore` seam (node-by-id / nodes-by-kind / one-hop `REL`), sorted by id; anything outside the subset raises `UnsupportedOfflineQuery`. Explicitly a labeled SUBSET (there is no local Neptune — see `docs/architecture/develop-and-test-offline.md`); live Neptune is the fidelity oracle. Pure-Python, PyYAML-free. |
| `text2cypher.py` | (text2opencypher-guarded) The flexible orchestration: `text2cypher_query` (generate → validate → **bounded self-heal** → execute → synthesize) + `Text2CypherResult.render()` audit trace. The risky counterpart to `governed.py`. PyYAML-free. |
| `templates.py` | (opencypher-templates) The governed **Cypher Templates** library: a fixed, reviewed registry of expert-authored, parameterized, **read-only** openCypher templates, each with a paired app-layer `evaluate` over the `GraphStore` seam (the dual form). Pure-Python (no yaml) — Lambda-bundle-safe. |
| `params.py` | (opencypher-templates) Deterministic parameter extraction + validation (the governance boundary): entity slots via `link_question`/`normalize` confirmed against the store, enum/int slots validated; a bad required slot → `ExtractionFailure` (no query runs). |
| `select.py` | (opencypher-templates) Template **selection** seam — `BedrockTemplateSelector` (Converse, returns one validated template id; an id outside the fixed set → `None`) + offline non-semantic `RuleTemplateSelector`. The LLM selects only; params are extracted deterministically. |
| `governed.py` | (opencypher-templates) The governed orchestration: `governed_query` (select → extract → `execute_template` → synthesize) + `GovernedResult.render()` audit trace; `execute_template` dispatches the dual form (Neptune `run_template_query` live / `evaluate` offline, sorted-identical). PyYAML-free. |
| `selfquery.py` | (metadata-filtering) The **self-query** path: a FIXED field schema (`FIELDS` — `source` enum + `entity_ids` entity) + `MetadataFilter` (OR-within / AND-across `terms`; `as_filter_clauses` for OpenSearch + `matches` for in-memory) + the deterministic **`validate_filter`** chokepoint (pure — no store: `source` against the enum, `entity_ids` via the pure `link_question`, undeclared/unresolvable dropped+recorded) + extractor seam (`BedrockMetadataExtractor` Converse / offline non-semantic `RuleMetadataExtractor`, both returning a validated `FilterExtraction`) + the `selfquery_query` orchestrator (extract → filtered `vector_search`/`hybrid_query` DURING the ANN scan → synthesize) + `SelfQueryResult.render()`. PyYAML-free. |
| `parentchild.py` | (parent-child-retrieval) The **Parent-Child Retriever** path: `group_into_parents` (ingest-side — groups embedded chunks by the `{source}/{doc_path}` key, orders children by ordinal, parent body from a `bodies` map with a loud `ValueError` on a miss, heading = ordinal-0 child, `entity_ids`/`visibility` inherited) + `parentchild_query` (query-side — embed → nested child match → synthesize over the **parent body**, wrapped as the `VectorHit` the synthesizer reads so citations resolve to the parent `doc_path`) + `ParentChildResult.render()` (question → matched child per parent → returned parents (full body) → answer). Composes AND with `clearance`. PyYAML-free. The nested store + value types live in `store/parentchild_base.py` (ABC + `ChildVector`/`ParentDoc`/`ParentHit`), `store/parentchild_opensearch.py` (nested `knn_vector` index, `score_mode:max` + `inner_hits`, parent-level visibility `terms`), `store/parentchild_memory.py` (best-child cosine, backend-identical). |
| `showcase/` | (slice 3) The consolidated showcase query set (`queries.yaml`) + `load_showcase()` loader (CLI/test-only; uses yaml, never imported by the Lambda). (slice 4) `queries.yaml` also carries `permission_queries` (the two-persona contrast) + `load_permission_showcase()`. (opencypher-templates) also `governed_queries` (template + bound param + gold rows per query) + `load_governed_showcase()`. (text2opencypher-guarded) also `text2cypher_queries` (gold rows + optional `shared_with_template` for the head-to-head) + `load_text2cypher_showcase()`. (metadata-filtering) also `selfquery_queries` (expected_filter + visible/excluded chunk split, spanning vector+hybrid) + `load_selfquery_showcase()`. (parent-child-retrieval) also `parentchild_queries` (expected matched child + returned parent + the flat-vs-parent-child contrast) + `load_parentchild_showcase()`. |
| `visibility.py` | (slice 4) **Pure, PyYAML-free** read-side of the synthetic permission filter (a TEACHING stand-in for ACLs, not real authz): the ordered `Visibility` tiers, most-restrictive-wins `compose`, `Clearance`, `PERSONAS`, and `resolve_clearance` (fail-closed — unknown persona raises `ValueError`). Imported by the query path (hybrid/compare/query_lambda) — must stay yaml-free. |
| `labels.py` | (slice 4) **Ingest-path only** (uses yaml): loads the packaged `labels.yaml` (entity-id→tier) and stamps node/edge (`label_graph`, edge = `compose(src,dst)`) + chunk (`label_chunks`, = `compose(owners)`) visibility during the dual-write. **Never imported by the query Lambda** (a `sys.modules` test guards it). |
| `cli.py` | `graphrag` CLI: `ingest`, `graph-query`, `resolve-eval`, `vector-ingest`, `vector-query`, `vector-eval`, (slice 3) `hybrid-query` / `compare` (offline default + live SigV4 Function-URL client), (slice 5) `delta` / `rebuild` / `delta-demo` (the before/after freshness demo; `scripts/delta-demo.sh` drives it from real git history), and (opencypher-templates) `governed-query` (the governed Cypher-Templates path; offline default + live `--function-url` mode=governed), (text2opencypher-guarded) `text2cypher-query`, and (metadata-filtering) `selfquery-query` (self-query metadata filtering; `--mode vector|hybrid`; offline default + live `--function-url` mode=selfquery), and (parent-child-retrieval) `parentchild-query` (Parent-Child Retriever; offline default + live `--function-url` mode=parentchild). |

## Dependencies (recorded per AGENTS.md "record new dependencies before adding")

Runtime:
- **`pyyaml`** — YAML parsing. **Always `yaml.safe_load`** (never `yaml.load`):
  the corpus is untrusted external input parsed under the Fargate task role
  (CWE-502). Enforced by the ruff `S` ruleset (`S506`).
- **`boto3` / `botocore`** — SigV4 signing for the Neptune openCypher adapter
  **and the OpenSearch k-NN adapter** (service `es`), plus the `bedrock-runtime`
  client for Titan v2 embeddings **and (slice 3) Bedrock Claude synthesis via the
  Converse API**; credentials resolve via the default provider chain (the task /
  Lambda role), never an env/argv secret read. **Floor: `boto3>=1.35`** — the version
  at which `bedrock-runtime.converse` exists (slice 3 bumped it from `>=1.34`; a
  version-floor bump, not a new dependency).

**Slice 2 added no new runtime dependency** — the OpenSearch adapter signs with
`botocore` + `urllib` exactly as the Neptune adapter does, and Titan v2 uses the
`boto3` `bedrock-runtime` client. (`opensearch-py` was declined; it would add a
forever-dependency for what SigV4+urllib already does.)

**Slice 3 added no new runtime dependency** — Bedrock Claude synthesis uses the
`boto3` `bedrock-runtime` **Converse** API (not the `anthropic` SDK, which is absent
from the Lambda runtime / pure-Python bundle and would be a forever-dependency), and
the live Function-URL client signs with `botocore` + `urllib` exactly as the adapters
do. Only the `boto3` floor moved (`>=1.34 → >=1.35`).

**Slice 4 (permission-filtered retrieval) added no new runtime dependency and no new
infra resource** — the synthetic visibility filter is a parameterized openCypher `WHERE`
on the Neptune hop + an OpenSearch `terms` metadata filter over the existing adapters; the
persona rides the existing query Lambda's request body. Labels are a **teaching stand-in
for ACLs, never real authz** (charter principle 5). The read path stays PyYAML-free:
`visibility.py` (tiers, `compose`, `Clearance`, `resolve_clearance`) is pure and importable
by the Lambda, while `labels.py` (reads `labels.yaml`) is ingest-path-only and must never be
imported by the query graph — guarded by a `sys.modules` assertion in
`test_query_lambda.py` alongside the existing `import yaml` block.

**opencypher-templates (Cypher Templates) added no new runtime dependency and no new infra
resource** — template selection + synthesis use the existing `boto3` `bedrock-runtime`
**Converse** client (selection defaults to the synthesis model, so the existing
`bedrock:Converse` grant covers it), parameterized openCypher rides the existing
`NeptuneGraphStore._run`, and the live path rides the existing query Lambda + IAM-auth
Function URL via an **additive, back-compat `mode` field** (absent ⇒ `hybrid`). The governed
modules (`templates`, `params`, `select`, `governed`) are **PyYAML-free** and join the
query-Lambda import-graph guard.

**metadata-filtering (self-query) added no new runtime dependency and no new infra
resource** — extraction uses the existing `boto3` `bedrock-runtime` **Converse** client
(defaulting to the synthesis model, so the `bedrock:Converse` grant covers it), the
structured filter rides the existing `OpenSearchVectorStore.knn` request body as a
parameterized `terms` clause, and the live path rides the existing query Lambda + IAM-auth
Function URL via the **additive `mode: selfquery`** value. The only store change is the k-NN
index **method engine** (`nmslib` → **`lucene` HNSW**, in `store/opensearch.py:_knn_mapping`)
so the filter applies DURING the ANN scan (RFC-0001 §4) — an app-side mapping change applied
at `create_index` on a fresh index, not CDK. The self-query path **builds no Neptune store**
(entity validation is pure controlled-vocab resolution), so it adds no Neptune grant.
`selfquery.py` is **PyYAML-free** and joins the query-Lambda import-graph guard.

**parent-child-retrieval added no new runtime dependency and no new infra resource** — the
nested store rides the same SigV4/HTTPS plumbing as the flat `OpenSearchVectorStore` (a new
**index** `graphrag-parents` on the existing domain, created app-side at `create_index`, not
CDK), synthesis reuses the granted `bedrock:Converse`, the child vectors reuse the granted
Titan embed (computed **once** and written to both indexes by the full-ingest dual-write — no
re-embed), and the live path rides the existing query Lambda + IAM-auth Function URL via the
**additive `mode: parentchild`** value. The path **builds no Neptune store** (vector-only), so
it adds no Neptune grant. `parentchild.py` + `store/parentchild_*` are **PyYAML-free** and join
the query-Lambda import-graph guard.

**Pure-Python Lambda / PyYAML-free import graph.** `query_lambda.py` is bundled via
`Code.from_asset` over the package source (boto3/botocore from the runtime, **no
pyyaml**). It and its transitive imports (`hybrid`, `synthesize`, `entity_link`,
`compare`, `embed`, `store/*`, `model`, (opencypher-templates) `governed`, `templates`,
`select`, `params`, (metadata-filtering) `selfquery`, and (parent-child-retrieval) `parentchild` + `store/parentchild_*`) must never `import yaml` at module load. The
Lambda entity-links with `aliases={}` (the mechanical `@handle`/slug/KEP normalizers
resolve without the display-name alias table, which `resolve.load_aliases()` loads via
yaml); `showcase.load_showcase` also uses yaml and is **CLI/test-only**, never imported
by the Lambda. A test blocks `import yaml` and imports `query_lambda` to enforce this.

Dev: `pytest`, `ruff` (with the `S` security ruleset), `mypy`. Infra extra (not
imported by the runtime): `aws-cdk-lib`, `constructs`.

Adding a runtime dependency beyond these is an "Ask first" rail in the spec.

## Invariants worth knowing

- **The merge is upsert-by-normalized-ID, not a model.** Two mentions that
  normalize to the same ID become one node; the alias table (`aliases.yaml`) is the
  only non-mechanical step and is small, hand-authored data.
- **Traversal logic runs in the app layer behind the `GraphStore` seam, and the trace is
  backend-identical.** `traverse` (typed steps) is over `neighbors()`; `expand_neighborhood`
  (seed-and-expand) is over `neighbors_batch()` — whose **default** fans out over `neighbors()`
  and whose **Neptune override** issues one batched openCypher query per direction (added in
  slice 3 because the per-edge-kind fan-out timed out against Neptune Serverless). The override is
  trace-safe **only** because `expand_neighborhood` sorts the reached set + edge kinds, so order is
  backend-independent. Any new backend method must preserve that identical-trace property (sort, do
  not rely on store result order).
- **The fixture corpus is real, pinned excerpts** (see
  `tests/fixtures/corpus/README.md`) so the resolver eval is empirical.
- **Cypher Templates are dual-form, and the LLM only selects.** Each governed template
  carries the parameterized openCypher (the governed artifact, run live on Neptune) **and**
  a paired app-layer `evaluate` over the `GraphStore` seam (offline); `governed.execute_template`
  sorts both by node id so the backends are byte-identical — the same invariant `neighbors_batch`
  lives under. The selector (`select.py`) returns only a template id validated against the fixed
  set; parameter *values* are extracted + validated deterministically (`params.py`) and bound via
  `$param`, never interpolated — so the executable surface stays a fixed, reviewed, read-only
  library whatever the model returns (the governed half of the governed-vs-risky pair; the risky
  half, LLM-authored query text executed read-only, is the separate `text2opencypher-guarded` slice).
- **Text2cypher's read-only guarantee is layered, and the validator is NOT the guarantee.** The
  LLM writes the whole query (structure *and* literal values — there is no `$param` map to bind),
  so safety is defense-in-depth (ADR-0004): the `validate.py` static validator (layer 1) + a
  bounded self-heal + the **IAM read-only data-action scope** on the query-Lambda role (the *write*
  backstop — a write the validator missed is denied by AWS before the engine runs it) + the Neptune
  **engine query timeout** (the *read-cost* backstop). Never weaken the validator to "warn", never
  grant the query Lambda `WriteDataViaQuery`/`DeleteDataViaQuery`, and never let the raw Neptune
  error cross the Function URL (the `_serialize_text2cypher` envelope is sanitized). Offline,
  arbitrary openCypher runs against a **labeled bounded subset** (`cypher_eval.py`) — live Neptune
  is the dialect-fidelity oracle (`docs/architecture/develop-and-test-offline.md`).
- **The self-query filter is LLM-extracted but deterministically bounded, applied DURING the ANN
  scan, and composes with clearance.** The LLM only produces a filter over the FIXED `source`/
  `entity_ids` schema; `validate_filter` re-validates every value (enum membership; pure
  `link_question` resolution) and drops anything undeclared/unresolvable — no free-form model
  value is ever bound, and the value rides the request-body `terms` clause, never interpolated.
  The k-NN index method is **Lucene HNSW** (not `nmslib`) so the filter prunes candidates *during*
  the ANN scan (efficient filtering, RFC-0001 §4 — returns `k` from the qualifying subset, not a
  post-filter over the top-`k`); the in-memory `MetadataFilter.matches` predicate is
  backend-identical. The self-query `terms` and the slice-4 visibility `terms` are **independent**
  clauses on the same `knn` call, so a self-query filter can only narrow, never widen past a
  persona's clearance — and the fail-closed `None`-vs-empty-`Clearance` semantics survive the merge
  (a self-query filter is question-derived; the permission filter is the fixed persona clearance).
- **Parent-child retrieval matches small (child) and answers large (parent body), as one nested
  document.** Children carry the vectors (sized for match precision); the parent document carries the
  full prose in an app-stored `body` field and is the unit returned (RFC-0001 §3 — **not** an
  Elasticsearch `has_child` cross-doc join). The nested `knn` over `children.vector` scores each parent
  by its **best** child (`score_mode:max`) and `inner_hits` surfaces which child matched; synthesis
  reads the **parent body**, never the matched child fragment. Because the parent is the returned unit
  there is no duplicate-parent dedup. The child vectors are the flat index's vectors, embedded **once**
  and written to both indexes (the full-ingest dual-write — never re-embedded for the second index).
  The visibility `terms` rides the same nested query as a parent-level `bool.filter` composed AND with
  the child match (fail-closed `None`-vs-empty preserved), so parent-child can only narrow. The
  in-memory `MemoryParentChildStore` (best-child cosine) is backend-identical to the OpenSearch nested
  store on the fixture corpus.
