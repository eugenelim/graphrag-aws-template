"""T4 — template selection: Bedrock Converse (validated) + offline rule selector (AC4).

The Bedrock path is verified against a **mock** (no live call); a returned id outside the
fixed set is dropped to ``None`` (an unrecognized id never reaches the store). The security
posture is asserted in-test: catalog + question ride ``messages`` (data), ``system`` carries
the defensive directive, ``maxTokens`` is bounded.

# STUB: AC4
"""

from __future__ import annotations

from typing import Any

from graphrag.select import (
    DEFAULT_SELECT_MAX_TOKENS,
    BedrockTemplateSelector,
    RuleTemplateSelector,
)
from graphrag.synthesize import DEFAULT_SYNTHESIS_MODEL_ID
from graphrag.templates import TEMPLATES


class _FakeBedrock:
    """Records the converse() call and returns a canned text payload."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self.text}]}}, "stopReason": "end_turn"}


def test_bedrock_selector_returns_validated_id_and_is_well_formed() -> None:
    client = _FakeBedrock('{"template_id": "sig_owned_keps"}')
    selector = BedrockTemplateSelector(client=client)
    assert selector.model_id == DEFAULT_SYNTHESIS_MODEL_ID

    chosen = selector.select("Which KEPs does SIG Network own?", list(TEMPLATES))
    assert chosen == "sig_owned_keps"

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["modelId"] == DEFAULT_SYNTHESIS_MODEL_ID
    # system carries the defensive untrusted-data directive.
    system_text = " ".join(b["text"] for b in call["system"]).lower()
    assert "untrusted" in system_text and "instruction" in system_text
    # catalog + question ride messages (DATA), not system.
    user_text = " ".join(
        b["text"] for m in call["messages"] if m["role"] == "user" for b in m["content"]
    )
    assert "TEMPLATE CATALOG" in user_text
    assert "Which KEPs does SIG Network own?" in user_text
    assert "Which KEPs does SIG Network own?" not in system_text
    # maxTokens is bounded.
    max_tokens = call["inferenceConfig"]["maxTokens"]
    assert 0 < max_tokens <= 512
    assert DEFAULT_SELECT_MAX_TOKENS <= 512


def test_bedrock_selector_rejects_unknown_id() -> None:
    selector = BedrockTemplateSelector(client=_FakeBedrock('{"template_id": "drop_everything"}'))
    assert selector.select("anything", list(TEMPLATES)) is None


def test_bedrock_selector_handles_null_and_malformed() -> None:
    assert BedrockTemplateSelector(client=_FakeBedrock('{"template_id": null}')).select(
        "q", list(TEMPLATES)
    ) is None
    assert BedrockTemplateSelector(client=_FakeBedrock("not json at all")).select(
        "q", list(TEMPLATES)
    ) is None


def test_rule_selector_is_deterministic_and_non_semantic() -> None:
    sel = RuleTemplateSelector()
    assert "non-semantic" in sel.model_id.lower()
    templates = list(TEMPLATES)
    assert sel.select("Which KEPs does SIG Network own?", templates) == "sig_owned_keps"
    assert sel.select("Who tech-leads SIG Network?", templates) == "sig_tech_leads"
    assert sel.select("Which SIG owns KEP-2086?", templates) == "kep_owning_sig"
    assert sel.select("What SIGs does @thockin lead?", templates) == "person_led_sigs"
    # a question naming no known-vocabulary entity selects nothing.
    assert sel.select("what is the weather today", templates) is None
