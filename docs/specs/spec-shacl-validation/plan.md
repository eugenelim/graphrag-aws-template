# Plan: spec-shacl-validation

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Two tasks. T1 (ShaclGate + quarantine INSERT) is the primary deliverable — it wraps `graphrag.ontology.validate_graph()` and issues the quarantine SPARQL INSERT. T2 (CI completeness gate + import isolation) adds the `assert_class_shape_completeness` fixture and confirms the gate is importable without boto3.

The riskiest part is the Neptune INSERT failure path: `ShaclGate.validate()` must return `GateResult(outcome="quarantine_insert_failed")` without raising, and must log the error at ERROR level. A mock Neptune client that raises `ConnectionError` confirms this. The `GateResult` must not be an exception — the caller decides the consequence.

The quarantine record URI encoding is the other risk: `doc_uri` may contain characters (`:`, `/`) that appear as-is in URNs (safe in the NSS), but must produce a unique, stable key per `(doc_uri, sha)` pair. The plan decision: use `f"urn:quarantine:{sha}:{hashlib.sha256(doc_uri.encode()).hexdigest()[:16]}"` — short, stable, collision-resistant, and avoids any URI-encoding complexity.

No AWS credentials are needed for T1 or T2.

## Constraints

- ADR-0012: quarantine INSERT must target `urn:graph:quarantine` only; never a partition graph.
- ADR-0012: `biz:QuarantineRecord` must carry `biz:quarantineReason`, `biz:quarantinedAt`, `biz:violationPath`, and the document URI.
- ADR-0011: `ingestion_task_role` (WriteDataViaQuery + connect) is used for the quarantine INSERT; `mcp_lambda_role` is read-only and must never be used.
- spec-rdf-owl-ontology: `graphrag.ontology.validate_graph()` is imported directly — no re-implementation of pyshacl.
- `ShaclGate` and `GateResult` must be importable without boto3 or botocore.
- `ShaclGate.validate()` never raises on a SHACL violation — returns `GateResult`.
- Ruff + mypy CI gates must stay green.

## Construction tests

**T1 (ShaclGate):**
- `ShaclGate.validate(valid_graph, doc_uri, sha, mock_client)` returns `GateResult(outcome="passed")`; `mock_client.sparql_update` not called.
- `ShaclGate.validate(invalid_graph, doc_uri, sha, mock_client)` where `invalid_graph` is a `biz:Policy` missing `biz:effectiveDate` returns `GateResult(outcome="quarantined")`; `mock_client.sparql_update` called exactly once with `INSERT DATA { GRAPH <urn:graph:quarantine> { ... } }`.
- The INSERT SPARQL string (N-Triples format, no `@prefix` or Turtle abbreviations) contains the record subject URI `<urn:quarantine:{sha}:{hash16(doc_uri)}>`, `<rdf:type> <biz:QuarantineRecord>`, `<biz:quarantineReason> "..."^^<xsd:string>`, `<biz:quarantinedAt> "..."^^<xsd:dateTime>`, and `<biz:violationPath> <biz:effectiveDate>` (one triple per line with absolute URIs).
- A `biz:Policy` missing both `biz:effectiveDate` and `biz:scope`: the INSERT contains two `biz:violationPath` triples on the same subject.
- `mock_client.sparql_update` raises `ConnectionError` → `GateResult(outcome="quarantine_insert_failed")`; no exception propagates; ERROR log emitted.
- `mock_client.sparql_update` raises `ConnectionError` → the returned `GateResult.error` field contains the `str()` of the exception.

**T2 (CI completeness gate):**
- `assert_class_shape_completeness()` returns `[]` for the bundled well-formed `biz_ops.ttl` + `biz_ops_shapes.ttl`.
- Fixture: ontology graph with extra `owl:Class biz:AuditLog` and no matching `sh:NodeShape` → returns `["biz:AuditLog"]` (non-empty list).
- A pytest test parametrized over both variants: well-formed returns empty; ill-formed returns non-empty (and the test FAILS for the ill-formed variant, confirming the CI gate works).
- `python -c "from graphrag.validation.shacl import ShaclGate, GateResult"` exits 0 without boto3 installed.

## Design (LLD)

### Design decisions

