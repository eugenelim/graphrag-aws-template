# Roadmap

> Direction for the next 2-4 quarters. **Not** commitments. The whole point
> of writing this down is that it can change.

**Last updated:** 2026-07-24
**Reviewed:** quarterly. Next review: 2026-10-24.

If the current date is more than 90 days past "Last updated", treat this
file as stale and ask before relying on it.

---

## ini-002 — Business Operations Knowledge Graph

Initiative pivot ratified in [RFC-0004](../rfc/0004-biz-ops-kg-pivot.md)
(2026-07-23). Replaces the Kubernetes demo corpus with a generic business-operations
knowledge platform — Neptune SPARQL/RDF, OWL ontology, MCP tool server, git-delta
ingestion, OTEL observability.

### Shipped (wave 1–3, PRs #65–#83)

- **Neptune SPARQL engine** (Terraform) — named-graph primitive, IAM-auth
- **NeptuneSparqlStore** — SigV4 SPARQL client; rdflib in-memory offline substitute
- **MCP proxy** — stdio→HTTPS proxy for AI IDE (Claude Code, Cursor) connections
- **MCP mock server** — rdflib in-memory + HashEmbedder, all six tools, no AWS
- **SPARQL templates registry** — fixed read-only parameterized SPARQL library
- **Text2SPARQL guarded** — LLM-authored SPARQL behind mutation denylist + IAM backstop
- **MCP tool server** — production wiring (NeptuneSparqlStore + Bedrock routing)
- **NormativeRetriever** — exhaustive SPARQL + vector-threshold union for `get_policies`
- **SHACL validation gate** — pyshacl gate + quarantine INSERT + CI completeness gate
- **RDF/OWL ontology loader** + `graphrag.provenance` — ontology Neptune loader, PROV-O emission

### Now (wave 4 — in-flight)

Integration, observability, and API Gateway ingress. All gate on `mcp-tool-server`
(shipped).

- **Multi-strategy router** — server-side rules-first cascade over six strategies
  (`hybrid_graph`, `structured`, `graph_expand`, `vector_only`, `global`,
  `normative_exhaustive`); transparent strategy trace in every response
- **OTEL instrumentation** — AWS ADOT Lambda layer; OTLP to CloudWatch; span tree
  per `ask`/`get_policies` call; content off-by-default (ADR-0015)
- **MCP Lambda infra (Terraform)** — Lambda + ADOT layer + Function URL; wires
  OTEL collector; reserved concurrency cap
- **API Gateway HTTP API** — usage plan + API key auth; human/IDE ingress path;
  `x-api-key` header, no SigV4 on the client
- **Git ingestion trigger** — EventBridge rule on git push / scheduled pull;
  triggers Fargate ingestion task; commit-SHA delta pipeline (ADR-0016)

### Next (wave 5+ candidates — appetite-gated)

- **Ingestion extraction + cleanse** — format router (pandoc/docling/markitdown/
  Textract), Silver artifact write, PII flagging, SHACL gate, Gold artifact
  (chunks + PROV-O)
- **Git ingestion pipeline** — git clone/pull, commit-SHA delta, full medallion
  Bronze→Silver→Gold→Serving pipeline; DELETE orphan triples

### Later

- **OTEL CloudWatch alarm** — alarm on ADOT collector export-failure metric to
  detect a broken OTLP pipeline in production (backlog: `otel-collector-export-failure-alarm`)
- **Neptune audit-log export** — `enable_cloudwatch_logs_exports = ["audit"]` for
  detective control in regulated deployments (backlog: `neptune-audit-log-export`)
- **Neptune SPARQL DROP GRAPH IAM action verification** — live confirm that
  `DROP GRAPH` is gated by `DeleteDataViaQuery` under `mcp_lambda_role`
  (backlog: `neptune-sparql-dropgraph-iam-action-verify`)
- **Budgets threshold above standing floor** — raise Budgets alarm threshold
  above the ~$226/mo idle floor so the alert fires on traffic, not at idle
  (backlog: `biz-ops-budgets-threshold-above-standing-floor`)
- **BM25/sparse retrieval leg** — hybrid dense+sparse in OpenSearch
- **Cross-encoder reranking** — post-retrieval rerank pass

---

## Not in scope

- **Production authorisation / real ACLs / multi-tenancy.** Visibility labels
  (`biz:visibility`, `biz:hasPII`) and PII flags are labels and default query
  filters — not enforced access controls. This is a template; adopters add authz.
- **Functional source-code parsing.** The platform is Markdown + structured
  business-operations documents only.
- **A polished graphical UI.** The interface is MCP over HTTPS (CLI, AI IDE,
  AI agent).
- **High availability / scale / latency tuning.** Single-AZ, demo-scale by
  design (ADR-0002).
- **OWL reasoning / materialised inference.** Schema-only by decision (ADR-0012).

---

## Archive — pre-pivot K8s demo slices (shipped before ini-002)

> These slices built the original Kubernetes demo corpus
> (`community` + `enhancements` repos). Superseded by RFC-0004 (2026-07-23).

- Graph ingestion + cross-source entity resolution (`graph-ingestion-resolution`)
- Vector RAG baseline (`vector-rag-baseline`)
- Hybrid orchestration + three-mode comparison (`hybrid-orchestration`)
- Permission-filtered retrieval (`permission-filtered-retrieval`)
- Incremental delta re-ingest (`incremental-delta-reingest`)
- Pattern catalog: openCypher templates, text2openCypher, metadata filtering,
  parent-child retrieval, global community summary, schema-guided LLM extraction

---

## How this file is maintained

- **Owners:** the maintainers.
- **Updates:** roadmap items move between sections via small PRs. Substantive
  additions or deletions go through an RFC.
- **Review cadence:** quarterly. The review updates the "Last updated" date
  even if no items change.
- **Drift signal:** if items in "Now" haven't moved in two consecutive reviews,
  either they're not actually being worked on (move them out) or the roadmap
  doesn't reflect what the team is doing (rewrite it to match).
