"""graphrag.routing — server-side multi-strategy query routing.

Public API:
- ``StrategyEnum``         — fixed strategy vocabulary (6 members)
- ``StrategyTrace``        — routing + retrieval provenance dataclass
- ``LegSpan``              — per-retrieval-leg span
- ``RuleQueryRouter``      — deterministic, no AWS calls
- ``BedrockQueryRouter``   — LLM fallback, boto3 client injected
- ``route_ask``            — dispatch entry point for the ``ask`` tool
- ``route_get_policies``   — fixed trace for the ``get_policies`` tool
"""

from __future__ import annotations

import time

from graphrag.routing._bedrock_router import BedrockQueryRouter
from graphrag.routing._rule_router import AMBIGUOUS, RuleQueryRouter, _AmbiguousType
from graphrag.routing._types import LegSpan, StrategyEnum, StrategyTrace

__all__ = [
    "AMBIGUOUS",
    "BedrockQueryRouter",
    "LegSpan",
    "RuleQueryRouter",
    "StrategyEnum",
    "StrategyTrace",
    "route_ask",
    "route_get_policies",
]


def route_ask(
    question: str,
    entity_uris: list[str] | None = None,
    bedrock_client: object | None = None,
) -> StrategyTrace:
    """Route an ``ask`` question and return a :class:`StrategyTrace`.

    Chains :class:`RuleQueryRouter` → :class:`BedrockQueryRouter` (only when
    ``RuleQueryRouter`` returns ``AMBIGUOUS``).

    Parameters
    ----------
    question:
        The raw question text (treated as untrusted data throughout).
    entity_uris:
        Optional entity URIs pre-extracted by upstream NER.
    bedrock_client:
        A boto3 ``bedrock-runtime`` client.  Required when
        ``BedrockQueryRouter`` may be invoked (i.e. when the question is
        ambiguous).  Pass ``None`` only when you are certain the rule router
        will resolve the question.

    Returns
    -------
    StrategyTrace
        ``decided_by="rule"`` when ``RuleQueryRouter`` resolved;
        ``decided_by="bedrock"`` when ``BedrockQueryRouter`` was invoked.
    """
    t0 = time.monotonic()
    rule_router = RuleQueryRouter()
    result = rule_router.route(question, entity_uris=entity_uris)
    router_ms = int((time.monotonic() - t0) * 1000)

    if not isinstance(result, _AmbiguousType):
        strategy: StrategyEnum = result
        return StrategyTrace(
            strategy=strategy,
            decided_by="rule",
            legs=[],
            router_latency_ms=router_ms,
        )

    # Rule router returned AMBIGUOUS — invoke Bedrock fallback
    if bedrock_client is None:
        # The MCP tool must always pass a bedrock_client when the question may be ambiguous.
        # Raising here is correct: a None client with an ambiguous question is a programming
        # error, not a graceful-degradation case — a silent fallback would emit a dishonest
        # trace (ADR-0013 honesty constraint).
        raise ValueError(
            "route_ask: bedrock_client is required when RuleQueryRouter returns AMBIGUOUS. "
            "Pass a boto3 bedrock-runtime client."
        )

    bedrock_router = BedrockQueryRouter(bedrock_client)
    bedrock_strategy, bedrock_legs = bedrock_router.route(question)
    router_ms = int((time.monotonic() - t0) * 1000)
    return StrategyTrace(
        strategy=bedrock_strategy,
        decided_by="bedrock",
        legs=bedrock_legs,
        router_latency_ms=router_ms,
    )


def route_get_policies() -> StrategyTrace:
    """Return the fixed :class:`StrategyTrace` for the ``get_policies`` tool.

    ``strategy=normative_exhaustive`` and ``decided_by="none"`` are set
    unconditionally — neither router is invoked.
    """
    return StrategyTrace(
        strategy=StrategyEnum.normative_exhaustive,
        decided_by="none",
        legs=[],
    )
