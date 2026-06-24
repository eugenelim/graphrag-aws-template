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
| [`graph-ingestion-resolution`](graph-ingestion-resolution/spec.md) | Implementing | ADR-0001, ADR-0002, ADR-0003 | Slice 1 (lead). Graph ingest + cross-source resolution + CLI + slice-1 IaC. AC9 (live deploy) deferred. |
| [`hybrid-orchestration`](hybrid-orchestration/spec.md) | Implementing | ADR-0001, ADR-0002, ADR-0003 | Slice 3. Seed-and-expand hybrid in the in-VPC query Lambda (IAM-auth Function URL) + three-mode comparison runner + consolidated showcase set + presenter script; Bedrock Claude synthesis via boto3 Converse. Offline ACs (1–8, 10) met + reviewed; `cdk synth` validates the IaC; AC9 (live smoke) deferred — `hybrid-orchestration-live-deploy` (Docker not available to build the ingestion image). |

## Shipped specs (archived)

<!-- Once a feature is shipped, move its row here. The spec stays in place
     as documentation of the feature's contract. -->

| Spec | Status | Constrained by | Notes |
| --- | --- | --- | --- |
| [`vector-rag-baseline`](vector-rag-baseline/spec.md) | Shipped | ADR-0001, ADR-0002, ADR-0003 | Slice 2. Chunk → Titan v2 embed → OpenSearch k-NN + `vector-query` CLI with retrieval trace; credible-baseline query set (hit@5=1.0 + honest misses); live retrieve probe verified + torn down. |

## Adding a new spec

```bash
mkdir -p docs/specs/<feature-name>
cp .claude/skills/new-spec/assets/spec.md docs/specs/<feature-name>/spec.md
cp .claude/skills/new-spec/assets/plan.md docs/specs/<feature-name>/plan.md
```

Or, in Claude Code, run `/new-spec "<feature-name>"`.
