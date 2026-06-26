# Architecture Overview

> The map of this monorepo. Read this first when exploring. Updated whenever
> the directory layout or major dependencies change.

## Layout

```
.
├── AGENTS.md             # canonical agent context (CLAUDE.md is a symlink)
├── apps/                 # deployable applications
│   └── <app-name>/       # one directory per app
├── packages/             # shared libraries (consumed by apps and other packages)
│   └── <package-name>/
├── tools/                # build, dev, and ops tooling — not shipped to users
├── docs/
│   ├── CHARTER.md        # mission, scope, principles (one page)
│   ├── CONVENTIONS.md    # how we work
│   ├── adr/              # architecture decisions (frozen history)
│   ├── rfc/              # proposals (governance)
│   ├── specs/            # feature specs and plans
│   ├── architecture/     # this directory — current code structure (for contributors)
│   ├── product/          # current product state (roadmap, changelog) — for maintainers
│   └── guides/           # user-facing docs (Diátaxis: tutorials, how-to, reference, explanation)
├── .claude/
│   ├── skills/           # agent workflows for repeating tasks (each skill owns its templates under `assets/`)
│   ├── agents/           # subagent definitions
│   └── commands/         # custom slash commands
└── .github/              # CI, issue and PR templates
```

## Apps and packages

**Slices 1–5 have landed** — the graph half, the vector baseline, the hybrid
seed-and-expand mode + three-mode runner, permission-filtered retrieval, and incremental
delta re-ingest — plus the first **pattern-catalog** slice, `opencypher-templates` (the
governed **Cypher Templates** path: a fixed library of expert-authored parameterized
openCypher templates the LLM only *selects*, with parameters extracted/validated
deterministically and a full audit trace; the governed half of the governed-vs-risky
teaching pair) and its contrast `text2opencypher-guarded` (the **Text2Cypher** path: the
LLM *writes* the openCypher, executed read-only behind a layered guard — a static
validator + bounded self-heal + **IAM read-only data-action scoping** + a Neptune engine
query timeout, ADR-0004; the risky half of the pair). For building/exercising any of these
without a deployed stack — and the decision record for *why* text2cypher executes offline
against a labeled bounded subset rather than a local Neptune — see
[`develop-and-test-offline.md`](develop-and-test-offline.md). Current layout:

| Path | What | Stack |
| --- | --- | --- |
| `packages/graphrag/` | Core library + the `graphrag` CLI. Graph half: parse → extract → resolve → query (in-memory + Neptune). Vector half (slice 2): chunk → embed (Titan v2) → k-NN (in-memory + OpenSearch), `vector-query` + the credible-baseline `vector-eval`. Hybrid half (slice 3): question entity-linking, a `Synthesizer` seam (Bedrock Claude via Converse + offline template), bounded neighborhood expansion, the **seed-and-expand `hybrid_query`**, the three-mode `compare` runner, `hybrid-query`/`compare` CLI verbs, the in-VPC `query_lambda`, and a consolidated `showcase` set. Permission filter (slice 4): synthetic visibility labels (`visibility.py` pure read-side + `labels.py` ingest-side) carried as Neptune node/edge props + OpenSearch chunk metadata, a `--persona`/`persona` clearance, and the **during-traversal edge filter** across all three modes. | Python 3.11+ (`pyyaml`, `boto3`) |
| `apps/ingestion/` | On-demand Fargate task entrypoint — resolves the S3 corpus snapshot and runs `graphrag.ingest`; slice 2 added the **single-parse dual-write** (graph + vector) over the same corpus read; slice 4 labels both stores' visibility in that same pass. | Python + Dockerfile |
| `apps/infra/` | AWS CDK app — no-NAT VPC + endpoints (incl. `bedrock-runtime`) + Neptune Serverless + **single-node OpenSearch (k-NN)** + S3 + Fargate task def + two in-VPC smoke probes (graph + vector) + **the in-VPC query Lambda behind an IAM-auth Function URL** (slice 3) + Budgets alarm. | AWS CDK (Python) |

Build/test from the repo root: `pip install -e ".[dev,infra]"` then `pytest`,
`ruff check packages apps`, `mypy packages/graphrag/src apps`.

**Still to come** (per the design doc + brief Spec map): slices 4–5 add
permission-filtered retrieval and incremental delta re-ingest. Read:

- [`architecture/graphrag-aws-architecture/design.md`](graphrag-aws-architecture/design.md)
  — the topology and the two resolved decisions (hybrid orchestration; ephemeral
  VPC stack).
- [`infrastructure.md`](infrastructure.md) — the **infrastructure lens**: the live,
  rolled-up view of what AWS infra is provisioned today (topology, inventory, idle
  cost, the cross-cutting infra patterns) with an evolution log grown per slice.
- [`security.md`](security.md) — the consolidated security posture.
- [`deployment-and-verification.md`](deployment-and-verification.md) — how the
  stack deploys/tears down, the in-VPC smoke probes that verify the live graph +
  vector stores, and the live-deploy findings.
- [`../product/briefs/graphrag-aws-demo.md`](../product/briefs/graphrag-aws-demo.md)
  — the five shippable slices.

## Where to start

1. Read [`docs/CHARTER.md`](../CHARTER.md) — mission and scope. **(Currently a
   template — see the note in this repo's docs; charter content is RFC-gated.)**
2. Read this file (architecture overview).
3. Read the design doc + [`docs/adr/`](../adr/) — the architecture is decided
   before the code exists, so these are the current source of truth.
4. Skim [`docs/product/roadmap.md`](../product/roadmap.md) and the
   [brief](../product/briefs/graphrag-aws-demo.md) for the slice sequence.
5. When code lands, each `docs/specs/<slice>/` will carry a `spec.md` + `plan.md`
   alongside the resulting code in `apps/`.
