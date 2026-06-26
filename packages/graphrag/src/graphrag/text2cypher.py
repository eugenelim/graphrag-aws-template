"""Text2openCypher orchestration — the *flexible* (risky) path, end to end (AC3/AC5/AC6).

``text2cypher_query`` is the risky counterpart to ``governed.governed_query``: a question is
sent to a generator that **writes** openCypher (``generate.py``), the query is validated
read-only (``validate.py``), self-healed within a bounded cap, executed (live Neptune /
offline subset evaluator), and a display answer is synthesized over the rows. The
``Text2CypherResult`` carries the full **audit trace** — the schema shown to the model, every
generated query with its validation verdict, the query that actually executed, the rows, and
the answer — so a watcher sees exactly what the model wrote and that it was read-only-checked
(no black-box hop; charter principle 1).

The guard is **layered** (ADR-0004): the read-only validator (layer 1) + this bounded
self-heal + the IAM read-only data-action scope (the *write* backstop) + the Neptune engine
query timeout (the *read-cost* backstop). The validator and the orchestrator are layer 1 — the
guarantee lives in the IAM scope and the engine timeout, which hold even for the classes the
validator cannot catch.

PyYAML-free (rides the query Lambda's ``Code.from_asset`` bundle): imports only
``generate``/``validate``/``cypher_eval``/``synthesize``/``store``/``model``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cypher_eval import UnsupportedOfflineQuery, eval_read_query
from .generate import GRAPH_SCHEMA_DESCRIPTION, Text2CypherGenerator
from .model import Node
from .store.base import GraphStore
from .store.neptune import NeptuneGraphStore
from .synthesize import Synthesizer
from .validate import DEFAULT_MAX_LIMIT, ValidationResult, validate_read_only

# One initial generation + up to MAX_HEAL_ATTEMPTS re-generations on a validation/execution
# error (default 1 ⇒ at most 2 LLM generation calls per request — the per-request cost/DoS
# bound, ADR-0004). Raising it is an *Ask first* change (spec Boundaries).
MAX_HEAL_ATTEMPTS = 1


@dataclass
class GenerationAttempt:
    """One generate→validate cycle's provenance, recorded in the trace.

    ``error`` is set only when a query *validated* but failed to *execute* (a Neptune error or
    an offline-subset limitation)."""

    query: str
    validation: ValidationResult
    error: str | None = None


@dataclass
class Text2CypherResult:
    """The audit artifact of one text2cypher query.

    On success: the schema shown to the model, every generation attempt with its verdict, the
    ``executed_query``, the rows, the answer, and citations. On refusal: ``refusal_reason`` is
    set and ``executed_query`` is ``None`` — **no query ran**."""

    question: str
    schema: str
    attempts: list[GenerationAttempt] = field(default_factory=list)
    executed_query: str | None = None
    rows: list[Node] = field(default_factory=list)
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    refusal_reason: str | None = None

    def render(self) -> str:
        """Narrate the audit trace in order: question → schema → generated query (+ verdict, per
        attempt) → executed query → rows → answer (no black-box hop, charter principle 1)."""
        lines = [f"question: {self.question}", "schema:", self.schema, "generated attempts:"]
        for index, attempt in enumerate(self.attempts, start=1):
            verdict = (
                "valid"
                if attempt.validation.ok
                else f"rejected: {attempt.validation.violated_rule}"
            )
            lines.append(f"  {index}. {attempt.query}")
            lines.append(f"     verdict: {verdict}")
            if attempt.error is not None:
                lines.append(f"     execution error: {attempt.error}")
        if self.refusal_reason is not None:
            lines.append(f"refusal: {self.refusal_reason}")
            lines.append("(no query executed)")
            return "\n".join(lines)
        lines.append(f"executed query: {self.executed_query}")
        rows = ", ".join(node.id for node in self.rows) or "(none)"
        lines.append(f"rows: {rows}")
        lines.append("citations:")
        for cite in self.citations:
            lines.append(f"  - {cite}")
        lines.append(f"answer: {self.answer}")
        return "\n".join(lines)


def _execute(graph_store: GraphStore, query: str) -> list[Node]:
    """Execute a validated read query — live on Neptune (full engine) or against the bounded
    offline subset evaluator — and return its rows deduped + sorted by id (backend-independent)."""
    if isinstance(graph_store, NeptuneGraphStore):
        nodes = graph_store.run_read_query(query)
    else:
        nodes = eval_read_query(query, graph_store)
    by_id: dict[str, Node] = {}
    for node in nodes:
        by_id.setdefault(node.id, node)
    return [by_id[node_id] for node_id in sorted(by_id)]


def text2cypher_query(
    question: str,
    *,
    graph_store: GraphStore,
    generator: Text2CypherGenerator,
    synthesizer: Synthesizer,
    schema: str = GRAPH_SCHEMA_DESCRIPTION,
    max_limit: int = DEFAULT_MAX_LIMIT,
    max_heal_attempts: int = MAX_HEAL_ATTEMPTS,
) -> Text2CypherResult:
    """Generate → validate → bounded self-heal → execute → synthesize, with a full audit trace.

    A query that fails validation feeds the self-heal loop (the violated rule is the feedback —
    our own text, never the raw error); an execution error feeds it once with a generic signal
    (the raw Neptune error stays internal). After the cap, or on an offline-subset limitation,
    the result is a refusal with **no executed query** (AC3/AC5)."""
    result = Text2CypherResult(question=question, schema=schema)
    feedback: str | None = None
    for _ in range(max_heal_attempts + 1):
        query = generator.generate(question, schema, feedback=feedback)
        validation = validate_read_only(query, max_limit=max_limit)
        attempt = GenerationAttempt(query=query, validation=validation)
        result.attempts.append(attempt)
        if not validation.ok:
            # The feedback is our own rule text (safe); the generator additionally frames it as
            # untrusted data (ADR-0004 layer 2 — the self-heal is not an injection amplifier).
            feedback = (
                f"The previous query was rejected by the read-only validator: "
                f"{validation.violated_rule}. Return exactly one read-only query."
            )
            continue
        try:
            rows = _execute(graph_store, validation.normalized_query)
        except UnsupportedOfflineQuery as exc:
            # An offline-only limitation — re-generating won't help (live Neptune runs it), so
            # refuse clearly rather than burn the self-heal budget.
            attempt.error = f"offline-unsupported: {exc}"
            result.refusal_reason = (
                f"the generated query runs live on Neptune, not in the offline subset ({exc})"
            )
            return result
        except RuntimeError as exc:
            # An execution error (a Neptune error, an IAM denial on a validator-missed write, a
            # bad result shape). Feed back once with a generic signal — the raw error is partly
            # attacker-influenced/schema-bearing and stays internal (the Lambda boundary returns
            # a sanitized envelope; AC8).
            attempt.error = f"execution-error: {exc}"
            feedback = "The previous query failed to execute. Return one corrected read-only query."
            continue
        synthesis = synthesizer.synthesize(question, [], rows)
        result.executed_query = validation.normalized_query
        result.rows = rows
        result.answer = synthesis.answer
        result.citations = synthesis.citations
        return result

    last = result.attempts[-1] if result.attempts else None
    result.refusal_reason = (
        "no read-only query could be produced within the self-heal limit"
        if last is not None and not last.validation.ok
        else "the generated query could not be executed within the self-heal limit"
    )
    return result
