# ADR-0011: Neptune SPARQL/RDF over openCypher/LPG: named-graph primitive and Text2SPARQL read-only guard

- **Status:** Accepted
- **Date:** 2026-07-23
- **Decision-makers:** eugenelim
- **Supersedes:** [ADR-0004](0004-text2cypher-read-only-guard.md) (openCypher anchoring superseded; read-only control carried forward under SPARQL grammar)
- **Related:** [RFC-0004 §D2, §Security posture](../rfc/0004-biz-ops-kg-pivot.md); [ADR-0002](0002-ephemeral-vpc-store-topology.md) (topology unchanged); [ADR-0012](0012-owl-schema-only-and-named-graph-partition.md) (partition data model — recorded separately); [ADR-0004](0004-text2cypher-read-only-guard.md) (superseded); `spec-text2sparql-guarded`

## Decision summary

- **Decision:** We will run Neptune in SPARQL/RDF mode and re-ratify the ADR-0004 read-only query-role guard as the Text2SPARQL guard, with the mutation denylist re-authored for SPARQL grammar.
- **Because:** Named graphs are a first-class RDF primitive required by the retrieval-correctness partition (D3); LPG has no equivalent; SPARQL Update's `DROP GRAPH` makes the read-only guard more critical, not less.
- **Applies to:** The Neptune engine choice (SPARQL/RDF vs openCypher/LPG) and the `mcp_lambda_role` Neptune IAM grant.
- **Tradeoff accepted:** Full corpus re-ingest (openCypher and SPARQL data models are non-interoperable on Neptune); SPARQL is less ergonomic than openCypher for traversals; SPARQL has no path-discovery.
- **Revisit if:** AWS adds a native named-graph primitive to the openCypher/LPG engine, or a Neptune interop layer makes the swap reversible without re-ingest.

## Context

The project previously used openCypher over a labelled property graph (LPG) on Amazon Neptune (ADR-0001 / ADR-0004). RFC-0004 pivots to a business-operations knowledge platform whose retrieval-correctness model (D3) rests on **named-graph partitioning** — a hard query-time isolation boundary between normative (exhaustive recall) and descriptive (best-match) knowledge. Named graphs are a first-class SPARQL/RDF primitive (`FROM NAMED` / `GRAPH {}` scoping) with no LPG equivalent — the engine swap is **required** by D3, not a preference.

