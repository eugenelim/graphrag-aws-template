"""Tests for RuleQueryRouter — T2 (AC1–AC6 + import isolation).

Each test corresponds to one routing matrix row from ADR-0013.
Tests run without boto3/botocore installed (enforced by the import isolation check).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from graphrag.routing import RuleQueryRouter, StrategyEnum
from graphrag.routing._rule_router import AMBIGUOUS


@pytest.fixture()
def router() -> RuleQueryRouter:
    return RuleQueryRouter()


# ---------------------------------------------------------------------------
# AC1 — aggregation verb → structured
# ---------------------------------------------------------------------------


def test_ac1_aggregation_verb_routes_to_structured(router: RuleQueryRouter) -> None:
    result = router.route("How many employees are in the Finance department?")
    assert result == StrategyEnum.structured


def test_ac1_aggregation_verb_count(router: RuleQueryRouter) -> None:
    result = router.route("Count all the policies in the system")
    assert result == StrategyEnum.structured


def test_ac1_aggregation_verb_list_all(router: RuleQueryRouter) -> None:
    result = router.route("List all departments with more than 10 employees")
    assert result == StrategyEnum.structured


# ---------------------------------------------------------------------------
# AC2 — entity URI pattern + relationship verb → graph_expand
# ---------------------------------------------------------------------------


def test_ac2_entity_uri_and_relationship_verb_routes_to_graph_expand(
    router: RuleQueryRouter,
) -> None:
    result = router.route("What does biz:Finance relate to in the graph?")
    assert result == StrategyEnum.graph_expand


def test_ac2_entity_uri_and_related_to_verb(router: RuleQueryRouter) -> None:
    result = router.route("What is biz:HR related to?")
    assert result == StrategyEnum.graph_expand


def test_ac2_urn_entity_and_relationship_verb(router: RuleQueryRouter) -> None:
    result = router.route("What is urn:doc:repo:policy.md connected to?")
    assert result == StrategyEnum.graph_expand


# ---------------------------------------------------------------------------
# AC3 — single entity URI + factual question → hybrid_graph
# ---------------------------------------------------------------------------


def test_ac3_single_entity_uri_routes_to_hybrid_graph(router: RuleQueryRouter) -> None:
    result = router.route(
        "What does the Incident Response SOP say about severity levels?",
        entity_uris=["urn:doc:my-repo:sops/ir.md"],
    )
    assert result == StrategyEnum.hybrid_graph


def test_ac3_single_urn_in_question_routes_to_hybrid_graph(router: RuleQueryRouter) -> None:
    result = router.route("Tell me about urn:doc:repo:finance/policy.md and its scope")
    assert result == StrategyEnum.hybrid_graph


# ---------------------------------------------------------------------------
# AC4 — no entity, specific factual question → vector_only
# ---------------------------------------------------------------------------


def test_ac4_no_entity_factual_question_routes_to_vector_only(
    router: RuleQueryRouter,
) -> None:
    result = router.route("What is the best practice for customer onboarding?")
    assert result == StrategyEnum.vector_only


def test_ac4_no_entity_no_thematic_routes_to_vector_only(router: RuleQueryRouter) -> None:
    result = router.route("What are the data retention requirements?")
    assert result == StrategyEnum.vector_only


# ---------------------------------------------------------------------------
# AC5 — thematic / broad question → global
# ---------------------------------------------------------------------------


def test_ac5_thematic_question_routes_to_global(router: RuleQueryRouter) -> None:
    result = router.route("Tell me broadly about how the Finance domain operates")
    assert result == StrategyEnum.global_


def test_ac5_in_general_marker_routes_to_global(router: RuleQueryRouter) -> None:
    result = router.route("In general, how does data governance work here?")
    assert result == StrategyEnum.global_


def test_ac5_overview_marker_routes_to_global(router: RuleQueryRouter) -> None:
    result = router.route("Give me an overview of the HR policies")
    assert result == StrategyEnum.global_


# ---------------------------------------------------------------------------
# AC6 — multiple entity URIs + mixed signal → ambiguous
# ---------------------------------------------------------------------------


def test_ac6_multiple_entity_uris_mixed_signal_returns_ambiguous(
    router: RuleQueryRouter,
) -> None:
    result = router.route(
        "Can you explain the relationship between the IR SOP and the Finance policy?",
        entity_uris=[
            "urn:doc:my-repo:sops/ir.md",
            "urn:doc:my-repo:policies/finance.md",
        ],
    )
    assert result is AMBIGUOUS


def test_ac6_two_entity_uris_in_question_returns_ambiguous(router: RuleQueryRouter) -> None:
    """Two entity URIs detected in question text → ambiguous."""
    result = router.route("Compare urn:doc:repo:sops/ir.md with urn:doc:repo:policies/finance.md")
    assert result is AMBIGUOUS


# ---------------------------------------------------------------------------
# AC13 — import isolation (no boto3/botocore)
# ---------------------------------------------------------------------------


def test_ac13_rule_router_importable_without_boto3() -> None:
    """Subprocess check: import succeeds even when boto3/botocore are absent."""
    import os

    env = os.environ.copy()
    # Ensure the package source is on the path in the subprocess
    env["PYTHONPATH"] = str(pathlib.Path(__file__).parents[4] / "packages" / "graphrag" / "src")
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys; "
            # Block boto3 and botocore from being importable in the subprocess
            "sys.modules['boto3'] = None; "  # type: ignore[assignment]  # noqa: E501
            "sys.modules['botocore'] = None; "  # type: ignore[assignment]
            "from graphrag.routing._rule_router import RuleQueryRouter; "
            "r = RuleQueryRouter(); "
            "print('ok')"
        ),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)  # noqa: S603
    assert result.returncode == 0, f"Import failed:\n{result.stderr}"
    assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# Latency NFR — adversarial input
# ---------------------------------------------------------------------------


@pytest.mark.timeout(1)
def test_rule_router_latency_adversarial_input(router: RuleQueryRouter) -> None:
    """RuleQueryRouter must complete in < 1 s on a 4 000-char adversarial string."""
    adversarial = "a" * 4_000
    result = router.route(adversarial)
    # Any result is fine — we are testing latency, not routing outcome
    assert result in list(StrategyEnum) or result is AMBIGUOUS
