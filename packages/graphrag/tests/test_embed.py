"""T2 — Embedder protocol: offline determinism + Titan v2 request shape (AC2).

# STUB: AC2
"""

from __future__ import annotations

import io
import json
import math
from typing import Any

from graphrag.embed import TITAN_V2_MODEL_ID, BedrockTitanEmbedder, HashEmbedder


def test_hash_embedder_is_deterministic_and_unit_norm() -> None:
    emb = HashEmbedder(256)
    a1, b1 = emb.embed(["in-place pod resize", "service traffic policy"])
    a2 = emb.embed(["in-place pod resize"])[0]
    assert a1 == a2  # same text -> same vector
    assert a1 != b1  # different text -> different vector
    assert len(a1) == 256
    assert math.isclose(math.sqrt(sum(x * x for x in a1)), 1.0, rel_tol=1e-9)
    assert emb.dimensions == 256
    assert "non-semantic" in emb.model_id  # labels itself honestly


class _FakeBedrock:
    """Records invoke_model calls and returns a Titan-shaped body."""

    def __init__(self, vector: list[float]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._vector = vector

    def invoke_model(self, *, modelId: str, body: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append({"modelId": modelId, "body": json.loads(body)})
        return {"body": io.BytesIO(json.dumps({"embedding": self._vector}).encode("utf-8"))}


def test_titan_embedder_issues_well_formed_request_and_parses_vector() -> None:
    fake = _FakeBedrock([0.1] * 256)
    emb = BedrockTitanEmbedder(dimensions=256, normalize=True, client=fake)
    out = emb.embed(["risks of in-place pod resize"])

    assert out == [[0.1] * 256]
    assert emb.model_id == TITAN_V2_MODEL_ID
    sent = fake.calls[0]
    assert sent["modelId"] == "amazon.titan-embed-text-v2:0"
    assert sent["body"] == {
        "inputText": "risks of in-place pod resize",
        "dimensions": 256,
        "normalize": True,
    }


def test_titan_embedder_batches_each_text() -> None:
    fake = _FakeBedrock([0.0] * 256)
    BedrockTitanEmbedder(client=fake).embed(["a", "b", "c"])
    assert [c["body"]["inputText"] for c in fake.calls] == ["a", "b", "c"]
