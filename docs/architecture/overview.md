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

**Greenfield — no application code has been written yet.** This repo currently
holds the product and architecture record that *precedes* the code; `apps/` and
`packages/` are not yet populated. The planned shape is settled in the design doc
and recorded in ADRs — read those for "what's coming" until code lands:

- [`architecture/graphrag-aws-architecture/design.md`](graphrag-aws-architecture/design.md)
  — the topology and the two resolved decisions (hybrid orchestration; ephemeral
  VPC stack). Planned runtime components: an on-demand **Fargate ingestion/sync**
  task, an in-VPC **query Lambda** behind an IAM-auth Function URL, a thin local
  **CLI**, and the **Neptune + OpenSearch + Bedrock** stores.
- [`../product/briefs/graphrag-aws-demo.md`](../product/briefs/graphrag-aws-demo.md)
  — the five shippable slices (Spec map) the code will be built from.

Update this section with the real `apps/`/`packages/` listing once slice 1 lands.

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
