# Spec: rdf-owl-ontology

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (OWL schema-only + named-graph partition — primary decision this spec implements); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine choice and named-graph model); [RFC-0004](../../rfc/0004-biz-ops-kg-pivot.md) (§D3, §D4, §Honesty constraint)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** data

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.ontology` module delivers the type vocabulary and machine-verifiable
data-contract layer for the biz-ops knowledge platform. It ships two Turtle files
and a thin Python API — all usable offline with no AWS credentials:

1. **`biz_ops.ttl`** — an OWL 2 ontology used as vocabulary/schema only (no runtime
   reasoner). It defines nine document classes anchored to Schema.org and SKOS base
   types, eight key domain properties, and two chunk-specific properties. The `biz:`
   namespace defined here is the shared identifier space all RDF triples emitted by
   the ingestion pipeline carry.

2. **`biz_ops_shapes.ttl`** — a SHACL shapes library with one `sh:NodeShape` per
   document class. Each shape asserts the required-field and type constraints the
   ingestion pipeline must satisfy before a Neptune LOAD is permitted; a document
   failing any shape routes to `urn:graph:quarantine` rather than to its normative
   or descriptive partition.

3. **Python API** (`graphrag.ontology`) — `load_ontology()` returns the parsed OWL
   graph; `validate_graph(graph)` runs pyshacl with `inference="none"` and returns a
   typed `ValidationResult` dataclass (never raises on constraint failure) so callers
   own the quarantine-routing decision; `check_class_shape_completeness()` is the CI
   lint function that ensures every OWL class has a matching SHACL shape.

This module owns schema definitions only. Triple emission, Neptune LOAD operations,
and quarantine-graph routing are the ingestion pipeline's responsibility.

## Boundaries

### Always do

- Colocate `biz_ops.ttl` and `biz_ops_shapes.ttl` in
  `packages/graphrag/src/graphrag/ontology/` alongside the Python module; declare
  `"ontology/*.ttl"` as package data in `pyproject.toml` so they install with the
  wheel.
- Call `pyshacl.validate()` with `inference="none"` on every `validate_graph()` call.
  Hard-code this value — never accept it as a parameter.
- Return a `ValidationResult` dataclass from `validate_graph()` — never raise on a
  SHACL constraint failure; the caller decides quarantine routing.
- Anchor document classes to Schema.org (`schema:DigitalDocument`, `schema:CreativeWork`)
  and SKOS (`skos:ConceptScheme`, `skos:Concept`) base types.
- Include exactly one `sh:NodeShape` per document class. The
  `check_class_shape_completeness()` function (and its pytest fixture wrapper) fails CI
  if any OWL class lacks a matching shape.
- Add `rdflib` and `pyshacl` to `pyproject.toml [ingest]` group — ingest-only; the
  `dev` extra already pulls in `[ingest]` so tests get them automatically.
- Use `importlib.resources.files("graphrag.ontology")` to locate bundled `.ttl` files
  at runtime — the only pattern compatible with wheel installs.

### Ask first

- Adding a new top-level OWL document class: it determines which named graph a document
  lands in; changes the partition semantics governed by ADR-0012.
- Removing or renaming an existing class or property: the ingestion pipeline emits
  triples against this vocabulary; a rename without a coordinated update breaks SHACL
  validation silently.
- Bumping the minimum rdflib or pyshacl version floor: shared with the dev toolchain.

### Never do

- Run an OWL reasoner — no `inference` value other than `"none"` in any pyshacl call;
  ADR-0012 explicitly rejects all runtime OWL inference.
- Emit or mutate RDF triples — this module owns schema definitions only.
- Import boto3, botocore, or any AWS SDK at module load time — `import graphrag.ontology`
  must succeed with no AWS credentials and no network access.
- Place SHACL shape files outside the `graphrag.ontology` package — shapes must colocate
  with the ontology per the ADR-0012 Confirmation gate.
- Expose a raw pyshacl graph or rdflib result as the public API surface — the public
  interface is the typed `ValidationResult` / `ShapeViolation` dataclasses only.

## Testing Strategy

- **Goal-based check** — `biz_ops.ttl` parses and contains 9 OWL classes (AC1): a
  SPARQL SELECT against the parsed graph returns exactly 9 distinct `owl:Class` URIs; exits 0.
- **Goal-based check** — `biz_ops_shapes.ttl` parses and contains 9 shapes with correct
  constraints (AC2): a SPARQL SELECT against the parsed shapes graph returns exactly 9
  distinct `sh:NodeShape` URIs; a parametrized missing-field fixture (one per shape class,
  one required property omitted) confirms `conforms=False` for each.
- **TDD** — `validate_graph()` logic: well-formed fixture → `conforms=True`; each
  missing-required-property fixture → `conforms=False` with a `ShapeViolation` naming
  the failing path and source shape. Parametrized across all 9 document classes (AC3, AC4,
  AC5, and the 7 remaining shapes). Red-green-refactor; tests in
  `packages/graphrag/tests/test_ontology.py`.
- **TDD** — completeness lint (AC9): `check_class_shape_completeness(ontology_graph,
  shapes_graph)` returning an empty list for the well-formed pair, and a non-empty list
  for a fixture with an extra OWL class and no matching shape.
- **Goal-based check** — `load_ontology()` (AC6): a SPARQL SELECT against the returned
  graph confirms ≥ 9 OWL classes; exits 0.
- **Goal-based check** — SKOS concept addability (AC7): parse a SKOS instance into the
  loaded graph, SPARQL confirms retrieval; exits 0.
- **Goal-based check** — import isolation (AC8): `python -c "import sys; import
  graphrag.ontology; assert not {'boto3','botocore','aws_cdk'} & sys.modules.keys()"` exits 0.
