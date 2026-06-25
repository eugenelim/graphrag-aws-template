"""T3/T5 — text2openCypher orchestration: bounded self-heal + the audit trace (AC3/AC5).

Offline (rule/scripted generator + in-memory store + offline synthesizer): generate → validate
→ self-heal → execute → synthesize, with a render that narrates question → schema → generated
(+ verdict) → executed → rows → answer. A refusal (post-heal-cap, or an offline-subset
limitation, or a repeated execution error) returns no executed query.

NB the offline happy-path pins the **trace-ordering + refusal contract** (the durable
invariants), NOT generation quality — the rule generator is non-semantic; live semantic
generation is AC10.

# STUB: AC3, AC5
"""

from __future__ import annotations

from pathlib import Path

from graphrag.generate import RuleText2CypherGenerator
from graphrag.model import Node
from graphrag.resolve import resolve
from graphrag.sources import load_corpus
from graphrag.store import MemoryGraphStore
from graphrag.synthesize import TemplateSynthesizer
from graphrag.text2cypher import Text2CypherResult, text2cypher_query

_EXEMPLAR = "Which KEPs does SIG Network own?"
_VALID = (
    "MATCH (a:Entity {id: 'sig:sig-network'})-[r:REL {kind: 'OWNS'}]->(n:Entity) RETURN n LIMIT 5"
)
_MUTATING = "MATCH (n:Entity) DELETE n RETURN n LIMIT 5"


def _store(community_root: Path, enhancements_root: Path) -> MemoryGraphStore:
    return MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))


class _ScriptedGenerator:
    """Returns successive canned queries; records the feedback it was given each call."""

    def __init__(self, queries: list[str]) -> None:
        self._queries = list(queries)
        self.feedbacks: list[str | None] = []

    @property
    def model_id(self) -> str:
        return "scripted (test)"

    def generate(self, question: str, schema: str, *, feedback: str | None = None) -> str:
        self.feedbacks.append(feedback)
        return self._queries.pop(0) if self._queries else ""


class _RaisingStore(MemoryGraphStore):
    """A store whose hop execution always raises — to exercise the execution-error path."""

    def neighbors(self, *args: object, **kwargs: object) -> list[Node]:
        raise RuntimeError("simulated neptune execution error")


def test_offline_happy_path_executes_and_audits(
    community_root: Path, enhancements_root: Path
) -> None:
    result = text2cypher_query(
        _EXEMPLAR,
        graph_store=_store(community_root, enhancements_root),
        generator=RuleText2CypherGenerator(),
        synthesizer=TemplateSynthesizer(),
    )
    assert isinstance(result, Text2CypherResult)
    assert result.refusal_reason is None
    assert result.executed_query is not None
    assert [n.id for n in result.rows] == ["kep-1880", "kep-2086"]
    assert result.answer


def test_render_orders_the_audit_trace(community_root: Path, enhancements_root: Path) -> None:
    rendered = text2cypher_query(
        _EXEMPLAR,
        graph_store=_store(community_root, enhancements_root),
        generator=RuleText2CypherGenerator(),
        synthesizer=TemplateSynthesizer(),
    ).render()
    order = [
        rendered.index("question:"),
        rendered.index("schema:"),
        rendered.index("generated attempts:"),
        rendered.index("executed query:"),
        rendered.index("rows:"),
        rendered.index("answer:"),
    ]
    assert order == sorted(order)


def test_self_heal_recovers_within_the_cap(community_root: Path, enhancements_root: Path) -> None:
    gen = _ScriptedGenerator([_MUTATING, _VALID])  # invalid first, valid on the heal retry
    result = text2cypher_query(
        _EXEMPLAR,
        graph_store=_store(community_root, enhancements_root),
        generator=gen,
        synthesizer=TemplateSynthesizer(),
        max_heal_attempts=1,
    )
    assert len(result.attempts) == 2  # 1 initial + 1 re-generation
    assert result.attempts[0].validation.ok is False
    assert result.attempts[1].validation.ok is True
    assert result.executed_query is not None
    assert [n.id for n in result.rows] == ["kep-1880", "kep-2086"]
    # the feedback carried the violated rule (our own text), and the heal call received it.
    assert gen.feedbacks[0] is None
    assert gen.feedbacks[1] is not None and "rejected" in gen.feedbacks[1]


def test_refuses_after_heal_cap_with_no_executed_query(
    community_root: Path, enhancements_root: Path
) -> None:
    gen = _ScriptedGenerator([_MUTATING, "MATCH (n:Entity) SET n.x = 1 RETURN n LIMIT 5"])
    result = text2cypher_query(
        _EXEMPLAR,
        graph_store=_store(community_root, enhancements_root),
        generator=gen,
        synthesizer=TemplateSynthesizer(),
        max_heal_attempts=1,
    )
    assert result.refusal_reason is not None
    assert result.executed_query is None
    assert result.rows == []
    assert "(no query executed)" in result.render()


def test_offline_unsupported_query_refuses_runs_live(
    community_root: Path, enhancements_root: Path
) -> None:
    # A valid (read-only, bounded) but out-of-offline-subset query: refuse with "runs live",
    # never false rows; do not burn the self-heal budget (re-gen won't help offline).
    two_hop = (
        "MATCH (a:Entity {id: 'sig:sig-network'})-[:REL]->(b:Entity)-[:REL]->(n:Entity) "
        "RETURN n LIMIT 5"
    )
    gen = _ScriptedGenerator([two_hop])
    result = text2cypher_query(
        _EXEMPLAR,
        graph_store=_store(community_root, enhancements_root),
        generator=gen,
        synthesizer=TemplateSynthesizer(),
    )
    assert result.executed_query is None
    assert result.refusal_reason is not None and "offline subset" in result.refusal_reason
    assert len(result.attempts) == 1  # not re-generated


def test_execution_error_feeds_self_heal_then_refuses(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _RaisingStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))
    gen = _ScriptedGenerator([_VALID, _VALID])  # both valid, both raise at execution
    result = text2cypher_query(
        _EXEMPLAR,
        graph_store=store,
        generator=gen,
        synthesizer=TemplateSynthesizer(),
        max_heal_attempts=1,
    )
    assert result.executed_query is None
    assert result.refusal_reason is not None
    assert result.attempts[0].error is not None and "execution-error" in result.attempts[0].error
    # the raw error is recorded internally (the Lambda boundary sanitizes it for the caller),
    # and BOTH attempts hit the raising execution path (self-heal re-ran, then refused).
    assert len(result.attempts) == 2
    assert all("execution-error" in (a.error or "") for a in result.attempts)
