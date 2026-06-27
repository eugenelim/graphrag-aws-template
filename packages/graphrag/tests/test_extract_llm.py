"""AC3 — the extractor seam: Bedrock Converse extractor + offline rule extractor.

The Bedrock path is verified against a **mock** (no live call): a well-formed Converse request
(pinned defensive directive in ``system``; prose + schema as ``messages`` data, prose ABSENT
from ``system``; bounded ``maxTokens``; default-TLS client), fence/JSON-tolerant parsing, a
per-document candidate cap, and the no-widened-grant model-id equality. The rule extractor emits
within-schema candidates for the exemplar, labeled non-semantic.

# STUB: AC3
"""

from __future__ import annotations

import json
from typing import Any

from graphrag.extract_llm import (
    _EXTRACT_SYSTEM_PROMPT,
    DEFAULT_EXTRACT_MAX_TOKENS,
    EXTRACTION_SCHEMA,
    MAX_CANDIDATES_PER_DOC,
    BedrockTripleExtractor,
    RuleTripleExtractor,
)
from graphrag.model import EdgeKind
from graphrag.parse import ParsedMarkdown
from graphrag.sources import COMMUNITY, ENHANCEMENTS, ParsedDoc
from graphrag.synthesize import DEFAULT_SYNTHESIS_MODEL_ID


class _FakeBedrock:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self.text}]}}, "stopReason": "end_turn"}


def _sig_doc(body: str) -> ParsedDoc:
    return ParsedDoc(
        COMMUNITY,
        "sig-network/README.md",
        "sig_readme",
        payload={"slug": "sig-network"},
        markdown=ParsedMarkdown(front_matter={}, headings=[], body=body),
    )


def _kep_doc(number: str, body: str) -> ParsedDoc:
    return ParsedDoc(
        ENHANCEMENTS,
        f"keps/sig-node/{number}-x/README.md",
        "kep_readme",
        payload={"dir_number": number},
        markdown=ParsedMarkdown(front_matter={}, headings=[], body=body),
    )


# --- Bedrock extractor --------------------------------------------------------------------


def test_bedrock_extractor_is_well_formed_and_secure() -> None:
    payload = json.dumps(
        [
            {
                "subject_id": "sig:sig-network",
                "predicate": "COLLABORATES_WITH",
                "object_id": "sig:sig-node",
                "span": "SIG Network collaborates closely with SIG Node.",
            }
        ]
    )
    client = _FakeBedrock(payload)
    extractor = BedrockTripleExtractor(client=client)
    assert extractor.model_id == DEFAULT_SYNTHESIS_MODEL_ID  # no widened grant (AC7)

    doc = _sig_doc("SIG Network collaborates closely with SIG Node on routing.")
    cands = extractor.extract(doc, EXTRACTION_SCHEMA)
    assert len(cands) == 1
    assert cands[0].predicate == "COLLABORATES_WITH"
    assert cands[0].subject == "sig:sig-network" and cands[0].object == "sig:sig-node"
    assert cands[0].source_doc == doc.doc_id

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["modelId"] == DEFAULT_SYNTHESIS_MODEL_ID
    # The defensive directive is the PINNED module constant (not an ad-hoc string).
    assert call["system"] == [{"text": _EXTRACT_SYSTEM_PROMPT}]
    system_text = " ".join(b["text"] for b in call["system"]).lower()
    assert "untrusted" in system_text and "never invent" in system_text
    # Prose + schema ride messages (DATA); the prose is ABSENT from system.
    user_text = " ".join(
        b["text"] for m in call["messages"] if m["role"] == "user" for b in m["content"]
    )
    assert "collaborates closely with SIG Node" in user_text
    assert "collaborates closely with SIG Node" not in " ".join(b["text"] for b in call["system"])
    assert "EXTRACTION SCHEMA" in user_text
    # maxTokens is bounded.
    assert 0 < call["inferenceConfig"]["maxTokens"] <= 1024
    assert DEFAULT_EXTRACT_MAX_TOKENS <= 1024


