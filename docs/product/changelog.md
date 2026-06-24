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
