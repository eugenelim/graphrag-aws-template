# Architecture

The authoritative architecture record for this repository. One footprint — start here.

## Primary reference

**[`biz-ops-knowledge-graph/design.md`](biz-ops-knowledge-graph/design.md)**
— the current architecture in three views: conceptual, logical, and physical.
Covers the OWL ontology, Neptune SPARQL named graphs, MCP tool server, server-side
routing, OTEL observability, and git-based ingestion. **Read this first.**

## Supplementary operational docs

| Doc | What it covers | Status |
|---|---|---|
| [`overview.md`](overview.md) | Monorepo layout — apps, packages, tools, docs | Current (updated for ini-002) |
| [`security.md`](security.md) | Trust boundaries and IAM posture per slice | Partially current — being updated for ini-002 SPARQL/MCP boundaries |
| [`deployment-and-verification.md`](deployment-and-verification.md) | Deploy, verify, teardown mechanics | Partially current — Terraform tiers are accurate; smoke probes being updated for SPARQL |
| [`develop-and-test-offline.md`](develop-and-test-offline.md) | Offline-first dev posture; no-AWS test patterns | Partially current — offline SPARQL store (rdflib) replaces offline Neptune in ini-002 |

## Archive

[`_archive/`](_archive/) holds docs superseded by ini-002. Kept for historical
reference; scheduled for deletion once ini-002 implementation is complete.

| Archived doc | Superseded by |
|---|---|
| `_archive/graphrag-aws-architecture/design.md` | `biz-ops-knowledge-graph/design.md` |
| `_archive/infrastructure.md` | Physical view in `biz-ops-knowledge-graph/design.md` |
| `_archive/deployment-timing.md` | Timings will be re-baselined after ini-002 deploy |

## How to keep this section healthy

- Architecture docs describe **what is** — current code structure, current infra.
  Decisions go in [`../adr/`](../adr/); proposals go in [`../rfc/`](../rfc/).
- Update the primary design doc in the same PR as any infra or interface change.
- When a doc is superseded, move it to `_archive/` with a note; do not leave
  competing views in the live directory.
