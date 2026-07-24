"""text2sparql orchestrator — generate → validate → bounded self-heal → execute.

``text2sparql_query`` is the single public entry point:

1. Generates a SPARQL SELECT via ``BedrockText2SparqlGenerator``.
2. Validates against ``SparqlValidator`` (mutation denylist + structural checks).
3. On validation failure, re-generates once with the rule name as feedback in
   ``messages`` (not ``system``) — max 1 re-generation = 2 LLM calls total.
4. On success, executes against the injected store.
5. Returns a ``Text2SparqlResult`` with the full audit trace.

ADR-0014 content-capture policy: ``question`` is used only to build the Bedrock
``messages`` block.  It is never stored in ``Text2SparqlResult`` or logged above
DEBUG level.

ADR-0011 guard layers:
  - App-layer validator (this module + ``_validator.py``) — belt-and-suspenders.
  - IAM ``ReadDataViaQuery``-only grant on ``mcp_lambda_role`` — the load-bearing control.
"""

from __future__ import annotations

import logging

from ..store.sparql_base import SparqlStore
from ._executor import execute_select
from ._generator import BedrockText2SparqlGenerator
from ._types import GeneratedQuery, Text2SparqlResult
from ._validator import SparqlValidator

log = logging.getLogger(__name__)

# One initial generation + up to MAX_HEAL_ATTEMPTS re-generations on a validation or
# execution error (default 1 → at most 2 LLM calls per request — the per-request
# cost/DoS bound per ADR-0011).  Raising it is an *Ask first* change (spec Boundaries).
MAX_HEAL_ATTEMPTS = 1

_validator = SparqlValidator()


def text2sparql_query(
    question: str,
    *,
    schema_context: str,
    graph_uri: str,
    store: SparqlStore,
    generator: BedrockText2SparqlGenerator,
    max_heal_attempts: int = MAX_HEAL_ATTEMPTS,
) -> Text2SparqlResult:
    """Generate → validate → bounded self-heal → execute; return full audit trace.

    ``question`` is passed to the generator as untrusted data in ``messages``.
    It is NOT stored in the returned ``Text2SparqlResult`` (ADR-0014 content-capture
    policy — question text never in spans or results above DEBUG).

    A query that fails validation feeds the self-heal loop; a Neptune execution error
    also feeds it once with a sanitised signal.  After the cap, or if both attempts
    fail, the result is a refusal with ``executed_query=None`` and ``rows=[]``.
    """
    result = Text2SparqlResult(schema_context=schema_context)
    feedback: str | None = None

    for _ in range(max_heal_attempts + 1):
        query_text = generator.generate(question, schema_context, graph_uri, feedback=feedback)
        validation = _validator.validate(query_text)
        result.generated_queries.append(
            GeneratedQuery(query_text=query_text, validation_verdict=validation)
        )

        if not validation.valid:
            # The feedback is our own rule name (safe text; not attacker-controlled
            # content) — the generator frames it as untrusted data in messages (layer 2).
            feedback = (
                f"The previous query was rejected: rule={validation.rule!r}. "  # noqa: S608
                "Return exactly one SPARQL SELECT with FROM NAMED."
            )
            log.debug("validation rejected: rule=%s", validation.rule)
            continue

        try:
            rows = execute_select(store, query_text)
        except Exception as exc:  # noqa: BLE001
            # Execution error — sanitise before feeding back.  The raw error text is
            # partly attacker-influenced and schema-bearing; it stays internal.  Catching
            # broadly here is intentional: rdflib raises pyparsing.ParseException for
            # malformed queries; Neptune raises various undocumented error types.  Bedrock
            # errors never reach this path (they are raised before this try block).
            log.debug("execution error (internal, not returned to caller): %s", exc)
            feedback = (
                "The previous query failed to execute. "
                "Return one corrected SPARQL SELECT with FROM NAMED."
            )
            continue

        result.executed_query = query_text
        result.rows = rows
        return result

    # Cap reached — set refusal reason from the last attempt's state.
    result.refusal_reason = "max heal attempts reached"
    return result
