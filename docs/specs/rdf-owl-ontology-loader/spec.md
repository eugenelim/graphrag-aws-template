# Spec: rdf-owl-ontology-loader

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine); [`spec-rdf-owl-ontology`](../rdf-owl-ontology/spec.md) (ontology module contract — already Shipped); [`spec-provenance-citations`](../spec-provenance-citations/spec.md) (PROV-O vocabulary)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** data

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.ontology_loader` module loads the biz-ops OWL ontology (from
`graphrag.ontology.load_ontology()`) into a SPARQL store and emits PROV-O provenance
triples recording the load activity. It is offline-safe (works with `MemorySparqlStore`)
and is the mechanism used to bootstrap a fresh Neptune cluster with the type vocabulary
before document ingestion begins.

This spec ships alongside `graphrag.provenance` (PROV-O emission and citation
resolution) in the same PR. The loader emits provenance using the PROV-O vocabulary
per `spec-provenance-citations`.

## Boundaries

### Always do

- Use `graphrag.ontology.load_ontology()` to obtain the OWL graph — never re-read the
  Turtle file directly.
- Use `SparqlStore.load_turtle()` to insert triples — the method handles N-Triple escaping
  and INSERT DATA construction safely.
- Emit a minimal PROV-O activity graph alongside the ontology triples in the same named
  graph: one `prov:Activity`, one `prov:Entity` for the ontology, one `prov:SoftwareAgent`
  for `<urn:agent:ontology-loader>`.
- Default named graph: `urn:graph:ontology` — a dedicated schema-vocabulary partition
  separate from `urn:graph:normative` and `urn:graph:descriptive`.
- Accept `named_graph` as a parameter so callers can override the target graph URI.
- Keep `OntologyLoader` importable without boto3 or botocore.

### Never do

- Import boto3 or botocore in `graphrag.ontology_loader`.
- Call `sparql_update()` directly — use `load_turtle()` instead.
- Raise if PROV-O provenance emission fails — ontology triples are the primary load.

## Testing Strategy

- **TDD** — ontology triples loaded (AC1): `OntologyLoader(store).load()` + SPARQL SELECT
  returns exactly 9 `owl:Class` URIs from `urn:graph:ontology`.
- **TDD** — provenance emitted (AC2): SPARQL SELECT returns a `prov:Activity` whose URI
  contains `"load-ontology"`, and a `prov:SoftwareAgent` for `<urn:agent:ontology-loader>`.
- **TDD** — custom named graph (AC3): `load(named_graph="urn:graph:custom")` puts triples
  in `urn:graph:custom`, not `urn:graph:ontology`.
- **Goal-based check** — import isolation (AC4): subprocess check exits 0 without boto3.
- **Goal-based check** — ruff + mypy (AC5): both pass with zero errors.

## Acceptance Criteria

- [x] `OntologyLoader(store).load()` loads the bundled `biz_ops.ttl` triples into
  `urn:graph:ontology` by default; a SPARQL SELECT returns exactly 9 `owl:Class` URIs
  from that named graph. Verified with `MemorySparqlStore`.
- [x] `OntologyLoader(store).load()` emits at least one `prov:Activity` triple into
  the same named graph; the activity URI contains `"load-ontology"` and the graph
  also contains a `prov:SoftwareAgent` triple for `<urn:agent:ontology-loader>`.
- [x] `OntologyLoader(store).load(named_graph="urn:graph:custom")` loads triples
  into `urn:graph:custom`; a SELECT on `urn:graph:ontology` returns nothing.
- [x] `python -c "import graphrag.ontology_loader"` exits 0 in an environment where
  boto3 and botocore are not installed.
- [x] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/ontology_loader/`
  with zero errors.

## Assumptions

- `graphrag.ontology.load_ontology()` returns an `rdflib.Graph` (Shipped, stable API).
- `SparqlStore.load_turtle()` accepts Turtle string + named graph URI (Shipped, stable API).
- `rdflib` is available (already in `pyproject.toml [ingest]`).
- `rdflib.namespace.PROV` is available in rdflib ≥ 6.3.
- Named graph `urn:graph:ontology` is a new partition; no existing code queries it.
