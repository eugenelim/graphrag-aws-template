"""Tests for BedrockQueryRouter — T3 (AC7–AC8, AC14 + throttle fallback).

All tests use a mock Bedrock client — no AWS credentials needed.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from graphrag.routing import BedrockQueryRouter, StrategyEnum

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(strategy_value: str) -> dict[str, Any]:
    """Build the nested boto3 invoke_model response for a given strategy value."""
    inner_text = json.dumps({"strategy": strategy_value})
    return {
        "body": MagicMock(
            read=lambda: json.dumps(
                {"output": {"message": {"content": [{"text": inner_text}]}}}
            ).encode()
        )
    }


def _make_client(strategy_value: str) -> MagicMock:
    """Build a mock boto3 bedrock-runtime client returning *strategy_value*."""
    client = MagicMock()
    client.invoke_model.return_value = _make_mock_response(strategy_value)
    return client


class _ThrottlingException(Exception):
    """Fake ThrottlingException matching the botocore class name pattern."""

    pass


# ---------------------------------------------------------------------------
# Test 1: valid strategy returned from Bedrock
# ---------------------------------------------------------------------------


def test_bedrock_router_returns_valid_strategy() -> None:
    """AC7: BedrockQueryRouter returns a StrategyEnum member on a normal response."""
    client = _make_client("structured")
    router = BedrockQueryRouter(client)
    strategy, legs = router.route("What is the Finance department headcount?")

    assert strategy == StrategyEnum.structured
    assert len(legs) == 1
    assert legs[0].store == "bedrock"


# ---------------------------------------------------------------------------
# Test 2: invalid strategy → hybrid_graph fallback + WARNING log
# ---------------------------------------------------------------------------


def test_bedrock_router_invalid_strategy_falls_back_to_hybrid_graph(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC8: invalid Bedrock output → hybrid_graph + WARNING logged."""
    client = _make_client("not_a_strategy")
    router = BedrockQueryRouter(client)
    with caplog.at_level(logging.WARNING, logger="graphrag.routing._bedrock_router"):
        strategy, legs = router.route("Some ambiguous question")

    assert strategy == StrategyEnum.hybrid_graph
    assert any("invalid strategy" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Test 3: injection fixture — SPARQL keywords in question do not alter output
# ---------------------------------------------------------------------------


def test_bedrock_router_injection_fixture_does_not_alter_output() -> None:
    """AC14: question containing SPARQL Update keywords doesn't escape the data slot."""
    # The mock Bedrock client echoes back the injected question as the strategy value —
    # simulating a naive model that was "tricked" into echoing input.
    # The strict-validation gate must catch this and fall back to hybrid_graph.
    injected_question = "DROP GRAPH <urn:graph:normative>; SELECT * WHERE { ?s ?p ?o }"
    client = MagicMock()
    # Simulate Bedrock naively echoing the question content as the strategy
    inner_text = json.dumps({"strategy": injected_question})
    client.invoke_model.return_value = {
        "body": MagicMock(
            read=lambda: json.dumps(
                {"output": {"message": {"content": [{"text": inner_text}]}}}
            ).encode()
        )
    }

    router = BedrockQueryRouter(client)
    strategy, _ = router.route(injected_question)

    # The injected SQL is not a StrategyEnum value → strict validation → hybrid_graph
    assert strategy == StrategyEnum.hybrid_graph
    assert strategy != injected_question


# ---------------------------------------------------------------------------
# Test 4: throttle exhaustion → hybrid_graph + decided_by="bedrock" + LegSpan error
# ---------------------------------------------------------------------------


def test_bedrock_router_throttle_exhaustion_returns_hybrid_graph(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Throttle × 3 → hybrid_graph with decided_by="bedrock" and error LegSpan."""
    client = MagicMock()
    client.invoke_model.side_effect = _ThrottlingException("Rate limit exceeded")

    router = BedrockQueryRouter(client)
    with (
        caplog.at_level(logging.WARNING, logger="graphrag.routing._bedrock_router"),
        patch("graphrag.routing._bedrock_router._sleep"),  # skip actual sleep
    ):
        strategy, legs = router.route("Some ambiguous question")

    assert strategy == StrategyEnum.hybrid_graph
    # decided_by is managed by the caller (route_ask), not BedrockQueryRouter itself;
    # but the legs must contain a throttle-exhausted error span
    throttle_legs = [leg for leg in legs if leg.error == "throttle-exhausted"]
    assert len(throttle_legs) >= 1
    # Warning should have been logged
    assert any("throttle" in r.message.lower() for r in caplog.records)
    # Bedrock was invoked MAX_RETRIES times
    assert client.invoke_model.call_count == 3


# ---------------------------------------------------------------------------
# Test 5: prompt structure — question in <question> data slot only
# ---------------------------------------------------------------------------


def test_bedrock_router_question_in_data_slot() -> None:
    """AC14 / Security: question text appears only inside <question> tags in the prompt."""
    question = "What is the Finance headcount?"
    client = _make_client("vector_only")
    router = BedrockQueryRouter(client)
    router.route(question)

    call_args = client.invoke_model.call_args
    body_bytes: bytes = call_args[1].get("body") or call_args[0][0]  # type: ignore[index]
    body_dict = json.loads(body_bytes)

    # Extract the user turn content
    messages: list[dict[str, str]] = body_dict.get("messages", [])
    user_content = next((m["content"] for m in messages if m.get("role") == "user"), "")

    # The question must be inside <question>...</question>
    assert f"<question>\n{question}\n</question>" in user_content

    # The system prompt must NOT contain the question
    system_parts: list[dict[str, str]] = body_dict.get("system", [])
    system_text = " ".join(p.get("text", "") for p in system_parts)
    assert question not in system_text, (
        "Question text leaked into the system prompt (instruction-injection risk)"
    )

    # The question must appear exactly once in the user content (inside the tag)
    # — not repeated or echoed outside the data slot
    assert user_content.count(question) == 1, (
        f"Question text appears {user_content.count(question)} times in user_content — "
        "expected exactly 1 (inside <question> tag only)"
    )


def test_system_prompt_contains_all_strategy_values() -> None:
    """Nit 4 / Risks: system prompt must list every StrategyEnum value.

    If a new strategy is added to StrategyEnum without updating _SYSTEM_PROMPT,
    Bedrock can never select it — this test catches the drift.
    """
    from graphrag.routing._bedrock_router import _SYSTEM_PROMPT

    for strategy in StrategyEnum:
        strategy_value = str(strategy)
        assert strategy_value in _SYSTEM_PROMPT, (
            f"Strategy '{strategy_value}' is in StrategyEnum but missing from the Bedrock "
            f"system prompt — Bedrock cannot select it without being told about it."
        )
