# Spec: update-architecture-docs

**Mode:** light (no risk trigger fired — documentation-only change)
**Status:** Shipped
**Initiative:** ini-002 · M2

## Objective

Update architecture documentation to reflect the wave-3 ini-002 implementation
(PRs #65–#83) accurately — describe what is shipped, annotate what is wave-4
in-flight, and archive the superseded K8s demo content. Components: Neptune SPARQL
engine, NeptuneSparqlStore, MCP tool server (6 tools), NormativeRetriever, SHACL
gate, RDF/OWL ontology loader, and PROV-O chain.

## Acceptance Criteria

- [x] `docs/architecture/overview.md` reflects the ini-002 biz-ops package layout
  (no openCypher/K8s demo references in the library component descriptions); the
  `apps/ingestion/` entry is accurate (pre-pivot K8s task still in place); wave-4
  infra items noted as in-flight
- [x] `docs/architecture/security.md` is rewritten for SPARQL/MCP/IAM trust
  boundaries reflecting wave-3 shipped state; API Gateway annotated as wave-4
  in-flight; VPC endpoints list accurate (5 shipped + wave-4 additions noted)
- [x] `docs/product/roadmap.md` has an ini-002 section covering the shipped
  wave-3 items, wave-4 in-flight items, and wave-5+ candidates; old K8s demo
  content moved to archive section
- [x] `docs/adr/0005-…` status updated to `Superseded by ADR-0013 and ADR-0014`
  and a `## Supersession record` section added (backlog: `adr-0005-supersession-record`)
- [x] `docs/architecture/README.md` "Partially current" banner removed from the
  security.md entry now that security.md is current; implementation sequence updated
- [x] `workspace.toml` moves `docs/update-architecture-docs` to shipped

## Tasks

1. [x] Create this spec file
2. [x] Rewrite `docs/architecture/overview.md`
3. [x] Rewrite `docs/architecture/security.md`
4. [x] Update `docs/product/roadmap.md`
5. [x] Update `docs/adr/0005-community-detection-in-fargate-louvain.md`
6. [x] Update `docs/architecture/README.md`
7. [x] Update `workspace.toml`
