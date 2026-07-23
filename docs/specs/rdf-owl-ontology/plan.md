# Plan: rdf-owl-ontology

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

The work breaks into a clean dependency chain. T1 (pyproject.toml dependency + package-data
declarations) and T2 (OWL ontology Turtle file) are independent and can start in parallel.
T3 (SHACL shapes) depends on T2 because shapes reference class URIs defined in the ontology.
T4 (Python API + tests) depends on T1, T2, and T3 — it needs the dependencies installed and
both Turtle files present. T5 (completeness lint) depends on T4 since the lint function is
part of the Python API.

The riskiest part is the pyshacl violation-report extraction in T4: pyshacl returns a
SHACL validation report graph, and we map its nodes into typed `ShapeViolation` dataclasses.
This mapping is exercised by the TDD tests before any integration path uses it.

No AWS resources are touched by any task in this spec. All verification is offline.

## Constraints

- ADR-0012: OWL schema-only, no runtime reasoner — `inference="none"` hard-coded, never
  parameterised.
- ADR-0012 Confirmation: SHACL shapes colocated with the ontology; class-without-shape
  fails CI.
- ADR-0011: SPARQL/RDF engine — the ontology file is SPARQL-query-compatible Turtle.
- pyproject.toml: new dependencies go in the `[ingest]` group; `dev` already includes
  `graphrag-aws-demo[ingest]` so tests pick them up automatically.
- Ruff + mypy CI gates must stay green (existing project gates from `pyproject.toml`).
- `importlib.resources.files()` API for bundled file access — compatible with Python 3.11+
  and wheel installs.

## Construction tests

**Cross-cutting integration tests (run after T3 / during T4):**

- Parse both Turtle files together into a combined graph and run `pyshacl.validate()` with
  the ontology as data and shapes as constraints — the ontology itself must conform (it
  declares classes and properties, not instances, so no `sh:targetClass` shape fires).
- Import isolation subprocess check: `python -c "import sys; import graphrag.ontology;
  assert not {'boto3','botocore','aws_cdk'} & sys.modules.keys()"` exits 0 (all three
  AWS SDK modules absent — not just boto3).

**Manual verification:** none — all verification is automated.

## Design (LLD)

### Design decisions

- **`ValidationResult` as a dataclass, not an exception.** Raising on SHACL failure forces
  callers into try/except for a normal control flow (valid → load; invalid → quarantine).
  A typed return value makes routing explicit. Traces to: AC3, AC4, AC5.
- **`inference="none"` hard-coded, not parameterised.** ADR-0012 makes OWL reasoning a
  Never-do; exposing an `inference` parameter would invite accidental or future misuse.
  Traces to: Boundaries §Never do.
- **Bundled `.ttl` files as package data.** The module must be callable offline (CI, dev
  environment, cold-start) with zero network access. `importlib.resources.files()` locates
  them inside the installed wheel. Traces to: AC8.
- **`biz:` namespace as a module-level `rdflib.Namespace` constant.** Shared across
  `biz_ops.ttl`, `biz_ops_shapes.ttl`, and Python code — single source of truth, preventing
  prefix-IRI drift between the three artefacts. Traces to: AC1, AC2.
- **pyshacl violation report parsed via SPARQL on the report graph.** pyshacl returns a
  `(conforms, report_graph, report_text)` tuple; the report graph contains
  `sh:ValidationResult` nodes with `sh:focusNode`, `sh:resultPath`, `sh:sourceShape`, and
  `sh:resultMessage` triples. A SPARQL SELECT on `report_graph` is the documented extraction
  path (SHACL spec §result-graph). Traces to: AC4, AC5.

### Data & schema

**`biz:` namespace IRI:** `https://graphrag-aws.demo/biz-ops/ontology#`
This is a demo-scoped IRI (non-dereferenceable; fine for a reference demo). It must be
identical in `biz_ops.ttl`, `biz_ops_shapes.ttl`, and the Python constant:
```python
BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
```