- **Quarantine record URI: `urn:quarantine:{sha}:{hash16(doc_uri)}`.** Using `hashlib.sha256(doc_uri.encode()).hexdigest()[:16]` gives a 16-char hex suffix that is stable, short, and collision-resistant for reasonable corpus sizes. The SHA is the first segment to make records sortable by commit in the Neptune graph. Alternative (`urn:quarantine:{doc_uri}:{sha}` with raw URI) was considered but risks URN parsing ambiguity in tools that split on `:`.
- **Violation paths from pyshacl `ValidationResult`.** `graphrag.ontology.validate_graph()` returns a `ValidationResult` object from pyshacl. The violation details are in `ValidationResult.results_graph` (rdflib `Graph` containing SHACL violation reports as triples). The gate extracts `sh:resultPath` from the results graph for each `sh:ValidationResult` subject — this gives the property path that failed, structured as an RDF term. Each failing path becomes one `biz:violationPath` triple in the quarantine record.
- **`biz:quarantineReason` = violation message.** For each violation, `sh:resultMessage` from the pyshacl results graph is concatenated (comma-separated) as the `biz:quarantineReason` string. If no `sh:resultMessage` is present, the violation path name is used as the reason.
- **Quarantine INSERT is built as an rdflib.Graph — never by f-string interpolation.** The quarantine record is assembled as an `rdflib.Graph` with `Literal(reason, datatype=XSD.string)`, `Literal(timestamp, datatype=XSD.dateTime)`, and `URIRef(doc_uri)`. The graph is serialized to Turtle via rdflib, and the INSERT DATA wraps the Turtle body: `INSERT DATA { GRAPH <urn:graph:quarantine> { {turtle} } }`. This is the only safe approach because `sh:resultMessage` values may contain characters derived from source document content (e.g., offending property values) that would break raw SPARQL string interpolation. The quarantine INSERT uses `ingestion_task_role` (WriteDataViaQuery) — the only write credential in the system — so SPARQL injection here could mutate partition graphs. rdflib's `Literal.n3()` provides the escaping guarantee.
- **Neptune client interface: `sparql_update(sparql: str) -> None`.** The gate only needs SPARQL UPDATE capability. The mock client has `sparql_update: MagicMock` — the gate asserts on `mock_client.sparql_update.call_args[0][0]` (the SPARQL string argument).
- **`GateResult` is a dataclass, not an exception.** Three outcomes: `"passed"` (no Neptune call), `"quarantined"` (quarantine INSERT succeeded), `"quarantine_insert_failed"` (INSERT raised; `error` field carries `str(exception)`). The caller (ingestion pipeline) branches on `outcome` — both non-passed outcomes skip the Gold partition INSERT.

### Data & schema

```python
# graphrag/validation/shacl/_types.py

from dataclasses import dataclass

@dataclass
class GateResult:
    outcome: str          # "passed" | "quarantined" | "quarantine_insert_failed"
    error: str | None = None   # set when outcome="quarantine_insert_failed"
```

**Quarantine INSERT construction (rdflib-based — no raw interpolation):**

```python
# graphrag/validation/shacl/_gate.py (illustrative)
from rdflib import Graph, URIRef, Literal, Namespace, RDF
from rdflib.namespace import XSD

BIZ = Namespace("https://example.org/biz#")

def _build_quarantine_graph(record_uri, doc_uri, reason, timestamp, violation_paths):
    g = Graph()
    r = URIRef(record_uri)
    g.add((r, RDF.type, BIZ.QuarantineRecord))
    g.add((r, BIZ.documentURI, URIRef(doc_uri)))
    g.add((r, BIZ.quarantineReason, Literal(reason, datatype=XSD.string)))
    g.add((r, BIZ.quarantinedAt, Literal(timestamp, datatype=XSD.dateTime)))
    for path in violation_paths:
        g.add((r, BIZ.violationPath, URIRef(path)))
    return g

# Serialize and wrap in INSERT DATA.
# Use N-Triples (absolute URIs, no @prefix directives) — Turtle @prefix is not
# legal inside a SPARQL INSERT DATA quad block. SPARQL PREFIX belongs in the
# prologue; N-Triples sidesteps this by emitting only absolute URI/literal triples.
nt_body = g.serialize(format="nt")
sparql = f"INSERT DATA {{ GRAPH <urn:graph:quarantine> {{ {nt_body} }} }}"
neptune_client.sparql_update(sparql)
```

The `Literal(reason, datatype=XSD.string)` escaping by rdflib ensures violation messages containing `"`, `\`, or SPARQL structural characters are encoded safely. This is the only correct approach given the violation reason is document-derived and may contain attacker-controlled content.

### Component / module decomposition

```
packages/graphrag/src/graphrag/validation/
├── __init__.py
└── shacl/
    ├── __init__.py      # exports: ShaclGate, GateResult, assert_class_shape_completeness
    ├── _types.py        # GateResult dataclass
    ├── _gate.py         # ShaclGate.validate() — wraps validate_graph(), builds quarantine INSERT
    └── _completeness.py # assert_class_shape_completeness() — wraps check_class_shape_completeness()

