# Architecture

The authoritative architecture record for this repository. One footprint — start here.

## Reading order

| # | Doc | What it covers | Status |
|---|---|---|---|
| 1 | **[`biz-ops-knowledge-graph/design.md`](biz-ops-knowledge-graph/design.md)** | Full platform architecture — conceptual, logical, and physical views. OWL ontology, Neptune SPARQL named graphs, MCP tool server, medallion ingestion, extraction pipeline, PROV-O provenance, OTEL, client connection modes. | Current — ini-002 |
| 2 | [`security.md`](security.md) | Trust boundaries, IAM roles, and network posture | Partially current — being updated for ini-002 SPARQL/MCP boundaries |
| 3 | [`deployment-and-verification.md`](deployment-and-verification.md) | Deploy, verify, and teardown mechanics | Partially current — Terraform tiers accurate; smoke probes being updated for SPARQL |
| 4 | [`develop-and-test-offline.md`](develop-and-test-offline.md) | Offline-first dev posture; working without a live AWS stack | Partially current — rdflib SPARQL store replaces offline Neptune in ini-002 |
| 5 | [`overview.md`](overview.md) | Monorepo layout — apps, packages, tools, docs directories | Current |

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