**OWL class hierarchy (Turtle sketch, abridged):**
```turtle
@prefix biz: <https://graphrag-aws.demo/biz-ops/ontology#> .
@prefix schema: <https://schema.org/> .
@prefix skos:   <http://www.w3.org/2004/02/skos/core#> .
@prefix owl:    <http://www.w3.org/2002/07/owl#> .
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .

biz:Policy       a owl:Class ; rdfs:subClassOf schema:DigitalDocument .
biz:Standard     a owl:Class ; rdfs:subClassOf biz:Policy .
biz:Guideline    a owl:Class ; rdfs:subClassOf biz:Policy .
biz:SOP          a owl:Class ; rdfs:subClassOf schema:CreativeWork .
biz:JobAid       a owl:Class ; rdfs:subClassOf schema:CreativeWork .
biz:Transcript   a owl:Class ; rdfs:subClassOf schema:CreativeWork .
biz:Chunk        a owl:Class ; rdfs:subClassOf schema:CreativeWork .
biz:BusinessDomain a owl:Class ; rdfs:subClassOf skos:ConceptScheme .
biz:Journey      a owl:Class ; rdfs:subClassOf skos:Concept .
```

**Properties — domain annotations vs. SHACL-enforced constraints:**

The table separates OWL `rdfs:domain`/`rdfs:range` annotations (informational) from
properties that carry a `sh:minCount 1` in a SHACL shape (enforced by the validator).

| Property | rdfs:domain | rdfs:range | SHACL minCount 1 on shapes |
|---|---|---|---|
| `biz:inDomain` | document classes | `biz:BusinessDomain` | none (domain annotation only) |
| `biz:inJourney` | document classes | `biz:Journey` | none (domain annotation only) |
| `biz:hasChunk` | document classes | `biz:Chunk` | none (domain annotation only) |
| `biz:scope` | `biz:Policy` | `xsd:string` | PolicyShape, StandardShape, GuidelineShape |
| `biz:effectiveDate` | `biz:Policy` | `xsd:date` | PolicyShape, StandardShape, GuidelineShape (also maxCount 1) |
| `biz:visibility` | any | `xsd:string` | none (optional annotation) |
| `biz:hasPII` | `biz:Policy` | `xsd:boolean` | PolicyShape, StandardShape, GuidelineShape (also maxCount 1) |
| `biz:gitCommitSHA` | any | `xsd:string` | PolicyShape, StandardShape, GuidelineShape, SOPShape, JobAidShape, TranscriptShape |
| `biz:chunkIndex` | `biz:Chunk` | `xsd:integer` | ChunkShape |
| `biz:embeddingModel` | `biz:Chunk` | `xsd:string` | ChunkShape |

**SHACL shapes — full required-property list (all 9 shapes):**

