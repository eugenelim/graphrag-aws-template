# Changelog

All notable user-visible changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Maintenance: this file is updated in the same PR that introduces the
> change. CI will warn (configurable: block) when a PR touches code that
> changes user-visible behavior but does not touch this file.
>
> Entries can be drafted from conventional commits: `git log --oneline`
> filtered to `feat:` and `fix:` since the last tag is a starting point,
> not a finished product. Rewrite for users, not contributors. See the
> [Common Changelog guidance](https://common-changelog.org/) — the audience
> is humans who use the software, not humans who wrote it.

## [Unreleased]

### Added

- **Permission-filtered retrieval via synthetic visibility labels (slice 4).** Synthetic
  visibility labels (a **teaching stand-in for ACLs — not real authz**) are attached to the
  corpus at ingest and propagated to **both** stores (Neptune node *and* edge properties;
  OpenSearch chunk metadata). A `--persona` flag (CLI) / `persona` field (the in-VPC query
  Lambda) selects a clearance — `public-reader`, `member`, or `maintainer` — and **all three
  retrieval modes** (vector, graph, hybrid) return results filtered to what that persona may
  see. The graph filter is applied **during traversal, on edges**, so a forbidden entity
  never enters the frontier and cannot leak via a reachability path; the vector filter rides
  the OpenSearch k-NN search as a metadata filter. Each trace names the active clearance and
  what was filtered out. Omitting the persona leaves retrieval unrestricted (slice-1–3
  behavior unchanged). No new dependency, no new infrastructure resource — the persona rides
  the existing query Lambda's request body. The labels are presented as a synthetic stand-in
  everywhere they surface.

- **Hybrid seed-and-expand retrieval + three-mode comparison (slice 3).** A question
  now runs the **hybrid** path: it seeds graph entities from *both* the owners of the
  top-k vector hits and the entities linked from the question itself, expands 1–2 hops
  in the graph, merges the prose chunks with the structural facts, and synthesizes a
  grounded answer with **Bedrock Claude** (via the Converse API; an offline
  deterministic synthesizer backs CI). New `graphrag` CLI verbs: `hybrid-query` and
  `compare` (vector-only / graph-only / hybrid side by side), offline by default and
  live via a SigV4-signed call to the in-VPC query Lambda's IAM-auth Function URL. Each
  verb prints an ordered **seeds-by-source → hops → citations → answer** trace (no
  black-box hop). A consolidated **showcase** query set (≥5–6 per mode) and a presenter
  script (`docs/guides/tutorials/three-mode-demo.md`) drive the demo from one place.
- **Slice-3 AWS topology (CDK).** The in-VPC **query Lambda** behind an **IAM-auth
  Function URL** (the only public ingress; invoke scoped to a named principal, never
  `*`); a least-privilege role (Neptune-data + OpenSearch-data + Bedrock invoke for
  Titan *and* the synthesis Claude inference-profile + foundation-model ARNs, no
  wildcard); a stack-managed log group; the Function URL exported. Scale-to-zero — the
  Budgets limit holds at $150 (no new standing cost). No new runtime dependency (the
  `boto3` floor moves `>=1.34 → >=1.35` for `bedrock-runtime.converse`).
- **Slice 3 verified live + torn down (2026-06-24).** Deployed end-to-end; the SigV4
  Function-URL hybrid query returned in **22.7 s** with the dual-seed trace (`question:
  person:thockin` + vector owners → 2-hop expansion to the owned KEPs) + a Bedrock Claude
  answer (AC9). The live run found + fixed two infra bugs — the `deploy.sh`
  `InvokerRoleArn` gap and the `QuerySg` outbound block — and added a **batched
  `neighbors_batch`** (one openCypher query/hop, default fan-out keeps the trace
  identical) so the expansion is demo-fast against Neptune Serverless. Stack torn down;
  no billable resource remains.
- **Graph ingestion + cross-source entity resolution (slice 1).** Parse Markdown +
  YAML from the Kubernetes `community` and `enhancements` repos, extract
  SIG/Person/KEP/Subproject entities and their edges, and resolve shared entities
  across both sources into single graph nodes (normalized match + a small alias
  table — no model). New `graphrag` CLI: `ingest`, `graph-query` (bounded multi-hop
  traversal with a visible trace), and `resolve-eval`. The resolver clears the
  de-risk verdict's ≥80% open-confirmation bar (precision 1.00, recall 0.875 on a
  real, pinned labeled sample).
- **Slice-1 AWS topology (CDK).** A teardown-first stack — no-NAT VPC, the
  `s3`/`ecr.api`/`ecr.dkr`/`logs`/`sts` VPC endpoints, Neptune Serverless, an
  encrypted private S3 corpus bucket, a least-privilege Fargate ingestion task, and
  a Budgets cost alarm. Live deploy/destroy verification is tracked in the backlog.
- **Vector RAG baseline (slice 2).** Chunk the prose-rich doc subset (SIG/KEP
  READMEs) → Amazon Titan Text Embeddings v2 (256-dim) → single-node Amazon
  OpenSearch k-NN. New CLI verbs: `vector-ingest`, `vector-query` (top-k chunks with
  a legible retrieval trace + source provenance + owning entity), and `vector-eval`.
  The baseline is **credible, not a strawman** (charter principle 2): a curated
  query set clears **hit@5 = 1.0** on semantic-led questions against real Titan v2
  embeddings (reproducible from committed frozen vectors) while honestly **missing**
  the entity-scoping questions the slice-3 graph mode will win.
- **Slice-2 AWS topology (CDK).** Adds a single-node, VPC-private, encrypted
  OpenSearch domain (k-NN) and the `bedrock-runtime` VPC endpoint to the same
  teardown-first stack, with least-privilege `es:ESHttp*` + `bedrock:InvokeModel`
  roles; an in-VPC vector smoke probe verifies live index→retrieve; ingestion now
  single-parse dual-writes the graph and vector stores. Budgets limit raised to
  `$150/mo` for the second standing store.

### Changed

- (nothing yet)

### Deprecated

- (nothing yet)

### Removed

- (nothing yet)

### Fixed

- (nothing yet)

### Security

- (nothing yet)
