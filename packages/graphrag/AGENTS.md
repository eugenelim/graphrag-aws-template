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
| `query_lambda.py` | (slice 3) In-VPC query Lambda handler behind an IAM-auth Function URL; reuses the same `hybrid_query`; PyYAML-free import graph; entity-links with `aliases={}` (mechanical normalizers only — no display-name table in the bundle). |
| `showcase/` | (slice 3) The consolidated showcase query set (`queries.yaml`) + `load_showcase()` loader (CLI/test-only; uses yaml, never imported by the Lambda). (slice 4) `queries.yaml` also carries `permission_queries` (the two-persona contrast) + `load_permission_showcase()`. |
| `visibility.py` | (slice 4) **Pure, PyYAML-free** read-side of the synthetic permission filter (a TEACHING stand-in for ACLs, not real authz): the ordered `Visibility` tiers, most-restrictive-wins `compose`, `Clearance`, `PERSONAS`, and `resolve_clearance` (fail-closed — unknown persona raises `ValueError`). Imported by the query path (hybrid/compare/query_lambda) — must stay yaml-free. |
| `labels.py` | (slice 4) **Ingest-path only** (uses yaml): loads the packaged `labels.yaml` (entity-id→tier) and stamps node/edge (`label_graph`, edge = `compose(src,dst)`) + chunk (`label_chunks`, = `compose(owners)`) visibility during the dual-write. **Never imported by the query Lambda** (a `sys.modules` test guards it). |
| `cli.py` | `graphrag` CLI: `ingest`, `graph-query`, `resolve-eval`, `vector-ingest`, `vector-query`, `vector-eval`, (slice 3) `hybrid-query` / `compare` (offline default + live SigV4 Function-URL client), and (slice 5) `delta` / `rebuild` / `delta-demo` (the before/after freshness demo; `scripts/delta-demo.sh` drives it from real git history). |

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

**Pure-Python Lambda / PyYAML-free import graph.** `query_lambda.py` is bundled via
`Code.from_asset` over the package source (boto3/botocore from the runtime, **no
pyyaml**). It and its transitive imports (`hybrid`, `synthesize`, `entity_link`,
`compare`, `embed`, `store/*`, `model`) must never `import yaml` at module load. The
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