The switch is a **one-way door for data**: Amazon Neptune hosts either a property-graph database (openCypher) or an RDF database (SPARQL) on the same cluster; the two models are non-interoperable ([AWS Neptune FAQ](https://aws.amazon.com/neptune/faqs/)). Existing openCypher corpus data must be discarded and re-ingested as RDF. The VPC topology (ADR-0002), IAM auth mechanism, and subnet placement are unchanged.

The engine swap also re-opens the read-only guard. ADR-0004 guarded LLM-authored openCypher mutations with a four-layer defense whose primary backstop was the `mcp_lambda_role` IAM grant scoped to `ReadDataViaQuery` only. SPARQL Update grammar introduces `DROP GRAPH` — a single statement that, if escaped under an over-broad grant, would destroy an entire named graph (e.g. `urn:graph:normative`) and silently collapse the exhaustive-recall guarantee the platform depends on. This makes re-ratifying the read-only guard with SPARQL-grammar-aware controls more critical than under openCypher, not less.

## Decision

> We will run Neptune in SPARQL/RDF mode (endpoint `/sparql`; SPARQL 1.1 query + Update). Named graphs (`urn:graph:normative`, `urn:graph:descriptive`, `urn:graph:taxonomy`, `urn:graph:ontology`, `urn:graph:quarantine`) are the isolation boundary for retrieval correctness (ADR-0012). The `mcp_lambda_role` Neptune IAM grant is **`ReadDataViaQuery` + `connect` only** — no `WriteDataViaQuery`, no `DeleteDataViaQuery` — carrying forward ADR-0004's primary backstop under SPARQL grammar. The app-layer mutation denylist is re-authored for SPARQL keywords (`INSERT`, `DELETE`, `DROP`, `CLEAR`, `LOAD`, `CREATE`) in place of the openCypher list. The Neptune query timeout is carried forward as the read-cost backstop.

Concretely:

1. **Engine endpoint:** `/sparql` replaces `/openCypher`; SPARQL 1.1 query language; data as RDF triples (Turtle / N-Triples / JSON-LD).
2. **Named graphs** act as the partition boundary; `FROM NAMED` scoping is a hard constraint on all retrieval queries — not a routing hint.
3. **`mcp_lambda_role` IAM grant:** `ReadDataViaQuery` + `connect` only (same role as ADR-0004's proven backstop; re-confirmed for SPARQL Update). The `ingestion_task_role` retains `WriteDataViaQuery` (legitimate writes).
4. **App-layer SPARQL denylist (layer 1, not the guarantee):** before any model-authored SPARQL is executed, the string is checked for SPARQL Update keywords and unbounded traversal patterns. Failure refuses the query and feeds the bounded self-heal loop. This is belt-and-suspenders — the IAM scope is the load-bearing control.
5. **Engine query timeout:** carried forward from ADR-0004 as the read-cost backstop for runaway reads.
6. **Offline substitute:** `store/neptune_sparql_memory.py` (`rdflib` in-memory SPARQL) replaces `store/neptune_memory.py`; the full named-graph partition model is testable without AWS credentials.
7. **OpenSearch `named_graph` field:** each chunk document gains a `named_graph` field used as a mandatory `bool.filter` on all k-NN queries — partition isolation inside the vector store, not only in Neptune.

## Decision drivers

- **Named-graph isolation is required by D3.** The retrieval-correctness guarantee rests on a hard partition boundary expressible only as named graphs in RDF/SPARQL. LPG has no equivalent primitive.
- **W3C standard stack.** SPARQL/RDF unlocks OWL (ontology), SHACL (validation), and PROV-O (provenance) — the three W3C standards the rest of the design uses (ADR-0012). None are natively composable with the openCypher/LPG model.
- **SPARQL Update severity increases the guard urgency.** `DROP GRAPH` under a write-capable grant destroys a partition silently; this ADR re-confirms the IAM read-only scope as load-bearing before live deploy.
- **Offline substitute credibility.** `rdflib` is a production-quality Python SPARQL engine; the offline CI gate exercises the same SPARQL queries without AWS. No equivalent offline substitute exists for openCypher.

## Consequences

**Positive:**
- Named-graph partition isolation is structural — a SPARQL query scoped to `urn:graph:normative` structurally cannot touch `urn:graph:descriptive`; isolation is not routing-hint-dependent.
- W3C standard stack (OWL, SHACL, PROV-O) is unlocked without an additional service.
- `rdflib` offline substitute enables full partition semantics in CI with no AWS credentials.
- The OpenSearch `named_graph` filter extends partition isolation into the vector store.
- The read-only backstop is proven at the SPARQL endpoint — the live-smoke AC in `spec-text2sparql-guarded` confirms `mcp_lambda_role` cannot execute `DROP GRAPH` or `INSERT DATA`.

**Negative:**
- **Full corpus re-ingest required.** openCypher and SPARQL/RDF are non-interoperable on the same Neptune cluster. Existing openCypher graph data is discarded; everything re-ingests as RDF from the git source.
- **SPARQL is less ergonomic than openCypher** for typical graph traversals; triple-pattern matching is more verbose than openCypher `MATCH` clauses.
- **No path-discovery in SPARQL.** Neptune/AWS flags that SPARQL cannot report *which* path a traversal took. The design uses reachability/expansion, not path reporting — accepted limitation.
- **`DROP GRAPH` mutation risk.** A single escaped SPARQL Update statement destroys a partition; no openCypher equivalent exists. The IAM read-only backstop is load-bearing, not optional hardening.

**Revisit if:** AWS adds a native named-graph primitive to the LPG/openCypher engine; or a Neptune interop mode makes the data models shareable without re-ingest.

## Confirmation

- **Mode:** lint/CI + architecture fitness test + live smoke
- **Signal (Terraform plan assertion, `apps/infra-tf/tests/test_plan.py`):** `mcp_lambda_role` Neptune statement grants `ReadDataViaQuery` + `connect` and does **not** grant `WriteDataViaQuery` or `DeleteDataViaQuery`. `ingestion_task_role` retains the full read-write grant. Fails if a future edit re-broadens the query-role grant.
- **Signal (offline partition gate):** `rdflib` in-memory — a normative-scoped SPARQL `SELECT` returns zero descriptive results; an unfiltered normative `SELECT` returns all normative triples (no top-k drop). Part of the offline CI gate suite.
- **Signal (live smoke, `spec-text2sparql-guarded` AC):** a test-forced SPARQL `DROP GRAPH` and `INSERT DATA` under `mcp_lambda_role` are rejected by IAM at the `/sparql` endpoint — proving the backstop at the engine, not just the grant's shape.
- **Owner:** eugenelim; gate owner: `spec-text2sparql-guarded`

## Alternatives considered

- **openCypher/LPG (do-nothing).** Keeps the existing engine and code. *Rejected against the named-graph-isolation driver:* LPG has no named-graph primitive; the D3 partition boundary cannot be expressed as a structural constraint. ISO/IEC GQL (the ISO standard that formalized LPG) still has no named-graph primitive, so this is not a near-term gap to close.
- **Neptune Analytics (Poseidon engine).** Offers LPG+RDF interop on a graph analytics engine. *Rejected:* analytics full-scan posture (optimised for bulk traversal, not interactive retrieval); a different AWS service from Neptune Serverless; adds a second standing engine cost; over-scoped for an interactive teardown-first demo.
- **Non-Neptune triple store (Apache Jena, Oxigraph, etc.).** Removes the Neptune dependency. *Rejected:* leaves the managed-AWS teaching posture the demo requires; no native IAM auth; additional operational surface.
- **Keep openCypher + simulate partitions via node labels.** Label-based "partitions" inside a single LPG. *Rejected:* a query spanning labels is valid openCypher — isolation is a hint, not a structural guarantee. The guarantee cannot be stated as a synth-checkable fact.

## References

- [RFC-0004 §D2, §Security posture](../rfc/0004-biz-ops-kg-pivot.md)
- [AWS Neptune FAQ — openCypher and SPARQL non-interoperable on one cluster](https://aws.amazon.com/neptune/faqs/)
- [AWS Neptune SPARQL 1.1 standards compliance — named graph support](https://docs.aws.amazon.com/neptune/latest/userguide/feature-sparql-compliance.html)
- [AWS Well-Architected: RDF for federation/named-graph partitioning](https://docs.aws.amazon.com/prescriptive-guidance/latest/neptune-well-architected-framework/performance-efficiency-pillar.html)
- [Neptune IAM data-access actions](https://docs.aws.amazon.com/neptune/latest/userguide/iam-dp-actions.html)
- [OWASP Top 10 for LLM Applications 2025 — LLM01 Prompt Injection](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- ADR-0002 (topology unchanged); ADR-0004 (superseded — openCypher guard replaced); ADR-0012 (partition data model)