- **TDD** — `validate_graph()` datatype and cardinality (AC11): a `biz:Policy` with
  `biz:effectiveDate` typed as plain `xsd:string` → `conforms=False`; a `biz:Policy`
  with `biz:effectiveDate` asserted twice as valid `^^xsd:date` literals → `conforms=False`
  (only `sh:maxCount 1` can trip when datatype is correct and minCount satisfied); tests
  in `packages/graphrag/tests/test_ontology.py`.
- **Goal-based check** — dependency and lint gate: `ruff check` and `mypy` pass on the
  new module with zero errors (AC10).

## Acceptance Criteria

- [x] `packages/graphrag/src/graphrag/ontology/biz_ops.ttl` parses without error
  (`rdflib.Graph().parse()`); a SPARQL `SELECT ?c WHERE { ?c a owl:Class }` returns
  exactly 9 distinct class URIs matching the ADR-0012 §Decision hierarchy:
  `biz:Policy` (subClassOf `schema:DigitalDocument`), `biz:Standard` and
  `biz:Guideline` (subClassOf `biz:Policy`), `biz:SOP`, `biz:JobAid`, `biz:Transcript`,
  `biz:Chunk` (subClassOf `schema:CreativeWork`), `biz:BusinessDomain` (subClassOf
  `skos:ConceptScheme`), `biz:Journey` (subClassOf `skos:Concept`).
- [x] `packages/graphrag/src/graphrag/ontology/biz_ops_shapes.ttl` parses without
  error; a SPARQL `SELECT ?s WHERE { ?s a sh:NodeShape }` returns exactly 9 distinct
  shape URIs (one per document class); each shape declares `sh:targetClass` pointing to
  its class and `sh:minCount 1` constraints matching the per-shape constraint table in
  `plan.md` Design §Data & schema (which derives the two representative shapes from
  ADR-0012 §SHACL shapes and specifies the remaining seven).
- [x] `validate_graph(g)` returns `ValidationResult(conforms=True, violations=[])` for
  a well-formed `rdflib.Graph` containing a `biz:Policy` triple-set with all five
  required properties: `schema:name`, `biz:effectiveDate`, `biz:scope`, `biz:hasPII`,
  `biz:gitCommitSHA`.
- [x] `validate_graph(g)` returns `ValidationResult(conforms=False, violations=[v])`
  for a `biz:Policy` missing `biz:effectiveDate`; `v.path` resolves to `biz:effectiveDate`
  (note: `v.source_shape` may be a blank-node identifier for the inline property shape, not
  the named `biz:PolicyShape` — assertions pin on `v.path`, not `v.source_shape`).
- [x] `validate_graph(g)` returns `ValidationResult(conforms=False, violations=[v])`
  for a `biz:Chunk` missing `prov:wasDerivedFrom`; `v.path` resolves to
  `prov:wasDerivedFrom`.
