"""Tests for graphrag.mcp._production.init_production().

Covers:
- Successful construction with both endpoints set (mocked boto3 + botocore)
- Neptune-only path (no OpenSearch → MemoryVectorStore fallback)
- Missing NEPTUNE_SPARQL_ENDPOINT → RuntimeError with clear message
- Correct client types in _tools._store after init
- Idempotency: calling twice is safe (second call replaces the store)
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

import graphrag.mcp._tools as _tools
from graphrag.embed import HashEmbedder
from graphrag.mcp._tools import _ProductionStore
from graphrag.store.neptune_sparql import NeptuneSparqlStore
from graphrag.store.opensearch import OpenSearchVectorStore
from graphrag.store.vector_memory import MemoryVectorStore

# ---------------------------------------------------------------------------
# Store isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_store() -> Generator[None, None, None]:
    """Snapshot and restore ``_tools._store`` around every test.

    Prevents production-init tests from leaking a ``_ProductionStore`` into
    other test modules that expect a ``_MockStore`` (the default).
    """

    original = _tools._store
    yield
    _tools._store = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_boto3_client_mock() -> MagicMock:
    """Return a mock boto3.client() that returns a dummy Bedrock client."""
    mock_client = MagicMock()
    return mock_client


# ---------------------------------------------------------------------------
# Missing required env var
# ---------------------------------------------------------------------------


def test_init_production_missing_endpoint_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """RuntimeError with clear message when NEPTUNE_SPARQL_ENDPOINT is unset."""
    monkeypatch.delenv("NEPTUNE_SPARQL_ENDPOINT", raising=False)
    from graphrag.mcp._production import init_production

    with pytest.raises(RuntimeError, match="NEPTUNE_SPARQL_ENDPOINT"):
        init_production()


def test_init_production_empty_endpoint_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty string is treated the same as unset (falsy)."""
    monkeypatch.setenv("NEPTUNE_SPARQL_ENDPOINT", "")
    from graphrag.mcp._production import init_production

    with pytest.raises(RuntimeError, match="NEPTUNE_SPARQL_ENDPOINT"):
        init_production()


# ---------------------------------------------------------------------------
# Neptune-only path (no OpenSearch)
# ---------------------------------------------------------------------------


def test_init_production_neptune_only_uses_memory_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When OPENSEARCH_ENDPOINT is not set, falls back to MemoryVectorStore."""
    monkeypatch.setenv("NEPTUNE_SPARQL_ENDPOINT", "https://neptune.example.aws/sparql")
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    mock_boto3_client = MagicMock()
    with patch("boto3.client", return_value=mock_boto3_client) as mock_boto3:
        from graphrag.mcp._production import init_production

        init_production()

        mock_boto3.assert_called_once_with("bedrock-runtime", region_name="us-east-1")

    store = _tools._store
    assert isinstance(store, _ProductionStore), "store must be _ProductionStore"
    assert isinstance(store.neptune, NeptuneSparqlStore)
    assert isinstance(store.vector, MemoryVectorStore), "fallback must be MemoryVectorStore"
    assert isinstance(store.embedder, HashEmbedder)
    assert store.bedrock_client is mock_boto3_client


# ---------------------------------------------------------------------------
# Both endpoints set
# ---------------------------------------------------------------------------


def test_init_production_with_opensearch(monkeypatch: pytest.MonkeyPatch) -> None:
    """When OPENSEARCH_ENDPOINT is set, vector store is OpenSearchVectorStore."""
    monkeypatch.setenv("NEPTUNE_SPARQL_ENDPOINT", "https://neptune.example.aws/sparql")
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://opensearch.example.aws")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    mock_boto3_client = MagicMock()
    with patch("boto3.client", return_value=mock_boto3_client):
        from graphrag.mcp._production import init_production

        init_production()

    store = _tools._store
    assert isinstance(store, _ProductionStore)
    assert isinstance(store.neptune, NeptuneSparqlStore)
    assert isinstance(store.vector, OpenSearchVectorStore)
    # Region propagated correctly
    assert store.neptune.region == "eu-west-1"
    assert store.vector.region == "eu-west-1"
    assert isinstance(store.embedder, HashEmbedder)


# ---------------------------------------------------------------------------
# Region defaulting
# ---------------------------------------------------------------------------


def test_init_production_region_defaults_to_us_east_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AWS_REGION defaults to us-east-1 when not explicitly set."""
    monkeypatch.setenv("NEPTUNE_SPARQL_ENDPOINT", "https://neptune.example.aws/sparql")
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)

    with patch("boto3.client", return_value=MagicMock()):
        from graphrag.mcp._production import init_production

        init_production()

    store = _tools._store
    assert isinstance(store, _ProductionStore)
    assert store.neptune.region == "us-east-1"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_init_production_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling init_production() twice replaces the store (second call wins)."""
    monkeypatch.setenv("NEPTUNE_SPARQL_ENDPOINT", "https://neptune.example.aws/sparql")
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    with patch("boto3.client", return_value=MagicMock()):
        from graphrag.mcp._production import init_production

        init_production()
        first_store = _tools._store
        init_production()
        second_store = _tools._store

    # Second call created a new store object
    assert first_store is not second_store
    assert isinstance(second_store, _ProductionStore)


# ---------------------------------------------------------------------------
# _store type after init
# ---------------------------------------------------------------------------


def test_init_production_sets_production_store_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After init_production(), _tools._store is a _ProductionStore."""
    monkeypatch.setenv("NEPTUNE_SPARQL_ENDPOINT", "https://neptune.example.aws/sparql")
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)

    with patch("boto3.client", return_value=MagicMock()):
        from graphrag.mcp._production import init_production

        init_production()

    assert isinstance(_tools._store, _ProductionStore)


