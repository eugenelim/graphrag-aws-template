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
