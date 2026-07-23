# Architecture

Authoritative architecture reference for ini-002 — the Business Operations Knowledge
Graph platform. One footprint, one reading order.

---

## Reading order

### 1 · Repo orientation — [`overview.md`](overview.md)

Where things live: apps, packages, tools, docs directories and what goes in each.
Read this first if you're new to the codebase. (5 min)

---

### 2 · Platform architecture — [`biz-ops-knowledge-graph/design.md`](biz-ops-knowledge-graph/design.md)

The full platform design in three views. (30–45 min)

| View | What you'll learn |
|---|---|
| **Conceptual** | The two knowledge kinds (normative vs descriptive) and why they must not share a retrieval path; the OWL ontology and named graph partitioning; PII labelling model; git as canonical source |
| **Logical** | MCP tool server (FastMCP + Mangum); 6 generic tools and when each is called; server-side routing strategy matrix; medallion ingestion pipeline (Bronze → Silver → Gold → Serving); format-specific extraction router; cleansing gates; PROV-O provenance and citation format; OTEL span tree |
| **Physical** | AWS resource inventory and config; IAM roles (4 roles, no wildcard Resource); three client connection modes (local mock / API Gateway HTTP API / Function URL SigV4); risks and failure modes with recovery paths |

---

### 3 · Security posture — [`security.md`](security.md)

Trust boundaries, IAM roles, and network segmentation per implementation slice. Read
this before touching any IAM or infrastructure code.

> Partially current — being updated for ini-002 SPARQL/MCP trust boundaries.

---

### 4 · Local development — [`develop-and-test-offline.md`](develop-and-test-offline.md)

How to work without a live AWS stack. The offline mock server (rdflib in-memory SPARQL
store + HashEmbedder + TemplateSynthesizer) runs all six MCP tools against the fixture
corpus without AWS credentials.

> Partially current — rdflib SPARQL store replaces offline Neptune in ini-002;
> `RuleText2CypherGenerator` becomes `RuleText2SPARQLGenerator`.

---

### 5 · Deploy and verify — [`deployment-and-verification.md`](deployment-and-verification.md)

Deploy the stack, run smoke probes, tear down cleanly. Read this when you're ready to
deploy to AWS — not before.

> Partially current — Terraform tiers are accurate; SPARQL and MCP smoke probes
> being updated for ini-002.

---

## Implementation sequence

Work is coordinated by `workspace.toml` (ini-002 initiative, status: shaping).
Shape artifacts (ADRs and specs) gate implementation work across two queues:

- **Shape queue — 16 items across 6 waves.** Waves 1–2: pivot rationale and core
  technology decisions (Neptune SPARQL, OWL schema-only). Waves 3–4: data model,
  routing, ingestion, and feature specs. Waves 5–6: MCP interface and observability.
- **Work queue — 20 items across 4 waves.** Nothing in Wave 2+ starts until its
  named shape dependencies are agreed. Wave 2 builds the SPARQL store and mock MCP
  server in parallel (mock has no store dependency). Wave 3 is the live feature
  implementations (extraction, ontology loader, git ingestion, MCP tool server,
  mcp-proxy). Wave 4 is integration, OTEL, and API Gateway infra.

Run `workspace-status` to see ready, blocked, and active items.

---

## Archive

[`_archive/`](_archive/) holds docs superseded by ini-002. Retained for historical
reference; scheduled for deletion once ini-002 implementation is complete.

| Archived | Superseded by |
|---|---|
| `_archive/graphrag-aws-architecture/design.md` | `biz-ops-knowledge-graph/design.md` |
| `_archive/infrastructure.md` | Physical view in `biz-ops-knowledge-graph/design.md` |
| `_archive/deployment-timing.md` | Will be re-baselined after ini-002 deploy |

---

## Keeping this healthy

- Architecture docs describe **what is** — current code structure, current infra.
  Decisions go in [`../adr/`](../adr/); proposals go in [`../rfc/`](../rfc/).
- Update the primary design doc in the same PR as any infra or interface change.
- When a doc is superseded, move it to `_archive/` with a banner note.
