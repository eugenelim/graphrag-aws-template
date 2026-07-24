# Spec: spec-shacl-validation

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (SHACL gate before Neptune LOAD; quarantine routing on violation; `biz:QuarantineRecord` structure); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (`ingestion_task_role` WriteDataViaQuery for quarantine INSERT; quarantine graph is `urn:graph:quarantine`); [`spec-rdf-owl-ontology`](../rdf-owl-ontology/spec.md) (`graphrag.ontology.validate_graph()` is the underlying validation API this module wraps; already shipped)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** gate

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.validation.shacl` module implements the SHACL validation gate that sits between RDF triple emission and Neptune LOAD in the ingestion pipeline. It wraps `graphrag.ontology.validate_graph()` (from `spec-rdf-owl-ontology`, already shipped) with:

1. **`ShaclGate`** — the gate class the ingestion pipeline calls after `RDFEmitter` produces a Gold Turtle graph. On validation pass, returns `GateResult(outcome="passed")`; no Neptune call is issued. On validation failure, issues a SPARQL `INSERT DATA` into `urn:graph:quarantine` containing a structured `biz:QuarantineRecord` and returns `GateResult(outcome="quarantined")`.

2. **`QuarantineRecord` RDF structure** — the triples written to `urn:graph:quarantine` on every validation failure. Each record carries: the document URI, the commit SHA, the SHACL violation path(s), the violation message, and the quarantine timestamp. These records are durable — they survive across ingestion runs and are queryable by a review workflow.

3. **CI completeness gate** — a `pytest` fixture (`assert_class_shape_completeness`) wrapping `graphrag.ontology.check_class_shape_completeness()`. The fixture fails CI if any OWL class in `biz_ops.ttl` lacks a matching `sh:NodeShape` in `biz_ops_shapes.ttl`. This gate ensures new OWL classes added by future contributors always have a SHACL shape before the shape file is merged.

This module is a separate package from `graphrag.ontology` because:
- `graphrag.ontology` has no Neptune dependency (it is pure Python + rdflib/pyshacl)
- `graphrag.validation.shacl` requires the Neptune SPARQL store to issue the quarantine INSERT

**Integration seam:** `spec-ingestion-extraction-cleanse` currently calls `graphrag.ontology.validate_graph()` directly and emits quarantine records inline via its own `_provenance.py`. That plan was authored before this module existed. The implementation task for `packages/graphrag/shacl-validation` delivers the `ShaclGate` module; wiring it into the ingestion pipeline (replacing the inline quarantine INSERT in `spec-ingestion-extraction-cleanse`) is owned by that spec's implementation task (deferred: shacl-gate-ingestion-seam). The quarantine INSERT owner must be exactly one module; the cleanse spec must not add its own inline INSERT once `ShaclGate` exists.

## Boundaries

### Always do

- Call `graphrag.ontology.validate_graph(graph)` with the emitted RDF graph before every Neptune LOAD — never bypass the SHACL gate.
- Issue the quarantine INSERT using `ingestion_task_role` credentials (WriteDataViaQuery + connect) — not `mcp_lambda_role`.
- Scope the quarantine INSERT to `urn:graph:quarantine` with `INSERT DATA { GRAPH <urn:graph:quarantine> { ... } }` — never write to a partition graph from this module.
- Include at minimum: `biz:quarantineReason`, `biz:quarantinedAt`, `biz:violationPath`, and the document URI in every `biz:QuarantineRecord`.
- Return a `GateResult` dataclass — never raise on a SHACL violation. The caller decides what to do with the quarantined outcome.

### Ask first

- Adding new fields to `biz:QuarantineRecord` — downstream review workflows depend on the record structure; changes must be coordinated with any query that reads quarantine records.
- Changing the quarantine record URI scheme (`urn:quarantine:{sha}:{hash16(doc_uri)}`) — existing records in Neptune use this scheme; changing it would orphan historical records.
- Changing the `urn:graph:quarantine` graph URI — this is the canonical quarantine partition across all consumers.

### Never do

- Issue SPARQL to any graph other than `urn:graph:quarantine` from this module — partition writes are the ingestion orchestrator's scope.
- Raise an exception on a SHACL violation — return `GateResult(outcome="quarantined")`; the caller handles the consequence.
- Write a Gold S3 artifact or Neptune partition INSERT from this module — the gate only issues the quarantine INSERT; the pipeline caller skips the Gold path on quarantine.
- Use `mcp_lambda_role` for the quarantine INSERT — that role is read-only and would fail with an IAM AccessDeniedException.
- Import boto3 or botocore in `ShaclGate.__init__` or the `GateResult` dataclass — only the Neptune client calls touch AWS; the gate logic and record builder are AWS-free.

## Testing Strategy

- **TDD** — pass case (AC1): `ShaclGate.validate(valid_graph, doc_uri, sha, neptune_client)` returns `GateResult(outcome="passed")`; the mock Neptune client receives no SPARQL calls (confirmed via mock assertion).
- **TDD** — fail case → quarantine INSERT (AC2): a `biz:Policy` graph missing `biz:effectiveDate` → `GateResult(outcome="quarantined")`; mock Neptune client receives exactly one `INSERT DATA { GRAPH <urn:graph:quarantine> { ... } }` call; the SPARQL string contains a `biz:QuarantineRecord` subject with `biz:quarantineReason`, `biz:quarantinedAt`, and `biz:violationPath`.
- **TDD** — multi-violation record (AC3): a `biz:Policy` missing both `biz:effectiveDate` and `biz:scope` → `GateResult(outcome="quarantined")`; the quarantine INSERT contains two `biz:violationPath` triples (one per failing SHACL property path).
- **TDD** — Neptune INSERT failure → gate returns `GateResult(outcome="quarantine_insert_failed", error=...)` without raising (AC4): mock Neptune client raises `ConnectionError`; `ShaclGate.validate()` returns `GateResult(outcome="quarantine_insert_failed")` and logs the failure at ERROR level; no exception propagates to the caller.
- **Goal-based check** — CI completeness gate (AC5): `assert_class_shape_completeness()` returns `[]` for the bundled well-formed pair; a fixture with an extra OWL class and no matching shape produces a non-empty list; the `pytest` test that wraps it fails CI in that case.
- **Goal-based check** — import isolation (AC6): `python -c "from graphrag.validation.shacl import ShaclGate, GateResult"` exits 0 without boto3 installed (the gate class and GateResult dataclass are AWS-free; only the Neptune client injection requires boto3).

## Acceptance Criteria

- [x] `ShaclGate.validate(valid_graph, doc_uri="urn:doc:my-repo:policies/x.md", sha="abc123", neptune_client=mock_client)` where `valid_graph` is a well-formed `biz:Policy` graph with all required SHACL properties returns `GateResult(outcome="passed")`; `mock_client.sparql_update` is not called.
- [x] `ShaclGate.validate(invalid_graph, doc_uri, sha, neptune_client)` where `invalid_graph` is a `biz:Policy` missing `biz:effectiveDate` returns `GateResult(outcome="quarantined")`; `mock_client.sparql_update` is called exactly once with a SPARQL `INSERT DATA { GRAPH <urn:graph:quarantine> { <urn:quarantine:{sha}:{hash16(doc_uri)}> a biz:QuarantineRecord ; biz:quarantineReason "..."^^xsd:string ; biz:quarantinedAt "..."^^xsd:dateTime ; biz:violationPath biz:effectiveDate . } }`. The quarantine INSERT SPARQL string is built by serializing an `rdflib.Graph` containing properly typed `Literal` and `URIRef` objects — never by f-string interpolation of untrusted values. The `biz:quarantineReason` string names the failing property path.
- [x] A construction test injects a `biz:quarantineReason` value containing `" . } GRAPH <urn:graph:normative> { <urn:evil> a biz:Policy } #` (an injection payload derived from a malicious `sh:resultMessage`). The emitted SPARQL INSERT is parsed as a valid Turtle literal by rdflib and the injected closing brace does not escape the literal — confirming rdflib's `Literal.n3()` serialization provides the escaping guarantee. The Neptune client receives one valid SPARQL call; no normative graph write occurs.
- [x] For a `biz:Policy` missing both `biz:effectiveDate` and `biz:scope`, the quarantine INSERT contains two `biz:violationPath` assertions (one per failing path) on the same `biz:QuarantineRecord` subject.
- [x] When the Neptune client raises `ConnectionError` during the quarantine INSERT, `ShaclGate.validate()` returns `GateResult(outcome="quarantine_insert_failed", error="ConnectionError: ...")` without raising an exception; an ERROR-level log line is emitted with the connection error detail.
- [x] A `pytest` test calling `from graphrag.validation.shacl import assert_class_shape_completeness` and `assert_class_shape_completeness()` passes (returns `[]`) against the bundled well-formed `biz_ops.ttl` + `biz_ops_shapes.ttl`. The same test, when given a fixture ontology graph containing an extra `owl:Class biz:AuditLog` with no matching `sh:NodeShape`, returns a non-empty list — and the test is parameterized to assert this variant fails.
- [x] `python -c "from graphrag.validation.shacl import ShaclGate, GateResult"` exits 0 without boto3 or botocore installed. `ShaclGate` and `GateResult` are importable from an environment that has only `rdflib` and `pyshacl`.
- [x] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/validation/` with zero errors.

## Assumptions

- Technical: `graphrag.validation.shacl` lives in `packages/graphrag/src/graphrag/validation/`; tests in `packages/graphrag/tests/validation/`.
- Technical: The Neptune client injected into `ShaclGate` is the `NeptuneSparqlStore` from `packages/graphrag/neptune-sparql-store`. The offline substitute for tests is an `rdflib` ConjunctiveGraph mock that records SPARQL UPDATE calls.
- Technical: `graphrag.ontology.validate_graph()` (from `spec-rdf-owl-ontology`) is available as a dependency — `graphrag.validation.shacl` imports it directly. No re-implementation of pyshacl invocation.
- Technical: The quarantine record URI scheme is `urn:quarantine:{sha}:{hash16(doc_uri)}` where `hash16` is the first 16 hex characters of `SHA-256(doc_uri.encode())`. This avoids URI encoding complexity for `doc_uri` values containing special characters, and makes records sortable by commit SHA. The scheme is stable and collision-resistant for realistic corpus sizes (birthday bound at ~2^32 records per SHA).
- Technical: `biz:quarantinedAt` is an `xsd:dateTime` literal; the timestamp is `datetime.datetime.utcnow().isoformat()` at the time of the validation call. No external time dependency is injected (not a testability concern since the timestamp is checked for format, not value, in tests).
- Technical: `pyshacl` and `rdflib` are already in `pyproject.toml [ingest]` dependency group (from `spec-rdf-owl-ontology`). No new dependencies.
- Product: The CI completeness gate (`assert_class_shape_completeness`) is the same gate referenced in ADR-0012's Confirmation section: "a PR that adds a new class without an accompanying shape fails the linter." This spec wires that gate into the `pytest` test suite as a fixture-based assertion; it does not require a separate CI step.
- Product: The `GateResult` outcome `"quarantine_insert_failed"` is a distinct outcome from `"quarantined"`. The ingestion orchestrator (`spec-git-ingestion`) treats both as non-loaded outcomes for the partition INSERT decision, but they are distinct for observability and alerting.