def test_bedrock_extractor_strips_code_fence() -> None:
    payload = (
        "```json\n"
        + json.dumps(
            [
                {
                    "subject_id": "kep-2086",
                    "predicate": "DEPENDS_ON",
                    "object_id": "kep-1880",
                    "span": "x",
                }
            ]
        )
        + "\n```"
    )
    cands = BedrockTripleExtractor(client=_FakeBedrock(payload)).extract(
        _kep_doc("2086", "depends on KEP-1880"), EXTRACTION_SCHEMA
    )
    assert len(cands) == 1 and cands[0].predicate == "DEPENDS_ON"


def test_bedrock_extractor_empty_or_garbled_yields_no_candidates() -> None:
    doc = _sig_doc("some prose")
    assert BedrockTripleExtractor(client=_FakeBedrock("")).extract(doc, EXTRACTION_SCHEMA) == []
    assert BedrockTripleExtractor(client=_FakeBedrock("not json at all")).extract(
        doc, EXTRACTION_SCHEMA
    ) == []


def test_bedrock_extractor_caps_candidates_per_doc() -> None:
    many = [
        {
            "subject_id": f"sig:s{i}",
            "predicate": "COLLABORATES_WITH",
            "object_id": "sig:t",
            "span": "x",
        }
        for i in range(MAX_CANDIDATES_PER_DOC + 10)
    ]
    cands = BedrockTripleExtractor(client=_FakeBedrock(json.dumps(many))).extract(
        _sig_doc("prose"), EXTRACTION_SCHEMA
    )
    assert len(cands) == MAX_CANDIDATES_PER_DOC


def test_bedrock_extractor_uses_default_tls_client() -> None:
    # The injected client is the default botocore-chain client; the builder never sets
    # verify=False / a plaintext endpoint (defense-in-depth — mirrors synthesize.py).
    import inspect

    import graphrag.extract_llm as mod

    src = inspect.getsource(mod.BedrockTripleExtractor._bedrock)
    assert "verify=False" not in src and "endpoint_url" not in src


# --- Offline rule extractor ---------------------------------------------------------------


def test_rule_extractor_is_labeled_non_semantic() -> None:
    assert "non-semantic" in RuleTripleExtractor().model_id


def test_rule_extractor_emits_collaboration_for_the_sig_exemplar() -> None:
    doc = _sig_doc("SIG Network collaborates closely with SIG Node on node-local routing.")
    cands = RuleTripleExtractor().extract(doc, EXTRACTION_SCHEMA)
    assert len(cands) == 1
    assert cands[0].predicate == EdgeKind.COLLABORATES_WITH.value
    assert cands[0].subject == "sig-network"
    assert "SIG Node" in cands[0].object
    assert "collaborates" in cands[0].span.lower()


def test_rule_extractor_emits_kep_dependency_and_supersession() -> None:
    dep = RuleTripleExtractor().extract(
        _kep_doc("2086", "This work depends on the ranges introduced in KEP-1880."),
        EXTRACTION_SCHEMA,
    )
    assert [c.predicate for c in dep] == [EdgeKind.DEPENDS_ON.value]
    assert dep[0].subject == "kep-2086" and dep[0].object == "KEP-1880"

    sup = RuleTripleExtractor().extract(
        _kep_doc("1287", "This proposal supersedes the legacy design, KEP-0009."),
        EXTRACTION_SCHEMA,
    )
    assert [c.predicate for c in sup] == [EdgeKind.SUPERSEDES.value]
    assert sup[0].object == "KEP-0009"


def test_rule_extractor_no_prose_yields_nothing() -> None:
    doc = _sig_doc("Nothing relational here.")
    assert RuleTripleExtractor().extract(doc, EXTRACTION_SCHEMA) == []
