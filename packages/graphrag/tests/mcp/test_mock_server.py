"""AC2, AC6, AC7 — offline mock tool invocations.

All tests run without AWS environment variables.  The mock store is
initialised once per session via a session-scoped pytest fixture.
Tool functions are called in-process via FastMCP's ``call_tool`` coroutine.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# ---------------------------------------------------------------------------
# Session-scoped fixture: initialise mock stores once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def mock_store() -> None:
    """Initialise the in-memory mock store from the fixture corpus."""
    # Ensure no AWS environment variables leak into the mock
    for key in list(os.environ.keys()):
        if key.startswith("AWS_"):
            os.environ.pop(key, None)

    from graphrag.mcp._mock import init_mock

    init_mock()


# ---------------------------------------------------------------------------
# Helper: synchronous wrapper for async tool calls
# ---------------------------------------------------------------------------


def _call(tool_name: str, **kwargs: object) -> object:
    """Call a FastMCP tool by name and return the raw result tuple."""
    from graphrag.mcp._tools import mcp

    return asyncio.run(mcp.call_tool(tool_name, arguments=kwargs))


# ---------------------------------------------------------------------------
# AC2: all six tools return non-empty schema-valid responses
# ---------------------------------------------------------------------------


def test_ask_returns_non_empty_response() -> None:
    """ask() returns an AskResponse with a non-empty answer string."""
    _, result = _call("ask", question="What are the HR policies?")
    assert "answer" in result, "AskResponse must have 'answer' key"
    assert isinstance(result["answer"], str)
    assert result["answer"]  # non-empty
    assert "citations" in result
    assert "strategy_trace" in result


def test_search_returns_results() -> None:
    """search() returns a list of SearchResult dicts."""
    texts, result = _call("search", question="approval workflow", k=5)
    assert "result" in result
    items = result["result"]
    assert isinstance(items, list)
    # Fixture has 6 entities (3 policies + 2 SOPs + 1 domain); at least 1 should score
    assert len(items) >= 1
    first = items[0]
    assert "uri" in first
    assert "title" in first
    assert "score" in first
    assert isinstance(first["score"], float)


def test_search_graph_returns_subgraph() -> None:
    """search_graph() returns a SubgraphResult with nodes and edges for a fixture URI."""
    _, result = _call("search_graph", uri="urn:biz:policy:hr-leave", hops=1)
    assert result["root_uri"] == "urn:biz:policy:hr-leave"
    assert "nodes" in result
    assert "edges" in result
    # Root node must be present
    node_uris = {n["uri"] for n in result["nodes"]}
    assert "urn:biz:policy:hr-leave" in node_uris
    # Traversal must return non-empty edges (HR Leave Policy has schema:name, biz:scope, etc.)
    assert len(result["edges"]) > 0, (
        "search_graph returned zero edges — SPARQL GRAPH clause may be missing "
        f"(fixture uses named graphs). nodes={result['nodes']}"
    )
    # At least one known neighbour should appear in the node set
    neighbour_uris = node_uris - {"urn:biz:policy:hr-leave"}
    assert len(neighbour_uris) > 0, "Expected at least one neighbour node beyond the root"


def test_get_policies_returns_all() -> None:
    """get_policies() without domain filter returns all 3 fixture policies (AC6)."""
    texts, result = _call("get_policies", context="workflow approval required", domain=None)
    assert "result" in result
    policies = result["result"]
    assert len(policies) == 3, (
        f"Expected 3 policies (all fixture policies), got {len(policies)}: {policies}"
    )
    for pol in policies:
        assert "uri" in pol
        assert "title" in pol


def test_query_known_template() -> None:
    """query() with 'policies_by_domain' and domain='hr' returns exactly 1 row (AC7)."""
    _, result = _call(
        "query",
        template_name="policies_by_domain",
        params={"domain": "hr"},
    )
    assert result["template_name"] == "policies_by_domain"
    assert result["row_count"] == 1, (
        f"Expected exactly 1 row for domain=hr (hr-leave policy), got {result['row_count']}"
    )
    assert result.get("error") is None
    assert len(result["rows"]) == result["row_count"]


def test_query_unknown_template_returns_error_not_exception() -> None:
    """query() with an unknown template_name returns error result, not an exception (AC7)."""
    _, result = _call(
        "query",
        template_name="nonexistent_template_xyz",
        params={},
    )
    assert result["template_name"] == "nonexistent_template_xyz"
    assert result["row_count"] == 0
    assert result["rows"] == []
    assert result.get("error") == "template not found"


def test_summarize_returns_non_empty() -> None:
    """summarize() returns a SummaryResult with a non-empty summary string."""
    _, result = _call("summarize", topic="HR governance")
    assert "topic" in result
    assert "summary" in result
    assert result["summary"]  # non-empty
    assert "citations" in result


# ---------------------------------------------------------------------------
# AC6: get_policies is exhaustive — no top-k cutoff
# ---------------------------------------------------------------------------


def test_get_policies_exhaustive_no_cutoff() -> None:
    """get_policies() returns all 3 policies regardless of fixture ordering."""
    _, result = _call("get_policies", context="any context")
    policies = result["result"]
    assert len(policies) == 3, "get_policies must be exhaustive — fixture has 3 biz:Policy triples"
    uris = {p["uri"] for p in policies}
    assert "urn:biz:policy:hr-leave" in uris
    assert "urn:biz:policy:expense-reimbursement" in uris
    assert "urn:biz:policy:data-handling" in uris


# ---------------------------------------------------------------------------
# AC2: search with type filter — unknown type returns empty list (not exception)
# ---------------------------------------------------------------------------


def test_search_unknown_type_returns_empty() -> None:
    """search() with an unknown type filter returns [] — no exception."""
    _, result = _call("search", question="any question", type="biz:UnknownClass", k=10)
    assert "result" in result
    assert result["result"] == []


# ---------------------------------------------------------------------------
# AC6: get_policies domain filter is effective
# ---------------------------------------------------------------------------


def test_get_policies_domain_filter_returns_scoped_policies() -> None:
    """get_policies() with domain='hr' returns exactly 1 policy (HR Leave Policy)."""
    _, result = _call("get_policies", context="leave approval", domain="hr")
    assert "result" in result
    policies = result["result"]
    assert len(policies) == 1, f"Expected 1 HR-scoped policy, got {len(policies)}: {policies}"
    assert policies[0]["uri"] == "urn:biz:policy:hr-leave"
    assert policies[0]["domain"] == "hr"
