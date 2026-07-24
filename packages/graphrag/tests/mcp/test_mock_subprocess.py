"""AC2 (subprocess-transport) — streamable-HTTP smoke test.

Starts ``python -m graphrag.mcp --mock`` as a real subprocess with no AWS
environment variables set, then exercises all six tools via HTTP POST to the
MCP streamable-HTTP endpoint.  Asserts HTTP 200 + schema-valid JSON for each
tool, and a clean exit after SIGTERM.

Requires: no AWS credentials in the subprocess environment.
Marked ``pytest.mark.timeout(60)`` (effective when pytest-timeout is installed).
"""

from __future__ import annotations

import collections.abc
import json
import os
import signal
import socket
import subprocess
import sys
import time

import pytest
import requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Return an ephemeral port that is currently free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll until ``host:port`` accepts a TCP connection; return True on success."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect((host, port))
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.25)
    return False


def _parse_sse_result(response_text: str) -> dict:
    """Extract the JSON-RPC result payload from an SSE event stream.

    MCP streamable-HTTP responses are ``text/event-stream``::

        event: message
        data: {<json-rpc response>}

    For tool calls, ``result.structuredContent`` carries the typed response
    when available; otherwise ``result.content[0].text`` is parsed.
    """
    for line in response_text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if "result" in data:
                result = data["result"]
                if "structuredContent" in result:
                    return result["structuredContent"]  # type: ignore[no-any-return]
                if result.get("content"):
                    return json.loads(result["content"][0]["text"])  # type: ignore[no-any-return]
            if "error" in data:
                raise AssertionError(f"JSON-RPC error in response: {data['error']}")
    raise AssertionError(f"No parseable data event in response:\n{response_text!r}")