```turtle
# From ADR-0012 §SHACL shapes (two representative shapes):
biz:PolicyShape a sh:NodeShape ; sh:targetClass biz:Policy ;
    sh:property [ sh:path schema:name ;       sh:minCount 1 ; sh:datatype xsd:string ] ;
    sh:property [ sh:path biz:effectiveDate ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:date ] ;
    sh:property [ sh:path biz:scope ;         sh:minCount 1 ] ;
    sh:property [ sh:path biz:hasPII ;        sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:boolean ] ;
    sh:property [ sh:path biz:gitCommitSHA ;  sh:minCount 1 ; sh:datatype xsd:string ] .

# StandardShape and GuidelineShape copy Policy's property constraints verbatim
# with their own sh:targetClass (NOT biz:Policy):
biz:StandardShape  a sh:NodeShape ; sh:targetClass biz:Standard  ; <same 5 properties as PolicyShape> .
biz:GuidelineShape a sh:NodeShape ; sh:targetClass biz:Guideline ; <same 5 properties as PolicyShape> .

# Content-document shapes — schema:name + biz:gitCommitSHA only:
biz:SOPShape        a sh:NodeShape ; sh:targetClass biz:SOP        ;
    sh:property [ sh:path schema:name ;      sh:minCount 1 ; sh:datatype xsd:string ] ;
    sh:property [ sh:path biz:gitCommitSHA ; sh:minCount 1 ; sh:datatype xsd:string ] .
biz:JobAidShape     a sh:NodeShape ; sh:targetClass biz:JobAid     ; <same as SOPShape> .
biz:TranscriptShape a sh:NodeShape ; sh:targetClass biz:Transcript ; <same as SOPShape> .

# From ADR-0012 §SHACL shapes:
biz:ChunkShape a sh:NodeShape ; sh:targetClass biz:Chunk ;
    sh:property [ sh:path prov:wasDerivedFrom ; sh:minCount 1 ; sh:maxCount 1 ] ;
    sh:property [ sh:path biz:chunkIndex ;      sh:minCount 1 ; sh:datatype xsd:integer ] ;
    sh:property [ sh:path biz:embeddingModel ;  sh:minCount 1 ; sh:datatype xsd:string ] .

# Taxonomy shapes — skos:prefLabel only:
biz:BusinessDomainShape a sh:NodeShape ; sh:targetClass biz:BusinessDomain ;
    sh:property [ sh:path skos:prefLabel ; sh:minCount 1 ] .
biz:JourneyShape a sh:NodeShape ; sh:targetClass biz:Journey ;
    sh:property [ sh:path skos:prefLabel ; sh:minCount 1 ] .
```
```

**Python API (public surface):**
```python
@dataclass
class ShapeViolation:
    focus_node: str     # URI of the failing node
    path: str           # property path that failed (prefixed IRI string); "" if absent
    message: str        # human-readable constraint failure from sh:resultMessage
    source_shape: str   # raw sh:sourceShape value — may be a blank-node id (e.g. "Nab12…")
                        # when shapes use inline blank-node sh:property blocks; callers
                        # must not assume this is a named shape URI

@dataclass
class ValidationResult:
    conforms: bool
    violations: list[ShapeViolation]

