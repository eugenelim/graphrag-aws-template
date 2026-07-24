"""RuleQueryRouter — deterministic signal detection with no AWS dependencies.

This file must remain importable without boto3 or botocore installed.
See spec-multi-strategy-routing Boundaries § Never do.

Routing precedence (evaluation order — see plan.md Design decisions):
1. Aggregation verb present                         →  structured
2. Relationship verb + any entity URI present       →  graph_expand
3. Two or more entity URIs, no dominant signal      →  ambiguous  (sentinel)
4. One entity URI, no aggregation/relationship verb →  hybrid_graph
5. Thematic marker, no entity URI                  →  global
6. No signal detected                               →  vector_only

``ambiguous`` is returned as the singleton ``_AMBIGUOUS`` — a sentinel, never
stored in a ``StrategyTrace``.  Callers check ``result is _AMBIGUOUS``.
"""

from __future__ import annotations

from graphrag.routing._signals import (
    detect_entity_uris,
    has_aggregation_verb,
    has_relationship_verb,
    has_thematic_marker,
)
from graphrag.routing._types import StrategyEnum

# ---------------------------------------------------------------------------
# Sentinel for "could not resolve — escalate to BedrockQueryRouter"
# ---------------------------------------------------------------------------
_AMBIGUOUS: object = object()
AMBIGUOUS: object = _AMBIGUOUS  # public alias for callers


class RuleQueryRouter:
    """Deterministic routing over keyword signals.

    Instantiate once and call :meth:`route` as many times as needed.
    No external service is called; no AWS SDK is imported.
    """

    def route(
        self,
        question: str,
        entity_uris: list[str] | None = None,
    ) -> StrategyEnum | object:
        """Classify *question* and return a :class:`StrategyEnum` or ``AMBIGUOUS``.

        Parameters
        ----------
        question:
            The raw question text (treated as untrusted data — never logged
            at INFO or above per ADR-0014 content-off-by-default principle).
        entity_uris:
            Optional list of entity URIs pre-extracted by an upstream NER /
            question-analyzer step.  When absent or empty, entity-URI signals
            are derived from the question text itself via regex.

        Returns
        -------
        StrategyEnum
            The resolved strategy.
        AMBIGUOUS
            When no single dominant signal is detected and Bedrock should
            decide.
        """
        # Merge entity URIs from explicit list and regex detection
        text_uris = detect_entity_uris(question)
        explicit_uris: list[str] = entity_uris or []
        all_uris: list[str] = list(dict.fromkeys(explicit_uris + text_uris))

        # ── Rule 1: aggregation verb → structured ────────────────────────────
        if has_aggregation_verb(question):
            return StrategyEnum.structured

        # ── Rule 2: relationship verb + any entity URI → graph_expand ────────
        if has_relationship_verb(question) and all_uris:
            return StrategyEnum.graph_expand

        # ── Rule 3: multiple entity URIs, no dominant signal → ambiguous ─────
        if len(all_uris) >= 2:
            return _AMBIGUOUS

        # ── Rule 4: single entity URI → hybrid_graph ─────────────────────────
        if len(all_uris) == 1:
            return StrategyEnum.hybrid_graph

        # ── Rule 5: thematic marker → global ─────────────────────────────────
        if has_thematic_marker(question):
            return StrategyEnum.global_

        # ── Rule 6: no signal → vector_only ──────────────────────────────────
        return StrategyEnum.vector_only