- [x] `load_ontology()` returns an `rdflib.Graph` from which a SPARQL
  `SELECT ?c WHERE { ?c a owl:Class }` returns at least 9 distinct URIs.
- [x] A `biz:BusinessDomain` SKOS concept instance (`biz:Finance a biz:BusinessDomain ;
  skos:prefLabel "Finance"@en`) loads into the graph returned by `load_ontology()` via
  `graph.parse(data=ttl_string, format="turtle")` without modifying `biz_ops.ttl`; a
  subsequent SPARQL `SELECT ?d WHERE { ?d a biz:BusinessDomain }` returns `biz:Finance`.
- [x] `import graphrag.ontology` in a fresh Python subprocess leaves `sys.modules` free
  of `boto3`, `botocore`, and `aws_cdk` immediately after import (all three asserted
  absent in the isolation check, not just `boto3`).
- [x] `check_class_shape_completeness(ontology_graph, shapes_graph)` returns `[]` for
  the bundled well-formed pair; returns a non-empty list when given a fixture ontology
  graph containing an extra `owl:Class` and a shapes graph with no matching
  `sh:NodeShape` for that class.
- [x] `validate_graph(g)` returns `ValidationResult(conforms=False)` for a `biz:Policy`
  where `biz:effectiveDate` is a plain `xsd:string` literal instead of `xsd:date`
  (datatype constraint); and for a `biz:Policy` where `biz:effectiveDate` is asserted as
  two **distinct** valid `^^xsd:date` literals (`"2024-01-01"^^xsd:date` and
  `"2024-07-01"^^xsd:date`) so only `sh:maxCount 1` can trip — RDF set-semantics
  deduplicate identical triples, so both literals must differ. Confirms that `sh:datatype`
  and `sh:maxCount` constraints are present and enforced, not merely counted.
- [x] `ruff check packages/graphrag/src/graphrag/ontology/` and `mypy
  packages/graphrag/src/graphrag/ontology/` both exit 0 with zero errors or warnings.

## Assumptions

- Technical: Python 3.11+ is the runtime floor (`requires-python = ">=3.11"` in
  `pyproject.toml`).
- Technical: rdflib and pyshacl are not yet in `pyproject.toml` — both are new
  dependencies for the `[ingest]` group (source: `pyproject.toml`; confirmed: the
  `[ingest]` group contains only `networkx>=3.0`).
- Technical: the package source root is `packages/graphrag/src/` (source:
  `[tool.setuptools.packages.find]` in `pyproject.toml`); the new module lives at
  `packages/graphrag/src/graphrag/ontology/`.
- Technical: the test suite root includes `packages/graphrag/tests/` (source:
  `[tool.pytest.ini_options]`); new tests go in `packages/graphrag/tests/test_ontology.py`.
- Technical: `.ttl` files require an explicit `[tool.setuptools.package-data]` entry to
  be installed with the wheel (source: existing pattern for `aliases.yaml`, `labels.yaml`
  in `pyproject.toml`).
- Technical: mypy overrides for rdflib and pyshacl are needed — they lack inline type
  stubs (source: existing `[[tool.mypy.overrides]]` pattern for boto3, networkx).
- Technical: the src-layout path `packages/graphrag/src/graphrag/ontology/` realizes the
  colocation intent stated in ADR-0012 §Confirmation as `packages/graphrag/ontology/` —
  the `src/` level is the package root per `[tool.setuptools.packages.find]`.
- Product: SHACL shapes validate emission correctness (required fields present and typed),
  not semantic correctness of `rdf:type` assignment — ingest-time classification accuracy
  is honesty-constraint residual #1 per ADR-0012 §Honesty constraint; this spec does not
  cover classification logic.
- Product: intra-partition attribute mismatch (a correctly-partitioned document tagged with
  the wrong domain falls through the SPARQL leg to the vector-threshold leg) is
  honesty-constraint residual #2 per ADR-0012 §Honesty constraint; this is a
  retrieval-layer concern outside this schema-only module.
- Process: spec transitions Draft → Approved require adversarial review to pass
  (`work-loop` skill, PLAN pre-EXECUTE gate); user signs off on Approved status.
