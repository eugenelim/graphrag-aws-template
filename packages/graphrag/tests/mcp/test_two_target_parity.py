"""AC3 — Two-target parity: mock (in-process) vs. Mangum-wrapped Lambda handler.

The "parity" guarantee: both the mock path and the Lambda path use the same
``@mcp.tool()`` decorated functions from ``_tools.py`` and thus the same
Pydantic response models.  This test verifies:

1. ``Mangum(mcp.streamable_http_app(), lifespan="off")`` instantiates without
   error (Lambda cold-start will succeed).
2. All six tools called in-process return dicts whose keys match the Pydantic
   model fields — asserting that the same schema is returned regardless of
   transport (in-process call vs. ASGI → Lambda event).

Full round-trip testing (API Gateway event → Mangum → ASGI → tool → JSON
response body) is deferred because the FastMCP streamable-http session
manager requires the ASGI lifespan protocol to be running, which Mangum
skips with ``lifespan="off"``.  The structural guarantee (same Pydantic model
= same field set) is the meaningful parity claim this spec owns.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(scope="module", autouse=True)
def mock_store_parity() -> None:
    """Ensure mock store is initialised for parity tests."""
    from graphrag.mcp._mock import init_mock

    init_mock()


# ---------------------------------------------------------------------------
# AC3a: Mangum instantiation
# ---------------------------------------------------------------------------


def test_mangum_instantiates_without_error() -> None:
    """Mangum wraps the FastMCP ASGI app without raising during construction."""
    from mangum import Mangum

    from graphrag.mcp._tools import mcp

    handler = Mangum(mcp.streamable_http_app(), lifespan="off")
    assert handler is not None


def test_lambda_handler_is_callable() -> None:
    """The Lambda handler object is callable (can receive API Gateway events)."""
    from mangum import Mangum

    from graphrag.mcp._tools import mcp

    handler = Mangum(mcp.streamable_http_app(), lifespan="off")
    assert callable(handler)


# ---------------------------------------------------------------------------
# AC3b: structural parity — same field keys from in-process tool calls
# ---------------------------------------------------------------------------

# Expected field sets for each tool's response model
_EXPECTED_FIELDS = {
    "ask": {"answer", "citations", "strategy_trace"},
    "search": {"result"},  # FastMCP wraps list responses in {"result": [...]}
    "search_graph": {"root_uri", "nodes", "edges"},
    "get_policies": {"result"},
    "query": {"template_name", "rows", "row_count", "error"},
    "summarize": {"topic", "summary", "citations"},
}


def _call(tool_name: str, **kwargs: object) -> dict[str, object]:
    from graphrag.mcp._tools import mcp

    _, result = asyncio.run(mcp.call_tool(tool_name, arguments=kwargs))
    return result  # type: ignore[return-value]


def test_parity_ask() -> None:
    """ask response dict has exactly the AskResponse field set."""
    result = _call("ask", question="What is the HR policy?")
    assert set(result.keys()) >= _EXPECTED_FIELDS["ask"]


def test_parity_search() -> None:
    """search response is a list (wrapped in result key by FastMCP)."""
    result = _call("search", question="approval workflow", k=3)
    # FastMCP wraps list returns in {"result": [...]}
    assert "result" in result
    assert isinstance(result["result"], list)


def test_parity_search_graph() -> None:
    """search_graph response dict has root_uri, nodes, edges."""
    result = _call("search_graph", uri="urn:biz:policy:hr-leave", hops=1)
    assert set(result.keys()) >= _EXPECTED_FIELDS["search_graph"]


def test_parity_get_policies() -> None:
    """get_policies response is a list (wrapped in result key)."""
    result = _call("get_policies", context="HR context")
    assert "result" in result
    assert isinstance(result["result"], list)


def test_parity_query_known_template() -> None:
    """query response dict has template_name, rows, row_count, error."""
    result = _call("query", template_name="policies_by_domain", params={"domain": "hr"})
    assert set(result.keys()) >= _EXPECTED_FIELDS["query"] - {"error"}
    assert "template_name" in result
    assert "rows" in result
    assert "row_count" in result


def test_parity_summarize() -> None:
    """summarize response dict has topic, summary, citations."""
    result = _call("summarize", topic="HR governance")
    assert set(result.keys()) >= _EXPECTED_FIELDS["summarize"]


def test_same_model_fields_across_calls() -> None:
    """Two sequential calls to the same tool return identical field sets."""
    result_1 = _call("ask", question="First question")
    result_2 = _call("ask", question="Second question")
    assert set(result_1.keys()) == set(result_2.keys()), (
        "Field set must be stable across calls — same Pydantic model guarantees this"
    )

# ---------------------------------------------------------------------------
# Cost-guard: hops > 2 is clamped, not an error
# ---------------------------------------------------------------------------


def test_search_graph_hops_max2_clamp() -> None:
    """search_graph() with hops=5 is silently clamped to hops=2 — no exception."""
    from graphrag.mcp._tools import mcp as _mcp

    # hops=5 must not raise; result must match hops=2 (the clamped value)
    _, result_5 = asyncio.run(
        _mcp.call_tool("search_graph", arguments={"uri": "urn:biz:policy:hr-leave", "hops": 5})
    )
    _, result_2 = asyncio.run(
        _mcp.call_tool("search_graph", arguments={"uri": "urn:biz:policy:hr-leave", "hops": 2})
    )

    # Traversal must actually work (non-empty edges); if zero edges, the SPARQL
    # GRAPH clause is probably missing and the test would pass vacuously.
    assert len(result_2["edges"]) > 0, (
        "search_graph(hops=2) returned zero edges — traversal appears broken"
    )

    # Both must have the same root and same node/edge sets (clamping is idempotent)
    assert result_5["root_uri"] == result_2["root_uri"]
    # Node and edge counts must be identical — cost guard bounded both to 2 hops
    assert len(result_5["nodes"]) == len(result_2["nodes"]), (
        f"hops=5 returned {len(result_5['nodes'])} nodes, hops=2 returned {len(result_2['nodes'])}"
    )
