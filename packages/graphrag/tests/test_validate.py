"""T1 — read-only static validator, the text2cypher guard's layer 1 (AC1).

A reject table (mutating clauses, any CALL, multi-statement, comment-hidden mutation,
RETURN-less, unbounded variable-length path, mutating keyword inside a string literal —
the conservative false-reject) and an accept table (bounded reads, bounded var-length,
LIMIT injection/capping). The validator is layer 1, NOT the guarantee (IAM write-scope +
the Neptune engine query timeout back it — ADR-0004), so the string-literal false-reject
is the accepted, pinned trade-off.

# STUB: AC1
"""

from __future__ import annotations

import pytest

from graphrag.validate import DEFAULT_MAX_LIMIT, validate_read_only

_GOOD = "MATCH (n:Entity {id: 'sig:sig-network'}) RETURN n LIMIT 10"


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:Entity) CREATE (m:Entity {id: 'x'}) RETURN n LIMIT 5",
        "match (n:Entity) where n.id='x' set n.kind='SIG' return n limit 5",
        "MATCH (n:Entity {id:'x'}) MERGE (m:Entity {id:'y'}) RETURN n LIMIT 5",
        "MATCH (n:Entity {id:'x'}) DETACH DELETE n",
        "MATCH (n:Entity {id:'x'}) REMOVE n.kind RETURN n LIMIT 5",
        "DROP INDEX foo",
        # any CALL is rejected (read or write), so the two-action Neptune grant suffices.
        "CALL db.labels() YIELD label RETURN label LIMIT 5",
        # two statements.
        "MATCH (n:Entity {id:'x'}) RETURN n LIMIT 5; CREATE (m:Entity {id:'y'})",
        # a mutation hidden after a line comment.
        "MATCH (n:Entity {id:'x'}) RETURN n LIMIT 5 // harmless\nCREATE (m:Entity {id:'y'})",
        # no RETURN — nothing to bound or execute.
        "MATCH (n:Entity {id:'x'}) WHERE n.kind = 'SIG'",
        # unbounded variable-length paths.
        "MATCH (a:Entity {id:'x'})-[*]->(b:Entity) RETURN b AS n LIMIT 5",
        "MATCH (a:Entity {id:'x'})-[r:REL*..]->(b:Entity) RETURN b AS n LIMIT 5",
        "MATCH (a:Entity {id:'x'})-[r:REL*2..]->(b:Entity) RETURN b AS n LIMIT 5",
        # mutating keyword inside a string literal — the conservative false-reject.
        "MATCH (n:Entity) WHERE n.title CONTAINS 'how to DELETE a KEP' RETURN n LIMIT 5",
    ],
)
def test_rejects_non_read_only_or_unbounded(query: str) -> None:
    result = validate_read_only(query)
    assert result.ok is False
    assert result.violated_rule  # a rule is named
    assert result.normalized_query == ""  # a rejected query is never normalized for execution


@pytest.mark.parametrize(
    "query",
    [
        _GOOD,
        "MATCH (n:Entity) WHERE n.kind = 'SIG' RETURN n ORDER BY n.id LIMIT 3",
        # a BOUNDED variable-length path is allowed.
        "MATCH (a:Entity {id:'x'})-[r:REL*1..2]->(b:Entity) RETURN b AS n LIMIT 5",
        "MATCH (a:Entity {id:'x'})-[r:REL*..3]->(b:Entity) RETURN b AS n LIMIT 5",
        # an exact-length hop is bounded.
        "MATCH (a:Entity {id:'x'})-[r:REL*2]->(b:Entity) RETURN b AS n LIMIT 5",
    ],
)
def test_accepts_bounded_reads(query: str) -> None:
    result = validate_read_only(query)
    assert result.ok is True
    assert result.violated_rule is None
    assert "RETURN" in result.normalized_query.upper()


def test_missing_limit_is_injected_at_max() -> None:
    result = validate_read_only("MATCH (n:Entity {id: 'sig:sig-network'}) RETURN n")
    assert result.ok is True
    assert result.normalized_query.rstrip().endswith(f"LIMIT {DEFAULT_MAX_LIMIT}")


def test_over_bound_limit_is_capped() -> None:
    result = validate_read_only("MATCH (n:Entity) RETURN n LIMIT 100000", max_limit=25)
    assert result.ok is True
    assert result.normalized_query.rstrip().endswith("LIMIT 25")
    assert "100000" not in result.normalized_query


def test_trailing_semicolon_is_a_single_statement() -> None:
    result = validate_read_only("MATCH (n:Entity {id:'x'}) RETURN n LIMIT 5;")
    assert result.ok is True
    assert ";" not in result.normalized_query  # normalized form drops the trailing ;
