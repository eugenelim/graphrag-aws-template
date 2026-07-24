"""Shared types for graphrag.routing — StrategyEnum, StrategyTrace, LegSpan.

``StrategyEnum`` is a ``StrEnum`` so values serialise to their string form
directly (e.g. ``json.dumps({"strategy": StrategyEnum.hybrid_graph})``).

``StrategyEnum.global_`` uses a trailing underscore because ``global`` is a
Python keyword; the *string value* is ``"global"`` so JSON callers receive the
expected key — but Python code must reference ``StrategyEnum.global_``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Literal


class StrategyEnum(enum.StrEnum):
    """Strategy vocabulary.  Exactly 6 members; ``ambiguous`` is NOT a member.

    ``ambiguous`` is a sentinel returned only by :class:`RuleQueryRouter` to
    indicate that no single dominant signal was detected.  It is never stored
    in a :class:`StrategyTrace`.
    """

    hybrid_graph = "hybrid_graph"
    structured = "structured"
    graph_expand = "graph_expand"
    vector_only = "vector_only"
    global_ = "global"  # "global" is a Python keyword; string value stays "global"
    normative_exhaustive = "normative_exhaustive"


@dataclass
class LegSpan:
    """Per-retrieval-leg span — store identity + optional timing and error."""

    store: str  # "opensearch" | "neptune" | "bedrock"
    latency_ms: int | None = None
    error: str | None = None  # e.g. "throttle-exhausted" for the retry-exhaustion path


@dataclass
class StrategyTrace:
    """Routing and retrieval provenance returned with every ask/get_policies response.

    ``decided_by`` vocabulary:
    - ``"rule"``    — ``RuleQueryRouter`` resolved to a concrete strategy.
    - ``"bedrock"`` — ``BedrockQueryRouter`` was invoked (either normally or after
                      throttle exhaustion with fallback to ``hybrid_graph``).
    - ``"none"``    — ``get_policies`` fixed path; neither router was consulted.
    """

    strategy: StrategyEnum
    decided_by: Literal["rule", "bedrock", "none"]
    legs: list[LegSpan] = field(default_factory=list)
    router_latency_ms: int | None = None
