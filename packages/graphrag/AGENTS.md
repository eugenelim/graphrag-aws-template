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
| `ingest.py` | Orchestration + the narratable `IngestReport`. |
| `chunk.py` | (slice 2) Chunk the prose-rich subset (SIG/KEP READMEs) → `Chunk` with provenance + owning-entity IDs (== graph node IDs). |
| `embed.py` | (slice 2) `Embedder` protocol — `BedrockTitanEmbedder` (Titan v2, 256-dim) + offline `HashEmbedder`. |
| `vector.py` | (slice 2) `vector_search` + the retrieval trace (`VectorQueryResult`). |
| `vector_eval.py` | (slice 2) Curated-query-set hit@k over frozen real Titan v2 vectors (credible-baseline confirmation). |
| `store/` | `GraphStore` (+ memory/Neptune) **and** `VectorStore` ABC + `MemoryVectorStore` + `OpenSearchVectorStore` (slice 2). |
| `vector_smoke_lambda.py` | (slice 2) In-VPC probe: embed → index → k-NN-retrieve an ingested chunk → cleanup. |
| `cli.py` | `graphrag` CLI: `ingest`, `graph-query`, `resolve-eval`, `vector-ingest`, `vector-query`, `vector-eval`. |

## Dependencies (recorded per AGENTS.md "record new dependencies before adding")

Runtime:
- **`pyyaml`** — YAML parsing. **Always `yaml.safe_load`** (never `yaml.load`):
  the corpus is untrusted external input parsed under the Fargate task role
  (CWE-502). Enforced by the ruff `S` ruleset (`S506`).
- **`boto3` / `botocore`** — SigV4 signing for the Neptune openCypher adapter
  **and the OpenSearch k-NN adapter** (service `es`), plus the `bedrock-runtime`
  client for Titan v2 embeddings; credentials resolve via the default provider
  chain (the task role), never an env/argv secret read.

**Slice 2 added no new runtime dependency** — the OpenSearch adapter signs with
`botocore` + `urllib` exactly as the Neptune adapter does, and Titan v2 uses the
`boto3` `bedrock-runtime` client. (`opensearch-py` was declined; it would add a
forever-dependency for what SigV4+urllib already does.)

Dev: `pytest`, `ruff` (with the `S` security ruleset), `mypy`. Infra extra (not
imported by the runtime): `aws-cdk-lib`, `constructs`.

Adding a runtime dependency beyond these is an "Ask first" rail in the spec.

## Invariants worth knowing

- **The merge is upsert-by-normalized-ID, not a model.** Two mentions that
  normalize to the same ID become one node; the alias table (`aliases.yaml`) is the
  only non-mechanical step and is small, hand-authored data.
- **Traversal runs in the app layer over `neighbors()`**, so the in-memory and
  Neptune backends produce an identical trace. Do not push traversal into
  openCypher without re-reading the spec's Boundaries rail (it would diverge the
  backends; deferred to slice 3).
- **The fixture corpus is real, pinned excerpts** (see
  `tests/fixtures/corpus/README.md`) so the resolver eval is empirical.
