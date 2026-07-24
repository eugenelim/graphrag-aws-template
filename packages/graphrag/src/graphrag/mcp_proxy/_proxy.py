"""Core stdio→HTTPS proxy logic for graphrag.mcp_proxy."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import IO


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block all redirects to prevent API key leakage to redirect targets.

    urllib follows redirects by default and copies request headers (including
    ``x-api-key``) to the redirect target without re-checking the scheme or
    host — a compromised upstream returning ``302 Location: http://...`` would
    transmit the API key in cleartext.  This handler prevents that by raising
    ``URLError`` on any 3xx response.
    """

    def redirect_request(  # noqa: PLR0913
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        raise urllib.error.URLError(
            f"redirect blocked (HTTP {code}) — refusing to follow to {newurl!r}"
            " to protect the API key"
        )


# Module-level opener with redirects disabled; patched in tests.
_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _load_config() -> tuple[str, str, int]:
    """Load and validate proxy config from environment variables.

    Returns:
        A 3-tuple of (endpoint_url, api_key, timeout_seconds).

    Raises:
        SystemExit: if required vars are missing, the endpoint is not HTTPS,
            or MCP_TIMEOUT is not a positive integer.
    """
    endpoint = os.environ.get("MCP_ENDPOINT_URL", "")
    api_key = os.environ.get("MCP_API_KEY", "")
    timeout_str = os.environ.get("MCP_TIMEOUT", "60")

    errors: list[str] = []

    if not endpoint:
        errors.append("MCP_ENDPOINT_URL is required but not set")
    elif not endpoint.lower().startswith("https://"):
        errors.append(
            f"MCP_ENDPOINT_URL must use the https:// scheme — non-HTTPS endpoints are"
            f" rejected for security (got: {endpoint!r})"
        )

    if not api_key:
        errors.append("MCP_API_KEY is required but not set")

    timeout = 60
    try:
        timeout = int(timeout_str)
        if timeout <= 0:
            errors.append(f"MCP_TIMEOUT must be a positive integer (got: {timeout_str!r})")
    except ValueError:
        errors.append(f"MCP_TIMEOUT must be an integer number of seconds (got: {timeout_str!r})")

    if errors:
        for msg in errors:
            print(f"graphrag-mcp-proxy error: {msg}", file=sys.stderr)
        sys.exit(1)

    return endpoint, api_key, timeout


def proxy_loop(
    endpoint: str,
    api_key: str,
    timeout: int = 60,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> None:
    """Read newline-delimited MCP JSON frames from stdin and forward to endpoint.

    Each non-empty line from *stdin* is POSTed to *endpoint* with the
    ``x-api-key`` header set to *api_key*.  The response body is written to
    *stdout* followed by a newline and flushed.

    Redirects are blocked to prevent API key leakage (see ``_NoRedirectHandler``).
    On any exception (including ``HTTPError`` from 4xx/5xx responses) the proxy
    writes a JSON-RPC internal-error frame to *stdout* and continues — it never
    crashes on a single request failure.  The raw error body from the server is
    intentionally not forwarded; callers see a generic ``-32603`` frame.

    Args:
        endpoint: The HTTPS API Gateway URL.  Must start with ``https://``.
        api_key: The API key value.  Never written to any output stream.
        timeout: Per-request timeout in seconds.
        stdin: Input stream (defaults to ``sys.stdin``).
        stdout: Output stream (defaults to ``sys.stdout``).
    """
    _stdin: IO[str] = stdin if stdin is not None else sys.stdin
    _stdout: IO[str] = stdout if stdout is not None else sys.stdout

    for line in _stdin:
        line = line.strip()
        if not line:
            continue

        # S310: URL is validated to start with https:// in _load_config before
        # proxy_loop is called; the endpoint is a required, validated parameter.
        req = urllib.request.Request(  # noqa: S310
            endpoint,
            data=line.encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
            },
            method="POST",
        )

        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                _stdout.write(resp.read().decode() + "\n")
                _stdout.flush()
        except Exception as exc:
            error_frame = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(exc)},
                    "id": None,
                }
            )
            _stdout.write(error_frame + "\n")
            _stdout.flush()


def main() -> None:
    """Validate config from environment and start the stdio→HTTPS proxy loop."""
    endpoint, api_key, timeout = _load_config()
    print(
        f"graphrag-mcp-proxy: endpoint={endpoint} api-key=***set*** timeout={timeout}s",
        file=sys.stderr,
    )
    proxy_loop(endpoint, api_key, timeout)
