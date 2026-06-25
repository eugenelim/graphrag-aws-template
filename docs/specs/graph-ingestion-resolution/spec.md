# Spec: graph-ingestion-resolution

- **Status:** Shipped
- **Shape:** mixed
- **Brief:** [`docs/product/briefs/graphrag-aws-demo.md`](../../product/briefs/graphrag-aws-demo.md)
- **Constrained by:** [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (seed-and-expand reuses this resolver/alias table), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (ephemeral VPC + Neptune + Fargate topology), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC tool), [design doc](../../architecture/graphrag-aws-architecture/design.md)

> Slice 1 of the brief's Spec map (the lead slice; `Depends on: none`).

## Objective

Stand up the **graph half** of the GraphRAG demo end-to-end: parse Markdown +
YAML from **both** Kubernetes sources (`kubernetes/community` and
`kubernetes/enhancements`), extract organizational entities and the edges between
them (SIG, person, KEP, subproject, ownership/leadership/authorship), **resolve
the shared entities across the two sources into single graph nodes** via
normalized match + a small hand-authored alias table (no trained model), and
expose a **CLI multi-hop graph query with a visible trace**. The slice also
provisions the slice-1 subset of the ephemeral VPC topology (VPC + endpoints +
Neptune + the Fargate ingestion task) per ADR-0002.

This is slice 1 of the brief and the de-risk verdict's **open confirmation**: the
resolver is run over a hand-labeled sample of shared entities — drawn from *real,
pinned* excerpts of the two repos — and must clear the predeclared **≥80%
precision *and* recall** bar empirically, not by assertion of construction.

