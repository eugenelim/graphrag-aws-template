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

Trust boundaries, IAM roles, and network segmentation for the ini-002
SPARQL/MCP platform. Read this before touching any IAM or infrastructure code.

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

Work is coordinated by `workspace.toml` (ini-002 initiative).

- **Shape queue — 16 items across 6 waves — fully shipped.** All ADRs (0011–0016)
  and feature specs are agreed. The shaping queue is empty.
- **Work queue — wave 1–3 shipped; wave 4 in-flight.** Wave 1–3: Neptune Terraform,
  NeptuneSparqlStore, MCP mock server, MCP proxy, SPARQL templates, Text2SPARQL,
  SHACL validation, MCP tool server, NormativeRetriever, RDF/OWL ontology loader
  (PRs #65–#83). Wave 4: multi-strategy router, OTEL instrumentation, MCP Lambda
  infra, API Gateway, git ingestion trigger.

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
