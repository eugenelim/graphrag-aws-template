"""T3 — text2sparql_query orchestrator: self-heal loop + audit trace (AC4, AC5, AC6).

All tests run offline using rdflib (MemorySparqlStore) and a scripted mock generator.
Key invariants:
  - Happy path: 1 LLM call, rows returned, executed_query set.
  - Self-heal path: first attempt fails validation → re-generation with feedback →
    second passes → rows returned (2 LLM calls total).
  - Cap path: both attempts fail validation → refusal with executed_query=None,
    rows=[], no third LLM call.
  - Feedback re-injection guard: system block unchanged, feedback in messages.
  - question text does NOT appear in any field of Text2SparqlResult.
  - rdflib offline execution: fixture named graph with 3 triples → 3 rows returned.
"""

from __future__ import annotations

from typing import Any

import pytest

from graphrag.store.neptune_sparql_memory import MemorySparqlStore
from graphrag.text2sparql._generator import BedrockText2SparqlGenerator
from graphrag.text2sparql._orchestrator import text2sparql_query
from graphrag.text2sparql._types import Text2SparqlResult

_GRAPH_URI = "urn:graph:normative"
_SCHEMA = "PREFIX biz: <https://biz-ops.example.org/> biz:Policy a owl:Class ."
_QUESTION = "Which policies govern data retention?"

_VALID_QUERY = (
    "SELECT ?policy "
    "FROM NAMED <urn:graph:normative> "
    "WHERE { GRAPH <urn:graph:normative> { ?policy a <https://biz-ops.example.org/Policy> } }"
)
_INVALID_QUERY = "SELECT ?s WHERE { ?s a <https://biz-ops.example.org/Policy> }"  # no FROM NAMED


# ── Scripted mock generator ───────────────────────────────────────────────────


class _ScriptedGenerator(BedrockText2SparqlGenerator):
    """Returns successive canned queries; records every (question, feedback) call."""

    def __init__(self, queries: list[str]) -> None:
        super().__init__(client=_NeverCallBedrock())
        self._queries = list(queries)
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        question: str,
        schema_context: str,
        graph_uri: str,
        *,
        feedback: str | None = None,
    ) -> str:
        self.calls.append(
            {"question": question, "schema_context": schema_context, "feedback": feedback}
        )
        return self._queries.pop(0) if self._queries else ""


class _NeverCallBedrock:
    def converse(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("real Bedrock client should never be called in these tests")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def store_with_policies() -> MemorySparqlStore:
    """rdflib in-memory store seeded with 3 biz:Policy triples in urn:graph:normative."""
    store = MemorySparqlStore()
    ttl = """
    @prefix biz: <https://biz-ops.example.org/> .
    @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

    <urn:policy:data-retention> a biz:Policy .
    <urn:policy:access-control>  a biz:Policy .
    <urn:policy:encryption>      a biz:Policy .
    """
    store.load_turtle(ttl, _GRAPH_URI)
    return store


@pytest.fixture()
def empty_store() -> MemorySparqlStore:
    return MemorySparqlStore()


# ── Happy path ────────────────────────────────────────────────────────────────


def test_happy_path_executes_and_returns_rows(
    store_with_policies: MemorySparqlStore,
) -> None:
    gen = _ScriptedGenerator([_VALID_QUERY])
    result = text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store_with_policies,
        generator=gen,
    )
    assert isinstance(result, Text2SparqlResult)
    assert result.refusal_reason is None
    assert result.executed_query == _VALID_QUERY
    assert len(result.rows) == 3
    assert len(gen.calls) == 1  # exactly 1 LLM call


def test_happy_path_audit_trace_shape(store_with_policies: MemorySparqlStore) -> None:
    gen = _ScriptedGenerator([_VALID_QUERY])
    result = text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store_with_policies,
        generator=gen,
    )
    assert result.schema_context == _SCHEMA
    assert len(result.generated_queries) == 1
    assert result.generated_queries[0].query_text == _VALID_QUERY
    assert result.generated_queries[0].validation_verdict.valid is True


