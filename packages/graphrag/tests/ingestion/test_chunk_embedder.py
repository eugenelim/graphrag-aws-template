"""TDD tests for graphrag.ingestion._embed.ChunkEmbedder."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from graphrag.ingestion._embed import ChunkEmbedder, _build_chunks

DOC_URI = "urn:doc:test-repo:sops/ir.md"

# A fixture with 3 clear sentences
THREE_SENTENCE_TEXT = (
    "Security incidents must be reported immediately to the response team. "
    "The responder logs all actions in the incident tracking system. "
    "Escalate to management if the incident is not resolved within one hour."
)

_MOCK_EMBEDDING = [0.1, 0.2, 0.3, 0.4, 0.5]  # 5-dim dummy embedding


def _mock_bedrock_client(embedding: list[float] | None = None) -> MagicMock:
    """Return a boto3 bedrock-runtime mock that returns a fixed embedding."""
    if embedding is None:
        embedding = _MOCK_EMBEDDING
    client = MagicMock()
    body_bytes = json.dumps({"embedding": embedding}).encode()
    response_body = MagicMock()
    response_body.read.return_value = body_bytes
    client.invoke_model.return_value = {"body": response_body}
    return client


# ---------------------------------------------------------------------------
# T4-1: Fixture Markdown with 3 sentences → ≥ 1 chunk produced
# ---------------------------------------------------------------------------


def test_three_sentences_produce_at_least_one_chunk() -> None:
    client = _mock_bedrock_client()
    embedder = ChunkEmbedder(bedrock_client=client)
    chunks = embedder.embed(THREE_SENTENCE_TEXT, DOC_URI)
    assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# T4-2: Bedrock invoke_model called once per chunk
# ---------------------------------------------------------------------------


def test_invoke_model_called_once_per_chunk() -> None:
    client = _mock_bedrock_client()
    embedder = ChunkEmbedder(bedrock_client=client)

    # Force exactly 2 chunks by using text > CHUNK_SIZE_CHARS
    from graphrag.ingestion import _embed as embed_mod

    original_size = embed_mod.CHUNK_SIZE_CHARS
    try:
        embed_mod.CHUNK_SIZE_CHARS = 50  # tiny window → 3 chunks from 3 sentences
        chunks = embedder.embed(THREE_SENTENCE_TEXT, DOC_URI)
    finally:
        embed_mod.CHUNK_SIZE_CHARS = original_size

    assert client.invoke_model.call_count == len(chunks)


# ---------------------------------------------------------------------------
# T4-3: Gold vectors JSON has correct schema
# ---------------------------------------------------------------------------


def test_chunk_has_expected_fields() -> None:
    client = _mock_bedrock_client()
    embedder = ChunkEmbedder(bedrock_client=client)
    chunks = embedder.embed(THREE_SENTENCE_TEXT, DOC_URI)
    assert len(chunks) >= 1
    c = chunks[0]
    assert c.doc_uri == DOC_URI
    assert c.chunk_index == 0
    assert isinstance(c.text, str)
    assert isinstance(c.embedding, list)
    assert c.embedding == _MOCK_EMBEDDING


# ---------------------------------------------------------------------------
# T4-4: ThrottlingException × 3 → RuntimeError("embedding_throttle")
# ---------------------------------------------------------------------------


def test_throttle_raises_runtime_error_after_retries() -> None:
    from botocore.exceptions import ClientError

    def _make_throttle_error() -> ClientError:
        return ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "InvokeModel",
        )

    client = MagicMock()
    client.invoke_model.side_effect = _make_throttle_error()

    embedder = ChunkEmbedder(bedrock_client=client)

    with pytest.raises(RuntimeError, match="embedding_throttle"):
        with patch("graphrag.ingestion._embed.time.sleep"):  # skip real sleep
            embedder.embed(THREE_SENTENCE_TEXT, DOC_URI)


# ---------------------------------------------------------------------------
# _build_chunks unit tests
# ---------------------------------------------------------------------------


def test_build_chunks_returns_nonempty_list() -> None:
    chunks = _build_chunks(THREE_SENTENCE_TEXT)
    assert len(chunks) >= 1


def test_build_chunks_empty_text_returns_empty() -> None:
    assert _build_chunks("") == []
