"""Tests for graphrag.mcp_proxy._proxy."""

from __future__ import annotations

import io
import json
import unittest.mock
import urllib.error
import urllib.request

import pytest

from graphrag.mcp_proxy._proxy import _OPENER, main, proxy_loop

# ── AC1 / AC2: startup validation ────────────────────────────────────────────


def test_missing_endpoint_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with no MCP_ENDPOINT_URL must exit with code 1 (AC1)."""
    monkeypatch.delenv("MCP_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("MCP_API_KEY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


def test_missing_api_key_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with no MCP_API_KEY must exit with code 1 (AC1)."""
    monkeypatch.setenv(
        "MCP_ENDPOINT_URL", "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    )
    monkeypatch.delenv("MCP_API_KEY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


def test_http_endpoint_rejected_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with an http:// MCP_ENDPOINT_URL must exit with code 1 (AC2)."""
    monkeypatch.setenv("MCP_ENDPOINT_URL", "http://example.com/mcp")
    monkeypatch.setenv("MCP_API_KEY", "secret-key")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


def test_https_uppercase_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPS:// (uppercase scheme) is accepted — scheme check is case-insensitive."""
    monkeypatch.setenv(
        "MCP_ENDPOINT_URL", "HTTPS://example.execute-api.us-east-1.amazonaws.com/mcp"
    )
    monkeypatch.setenv("MCP_API_KEY", "secret-key")

    with unittest.mock.patch("graphrag.mcp_proxy._proxy.proxy_loop"):
        main()  # must not raise SystemExit


def test_invalid_timeout_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with a non-integer MCP_TIMEOUT must exit with code 1 (AC6)."""
    monkeypatch.setenv(
        "MCP_ENDPOINT_URL", "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    )
    monkeypatch.setenv("MCP_API_KEY", "secret-key")
    monkeypatch.setenv("MCP_TIMEOUT", "not-a-number")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


def test_zero_timeout_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with MCP_TIMEOUT=0 must exit with code 1 (must be positive)."""
    monkeypatch.setenv(
        "MCP_ENDPOINT_URL", "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    )
    monkeypatch.setenv("MCP_API_KEY", "secret-key")
    monkeypatch.setenv("MCP_TIMEOUT", "0")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


def test_timeout_env_wires_into_proxy_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_TIMEOUT is parsed and passed to proxy_loop (AC6)."""
    monkeypatch.setenv(
        "MCP_ENDPOINT_URL", "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    )
    monkeypatch.setenv("MCP_API_KEY", "secret-key")
    monkeypatch.setenv("MCP_TIMEOUT", "120")

    with unittest.mock.patch("graphrag.mcp_proxy._proxy.proxy_loop") as mock_loop:
        main()

    _endpoint, _api_key, timeout = mock_loop.call_args[0]
    assert timeout == 120


def test_timeout_default_is_60(monkeypatch: pytest.MonkeyPatch) -> None:
    """When MCP_TIMEOUT is absent, proxy_loop receives timeout=60 (AC6)."""
    monkeypatch.setenv(
        "MCP_ENDPOINT_URL", "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    )
    monkeypatch.setenv("MCP_API_KEY", "secret-key")
    monkeypatch.delenv("MCP_TIMEOUT", raising=False)

    with unittest.mock.patch("graphrag.mcp_proxy._proxy.proxy_loop") as mock_loop:
        main()

    _endpoint, _api_key, timeout = mock_loop.call_args[0]
    assert timeout == 60


# ── AC3: round-trip mock ──────────────────────────────────────────────────────


def test_round_trip_posts_frame_and_writes_response() -> None:
    """proxy_loop forwards a JSON-RPC frame and writes the response + newline (AC3)."""
    endpoint = "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    api_key = "test-api-key"  # pragma: allowlist secret
    request_frame = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1})
    response_body = json.dumps({"jsonrpc": "2.0", "result": {"tools": []}, "id": 1})

    captured_request: list[urllib.request.Request] = []

    mock_response = unittest.mock.MagicMock()
    mock_response.read.return_value = response_body.encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = unittest.mock.MagicMock(return_value=False)

    def fake_open(req: urllib.request.Request, timeout: int) -> unittest.mock.MagicMock:
        captured_request.append(req)
        return mock_response

    stdin = io.StringIO(request_frame + "\n")
    stdout = io.StringIO()

    with unittest.mock.patch.object(_OPENER, "open", fake_open):
        proxy_loop(endpoint, api_key, timeout=30, stdin=stdin, stdout=stdout)

    # Confirm request was sent with correct endpoint, method, and headers
    assert len(captured_request) == 1
    req = captured_request[0]
    assert req.full_url == endpoint
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    assert req.get_header("X-api-key") == api_key
    assert req.data == request_frame.encode()

    # Confirm response written to stdout with trailing newline (AC3)
    output = stdout.getvalue()
    assert output == response_body + "\n"


def test_empty_lines_skipped_no_request_sent() -> None:
    """Blank stdin lines do not produce an HTTP request."""
    endpoint = "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    api_key = "test-api-key"  # pragma: allowlist secret

    stdin = io.StringIO("\n  \n\t\n")
    stdout = io.StringIO()

    with unittest.mock.patch.object(_OPENER, "open") as mock_open:
        proxy_loop(endpoint, api_key, timeout=30, stdin=stdin, stdout=stdout)

    mock_open.assert_not_called()
    assert stdout.getvalue() == ""


# ── AC4: error forwarding ─────────────────────────────────────────────────────


def test_url_error_writes_jsonrpc_error_frame() -> None:
    """On URLError, proxy_loop writes a valid JSON-RPC error frame (AC4)."""
    endpoint = "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    api_key = "test-api-key"  # pragma: allowlist secret
    request_frame = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1})

    def fail_open(req: urllib.request.Request, timeout: int) -> None:
        raise urllib.error.URLError("connection refused")

    stdin = io.StringIO(request_frame + "\n")
    stdout = io.StringIO()

    with unittest.mock.patch.object(_OPENER, "open", fail_open):
        proxy_loop(endpoint, api_key, timeout=30, stdin=stdin, stdout=stdout)

    output = stdout.getvalue().strip()
    assert output, "stdout must not be empty after an error"

    frame = json.loads(output)
    assert frame["jsonrpc"] == "2.0"
    assert frame["error"]["code"] == -32603
    assert "connection refused" in frame["error"]["message"]
    assert frame["id"] is None


def test_generic_exception_writes_jsonrpc_error_frame() -> None:
    """On any Exception, proxy_loop writes a valid JSON-RPC error frame (AC4)."""
    endpoint = "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp"
    api_key = "test-api-key"  # pragma: allowlist secret
    request_frame = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 2})

    def raise_generic(req: urllib.request.Request, timeout: int) -> None:
        raise RuntimeError("unexpected server failure")

    stdin = io.StringIO(request_frame + "\n")
    stdout = io.StringIO()

    with unittest.mock.patch.object(_OPENER, "open", raise_generic):
        proxy_loop(endpoint, api_key, timeout=30, stdin=stdin, stdout=stdout)

    frame = json.loads(stdout.getvalue().strip())
    assert frame["jsonrpc"] == "2.0"
    assert frame["error"]["code"] == -32603
    assert "unexpected server failure" in frame["error"]["message"]


# ── AC5: api key not logged ───────────────────────────────────────────────────


def test_api_key_not_in_startup_log(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() startup message must not contain the api key value (AC5)."""
    api_key = "super-secret-key-value-12345"  # pragma: allowlist secret
    monkeypatch.setenv(
        "MCP_ENDPOINT_URL",
        "https://example.execute-api.us-east-1.amazonaws.com/prod/mcp",
    )
    monkeypatch.setenv("MCP_API_KEY", api_key)
    monkeypatch.setenv("MCP_TIMEOUT", "30")

    with unittest.mock.patch("graphrag.mcp_proxy._proxy.proxy_loop"):
        main()

    captured = capsys.readouterr()
    # Key value must not appear anywhere (AC5)
    assert api_key not in captured.out
    assert api_key not in captured.err
    # A fixed redacted placeholder must appear (AC5)
    assert "***" in captured.err