# ---------------------------------------------------------------------------
# Session-scoped fixture: start mock server subprocess once per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mcp_session() -> collections.abc.Generator[dict, None, None]:
    """Start the mock server, initialise an MCP session, yield session state.

    Yields a dict with ``session_id`` and ``port``.  Tears down the server
    after the module finishes.
    """
    port = _find_free_port()
    host = "127.0.0.1"

    # Strip AWS env vars so the subprocess cannot make AWS calls
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}

    proc = subprocess.Popen(
        [sys.executable, "-m", "graphrag.mcp", "--mock", "--host", host, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        # Wait for the server to be reachable
        assert _wait_for_port(host, port, timeout=30.0), (
            f"Mock server did not start within 30s on {host}:{port}"
        )

        base_url = f"http://{host}:{port}"

        # ── MCP session initialisation ──────────────────────────────────────
        init_resp = requests.post(
            f"{base_url}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-subprocess-transport", "version": "0.0.1"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        assert init_resp.status_code == 200, (
            f"initialize failed: {init_resp.status_code} {init_resp.text[:200]}"
        )
        session_id = init_resp.headers.get("mcp-session-id", "")
        assert session_id, "Server did not return mcp-session-id"

        # Notify server that client is initialised (required handshake step)
        notif_resp = requests.post(
            f"{base_url}/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "mcp-session-id": session_id,
            },
            timeout=10,
        )
        assert notif_resp.status_code in (200, 202, 204), (
            f"notifications/initialized failed: {notif_resp.status_code}"
        )

        yield {"session_id": session_id, "base_url": base_url, "proc": proc}

    finally:
        # Graceful shutdown: SIGTERM then wait up to 10 s
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Helper: call a tool via HTTP and return the parsed structured content
# ---------------------------------------------------------------------------


def _call_tool(session: dict, tool_name: str, arguments: dict, request_id: int = 42) -> dict:
    resp = requests.post(
        f"{session['base_url']}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "mcp-session-id": session["session_id"],
        },
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"tools/call {tool_name!r} returned HTTP {resp.status_code}: {resp.text[:300]}"
    )
    return _parse_sse_result(resp.text)


# ---------------------------------------------------------------------------
# AC2 subprocess transport: all 6 tools via real HTTP
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_ask_subprocess(mcp_session: dict) -> None:
    """ask() via subprocess HTTP returns a schema-valid AskResponse."""
    result = _call_tool(mcp_session, "ask", {"question": "What are the HR policies?"})
    assert "answer" in result, f"Missing 'answer' key in ask response: {result}"
    assert isinstance(result["answer"], str)
    assert result["answer"]  # non-empty
    assert "citations" in result
    assert "strategy_trace" in result


@pytest.mark.timeout(60)
def test_search_subprocess(mcp_session: dict) -> None:
    """search() via subprocess HTTP returns a list of SearchResult items."""
    result = _call_tool(mcp_session, "search", {"question": "onboarding"})
    # list tools: structuredContent has {result: [...]} or bare list
    items = result.get("result", result) if isinstance(result, dict) else result
    assert isinstance(items, list), f"Expected list from search, got: {type(items)}"
    assert len(items) >= 1, "search returned empty list"
    first = items[0]
    assert "uri" in first
    assert "title" in first
    assert "score" in first


@pytest.mark.timeout(60)
def test_search_graph_subprocess(mcp_session: dict) -> None:
    """search_graph() via subprocess HTTP returns a SubgraphResult."""
    result = _call_tool(
        mcp_session,
        "search_graph",
        {"uri": "urn:doc:mock-repo:policies/hr-policy.ttl"},
    )
    assert "root_uri" in result
    assert "nodes" in result
    assert "edges" in result
    assert isinstance(result["nodes"], list)
    assert isinstance(result["edges"], list)


@pytest.mark.timeout(60)
def test_get_policies_subprocess(mcp_session: dict) -> None:
    """get_policies() via subprocess HTTP returns PolicyResult items."""
    result = _call_tool(mcp_session, "get_policies", {"context": "workflow"})
    items = result.get("result", result) if isinstance(result, dict) else result
    assert isinstance(items, list), f"Expected list from get_policies, got: {type(items)}"
    assert len(items) >= 1, "get_policies returned empty list"
    first = items[0]
    assert "uri" in first
    assert "title" in first


@pytest.mark.timeout(60)
def test_query_subprocess(mcp_session: dict) -> None:
    """query() with 'policies_by_domain' template via subprocess HTTP returns QueryResult."""
    result = _call_tool(
        mcp_session,
        "query",
        {"template_name": "policies_by_domain", "params": {"domain": "hr"}},
    )
    assert "template_name" in result
    assert "rows" in result
    assert "row_count" in result
    assert result["row_count"] >= 1, "Expected at least 1 row for domain=hr"
    assert result["template_name"] == "policies_by_domain"


@pytest.mark.timeout(60)
def test_summarize_subprocess(mcp_session: dict) -> None:
    """summarize() via subprocess HTTP returns a SummaryResult."""
    result = _call_tool(mcp_session, "summarize", {"topic": "HR processes"})
    assert "topic" in result
    assert "summary" in result
    assert isinstance(result["summary"], str)
    assert result["summary"]  # non-empty
    assert "citations" in result


# ---------------------------------------------------------------------------
# Clean exit check: subprocess exits cleanly after SIGTERM
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_subprocess_clean_exit(mcp_session: dict) -> None:
    """The mock server exits cleanly (within 10 s) after SIGTERM.

    This test runs last (file-definition order for module-scoped fixture) and
    intentionally terminates the server so the fixture teardown is a no-op.
    """
    proc: subprocess.Popen[bytes] = mcp_session["proc"]
    assert proc.poll() is None, (
        f"Mock server already crashed before clean-exit test (returncode {proc.returncode})"
    )

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        pytest.fail("Mock server did not respond to SIGTERM within 10 s")

    # POSIX: process killed by SIGTERM has returncode = -signal.SIGTERM (-15).
    # A server that calls sys.exit(0) on SIGTERM would have returncode 0.
    # Both are "clean" (no crash, no hang).
    expected = (0, -signal.SIGTERM)
    assert proc.returncode in expected, (
        f"Mock server exited with unexpected returncode {proc.returncode!r} "
        f"(expected one of {expected})"
    )
