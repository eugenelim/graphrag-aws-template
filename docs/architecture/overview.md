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

**Slice 1 (`graph-ingestion-resolution`) has landed** — the graph half of the
demo. Current layout:

| Path | What | Stack |
| --- | --- | --- |
| `packages/graphrag/` | Core library + the `graphrag` CLI: parse → extract → resolve → query, the graph store abstraction (in-memory + Neptune openCypher adapter), and the resolver eval. | Python 3.11+ (`pyyaml`, `boto3`) |
| `apps/ingestion/` | On-demand Fargate task entrypoint — resolves the S3 corpus snapshot and runs the same `graphrag.ingest` the CLI runs. | Python + Dockerfile |
| `apps/infra/` | AWS CDK app — the slice-1 topology subset (no-NAT VPC + endpoints + Neptune Serverless + S3 + Fargate task def + Budgets alarm). | AWS CDK (Python) |

Build/test from the repo root: `pip install -e ".[dev,infra]"` then `pytest`,
`ruff check packages apps`, `mypy packages/graphrag/src apps`.

**Still to come** (per the design doc + brief Spec map): slice 2 adds OpenSearch +
Titan v2 embeddings + the `bedrock-runtime` endpoint; slice 3 adds the in-VPC query
Lambda and the three-mode comparison runner; slices 4–5 add permission-filtered
retrieval and incremental delta re-ingest. Read:

- [`architecture/graphrag-aws-architecture/design.md`](graphrag-aws-architecture/design.md)
  — the topology and the two resolved decisions (hybrid orchestration; ephemeral
  VPC stack).
- [`security.md`](security.md) — the consolidated security posture.
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
