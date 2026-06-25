# Specs

> Feature specifications and implementation plans. See
> [`../CONVENTIONS.md`](../CONVENTIONS.md#4-specs-and-plans--docsspecsfeature)
> for the spec / plan distinction and lifecycle.

Each feature gets a directory:

```
docs/specs/<feature>/
├── spec.md      ← the contract (objective, boundaries, testing strategy, acceptance criteria): what this feature does
├── plan.md      ← the strategy + construction tests: how we'll build it
└── notes/       ← (optional) research, sketches, rejected approaches
```

## Active specs

<!-- Update this list as features are added. -->

| Spec | Status | Constrained by | Notes |
| --- | --- | --- | --- |
| [`incremental-delta-reingest`](incremental-delta-reingest/spec.md) | Implementing | charter (pattern 8, principle 5), design (incremental-sync + corpus-snapshot OQ), ADR-0001/0002/0003 | Slice 5. Fargate `--delta` mode: content-hash-manifest git-delta detection (add/change/delete/move) → re-ingest only the delta → both stores consistent by (doc path + content hash) with explicit orphan removal (provenance-set reference counting) → `--rebuild` escape hatch → before/after CLI demo on real git history. AC9 live required. |

## Shipped specs (archived)

<!-- Once a feature is shipped, move its row here. The spec stays in place
     as documentation of the feature's contract. -->

| Spec | Status | Constrained by | Notes |
| --- | --- | --- | --- |
| [`graph-ingestion-resolution`](graph-ingestion-resolution/spec.md) | Shipped | ADR-0001, ADR-0002, ADR-0003 | Slice 1 (lead). Graph ingest + cross-source resolution + CLI + slice-1 IaC. AC9 (live deploy) deferred. |
| [`vector-rag-baseline`](vector-rag-baseline/spec.md) | Shipped | ADR-0001, ADR-0002, ADR-0003 | Slice 2. Chunk → Titan v2 embed → OpenSearch k-NN + `vector-query` CLI with retrieval trace; credible-baseline query set (hit@5=1.0 + honest misses); live retrieve probe verified + torn down. |
| [`hybrid-orchestration`](hybrid-orchestration/spec.md) | Shipped | ADR-0001, ADR-0002, ADR-0003 | Slice 3. Seed-and-expand hybrid in the in-VPC query Lambda (IAM-auth Function URL) + three-mode comparison runner + consolidated showcase + presenter script; Bedrock Claude synthesis via boto3 Converse; batched neighbor fetch. All 10 ACs met incl. **AC9 verified live** (22.7 s end-to-end, then torn down). Quality follow-up: `hybrid-orchestration-synthesis-edges`. |
| [`permission-filtered-retrieval`](permission-filtered-retrieval/spec.md) | Shipped | charter (principle 5, pattern 7), design D1, ADR-0001/0002/0003 | Slice 4. Synthetic visibility labels → both stores (Neptune node/edge props + OpenSearch metadata) → persona/clearance → permission-filtered retrieval across all three modes, with the graph filter applied **during traversal on edges** (the leak guard). No new dependency, no new infra. **All 10 ACs met incl. AC9 verified live (2026-06-24):** two-persona divergence end-to-end (restricted `kep-1287` absent for `public-reader`, present for `maintainer`), then torn down. Live run fixed one packaging bug (`labels.yaml` package-data). |

## Adding a new spec

```bash
mkdir -p docs/specs/<feature-name>
cp .claude/skills/new-spec/assets/spec.md docs/specs/<feature-name>/spec.md
cp .claude/skills/new-spec/assets/plan.md docs/specs/<feature-name>/plan.md
```

Or, in Claude Code, run `/new-spec "<feature-name>"`.