packages/graphrag/tests/validation/
├── test_shacl_gate.py
└── test_completeness.py
```

### Failure cases & resilience

- **Neptune client raises non-connection exception (e.g. `AccessDeniedException`).** Treated the same as `ConnectionError` — return `GateResult(outcome="quarantine_insert_failed", error=str(e))`; log at ERROR. The gate does not differentiate exception types.
- **`validate_graph()` itself raises (pyshacl internal error).** This is unexpected — pyshacl is a stable library. Let the exception propagate; this is a bug in the ontology shapes or the graph, not a validation outcome. The caller (ingestion pipeline) handles it as an unhandled exception (fail-fast, pipeline fails the task).
- **No violations in the `ValidationResult.results_graph`.** `valid=False` from `validate_graph()` but no `sh:ValidationResult` subjects found in the results graph. Emit a quarantine record with `biz:quarantineReason "validation failed: no violation details available"` and no `biz:violationPath` triples. Log at WARNING.
- **Empty document graph passed.** `validate_graph()` on an empty `rdflib.Graph()` returns `ValidationResult(conforms=True)` — no quarantine record emitted. The gate returns `GateResult(outcome="passed")`. (This is the correct behaviour: an empty graph has no SHACL violations, because there are no subjects to validate.)

### Quality attributes (NFRs)

- **AWS-free gate class.** `ShaclGate` and `GateResult` import only `rdflib`, `pyshacl` (via `graphrag.ontology`), `hashlib`, `datetime`, `logging`, and `dataclasses`. No boto3 or botocore.
- **Offline CI.** All tests run against mock Neptune clients; no live Neptune connection needed.
- **Mypy-clean.** Full type annotations on `GateResult`, `ShaclGate.validate()`, and `assert_class_shape_completeness()`.

## Tasks

### T1: ShaclGate + quarantine INSERT

**Depends on:** `packages/graphrag/ontology` (spec-rdf-owl-ontology, already shipped)

**Touches:**
- `packages/graphrag/src/graphrag/validation/__init__.py`
- `packages/graphrag/src/graphrag/validation/shacl/__init__.py`
- `packages/graphrag/src/graphrag/validation/shacl/_types.py`
- `packages/graphrag/src/graphrag/validation/shacl/_gate.py`
- `packages/graphrag/tests/validation/test_shacl_gate.py`
- `packages/graphrag/src/graphrag/ingestion/cleanse/_gate_integration.py` — deferred to `spec-ingestion-extraction-cleanse` (deferred: shacl-gate-ingestion-seam); that spec's implementation task owns replacing its inline quarantine INSERT with `ShaclGate.validate()`.

**Tests (TDD):** passed case (no Neptune call); single-violation quarantine INSERT (SPARQL string assertions); multi-violation quarantine INSERT (two `biz:violationPath` triples); Neptune failure → `quarantine_insert_failed` with no exception propagation and ERROR log.

**Done when:** all gate tests pass; `ruff check` and `mypy` clean.

---

### T2: CI completeness gate + import isolation

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/validation/shacl/_completeness.py`
- `packages/graphrag/tests/validation/test_completeness.py`

**Tests (TDD):** `assert_class_shape_completeness()` returns `[]` for well-formed pair; returns `["biz:AuditLog"]` for ill-formed fixture; pytest test parametrized over both variants; boto3-free import confirmed.

**Done when:** completeness tests pass; full test suite green; `python -c "from graphrag.validation.shacl import ShaclGate, GateResult"` exits 0 without boto3; `ruff check` and `mypy` clean.

## Rollout

- **Delivery:** no flag — `graphrag.validation.shacl` is a new module; no callers exist until `spec-ingestion-extraction-cleanse` imports `ShaclGate`.
- **Infrastructure:** the quarantine INSERT requires a live Neptune cluster with `ingestion_task_role` credentials and a `urn:graph:quarantine` named graph; both are provisioned by `infra-tf/neptune-sparql-engine`. No new infrastructure required beyond the existing Neptune cluster.
- **Deployment sequencing:** depends on `packages/graphrag/ontology` (shipped) and `packages/graphrag/neptune-sparql-store` (work queue dep for the Neptune client interface).

## Risks

- **`sh:resultPath` RDF term encoding.** The SHACL results graph may contain `sh:resultPath` as a compact URI (`biz:effectiveDate`) or as a full URI (`<https://example.org/biz#effectiveDate>`). The gate must handle both — extract the local name or use the full URI as the `biz:violationPath` value. Use `rdflib.term.URIRef.n3()` for consistent serialization.
- **Quarantine record duplication.** If the ingestion pipeline retries a failed document, a second quarantine record with the same URI is issued. Neptune accepts duplicate `INSERT DATA` triples (idempotent for the same triple value); the `biz:quarantinedAt` timestamp differs. This is acceptable — multiple quarantine records for the same `(doc_uri, sha)` are visible in the graph and indicate retries. Document as known behaviour.
- **pyshacl version drift.** The SHACL results graph schema (which predicates are used for violations) is stable in pyshacl ≥ 0.20. Pin pyshacl in `pyproject.toml` to avoid silent changes to the results graph structure.

## Changelog

- 2026-07-23: initial plan