Why this slice leads (per the intent's Decomposition): the centerpiece risk
(cross-source resolution) is already retired by two SURVIVED de-risk verdicts, so
ordering follows product value; slice 1 still stands up the ingestion pipeline and
the first datastore. Vector (slice 2) and hybrid (slice 3) build on this graph and
on the *same* resolver/alias table (ADR-0001's entity-linking is "nearly free"
precisely because it reuses what this slice builds).

## Boundaries

**In scope:**

- A `graphrag` Python package: source loading, Markdown-front-matter + YAML
  parsing, entity/edge extraction, cross-source resolution, a backend-abstracted
  graph store, a bounded multi-hop traversal with a trace, and a CLI.
- Two source adapters — `community` (`sigs.yaml` + SIG `README.md` charters) and
  `enhancements` (`keps/**/kep.yaml` + KEP `README.md`) — over the **prose-rich
  doc subset** (per de-risk verdict #2: KEP READMEs, SIG charters; not every terse
  fix-KEP).
- Entity kinds: **SIG, Person, KEP, Subproject**. Edge kinds: **CHAIRS,
  TECH_LEADS (Person→SIG), OWNS (SIG→KEP), AUTHORS, APPROVES (Person→KEP),
  HAS_SUBPROJECT (SIG→Subproject)**.
- Cross-source resolution: normalized-slug match for SIGs, normalized-handle match
  for Persons, and a small alias table (`aliases.yaml`) for the prose-name ↔
  `@handle` case the de-risk verdict flagged in pre-`kep.yaml` KEPs. **No trained
  model** (charter pattern 1; narratable).
- A resolver eval harness over a hand-labeled sample, asserting the ≥80%
  precision/recall bar (the open confirmation).
- Graph store abstraction with two implementations: an in-memory store (offline +
  test + reproducible-demo backend) and a **Neptune openCypher adapter** (SigV4,
  parameterized read queries) for the deployed stack. The multi-hop traversal runs
  in the application layer over a `neighbors()` primitive so the trace is
  **identical** across backends.
- A CLI: `graphrag ingest`, `graphrag graph-query` (seed + bounded edge steps,
  prints the trace), and `graphrag resolve-eval`.
- IaC (**AWS CDK, Python** — ADR-0003) for the slice-1 topology subset: VPC with
  private subnets and **no NAT**, the VPC endpoints the in-VPC ingestion needs
  (`s3` gateway, `ecr.api`, `ecr.dkr`, `logs`, `sts`), a **Neptune Serverless**
  cluster at minimum capacity (no public endpoint), an S3 corpus-snapshot bucket
  (public access blocked, encrypted), a **Fargate** task definition for ingestion
  with a **least-privilege** task role, and an **AWS Budgets** alarm. One-command
  `deploy`/`destroy` entrypoints.
- A bundled **fixture corpus** built from real, pinned excerpts of both repos, so
  parse → extract → resolve → query → eval run offline and deterministically.

**Out of scope (this slice):**

- **OpenSearch, embeddings, vector retrieval** — slice 2 (`vector-rag-baseline`).
  The `bedrock-runtime` VPC endpoint and the query Lambda are therefore *not*
  provisioned here; they arrive with the slices that need them.
- **Hybrid orchestration / the three-mode comparison runner** — slice 3.
- **Permission-filtered retrieval** (synthetic visibility labels) — slice 4.
- **Incremental delta re-ingest** — slice 5. This slice does a full (idempotent
  upsert) ingest only; `--rebuild`/delta semantics are slice 5.
- **A trained or ML entity-resolution model.** Charter pattern 1 pins
  normalized-match + alias table; anything learned is out of bounds.
- **Functional source-code parsing** (charter scope) — org-entity Markdown/YAML
  only.
- **Live multi-AZ / HA / scale tuning** (ADR-0002 non-goals).

**Scope note — the topology subset is in scope by design, not creep.** The
design doc's Rollout pins slice 1 to "stand up VPC + Neptune + ingestion + graph
query" and the intent's Decomposition row 1 resolves "into single **Neptune**
nodes"; the topology *concern* (charter pattern 3 / ADR-0002) is therefore
partially realized here for the stores this slice needs, with the rest
(`bedrock-runtime`, OpenSearch, query Lambda) deferred to the slices that need
them.

**Structural rail.** This slice may create exactly these top-level surfaces:
`packages/graphrag/` (the library + CLI), `apps/ingestion/` (the Fargate task),
`apps/infra/` (the CDK app), and a root `pyproject.toml`. It establishes the
monorepo `packages/` + `apps/` split the architecture overview already anticipates;
**no new top-level directory beyond these** (that would need an RFC per AGENTS.md).

**Ask first (Boundaries rails):**

- Adding a runtime dependency beyond `pyyaml` + `boto3` (record per AGENTS.md
  before adding).
- Changing the entity/edge model in a way slices 2–5 depend on (it is a published
  internal interface once vector/hybrid seed from it).
- Pushing multi-hop traversal *down into openCypher* (would let the local and
  Neptune backends diverge in trace shape — deferred to slice 3 deliberately).

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1–AC7 — TDD (fast unit/integration over the fixture corpus).** Parsing,
  extraction, resolution, the eval metric, the traversal/trace, the CLI, and the
  in-memory store are pure-Python and deterministic over the bundled fixture; each
  has a red-stub-first construction test in `plan.md`. The Neptune adapter is
  tested against a **mocked HTTPS/SigV4 endpoint** (no live cluster).
- **AC1 also pins a security control:** the parser uses `yaml.safe_load`; a fixture
  containing a `!!python/object` tag must parse inert (no object construction),
  asserted by a negative test. The `ruff` `S` (flake8-bandit) ruleset is enabled as
  the tool gate that catches an unsafe `yaml.load` regression (`S506`).
- **AC5 — goal-based check, framed as a pytest.** The resolver eval computes
  precision/recall over the hand-labeled sample (drawn from real, pinned repo
  excerpts) and the test *asserts* both ≥0.80. The bar is the de-risk verdict's;
  the test is the open confirmation made mechanical. A full-corpus run over a live
  clone is an opt-in CLI path and a deferred follow-on
  (`graph-ingestion-resolution-full-corpus-eval`).
- **AC8 — goal-based check (`cdk synth` + `aws_cdk.assertions.Template`).** The
  CDK stack is synthesized in-process (Python; no AWS account, no `cdk` CLI
  needed) and the test asserts the synthesized template contains: the VPC with no
  NAT gateway; each required VPC endpoint; the Neptune Serverless cluster **with no
  public endpoint**; the S3 bucket with **public access blocked + encryption**; the
  Fargate task definition with a **least-privilege** task role (scoped `s3` read on
  the corpus bucket, `neptune-db:connect` on the cluster, scoped CloudWatch logs —
  no wildcard `Resource`); and the Budgets alarm **with a threshold + a notification
  subscriber**. Gated to run only when `aws-cdk-lib` is importable (the `infra`
  extra); skipped with a clear marker otherwise.
- **AC9 — manual QA, deferred.** One-command `deploy`/`destroy` and the "no
  billable resource survives destroy" / Budgets-alarm-fires checks require a live
  AWS account; that verification is **deferred**
  (`graph-ingestion-resolution-live-deploy`). The entrypoints, their wiring, and
  the documented procedure ship and are reviewed here.
- **AC10 — goal-based check on trace *structure*.** Narratability is asserted by
  the ordered shape, not substring presence: the CLI emits the seed list, then one
  trace entry **per hop** naming the edge kind + direction and the frontier it
  produced, then the result set — in order. The test asserts the ordered
  seed→hop→result sequence (the AC6 exemplar shape), so a passing test means a
  human can follow the data flow, not merely that tokens appear.

Gates: `ruff` (lint+format, with the `S` security ruleset), `mypy` (typecheck),
`pytest` (tests). Wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [x] **AC1 — Dual-source parse, safely.** Given the fixture corpus, the loader
  parses `community` (`sigs.yaml` + SIG `README.md` front-matter/headings) and
  `enhancements` (`keps/**/kep.yaml` + KEP `README.md`) into typed parsed-document
  records with source provenance, without error; a doc with broken front-matter is
  skipped with a warning, not a crash. The parser uses `yaml.safe_load` — a fixture
  containing a `!!python/object` tag parses inert (asserted by a negative test).
  *(TDD + security)*
- [x] **AC2 — Entity extraction.** Extraction yields the expected **SIG, Person,
  KEP, Subproject** entities from the fixture with their key fields (slug/label,
  handle/display-name, KEP number/title/status/owning-sig, subproject name).
  *(TDD)*
- [x] **AC3 — Edge extraction.** Extraction yields the expected edges —
  **CHAIRS, TECH_LEADS** (Person→SIG), **OWNS** (SIG→KEP), **AUTHORS, APPROVES**
  (Person→KEP), **HAS_SUBPROJECT** (SIG→Subproject) — from the fixture. *(TDD)*
- [x] **AC4 — Cross-source resolution into single nodes (with negatives).** A SIG
  slug present in *both* sources resolves to exactly one SIG node; a GitHub handle
  present in both `sigs.yaml` leadership and `kep.yaml` author/approver lists
  resolves to exactly one Person node, including the `@handle`-vs-bare-`handle` and
  mixed-case (`@thockin` / `thockin` / `@SergeyKanzhelev`) normalization cases; the
  alias table merges a known prose-name ↔ `@handle` case. **Negatives hold:** two
  distinct handles do *not* merge, and a display name absent from the alias table
  stays split (no false merge). The resolved graph has no duplicate node for any
  shared entity. *(TDD)*
- [x] **AC5 — Resolver clears the ≥80% bar (open confirmation).** Running the
  resolver over the hand-labeled sample of shared entities (real SIG slugs + GitHub
  handles, with negatives) yields **precision ≥ 0.80 AND recall ≥ 0.80**; the test
  asserts both. The sample is drawn from real, pinned repo excerpts so the bar is
  empirical. *(goal-based pytest)*
- [x] **AC6 — CLI multi-hop graph query with a visible trace.** `graphrag
  graph-query` traverses from a seed entity over a bounded (1–2 hop) edge-step
  sequence and returns the correctly-scoped result set; its stdout names each seed
  entity, each hop (edge kind + direction), and each resulting node — legible
  enough to narrate. Verified on the entity-led exemplar: "the KEPs owned by the
  SIG that `@thockin` tech-leads" → seed `@thockin` →`TECH_LEADS`→ `sig-network`
  →`OWNS`→ {`kep-2086`, `kep-1880`} (and *not* the sig-node KEP-1287). *(TDD +
  narratability check)*
- [x] **AC7 — Backend-abstracted store; Neptune adapter is injection-safe and
  IAM-mediated.** The same traversal yields identical results/trace against the
  in-memory store and the Neptune adapter (the local≡Neptune **trace-identity**
  claim against a *live* cluster is part of the deferred AC9). The Neptune adapter
  emits **parameterized** openCypher for reads (no string interpolation of values),
  targets `https://` with TLS verification on, and resolves credentials via the
  default `botocore` provider chain (the task role) — no plaintext-credential env
  read at the call site. Exercised (upsert + neighbors) against a mocked
  SigV4/HTTPS endpoint; a non-2xx response raises loudly with the body. *(TDD with
  mock)*
- [x] **AC8 — IaC synthesizes the slice-1 topology, securely.** The CDK (Python)
  app synthesizes a stack containing: a VPC with private subnets and **no NAT
  gateway**; the VPC endpoints `s3` (gateway), `ecr.api`, `ecr.dkr`, `logs`, `sts`;
  a **Neptune Serverless** cluster at minimum capacity **with no public endpoint**;
  an S3 corpus-snapshot bucket with **public access blocked and default
  encryption**; a **Fargate** task definition whose task role is **least-privilege**
  (scoped `s3` read on the corpus bucket ARN, `neptune-db:connect` on the cluster
  resource, scoped CloudWatch `logs` — no wildcard `Resource`); and an AWS
  **Budgets** alarm with a concrete threshold and a notification subscriber — per
  ADR-0002. *(goal-based: `cdk synth` + template assertions; CDK-env-gated)*
- [ ] **AC9 — One-command deploy/destroy** *(deferred: graph-ingestion-resolution-live-deploy)*. `deploy` provisions the stack, uploads
  the corpus snapshot, and runs the ingestion task once; `destroy` removes every
  billable resource. Entrypoints + documented procedure ship here; the **live-AWS
  verification** is the deferred part. *(manual QA)*
- [x] **AC10 — Every stage is narratable (ordered trace).** `ingest`,
  `graph-query`, and `resolve-eval` each emit a human-readable trace whose
  **structure** is asserted: parsed counts → resolved merges (before/after) for
  ingest; seed list → per-hop entry (edge kind + direction + frontier) → result set
  in order for graph-query; TP/FP/FN → precision/recall for resolve-eval. No
  black-box hop (charter principle 1). *(goal-based)*

## Changelog

- 2026-06-23 — Spec authored (slice 1, lead), then `Approved` after spec-stage
  adversarial + security review (ADR-0003 added; AC9/full-corpus deferrals anchored
  in backlog; `yaml.safe_load`, least-privilege role, no-public-Neptune,
  S3-block-public, Budgets-threshold, parameterized-openCypher + TLS/credential
  controls pinned as ACs; AC10 tightened to trace structure).
- 2026-06-23 — `Implementing`: slice built; AC1–AC8 and AC10 met (53 tests + ruff/
  mypy green); diff-stage review fixes applied (S3-key path-traversal guard,
  Neptune malformed-result guard, IaC no-public-ingress / TLS-bucket-policy /
  wildcard-resource synth assertions).

**Why Status stays `Implementing`, not `Shipped`:** every offline-verifiable AC is
met, but the slice's product *is* a deployable stack and AC9 (live `cdk
deploy`/`destroy` + teardown smoke check) cannot be verified from CI — it is
deferred to `graph-ingestion-resolution-live-deploy`. The spec moves to `Shipped`
when a maintainer records that live verification.
