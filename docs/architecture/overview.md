# Architecture Overview

> The map of this monorepo. Read this first when exploring. Updated whenever
> the directory layout or major dependencies change.

## Layout

```
.
├── AGENTS.md             # canonical agent context (CLAUDE.md is a symlink)
├── apps/                 # deployable applications
│   └── <app-name>/       # one directory per app
├── packages/             # shared libraries (consumed by apps and other packages)
│   └── <package-name>/
├── tools/                # build, dev, and ops tooling — not shipped to users
├── docs/
│   ├── CHARTER.md        # mission, scope, principles (one page)
│   ├── CONVENTIONS.md    # how we work
│   ├── adr/              # architecture decisions (frozen history)
│   ├── rfc/              # proposals (governance)
│   ├── specs/            # feature specs and plans
│   ├── architecture/     # this directory — current code structure (for contributors)
│   ├── product/          # current product state (roadmap, changelog) — for maintainers
│   └── guides/           # user-facing docs (Diátaxis: tutorials, how-to, reference, explanation)
├── .claude/
│   ├── skills/           # agent workflows for repeating tasks (each skill owns its templates under `assets/`)
│   ├── agents/           # subagent definitions
│   └── commands/         # custom slash commands
└── .github/              # CI, issue and PR templates
```

## Apps and packages

Wave 3 (PRs #65–#83) shipped the core knowledge-platform library components in
`packages/graphrag/`. The wave-4 Terraform additions (MCP Lambda + ADOT, API
Gateway, EventBridge git-ingestion trigger) are in-flight and not yet deployed.

| Path | What | Stack |
| --- | --- | --- |
| `packages/graphrag/` | Core library and CLI. **Store layer:** `store/neptune_sparql.py` — SigV4 SPARQL client over Neptune (`/sparql` endpoint, IAM-auth, ADR-0011); `store/neptune_sparql_memory.py` — rdflib in-memory substitute for offline CI. **MCP tool server** (`mcp/`): six generic typed tools (`ask`, `search`, `search_graph`, `get_policies`, `query`, `summarize`) implemented with FastMCP + Mangum (ADR-0014); production wiring to `NeptuneSparqlStore` + Bedrock routing (`mcp/_production.py`); mock server (`mcp/_mock.py`) runs rdflib in-memory with no AWS credentials. **MCP proxy** (`mcp_proxy/`): stdio→HTTPS proxy for AI IDE connections. **SPARQL templates** (`sparql_templates.py`): fixed read-only parameterized SPARQL library; the LLM selects a template id, never authors query text. **Text2SPARQL guard** (`text2sparql/`): LLM-authored SPARQL behind a mutation-denylist validator, bounded self-heal, and a Neptune read-only IAM backstop (ADR-0011). **Normative retrieval** (`normative/`): `NormativeRetriever` — exhaustive SPARQL + vector-threshold union over `urn:graph:normative` for `get_policies`. **SHACL validation** (`validation/`): pyshacl gate on emitted RDF triples before Neptune LOAD; violations routed to `urn:graph:quarantine` with a structured report. **OWL ontology** (`ontology/`): `biz_ops.ttl` (Schema.org + SKOS base classes) + `biz_ops_shapes.ttl` (SHACL shapes); `ontology_loader/` loads the OWL ontology into Neptune at startup. **Provenance** (`provenance/`): W3C PROV-O triple emitter — document + chunk provenance resolved into MCP citation objects. | Python 3.12+ (`boto3`, `rdflib`, `pyshacl`, `mcp`, `mangum`) |
| `apps/ingestion/` | On-demand Fargate ingestion task — **pre-pivot (K8s demo corpus).** Resolves the S3 corpus snapshot (`community/` + `enhancements/` trees) and runs `graphrag.ingest` over the openCypher Neptune store (`NeptuneGraphStore`). The SPARQL/git-delta ingestion pipeline (ADR-0016) is wave-5+ and not yet wired here. | Python + Dockerfile |
| `apps/infra-tf/` | Terraform IaC (ADR-0010) — current IaC. No-NAT VPC + 5 VPC interface endpoints (ecr.api, ecr.dkr, logs, sts, bedrock-runtime) + S3 gateway + Neptune Serverless (SPARQL/RDF) + OpenSearch (Lucene HNSW) + S3 + the openCypher **query Lambda** (`graphrag.query_lambda`, `query_role` — read-only Neptune) + Neptune smoke probe (`smoke_probe_role`) + vector smoke probe (`vector_probe_role`) + Budgets alarm. _(Wave-4 additions in-flight: MCP Lambda (FastMCP+Mangum) with ADOT layer + IAM-auth Function URL, API Gateway HTTP API, EventBridge git-ingestion trigger.)_ | Terraform (HCL) |
| `apps/infra/` | AWS CDK IaC (historical — superseded by Terraform in ADR-0010). Retained for reference. | AWS CDK (Python) |

Build/test from the repo root: `pip install -e ".[dev,infra]"` then `pytest`,
`ruff check packages apps`, `mypy packages/graphrag/src apps`.

## Where to start

1. Read [`docs/CHARTER.md`](../CHARTER.md) — mission and scope.
2. Read this file (architecture overview).
3. Read [`docs/architecture/biz-ops-knowledge-graph/design.md`](biz-ops-knowledge-graph/design.md)
   — the full platform design (conceptual, logical, physical views).
4. Read [`docs/adr/`](../adr/) — architecture decisions; the ini-002 platform is
   shaped by ADR-0011 (Neptune SPARQL), ADR-0012 (OWL schema-only + named graphs),
   ADR-0013 (multi-strategy routing — wave-4), and ADR-0014 (MCP tool server).
5. Skim [`docs/product/roadmap.md`](../product/roadmap.md) for the current
   initiative state and wave-4 in-flight items.
6. Each `docs/specs/<slug>/` carries a `spec.md` + `plan.md` alongside the
   resulting code.
