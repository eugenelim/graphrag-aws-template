"""Tests for StrategyEnum and StrategyTrace — T1 and T4 integration cases.

Covers:
- StrategyEnum members, count, and string values (T1)
- StrategyTrace construction and serialisation (T1)
- global_ keyword clash (T1 / Risks)
- route_get_policies fixed trace (T4 / AC11–AC12)
- route_ask StrategyTrace completeness (T4 / AC9–AC10)
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from graphrag.routing import (
    StrategyEnum,
    StrategyTrace,
    route_ask,
    route_get_policies,
)
from graphrag.routing._types import LegSpan

# ---------------------------------------------------------------------------
# T1 — StrategyEnum
# ---------------------------------------------------------------------------


def test_strategy_enum_has_six_members() -> None:
    assert len(StrategyEnum) == 6


@pytest.mark.parametrize(
    ("member", "expected_str"),
    [
        (StrategyEnum.hybrid_graph, "hybrid_graph"),
        (StrategyEnum.structured, "structured"),
        (StrategyEnum.graph_expand, "graph_expand"),
        (StrategyEnum.vector_only, "vector_only"),
        (StrategyEnum.global_, "global"),
        (StrategyEnum.normative_exhaustive, "normative_exhaustive"),
    ],
)
def test_strategy_enum_string_values(member: StrategyEnum, expected_str: str) -> None:
    assert str(member) == expected_str


def test_strategy_enum_global_serialises_to_global_string() -> None:
    """StrategyEnum.global_ is the Python name; the JSON value must be "global"."""
    payload = {"strategy": StrategyEnum.global_}
    serialised = json.dumps(payload)
    assert '"global"' in serialised, f"Expected '\"global\"' in {serialised!r}"


def test_strategy_enum_invalid_value_raises() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        StrategyEnum("not_a_valid_strategy")


# ---------------------------------------------------------------------------
# T1 — StrategyTrace construction and serialisation
# ---------------------------------------------------------------------------


def test_strategy_trace_construction_and_asdict() -> None:
    leg = LegSpan(store="opensearch", latency_ms=5)
    trace = StrategyTrace(
        strategy=StrategyEnum.hybrid_graph,
        decided_by="rule",
        legs=[leg],
    )
    as_dict = dataclasses.asdict(trace)
    assert as_dict["strategy"] == "hybrid_graph"
    assert as_dict["decided_by"] == "rule"
    assert as_dict["legs"][0]["store"] == "opensearch"
    assert as_dict["legs"][0]["latency_ms"] == 5


def test_leg_span_error_field() -> None:
    leg = LegSpan(store="bedrock", error="throttle-exhausted")
    assert leg.error == "throttle-exhausted"
    assert leg.latency_ms is None


# ---------------------------------------------------------------------------
# T4 — route_get_policies isolation (AC11–AC12)
# ---------------------------------------------------------------------------


def test_route_get_policies_returns_normative_exhaustive() -> None:
    """AC11: route_get_policies returns normative_exhaustive, decided_by=none."""
    trace = route_get_policies()
    assert trace.strategy == StrategyEnum.normative_exhaustive
    assert trace.decided_by == "none"


def test_route_get_policies_does_not_invoke_routers() -> None:
    """AC12: neither RuleQueryRouter nor BedrockQueryRouter is constructed."""

    class _SpyRouter:
        """Raises on any instantiation — if constructed, the test fails."""

        def __new__(cls, *args: object, **kwargs: object) -> _SpyRouter:
            raise AssertionError("Router was constructed — isolation violated")

    # Replace RuleQueryRouter with a spy; route_get_policies must not touch it
    import graphrag.routing as routing_mod

    original_rule = routing_mod.RuleQueryRouter
    original_bedrock = routing_mod.BedrockQueryRouter
    routing_mod.RuleQueryRouter = _SpyRouter  # type: ignore[assignment]
    routing_mod.BedrockQueryRouter = _SpyRouter  # type: ignore[assignment]
    try:
        trace = route_get_policies()
        assert trace.strategy == StrategyEnum.normative_exhaustive
    finally:
        routing_mod.RuleQueryRouter = original_rule  # type: ignore[assignment]
        routing_mod.BedrockQueryRouter = original_bedrock  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# T4 — route_ask StrategyTrace completeness (AC9–AC10)
# ---------------------------------------------------------------------------


def test_route_ask_returns_strategy_trace_with_non_null_fields() -> None:
    """AC9: route_ask always returns StrategyTrace with non-null strategy and decided_by."""
    trace = route_ask("How many employees are in Finance?")
    assert trace.strategy is not None
    assert trace.decided_by is not None
    assert isinstance(trace.legs, list)


def test_route_ask_decided_by_rule_on_non_ambiguous() -> None:
    """AC10: decided_by="rule" when RuleQueryRouter resolved the strategy."""
    trace = route_ask("How many employees are in Finance?")
    assert trace.decided_by == "rule"
    assert trace.strategy == StrategyEnum.structured


def test_route_ask_decided_by_bedrock_on_ambiguous() -> None:
    """AC10: decided_by="bedrock" when BedrockQueryRouter was invoked."""
    from unittest.mock import MagicMock

    # Build a mock Bedrock client that returns a valid strategy
    mock_client = MagicMock()
    mock_response_text = json.dumps({"strategy": "hybrid_graph"})
    mock_client.invoke_model.return_value = {
        "body": MagicMock(
            read=lambda: json.dumps(
                {"output": {"message": {"content": [{"text": mock_response_text}]}}}
            ).encode()
        )
    }

    # Ambiguous question: two entity URIs, no dominant signal
    trace = route_ask(
        "Can you explain the relationship between the IR SOP and the Finance policy?",
        entity_uris=[
            "urn:doc:my-repo:sops/ir.md",
            "urn:doc:my-repo:policies/finance.md",
        ],
        bedrock_client=mock_client,
    )
    assert trace.decided_by == "bedrock"


def test_route_ask_bedrock_not_invoked_on_rule_resolution() -> None:
    """AC10 companion: when rule router resolves, Bedrock is NOT invoked."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()

    trace = route_ask(
        "How many employees are in Finance?",
        bedrock_client=mock_client,
    )
    assert trace.decided_by == "rule"
    mock_client.invoke_model.assert_not_called()


