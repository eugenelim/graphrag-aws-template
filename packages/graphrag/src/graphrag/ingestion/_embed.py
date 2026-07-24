"""ChunkEmbedder — chunk cleansed Markdown and call Bedrock for embeddings.

Chunking strategy (from spec):
- Sliding window of 512 tokens (approx 2048 chars) with 64-token overlap (256 chars)
- Sentence-aligned boundaries using a simple regex sentence splitter
- Token count is approximate (chars / 4); avoids a tokenizer dependency

Bedrock embedding model: amazon.titan-embed-text-v2:0
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

# Chunking constants (override via monkeypatching in tests)
CHUNK_SIZE_CHARS = 2048  # ~512 tokens
CHUNK_OVERLAP_CHARS = 256  # ~64 tokens

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
_MAX_RETRIES = 3


@dataclass
class Chunk:
    """A single text chunk with its embedding."""

    text: str
    embedding: list[float]
    chunk_index: int
    doc_uri: str


def _split_sentences(text: str) -> list[str]:
    """Split text at sentence boundaries."""
    return _SENTENCE_SPLIT_RE.split(text)


def _build_chunks(text: str) -> list[str]:
    """Split text into sliding-window chunks aligned at sentence boundaries."""
    if not text.strip():
        return []
    sentences = [s for s in _split_sentences(text) if s.strip()]
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        s_len = len(sentence)
        if current_len + s_len > CHUNK_SIZE_CHARS and current:
            chunks.append(" ".join(current))
            # Overlap: keep sentences from the end up to CHUNK_OVERLAP_CHARS
            overlap: list[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > CHUNK_OVERLAP_CHARS:
                    break
                overlap.insert(0, s)
                overlap_len += len(s)
            current = overlap
            current_len = overlap_len
        current.append(sentence)
        current_len += s_len

    if current:
        chunks.append(" ".join(current))

    return chunks


class ChunkEmbedder:
    """Chunk cleansed Markdown and retrieve Bedrock embeddings.

    Args:
        bedrock_client: Optional pre-instantiated boto3 bedrock-runtime client.
            If ``None``, created lazily inside ``embed()``.  Inject a mock in
            tests to avoid real Bedrock calls.
    """

    def __init__(self, bedrock_client: object | None = None) -> None:
        self._client = bedrock_client

    def embed(self, clean_text: str, doc_uri: str) -> list[Chunk]:
        """Split ``clean_text`` into chunks and call Bedrock for each embedding.

        Returns:
            List of Chunk instances with text + embedding populated.

        Raises:
            RuntimeError("embedding_throttle"): After 3 ThrottlingException retries.
        """
        import boto3
        import botocore.exceptions

        client = self._client or boto3.client("bedrock-runtime")
        chunk_texts = _build_chunks(clean_text)
        if not chunk_texts:
            chunk_texts = [clean_text] if clean_text.strip() else []

        result: list[Chunk] = []
        for idx, chunk_text in enumerate(chunk_texts):
            embedding = _invoke_with_retry(
                client,
                chunk_text,
                botocore.exceptions.ClientError,
            )
            result.append(
                Chunk(
                    text=chunk_text,
                    embedding=embedding,
                    chunk_index=idx,
                    doc_uri=doc_uri,
                )
            )
        return result


def _invoke_with_retry(
    client: Any,
    text: str,
    client_error_cls: type[Exception],
) -> list[float]:
    """Call Bedrock Titan embed with up to _MAX_RETRIES retries on ThrottlingException.

    Raises:
        RuntimeError("embedding_throttle"): When all retries are exhausted.
        The original ClientError for non-throttling errors.
    """
    body = json.dumps({"inputText": text})
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.invoke_model(
                modelId=_EMBEDDING_MODEL,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            payload = json.loads(response["body"].read())
            return payload["embedding"]
        except client_error_cls as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code == "ThrottlingException":
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError("embedding_throttle") from exc
            raise
    # Unreachable: loop always returns or raises.
    raise RuntimeError("embedding_throttle")  # pragma: no cover