def load_ontology() -> rdflib.Graph: ...
def validate_graph(
    data_graph: rdflib.Graph,
    shapes_graph: rdflib.Graph | None = None,
) -> ValidationResult: ...
def check_class_shape_completeness(
    ontology_graph: rdflib.Graph,
    shapes_graph: rdflib.Graph,
) -> list[str]: ...  # returns OWL class URIs that lack a matching sh:NodeShape
```

### Component / module decomposition

```
packages/graphrag/src/graphrag/ontology/
├── __init__.py            # public API exports
├── _validate.py           # validate_graph() + ShapeViolation + ValidationResult
├── _resources.py          # load_ontology(), _load_shapes() — importlib.resources wrappers
├── _lint.py               # check_class_shape_completeness()
├── biz_ops.ttl            # OWL 2 ontology (schema-only)
└── biz_ops_shapes.ttl     # SHACL shapes library
```

`__init__.py` re-exports the public names from the private submodules. Splitting into
`_validate.py`, `_resources.py`, `_lint.py` keeps each file single-responsibility and
testable independently — acceptable at this scale; do not add further layers.

### Failure, edge cases & resilience

- **Invalid RDF passed to `validate_graph()`**: pyshacl will raise a parsing error; we
  let it propagate — the caller's graph construction is responsible for parseable RDF.
  Document in `validate_graph()`'s docstring.
- **Bundled `.ttl` file missing from wheel**: `importlib.resources.files()` raises
  `FileNotFoundError`; re-raise with a message naming the missing file and the package.
- **pyshacl `conforms=False` with zero violations**: edge case in some pyshacl versions —
  treat `conforms` as authoritative; a `False` with empty violations produces
  `ValidationResult(conforms=False, violations=[])` and is valid.
- **`sh:resultPath` absent on a violation node**: some `sh:resultMessage`-only violations
  omit the path. Set `path=""` in that case rather than erroring.

### Quality attributes (NFRs)

- **CI-safe**: rdflib + pyshacl only; no network, no AWS at any code path (AC8).
- **Mypy-clean**: `disallow_untyped_defs = true` is the project standard; all public
  functions carry full type annotations (AC10).
- **Wheel-installable**: `importlib.resources.files()` is the correct API for Python 3.11+
  and works with both editable installs and built wheels.

## Tasks

### T1: Declare rdflib + pyshacl dependencies and package data

**Depends on:** none

**Touches:** `pyproject.toml`

**Tests:**
- Goal-based: `pip install -e ".[dev]"` succeeds; `python -c "import rdflib, pyshacl; print('ok')"` exits 0.
- Goal-based: `mypy packages/graphrag/src/graphrag/` does not emit "Cannot find implementation or library stub" for rdflib or pyshacl.

**Approach:**
1. Add to `[project.optional-dependencies]` `ingest` group (after `networkx>=3.0`):
   ```toml
   "rdflib>=6.3",
   "pyshacl>=0.25,<0.27",
   ```
2. Append `"ontology/*.ttl"` to the `graphrag` list in `[tool.setuptools.package-data]`.
3. Add two `[[tool.mypy.overrides]]` blocks for `rdflib`/`rdflib.*` and
   `pyshacl`/`pyshacl.*` with `ignore_missing_imports = true` (mirrors the existing boto3
   and networkx patterns).
4. Record the new dependencies in `packages/graphrag/AGENTS.md` §Dependencies under the
   "Ingest-only" subsection, following the existing networkx entry format:
   - **`rdflib`** (>=6.3) — RDF graph parsing, SPARQL queries, and the offline ontology
     graph (`graphrag.ontology`). Never imported by the query Lambda.
   - **`pyshacl`** (>=0.25,<0.27) — SHACL validation with `inference="none"` before
     Neptune LOAD (`graphrag.ontology.validate_graph`). Never imported by the query Lambda.

**Done when:** `python -c "import rdflib, pyshacl"` exits 0; mypy clean on an empty
`graphrag/ontology/__init__.py`; `ruff check` reports no issues; both deps recorded in
`packages/graphrag/AGENTS.md`.

---

### T2: Author `biz_ops.ttl` — OWL 2 ontology file

**Depends on:** none (parallel with T1)

**Touches:** `packages/graphrag/src/graphrag/ontology/biz_ops.ttl`
(also creates the `graphrag/ontology/` directory)

**Tests:**
- Goal-based: `python -c "from rdflib import Graph; g=Graph(); g.parse('packages/graphrag/src/graphrag/ontology/biz_ops.ttl'); print(len(g))"` exits 0, prints > 0.
- Goal-based: inline SPARQL `SELECT (COUNT(?c) AS ?n) WHERE { ?c a owl:Class }` against the loaded graph returns n = 9.
- Goal-based: inline SPARQL confirms `biz:Standard rdfs:subClassOf biz:Policy` (not directly `schema:DigitalDocument`) and `biz:Journey rdfs:subClassOf skos:Concept`.

**Approach:**
1. Create `packages/graphrag/src/graphrag/ontology/` directory; add `__init__.py` stub.
2. Write `biz_ops.ttl` with prefix declarations for `biz:`, `schema:`, `skos:`, `owl:`,
   `rdfs:`, `xsd:`, `prov:`.
3. Declare the ontology header (`a owl:Ontology`) with `rdfs:label` and `owl:versionInfo`.
4. Define the 9 document classes per the hierarchy in Design §Data & schema.
5. Define the 10 properties (`biz:inDomain` … `biz:embeddingModel`) with `rdfs:domain`,
   `rdfs:range`, and `rdfs:label` annotations.

**Done when:** SPARQL returns exactly 9 `owl:Class` URIs; `biz:Standard rdfs:subClassOf
biz:Policy` and `biz:Journey rdfs:subClassOf skos:Concept` are confirmed.

---

### T3: Author `biz_ops_shapes.ttl` — SHACL shapes library

**Depends on:** T2

**Touches:** `packages/graphrag/src/graphrag/ontology/biz_ops_shapes.ttl`

**Tests:**
- Goal-based: `python -c "from rdflib import Graph; g=Graph(); g.parse('packages/graphrag/src/graphrag/ontology/biz_ops_shapes.ttl'); print(len(g))"` exits 0.
- Goal-based: SPARQL `SELECT (COUNT(?s) AS ?n) WHERE { ?s a sh:NodeShape }` returns n = 9.
- Cross-cutting integration: import pyshacl; validate `biz_ops.ttl` (data graph) against
  `biz_ops_shapes.ttl` (shapes graph) with `inference="none"` — `conforms=True` (the
  ontology declares classes/properties, not instances; no `sh:targetClass` triggers).
- Import isolation: `python -c "import sys; import graphrag.ontology; assert not {'boto3','botocore','aws_cdk'} & sys.modules.keys()"` exits 0 (all three absent, not just boto3).

**Approach:**
1. Write `biz_ops_shapes.ttl` with prefix declarations for `sh:`, `biz:`, `schema:`,
   `prov:`, `xsd:`.
2. Implement 9 `sh:NodeShape` instances per the Design §Data & schema shapes table.
   For each shape, `sh:targetClass` must match the shape's own target class — not a
   copied value from another shape. Specifically:
   - `biz:PolicyShape` (`sh:targetClass biz:Policy`): 5 required properties per ADR-0012.
   - `biz:StandardShape` (`sh:targetClass biz:Standard`): same 5 property constraints as
     PolicyShape — copy only the `sh:property` blocks, **not** `sh:targetClass`.
   - `biz:GuidelineShape` (`sh:targetClass biz:Guideline`): same as StandardShape.
   - `biz:SOPShape` (`sh:targetClass biz:SOP`): `schema:name`, `biz:gitCommitSHA` (minCount 1 each).
   - `biz:JobAidShape` (`sh:targetClass biz:JobAid`): same as SOPShape.
   - `biz:TranscriptShape` (`sh:targetClass biz:Transcript`): same as SOPShape.
   - `biz:ChunkShape` (`sh:targetClass biz:Chunk`): `prov:wasDerivedFrom`, `biz:chunkIndex`,
     `biz:embeddingModel` per ADR-0012.
   - `biz:BusinessDomainShape` (`sh:targetClass biz:BusinessDomain`): `skos:prefLabel` (minCount 1).
   - `biz:JourneyShape` (`sh:targetClass biz:Journey`): `skos:prefLabel` (minCount 1).

**Done when:** SPARQL returns 9 `sh:NodeShape` URIs; `pyshacl.validate(ontology, shapes,
inference="none")` returns `conforms=True`.

---

### T4: Implement `graphrag.ontology` Python API

**Depends on:** T1, T2, T3

**Touches:**
- `packages/graphrag/src/graphrag/ontology/__init__.py`
- `packages/graphrag/src/graphrag/ontology/_resources.py`
- `packages/graphrag/src/graphrag/ontology/_validate.py`
- `packages/graphrag/tests/test_ontology.py`

**Tests (TDD — write red stubs first):**
- `test_validate_well_formed_policy`: build a well-formed `biz:Policy` graph with all 5
  required properties → `result.conforms is True` and `result.violations == []`. (AC3)
- `test_validate_missing_effective_date`: `biz:Policy` with `biz:effectiveDate` omitted →
  `result.conforms is False`; at least one violation with `v.path` containing
  `"effectiveDate"`. Do not assert on `v.source_shape` — pyshacl sets `sh:sourceShape` to
  the inline blank-node property shape, not the enclosing named `biz:PolicyShape`. (AC4)
- `test_validate_chunk_missing_derived_from`: `biz:Chunk` with `prov:wasDerivedFrom`
  omitted → `result.conforms is False`; violation path contains `"wasDerivedFrom"`. (AC5)
- `test_validate_missing_required_property` (parametrized — covers the remaining 7 shapes
  so every shape's constraints are behaviorally pinned, not just counted):
  ```python
  @pytest.mark.parametrize("class_uri,omit_prop", [
      ("biz:Standard",     "biz:effectiveDate"),   # StandardShape
      ("biz:Guideline",    "biz:scope"),            # GuidelineShape
      ("biz:SOP",          "schema:name"),          # SOPShape
      ("biz:JobAid",       "biz:gitCommitSHA"),     # JobAidShape
      ("biz:Transcript",   "schema:name"),          # TranscriptShape
      ("biz:BusinessDomain", "skos:prefLabel"),     # BusinessDomainShape
      ("biz:Journey",      "skos:prefLabel"),       # JourneyShape
  ])
  def test_validate_missing_required_property(class_uri, omit_prop): ...
  # Each case: build a minimal well-formed graph for the class; omit omit_prop;
  # assert result.conforms is False.
  ```
  (AC2 behavioral coverage)
- `test_load_ontology_returns_9_classes`: `load_ontology()` → SPARQL SELECT owl:Class
  returns ≥ 9 URIs. (AC6)
- `test_skos_concept_addable`: load ontology graph, parse `biz:Finance a biz:BusinessDomain
  ; skos:prefLabel "Finance"@en` into it, SPARQL confirms `biz:Finance` is returned. (AC7)
- `test_validate_wrong_datatype`: `biz:Policy` with `biz:effectiveDate` as a plain string
  literal (`"2024-01-01"` without `^^xsd:date`) → `result.conforms is False`. (AC11 datatype)
- `test_validate_max_count_violation`: `biz:Policy` with `biz:effectiveDate` asserted as
  two **distinct** valid `xsd:date` literals — `"2024-01-01"^^xsd:date` and
  `"2024-07-01"^^xsd:date` (identical literals deduplicate to one triple in an RDF set, so
  only distinct values actually assert two triples; datatype and minCount both pass, only
  `sh:maxCount 1` can trip) → `result.conforms is False`; at least one violation's `v.path`
  contains `"effectiveDate"`. Do not assert on `v.source_shape` (blank-node). (AC11 maxCount)
- `test_import_no_aws_deps`: `subprocess.run(["python", "-c", "import sys; import
  graphrag.ontology; assert not {'boto3','botocore','aws_cdk'} & sys.modules.keys()"])`
  → returncode 0. (AC8)

**Approach:**
1. Write all 9 failing tests in `test_ontology.py` (they fail because the module is a stub;
   count: well-formed-policy, missing-effectiveDate, chunk-missing-wasDerivedFrom,
   7x parametrized-missing-field, load-9-classes, skos-addable, wrong-datatype,
   max-count-violation, import-no-aws-deps).
2. Implement `_resources.py`: `_load_ontology_path()` and `_load_shapes_path()` using
   `importlib.resources.files("graphrag.ontology")` to find `biz_ops.ttl` and
   `biz_ops_shapes.ttl`; `load_ontology()` parses and returns the ontology graph;
   `_load_shapes()` (module-private) parses and returns the shapes graph.
3. Implement `_validate.py`: `ShapeViolation` and `ValidationResult` dataclasses;
   `validate_graph(data_graph, shapes_graph=None)` calling `pyshacl.validate()` with
   `inference="none"` and mapping the returned report graph via SPARQL into a list of
   `ShapeViolation` instances. When `shapes_graph is None`, resolve to `_load_shapes()`
   (the bundled library) — all conformance tests call `validate_graph` without an explicit
   shapes graph and depend on this default.
   - SPARQL pattern on the report graph:
     ```sparql
     SELECT ?fn ?path ?msg ?shape WHERE {
       ?r a sh:ValidationResult ;
          sh:focusNode ?fn ;
          sh:resultMessage ?msg ;
          sh:sourceShape ?shape .
       OPTIONAL { ?r sh:resultPath ?path }
     }
     ```
4. Re-export from `__init__.py`:
   `ValidationResult`, `ShapeViolation`, `load_ontology`, `validate_graph`.
5. Run tests red → implement → green → refactor.

**Fixture note:** `test_validate_well_formed_policy` must emit typed literals — `"2024-01-01"^^xsd:date` for `biz:effectiveDate`, `"true"^^xsd:boolean` for `biz:hasPII` — once the shapes carry `sh:datatype` constraints; a plain string literal would produce a datatype violation and break the positive test.

**Done when:** all 9 tests pass; `ruff check` and `mypy` clean on `graphrag/ontology/`.

---

### T5: Class-shape completeness lint function + CI fixture

**Depends on:** T4

**Touches:**
- `packages/graphrag/src/graphrag/ontology/_lint.py`
- `packages/graphrag/src/graphrag/ontology/__init__.py` (add export)
- `packages/graphrag/tests/test_ontology.py` (add 3 tests + the CI fixture)

**Tests (TDD):**
- `test_completeness_lint_clean_pair`: `check_class_shape_completeness(load_ontology(),
  _load_shapes())` returns `[]`. (AC9 positive case)
- `test_completeness_lint_detects_missing_shape`: build a fixture ontology graph with one
  extra `owl:Class` URI not in the shapes graph → result is a non-empty list containing
  that URI. (AC9 negative case)
- `test_ci_completeness_fixture`: a parametrize-free pytest test that calls
  `check_class_shape_completeness(load_ontology(), _load_shapes())` and asserts `== []` —
  this is the CI gate that fails if a future edit adds a class without a shape.

**Approach:**
1. Write 3 failing tests.
2. Implement `_lint.py`:
   ```python
   def check_class_shape_completeness(
       ontology_graph: rdflib.Graph,
       shapes_graph: rdflib.Graph,
   ) -> list[str]:
       owl_classes = {str(c) for c in ontology_graph.subjects(RDF.type, OWL.Class)}
       shaped_classes = {str(c) for _, _, c in shapes_graph.triples((None, SH.targetClass, None))}
       return sorted(owl_classes - shaped_classes)
   ```
3. Export `check_class_shape_completeness` from `__init__.py`.
4. Run tests red → implement → green.

**Done when:** all 3 tests pass; `ruff check` and `mypy` clean; `pytest
packages/graphrag/tests/test_ontology.py` exits 0.

## Rollout

- **Delivery:** no flag — the module is new and not imported by any existing code path;
  landing it changes no existing behavior.
- **Infrastructure:** none — the module is CI-safe; no AWS resources at this spec boundary.
- **External-system integration:** none at this boundary; Neptune LOAD is the
  `rdf-owl-ontology-loader` work item's scope.
- **Deployment sequencing:** this spec is a prerequisite for `packages/graphrag/rdf-owl-
  ontology-loader` and `packages/graphrag/shacl-validation` (both blocked in the work
  queue until this spec is Shipped).

## Risks

- **pyshacl violation report SPARQL extraction**: pyshacl's report graph structure is
  documented in the SHACL spec but nuanced — `sh:resultPath` may be absent on some
  violation nodes. The `_validate.py` implementation handles this via `OPTIONAL`. The TDD
  tests for AC4 and AC5 confirm the extraction works for the two representative failure
  modes; adding a third fixture for a path-absent violation is worthwhile if pyshacl's
  behavior proves inconsistent across 0.25/0.26.
- **`biz:` prefix IRI drift**: if the IRI in `biz_ops.ttl`, `biz_ops_shapes.ttl`, and
  the Python `BIZ = Namespace(...)` constant ever diverges, SHACL `sh:targetClass` matches
  fail silently — `validate_graph()` would return `conforms=True` for any input because no
  shape targets fire. AC2 (SPARQL counts 9 shapes) catches this at `biz_ops_shapes.ttl`
  parse time; the `check_class_shape_completeness` CI gate catches it in the Python API.
- **pyshacl version pinning**: pinned to `>=0.25,<0.27` to avoid breaking changes; the
  violation report API has been stable across 0.25.x but was revised between 0.24 and 0.25.
  Remove the upper bound once 0.27 is confirmed compatible.

## Changelog

- 2026-07-23: initial plan
