# Plan: rdf-owl-ontology-loader

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

## Approach

Two tasks. T1 (OntologyLoader) uses the already-shipped `graphrag.ontology.load_ontology()`
and `graphrag.store.sparql_base.SparqlStore` interfaces — no new dependencies. T2
(integration verification) runs the gates.

## Tasks

### T1: Implement OntologyLoader

**Depends on:** none (graphrag.provenance ships in same PR)

**Touches:**
- `packages/graphrag/src/graphrag/ontology_loader/__init__.py`
- `packages/graphrag/src/graphrag/ontology_loader/_loader.py`
- `packages/graphrag/tests/test_ontology_loader.py`

**Done when:** 5 tests pass; ruff + mypy clean.

## Tasks (continued)

### T2: Gate verification

**Depends on:** T1

**Done when:** full test suite green (`pytest packages/graphrag/tests/`); `ruff check`, `ruff format --check`, and `mypy` pass; PROV-O emission failure is non-fatal (test added in T1 pass).

## Changelog

- 2026-07-23: initial plan