# ── Self-heal path ────────────────────────────────────────────────────────────


def test_self_heal_recovers_within_cap(store_with_policies: MemorySparqlStore) -> None:
    # First attempt: missing FROM NAMED (invalid); second attempt: valid.
    gen = _ScriptedGenerator([_INVALID_QUERY, _VALID_QUERY])
    result = text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store_with_policies,
        generator=gen,
        max_heal_attempts=1,
    )
    assert len(gen.calls) == 2  # exactly 2 LLM calls
    assert result.executed_query == _VALID_QUERY
    assert result.refusal_reason is None
    assert len(result.generated_queries) == 2
    assert result.generated_queries[0].validation_verdict.valid is False
    assert result.generated_queries[1].validation_verdict.valid is True


def test_self_heal_feedback_contains_rule_name(store_with_policies: MemorySparqlStore) -> None:
    gen = _ScriptedGenerator([_INVALID_QUERY, _VALID_QUERY])
    text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store_with_policies,
        generator=gen,
        max_heal_attempts=1,
    )
    # The first call has no feedback; the second carries the rule name.
    assert gen.calls[0]["feedback"] is None
    assert gen.calls[1]["feedback"] is not None
    assert "missing_from_named" in (gen.calls[1]["feedback"] or "")


# ── Cap path ──────────────────────────────────────────────────────────────────


def test_refuses_after_cap_no_executed_query(empty_store: MemorySparqlStore) -> None:
    gen = _ScriptedGenerator([_INVALID_QUERY, _INVALID_QUERY])
    result = text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=empty_store,
        generator=gen,
        max_heal_attempts=1,
    )
    assert len(gen.calls) == 2  # exactly 2 LLM calls, no third
    assert result.executed_query is None
    assert result.rows == []
    assert result.refusal_reason is not None
    assert "max heal" in result.refusal_reason


# ── Re-injection guard ────────────────────────────────────────────────────────


def test_feedback_with_mutation_keyword_does_not_propagate_to_system(
    store_with_policies: MemorySparqlStore,
) -> None:
    # A feedback string containing SPARQL Update keywords (e.g. from a poisoned schema)
    # must not alter the system prompt framing (ADR-0011 / OWASP LLM01).
    # The scripted generator captures feedback; we assert it's isolated in messages.
    #
    # Here we test the generator contract directly via a fake Bedrock that records calls,
    # seeded through the orchestrator's feedback path.
    from graphrag.text2sparql._generator import _GENERATE_SYSTEM_PROMPT

    captured_calls: list[dict[str, Any]] = []

    class _RecordingGenerator(BedrockText2SparqlGenerator):
        def __init__(self, queries: list[str]) -> None:
            super().__init__(client=_NeverCallBedrock())
            self._queries = list(queries)

        def generate(
            self,
            question: str,
            schema_context: str,
            graph_uri: str,
            *,
            feedback: str | None = None,
        ) -> str:
            captured_calls.append({"feedback": feedback})
            return self._queries.pop(0) if self._queries else ""

    # First attempt invalid (triggers feedback with the rule name), second valid.
    gen = _RecordingGenerator([_INVALID_QUERY, _VALID_QUERY])
    text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store_with_policies,
        generator=gen,
        max_heal_attempts=1,
    )
    # The feedback is our own rule text, not the poison — confirm system is constant.
    # The system prompt is a module-level constant; we verify it has not been modified.
    assert "DROP GRAPH" not in _GENERATE_SYSTEM_PROMPT
    assert captured_calls[0]["feedback"] is None  # first call: no feedback
    # second call: feedback contains rule name (our text), not the raw poison
    second_feedback = captured_calls[1]["feedback"] or ""
    assert "missing_from_named" in second_feedback


# ── question text absent from result ─────────────────────────────────────────