# ---------------------------------------------------------------------------
# boto3 not imported at module level
# ---------------------------------------------------------------------------


def test_production_module_importable_without_boto3() -> None:
    """_production.py must be importable even if boto3 is not in scope at top-level.

    This is a static invariant — verified by confirming no top-level ``import boto3``
    exists in the module source.
    """
    import inspect

    from graphrag.mcp import _production

    source = inspect.getsource(_production)
    # "import boto3" must NOT appear at module level (outside any function/class)
    lines = source.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "import boto3":
            # Check indentation — if it's at the top level (no leading spaces), fail
            if not line.startswith(" ") and not line.startswith("\t"):
                pytest.fail(
                    f"boto3 imported at module level (line {i + 1}): {line!r}\n"
                    "boto3 must only be imported inside init_production()."
                )
    # Verify boto3 IS used somewhere inside a function (not a dead import check)
    assert "import boto3" in source, "_production.py must import boto3 inside init_production()"


# ---------------------------------------------------------------------------
# Production tool dispatch paths — verified against MemorySparqlStore
# ---------------------------------------------------------------------------


def _make_production_store_with_fixture() -> _tools._ProductionStore:
    """Build a _ProductionStore backed by MemorySparqlStore seeded from fixture corpus.

    Uses the same fixture as _mock.py but via the MemorySparqlStore interface
    (sparql_select returns list[dict]) rather than rdflib Dataset.query().
    """
    import warnings

    from graphrag.embed import HashEmbedder
    from graphrag.mcp._mock import _fixture_path, _seed_vector_store
    from graphrag.store.neptune_sparql_memory import MemorySparqlStore

    # Load fixture into MemorySparqlStore
    store = MemorySparqlStore()
    fixture = _fixture_path()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        store._graph.parse(str(fixture), format="trig")

    embedder = HashEmbedder()

    # Seed vector store using the same helper as mock
    import warnings as w

    with w.catch_warnings():
        w.simplefilter("ignore", DeprecationWarning)
        import rdflib

        rdflib_graph = rdflib.Dataset()
        rdflib_graph.parse(str(fixture), format="trig")
    vector, uri_meta = _seed_vector_store(rdflib_graph, embedder)

    return _tools._ProductionStore(
        neptune=store,
        vector=vector,
        bedrock_client=MagicMock(),
        embedder=embedder,
    )


