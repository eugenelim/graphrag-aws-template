# RFC-0001 notes — AWS feasibility of the graphrag.com pattern subset

> Promoted research backing the **Pattern coverage** table in
> [`../0001-adopt-project-charter.md`](../0001-adopt-project-charter.md). Each
> `Have`/`Planned` row was checked against current AWS documentation (June 2026)
> before the patterns were committed. Informal supporting material — the RFC body
> remains the contract.

## Verdict

All five `Planned` patterns are implementable on the existing stack (OpenSearch +
Neptune + Bedrock + Fargate/Lambda). The one dependency flagged as possibly too
heavy — Neptune Analytics for community detection — is **avoidable**: compute
communities in the Fargate ingest task and write results back to Neptune, so no
new standing managed service is introduced.

## Findings

### 1. Neptune Analytics community detection — VERIFIED (Louvain yes, Leiden no)

- Neptune Analytics ships **Louvain** (hierarchical, `maxLevels` param) and
  **label propagation** for community detection; **Leiden is not available**.
  Callable from openCypher via `CALL neptune.algo.louvain(...)`.
- Neptune Analytics is a **separate, in-memory** service from Neptune Database;
  it bulk-imports from a Neptune cluster/snapshot (engine ≥ 1.3.0). Community IDs
  computed there must be written back for query-time lookup.
- **Decision for this template:** compute Louvain (or `leidenalg` for true Leiden
  semantics) **in the Fargate ingest task** and write `communityId` + Bedrock
  summaries into Neptune as nodes. Keeps the no-standing-extra-service cost
  posture (ADR-0002); Neptune Analytics documented as the managed alternative.
- Caveats: Louvain can yield disconnected communities at intermediate levels
  (Leiden's advantage); the algorithm runs under an exclusive lock.
- Sources:
  [clustering algorithms](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/clustering-algorithms.html),
  [Louvain](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/louvain.html),
  [Analytics vs Database](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/neptune-analytics-vs-neptune-database.html),
  [import from Neptune](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/bulk-import-create-from-neptune.html).

### 2. Neptune openCypher — VERIFIED

- openCypher supported since engine 1.1.1.0 (HTTPS, Bolt, SDK).
- **Parameterized queries** fully supported (JSON parameter map; plan cache for
  repeated structures) → backs **Cypher Templates**.
- **Read-replica / reader endpoint is read-only-enforced** — write mutations are
  blocked regardless of session config → the guardrail for **Text2openCypher**
  (route LLM-generated queries there).
- Sources:
  [parameterized queries](https://docs.aws.amazon.com/neptune/latest/userguide/opencypher-parameterized-queries.html),
  [accessing with openCypher](https://docs.aws.amazon.com/neptune/latest/userguide/access-graph-opencypher.html),
  [openCypher transactions](https://docs.aws.amazon.com/neptune/latest/userguide/access-graph-opencypher-transactions.html).

### 3. OpenSearch parent-child — VERIFIED (nested, not cross-doc join)

- Nested `knn_vector` sub-fields let child-chunk vectors match while the parent
  doc is scored/returned; `expand_nested_docs`, Lucene Parent Block Join (2.12)
  for dedup. **Caveat:** it's nested-doc, not Elasticsearch `has_child` join — the
  app stores/fetches the parent body. → backs **Parent-Child Retriever**.
- Sources:
  [nested k-NN search](https://docs.opensearch.org/latest/vector-search/specialized-operations/nested-search-knn/),
  [multi-vector nested blog](https://opensearch.org/blog/enhanced-multi-vector-support-in-opensearch-knn/).

### 4. OpenSearch filtered k-NN — VERIFIED

- Efficient filtering **during** ANN search (not post-filter) on Lucene HNSW
  (2.4), Faiss HNSW (2.9), Faiss IVF (2.10); any DSL filter (term/range/bool/geo).
  Guarantees `k` results from the qualifying subset. → backs **Metadata
  Filtering / Self-Query**.
- Sources:
  [efficient filters blog](https://opensearch.org/blog/efficient-filters-in-knn/),
  [filtering docs](https://docs.opensearch.org/latest/vector-search/filter-search-knn/index/).

### 5. Bedrock Titan v2 + Claude via VPC endpoint — VERIFIED

- `amazon.titan-embed-text-v2:0` (≤8,192 tokens, configurable dims) and Claude
  models, both via `bedrock-runtime`; interface VPC endpoint
  `com.amazonaws.{region}.bedrock-runtime` with private DNS, no code change.
  (Anthropic models require one-time FTU acceptance.)
- Sources:
  [Titan v2 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-titan-text-embeddings-v2.html),
  [Bedrock VPC endpoints](https://docs.aws.amazon.com/bedrock/latest/userguide/vpc-interface-endpoints.html).

### 6. Neptune VPC-only — PARTIALLY VERIFIED (factual correction)

- VPC-only is the **default and IAM-enforceable**, but since engine **1.4.6.0**
  Neptune supports an **optional public endpoint** (IAM-auth, off by default). The
  design doc / ADR-0002 wording "Neptune has no public endpoint" is now too
  absolute — the *decision* (private topology) holds; the *rationale* should be
  softened to "VPC-only by configuration, enforced via IAM" in a future doc edit.
- Sources:
  [Neptune public endpoints](https://docs.aws.amazon.com/neptune/latest/userguide/neptune-public-endpoints.html),
  [securing Neptune with VPC](https://docs.aws.amazon.com/neptune/latest/userguide/security-vpc.html).