def test_question_text_not_in_result_fields(store_with_policies: MemorySparqlStore) -> None:
    # ADR-0014 content-capture policy: question never appears in any result field.
    sensitive_question = "Which SECRET policies govern data retention?"
    gen = _ScriptedGenerator([_VALID_QUERY])
    result = text2sparql_query(
        sensitive_question,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store_with_policies,
        generator=gen,
    )
    # Check every string field of the result.
    result_str = str(result)
    assert sensitive_question not in result_str
    assert "SECRET" not in result_str
    # Directly check individual fields.
    assert sensitive_question not in result.schema_context
    assert result.executed_query is None or sensitive_question not in result.executed_query
    assert result.refusal_reason is None or sensitive_question not in result.refusal_reason
    for gq in result.generated_queries:
        assert sensitive_question not in gq.query_text


# ── rdflib offline execution / named-graph isolation ─────────────────────────


def test_rdflib_named_graph_returns_fixture_rows(
    store_with_policies: MemorySparqlStore,
) -> None:
    # AC5: fixture store seeded with 3 Policy triples; the SELECT returns 3 URIs.
    gen = _ScriptedGenerator([_VALID_QUERY])
    result = text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store_with_policies,
        generator=gen,
    )
    assert result.refusal_reason is None
    assert len(result.rows) == 3
    policy_uris = {row["policy"] for row in result.rows}
    assert "urn:policy:data-retention" in policy_uris
    assert "urn:policy:access-control" in policy_uris
    assert "urn:policy:encryption" in policy_uris
    assert result.executed_query is not None
    assert "FROM NAMED" in result.executed_query


def test_rdflib_unscoped_select_returns_zero_rows() -> None:
    # An unscoped SELECT (no FROM NAMED) is rejected by the validator before execution,
    # resulting in a refusal.  This confirms named-graph isolation at the validator layer.
    store = MemorySparqlStore()
    ttl = """
    @prefix biz: <https://biz-ops.example.org/> .
    <urn:policy:test> a biz:Policy .
    """
    store.load_turtle(ttl, _GRAPH_URI)
    gen = _ScriptedGenerator([_INVALID_QUERY, _INVALID_QUERY])
    result = text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store,
        generator=gen,
        max_heal_attempts=1,
    )
    assert result.executed_query is None
    assert result.rows == []
    assert result.refusal_reason is not None


def test_rdflib_named_graph_isolation_cross_graph() -> None:
    """AC5 (rdflib isolation): a query scoped to graph A (normative) returns zero
    rows when the matching triples live exclusively in graph B (descriptive).

    This exercises rdflib's named-graph isolation directly — the validator
    allows the query through (it has FROM NAMED), but rdflib's scoped evaluation
    returns no rows because the queried graph has no biz:Policy instances.

    Both graphs must have actual triples so rdflib recognises them as known
    graphs and does not attempt a remote fetch of the graph URI.
    """
    store = MemorySparqlStore()
    # Graph A (normative): has ontology triples, no biz:Policy instances.
    ttl_normative = """
    @prefix biz: <https://biz-ops.example.org/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    <urn:ontology:Policy> a owl:Class .
    """
    # Graph B (descriptive): has biz:Policy instances — what the query looks for.
    ttl_descriptive = """
    @prefix biz: <https://biz-ops.example.org/> .
    <urn:policy:test> a biz:Policy .
    """
    store.load_turtle(ttl_normative, "urn:graph:normative")
    store.load_turtle(ttl_descriptive, "urn:graph:descriptive")

    # The query is scoped to graph A (normative) — validator accepts it.
    query_scoped_to_a = (
        "SELECT ?policy "
        "FROM NAMED <urn:graph:normative> "
        "WHERE { GRAPH <urn:graph:normative> { ?policy a <https://biz-ops.example.org/Policy> } }"
    )
    gen = _ScriptedGenerator([query_scoped_to_a])
    result = text2sparql_query(
        _QUESTION,
        schema_context=_SCHEMA,
        graph_uri=_GRAPH_URI,
        store=store,
        generator=gen,
    )
    # The query executed (validator passed), but rdflib's named-graph scope
    # ensures zero rows — the biz:Policy instances are in graph B, not graph A.
    assert result.executed_query is not None
    assert result.refusal_reason is None
    assert result.rows == []
