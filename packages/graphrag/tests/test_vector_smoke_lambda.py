"""T8 — vector smoke probe handler round-trips through mocks (AC7 offline guard).

The live in-VPC run is T11/AC7; here we prove the handler embeds -> indexes ->
retrieves -> cleans up, with Bedrock + OpenSearch mocked.
"""

from __future__ import annotations

from typing import Any

import pytest

from graphrag import vector_smoke_lambda as probe
from graphrag.store.vector_base import EmbeddedChunk, VectorHit


class _FakeEmbedder:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    @property
    def model_id(self) -> str:
        return "fake"

    @property
    def dimensions(self) -> int:
        return 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.5] * 256 for _ in texts]


class _FakeStore:
    def __init__(self, *a: Any, **k: Any) -> None:
        self.indexed: dict[str, EmbeddedChunk] = {}
        self.deleted: list[str] = []
        self.created = False

    def create_index(self) -> None:
        self.created = True

    def index_chunk(self, embedded: EmbeddedChunk, *, refresh: bool = False) -> None:
        self.indexed[embedded.chunk.id] = embedded

    def knn(self, vector: list[float], k: int) -> list[VectorHit]:
        return [VectorHit(ec.chunk, 1.0) for ec in self.indexed.values()][:k]

    def delete(self, ids: list[str]) -> None:
        self.deleted.extend(ids)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://vectors.example")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    store = _FakeStore()
    monkeypatch.setattr(probe, "BedrockTitanEmbedder", _FakeEmbedder)
    monkeypatch.setattr(probe, "OpenSearchVectorStore", lambda *a, **k: store)
    return store


def test_probe_indexes_then_retrieves_then_cleans_up(wired: _FakeStore) -> None:
    result = probe.lambda_handler({}, None)
    assert result["ok"] is True
    chunk_id = f"smoke-{result['run']}"
    assert result["retrieved_id"] == chunk_id  # the ingested chunk came back
    assert result["dims"] == 256
    # It created the index, indexed exactly its probe chunk, and deleted it afterwards.
    assert wired.created
    assert chunk_id in result["hits"]
    assert wired.deleted == [chunk_id]


def test_probe_reports_not_ok_when_retrieval_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://vectors.example")

    class _EmptyStore(_FakeStore):
        def knn(self, vector: list[float], k: int) -> list[VectorHit]:
            return []

    store = _EmptyStore()
    monkeypatch.setattr(probe, "BedrockTitanEmbedder", _FakeEmbedder)
    monkeypatch.setattr(probe, "OpenSearchVectorStore", lambda *a, **k: store)
    result = probe.lambda_handler({}, None)
    assert result["ok"] is False
    assert result["retrieved_id"] is None
    assert store.deleted  # still cleans up even on a miss (finally)
