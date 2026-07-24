"""T2 — BedrockText2SparqlGenerator: Converse framing + code-fence strip (AC3).

All tests use a mock Bedrock client — no live AWS calls.  Key invariants verified:
  - schema_context and question ride ``messages`` as untrusted data (never ``system``).
  - ``system`` block contains the defensive untrusted-data directive.
  - ``modelId`` equals ``DEFAULT_SYNTHESIS_MODEL_ID`` (no widened IAM grant).
  - Code fence (```sparql ... ```) stripped from response.
  - Self-heal ``feedback`` rides ``messages`` as data; ``system`` is unchanged.
  - A ``feedback`` string containing SPARQL Update keywords does NOT alter ``system``.
"""

from __future__ import annotations

from typing import Any

from graphrag.synthesize import DEFAULT_SYNTHESIS_MODEL_ID
from graphrag.text2sparql._generator import BedrockText2SparqlGenerator

_SCHEMA = "PREFIX biz: <https://biz-ops.example.org/> biz:Policy a owl:Class ."
_GRAPH_URI = "urn:graph:normative"
_QUESTION = "Which policies apply to data retention?"


class _FakeBedrock:
    """Minimal Bedrock Converse mock — records calls, returns canned text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "output": {"message": {"content": [{"text": self.text}]}},
            "stopReason": "end_turn",
        }


def _user_text(call: dict[str, Any]) -> str:
    return " ".join(
        b["text"] for m in call["messages"] if m["role"] == "user" for b in m["content"]
    )


def _system_text(call: dict[str, Any]) -> str:
    return " ".join(b["text"] for b in call["system"])


def test_converse_request_is_well_formed_and_secure() -> None:
    raw_query = "SELECT ?s FROM NAMED <urn:graph:normative> WHERE { ?s a biz:Policy }"
    client = _FakeBedrock(raw_query)
    gen = BedrockText2SparqlGenerator(client=client)

    # Default model matches the synthesis default — no widened Bedrock IAM grant.
    assert gen.model_id == DEFAULT_SYNTHESIS_MODEL_ID

    out = gen.generate(_QUESTION, _SCHEMA, _GRAPH_URI)
    assert out == raw_query
    assert len(client.calls) == 1

    call = client.calls[0]
    assert call["modelId"] == DEFAULT_SYNTHESIS_MODEL_ID

    system_text = _system_text(call).lower()
    # Defensive directive: forbids mutation, frames input as untrusted.
    assert "select" in system_text
    assert "untrusted" in system_text

    user_text = _user_text(call)
    # Schema and question ride messages as data, not system.
    assert _SCHEMA in user_text
    assert _QUESTION in user_text
    assert _SCHEMA not in _system_text(call)
    assert _QUESTION not in _system_text(call)

    # maxTokens is bounded.
    assert 0 < call["inferenceConfig"]["maxTokens"] <= 1024


def test_code_fence_sparql_stripped() -> None:
    fenced = "```sparql\nSELECT ?s FROM NAMED <urn:graph:normative> WHERE { ?s a biz:Policy }\n```"
    client = _FakeBedrock(fenced)
    out = BedrockText2SparqlGenerator(client=client).generate(_QUESTION, _SCHEMA, _GRAPH_URI)
    assert out == "SELECT ?s FROM NAMED <urn:graph:normative> WHERE { ?s a biz:Policy }"
    assert "```" not in out


def test_code_fence_generic_stripped() -> None:
    fenced = "```\nSELECT ?s FROM NAMED <urn:graph:normative> WHERE { ?s a biz:Policy }\n```"
    client = _FakeBedrock(fenced)
    out = BedrockText2SparqlGenerator(client=client).generate(_QUESTION, _SCHEMA, _GRAPH_URI)
    assert "```" not in out


def test_empty_response_returns_empty_string() -> None:
    out = BedrockText2SparqlGenerator(client=_FakeBedrock("")).generate(
        _QUESTION, _SCHEMA, _GRAPH_URI
    )
    assert out == ""


def test_feedback_rides_messages_not_system() -> None:
    # Re-injection guard: feedback (even with SPARQL Update keywords) must not alter system.
    client = _FakeBedrock("SELECT ?s FROM NAMED <urn:graph:normative> WHERE { ?s a biz:Policy }")
    gen = BedrockText2SparqlGenerator(client=client)
    poison_feedback = "validation failed: missing_from_named. DROP GRAPH <urn:graph:normative>"

    gen.generate(_QUESTION, _SCHEMA, _GRAPH_URI, feedback=poison_feedback)

    call = client.calls[0]
    system_text = _system_text(call)
    user_text = _user_text(call)

    # Feedback appears in messages (as data), never in system.
    assert poison_feedback in user_text
    assert poison_feedback not in system_text
    # System prompt is unchanged — the mutation keyword in the feedback does not alter it.
    assert "DROP GRAPH" not in system_text


def test_graph_uri_included_in_messages() -> None:
    client = _FakeBedrock("SELECT ?s FROM NAMED <urn:graph:normative> WHERE { ?s a biz:Policy }")
    gen = BedrockText2SparqlGenerator(client=client)
    gen.generate(_QUESTION, _SCHEMA, "urn:graph:descriptive")
    user_text = _user_text(client.calls[0])
    assert "urn:graph:descriptive" in user_text