@pytest.fixture()
def production_store() -> _tools._ProductionStore:
    return _make_production_store_with_fixture()


@pytest.fixture(autouse=False)
def with_production_store(production_store: _tools._ProductionStore) -> Generator[None, None, None]:
    """Inject production store into _tools._store for the test duration."""
    _tools._store = production_store
    yield
    # _restore_store autouse fixture handles cleanup


class TestProductionToolDispatch:
    """Tests for production code paths using MemorySparqlStore (no AWS)."""

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_get_policies_production_returns_all(self) -> None:
        """get_policies with _ProductionStore returns all 3 fixture policies via sparql_select."""
        from graphrag.mcp._tools import get_policies

        results = await get_policies(context="workflow approval required", domain=None)
        # Fixture has 3 biz:Policy triples in urn:graph:normative
        assert len(results) == 3, f"Expected 3 policies, got {len(results)}: {results}"

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_get_policies_production_domain_filter(self) -> None:
        """get_policies domain filter works via production SPARQL template."""
        from graphrag.mcp._tools import get_policies

        results = await get_policies(context="leave policy", domain="hr")
        assert len(results) == 1, f"Expected 1 hr policy, got {len(results)}"
        assert results[0].domain == "hr"

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_ask_production_returns_rule_router_answer(self) -> None:
        """ask() in production mode returns a RuleQueryRouter placeholder (not MOCK prefix)."""
        from graphrag.mcp._tools import ask

        result = await ask(question="What are the HR policies?")
        assert "RuleQueryRouter" in result.answer
        assert "[MOCK]" not in result.answer
        assert result.strategy_trace.strategy == "rule"

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_search_graph_production_returns_subgraph(self) -> None:
        """search_graph() in production mode uses _sparql_rows_from_production."""
        from graphrag.mcp._tools import search_graph

        fixture_uri = "https://graphrag-aws.demo/biz-ops/policy/LeavePolicy"
        result = await search_graph(uri=fixture_uri, hops=1)
        assert result.root_uri == fixture_uri
        # Nodes should include the root even if SPARQL returns no rows
        assert any(n["uri"] == fixture_uri for n in result.nodes)

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_query_production_template_dispatch(self) -> None:
        """query() in production mode dispatches to NeptuneSparqlStore (MemorySparqlStore here)."""
        from graphrag.mcp._tools import query

        result = await query(template_name="policies_by_domain", params={"domain": "hr"})
        assert result.template_name == "policies_by_domain"
        assert result.row_count >= 1
        assert result.error is None

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_search_production_returns_unfiltered_without_type(self) -> None:
        """search() in production mode without type filter returns results."""
        from graphrag.mcp._tools import search

        results = await search(question="policy", k=5)
        # Vector store is seeded from fixture — must return at least one result
        assert len(results) > 0, "Expected at least one result from seeded fixture vector store"
        # In production mode, doc_type is empty string (no metadata pre-population)
        for r in results:
            assert r.doc_type == ""
            assert r.partition == ""

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_search_production_with_type_filter_returns_unfiltered(self) -> None:
        """search() with a type filter in production returns unfiltered results (not empty).

        Concern 5 regression: production search must skip the type filter (no uri_meta),
        NOT return [] silently when a type IRI is passed.
        """
        from graphrag.mcp._tools import search

        biz_policy_iri = "https://graphrag-aws.demo/biz-ops/ontology#Policy"
        results = await search(question="policy", type=biz_policy_iri, k=5)
        # In production, the type filter is skipped — results should still be non-empty
        assert len(results) > 0, (
            "Production search with type filter returned [], but should skip the filter "
            "and return unfiltered results when uri_meta is not populated."
        )

    @pytest.mark.usefixtures("with_production_store")
    @pytest.mark.anyio
    async def test_summarize_production_returns_rule_router_placeholder(self) -> None:
        """summarize() in production mode returns a RuleQueryRouter placeholder."""
        from graphrag.mcp._tools import summarize

        result = await summarize(topic="HR governance")
        assert "RuleQueryRouter" in result.summary
        assert "[MOCK]" not in result.summary
        assert result.topic == "HR governance"
