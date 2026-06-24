"""Embeddings behind an injectable protocol (slice-2 AC2).

Two implementations, one seam:

- ``BedrockTitanEmbedder`` — Amazon Titan Text Embeddings v2 via ``bedrock-runtime``
  (model ``amazon.titan-embed-text-v2:0``, 256 dims, normalized). The default
  botocore-chain client is TLS-verified; credentials come from the provider chain,
  never a plaintext read here.
- ``HashEmbedder`` — a deterministic, **offline, non-semantic** embedder for CI
  mechanics tests. It is never the basis for the credible-baseline claim (AC6 runs
  against real Titan v2 vectors); the CLI labels it as non-semantic so a reader is
  never misled.

The ``Embedder`` protocol is injected wherever embeddings are produced, so no call
site hard-codes a Bedrock client.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Protocol

TITAN_V2_MODEL_ID = "amazon.titan-embed-text-v2:0"
DEFAULT_DIMENSIONS = 256


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class HashEmbedder:
    """Deterministic offline embedder — bag-of-tokens hashed into the space, L2-normalized.

    Same text -> same vector; different text -> (almost always) a different vector. It
    is **not** semantically meaningful and must not back a quality claim — it exists so
    the chunk -> embed -> k-NN -> trace pipeline is testable offline with no creds.
    """

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self._dims = dimensions

    @property
    def model_id(self) -> str:
        return f"hash-offline-{self._dims} (deterministic, non-semantic)"

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dims
            for token in text.lower().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                idx = int.from_bytes(digest[:4], "big") % self._dims
                vec[idx] += 1.0 if digest[4] & 1 else -1.0
            out.append(_l2_normalize(vec))
        return out


class BedrockTitanEmbedder:
    """Amazon Titan Text Embeddings v2 via ``bedrock-runtime`` (real embeddings).

    The Bedrock client is the default botocore-chain client over TLS (no
    ``verify=False``, no plaintext-HTTP ``endpoint_url`` override); credentials resolve
    via the default provider chain (the task / Lambda role).
    """

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        dimensions: int = DEFAULT_DIMENSIONS,
        normalize: bool = True,
        client: Any | None = None,
    ) -> None:
        self._region = region
        self._dims = dimensions
        self._normalize = normalize
        self._client = client

    @property
    def model_id(self) -> str:
        return TITAN_V2_MODEL_ID

    @property
    def dimensions(self) -> int:
        return self._dims

    def _bedrock(self) -> Any:
        if self._client is None:  # pragma: no cover - exercised only on the live path
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._bedrock()
        out: list[list[float]] = []
        for text in texts:
            body = json.dumps(
                {"inputText": text, "dimensions": self._dims, "normalize": self._normalize}
            )
            resp = client.invoke_model(modelId=TITAN_V2_MODEL_ID, body=body)
            payload = json.loads(resp["body"].read())
            out.append([float(x) for x in payload["embedding"]])
        return out
