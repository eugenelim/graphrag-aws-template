"""Tests for graphrag.normative._vector — the vector-threshold leg.

Uses a simple in-memory vector client (not MemoryVectorStore, which is
K8s-corpus-specific) to avoid twisting the Chunk abstraction for policy docs.
"""

from __future__ import annotations

import pytest

from graphrag.embed import HashEmbedder
from graphrag.normative._vector import (
    DEFAULT_THRESHOLD,
    NORMATIVE_GRAPH,
    NormativeVectorHit,
    vector_leg,
)

# ── Minimal in-memory vector client for tests ─────────────────────────────────


class MemoryNormativeVectorClient:
    """Simple in-memory NormativeVectorClient for offline tests.

    Stores pre-computed hits and returns them regardless of the query vector.
    This is sufficient for testing deduplication and threshold logic, where
    semantic similarity is not relevant.
    """

    def __init__(self, hits: list[NormativeVectorHit]) -> None:
        self._hits = hits

    def knn(
        self,
        vector: list[float],
        *,
        named_graph: str,
        k_max: int,
    ) -> list[NormativeVectorHit]:
        assert named_graph == NORMATIVE_GRAPH, (
            f"named_graph must always be {NORMATIVE_GRAPH!r}, got {named_graph!r}"
        )
        return self._hits[:k_max]


class FailingEmbedder:
    """Simulates a Bedrock InvokeModel failure."""

    @property
    def model_id(self) -> str:
        return "failing"

    @property
    def dimensions(self) -> int:
        return 256

    def fingerprint(self) -> str:
        return "fail"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Bedrock InvokeModel failed: throttled")


# ── Test fixtures ─────────────────────────────────────────────────────────────

_HIT_A = NormativeVectorHit(
    doc_uri="urn:biz:policy:policy-a",
    score=0.82,
    title="Policy A",
    doc_type="Policy",
    domain="Finance",
    pii_flagged=False,
    git_commit="shaA",
    git_path="policies/a.md",
)
_HIT_B = NormativeVectorHit(
    doc_uri="urn:biz:policy:policy-b",
    score=0.82,
    title="Policy B",
    doc_type="Standard",
    domain="HR",
    pii_flagged=False,
    git_commit="shaB",
    git_path="policies/b.md",
)
_HIT_C = NormativeVectorHit(
    doc_uri="urn:biz:policy:policy-c",
    score=0.65,  # below default threshold of 0.7
    title="Policy C",
    doc_type="Guideline",
    domain=None,
    pii_flagged=False,
)

_EMBEDDER = HashEmbedder()

# ── T-VL-1: deduplication — A already in SPARQL result ───────────────────────


def test_vector_leg_deduplicates_against_sparql_uris() -> None:
    """Policy A is in the SPARQL result; vector leg must NOT add it again."""
    client = MemoryNormativeVectorClient([_HIT_A, _HIT_B])
    sparql_uris = frozenset({"urn:biz:policy:policy-a"})  # A already in SPARQL
    additions = vector_leg(client, _EMBEDDER, "finance query", sparql_uris=sparql_uris)
    uris = {r.uri for r in additions}
    assert "urn:biz:policy:policy-a" not in uris  # deduplicated
    assert "urn:biz:policy:policy-b" in uris  # added (not in SPARQL)


def test_vector_leg_sparql_item_relevance_unchanged() -> None:
    """A in SPARQL result has relevance=None (SPARQL-sourced, not from vector)."""
    # This test confirms the deduplication leaves the SPARQL item unchanged;
    # the vector leg only returns _additions_, not the full set.
    client = MemoryNormativeVectorClient([_HIT_A])
    sparql_uris = frozenset({"urn:biz:policy:policy-a"})
    additions = vector_leg(client, _EMBEDDER, "query", sparql_uris=sparql_uris)
    assert additions == []  # A is deduplicated; no additions


# ── T-VL-2: addition — B not in SPARQL result, score >= threshold ─────────────


def test_vector_leg_adds_policy_above_threshold() -> None:
    """Policy B (score 0.82 >= 0.7) is added to the union with relevance=0.82."""
    client = MemoryNormativeVectorClient([_HIT_B])
    additions = vector_leg(client, _EMBEDDER, "hr onboarding", sparql_uris=frozenset())
    assert len(additions) == 1
    result = additions[0]
    assert result.uri == "urn:biz:policy:policy-b"
    assert result.relevance == pytest.approx(0.82)
    assert result.title == "Policy B"
    assert result.doc_type == "Standard"
    assert result.domain == "HR"
    assert result.pii_flagged is False
    assert result.git_commit == "shaB"
    assert result.git_path == "policies/b.md"


# ── T-VL-3: threshold — C below 0.7 not added ───────────────────────────────


def test_vector_leg_excludes_hit_below_threshold() -> None:
    """Policy C (score 0.65 < 0.7 default threshold) must not be included."""
    client = MemoryNormativeVectorClient([_HIT_C])
    additions = vector_leg(client, _EMBEDDER, "query", sparql_uris=frozenset())
    assert additions == []


def test_vector_leg_custom_threshold_respected() -> None:
    """With threshold=0.60, C (0.65) IS included; with 0.70, it is excluded."""
    client = MemoryNormativeVectorClient([_HIT_C])
    # Low threshold — C qualifies
    with_low = vector_leg(client, _EMBEDDER, "query", threshold=0.60, sparql_uris=frozenset())
    assert len(with_low) == 1
    assert with_low[0].uri == "urn:biz:policy:policy-c"

    # Default threshold — C excluded
    without = vector_leg(
        client, _EMBEDDER, "query", threshold=DEFAULT_THRESHOLD, sparql_uris=frozenset()
    )
    assert without == []


# ── T-VL-4: Bedrock embedding fails → graceful degrade ───────────────────────


def test_vector_leg_gracefully_degrades_on_embedder_failure() -> None:
    """Bedrock InvokeModel raises → vector_leg returns [] (not an exception)."""
    client = MemoryNormativeVectorClient([_HIT_A, _HIT_B])
    additions = vector_leg(
        client,
        FailingEmbedder(),
        "query",
        sparql_uris=frozenset(),
    )
    assert additions == []


# ── T-VL-5: named_graph filter — assert client always called with normative ───


def test_vector_leg_always_passes_normative_named_graph() -> None:
    """The knn call MUST use named_graph=urn:graph:normative (AC3)."""

    class AssertingClient:
        called_with_graph: str | None = None

        def knn(
            self, vector: list[float], *, named_graph: str, k_max: int
        ) -> list[NormativeVectorHit]:
            self.called_with_graph = named_graph
            return []

    asserting = AssertingClient()
    vector_leg(asserting, _EMBEDDER, "test", sparql_uris=frozenset())
    assert asserting.called_with_graph == NORMATIVE_GRAPH