def test_route_ask_throttle_exhaustion_e2e() -> None:
    """Concern 3: route_ask with throttling client carries decided_by=bedrock + error leg."""
    from unittest.mock import MagicMock, patch

    class _ThrottlingException(Exception):
        pass

    mock_client = MagicMock()
    mock_client.invoke_model.side_effect = _ThrottlingException("Rate limit")

    with patch("graphrag.routing._bedrock_router._sleep"):
        trace = route_ask(
            "Can you explain the relationship between IR SOP and Finance policy?",
            entity_uris=[
                "urn:doc:my-repo:sops/ir.md",
                "urn:doc:my-repo:policies/finance.md",
            ],
            bedrock_client=mock_client,
        )

    assert trace.strategy == StrategyEnum.hybrid_graph
    assert trace.decided_by == "bedrock"
    throttle_legs = [leg for leg in trace.legs if leg.error == "throttle-exhausted"]
    assert len(throttle_legs) >= 1


def test_route_ask_raises_on_ambiguous_without_bedrock_client() -> None:
    """Concern 2 coverage: route_ask raises ValueError when bedrock_client is None
    and RuleQueryRouter returns AMBIGUOUS (multi-URI question)."""
    with pytest.raises(ValueError, match="bedrock_client is required"):
        route_ask(
            "Can you explain the relationship between IR SOP and Finance policy?",
            entity_uris=[
                "urn:doc:my-repo:sops/ir.md",
                "urn:doc:my-repo:policies/finance.md",
            ],
            bedrock_client=None,
        )
