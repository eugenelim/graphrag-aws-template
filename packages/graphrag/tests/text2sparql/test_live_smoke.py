"""T4 — Live-smoke IAM backstop (AC8).

Confirms the Neptune IAM grant on ``mcp_lambda_role`` blocks SPARQL Update
statements at the ``/sparql`` endpoint — the load-bearing security control is at
the engine, independent of the app-layer validator.

These tests are skipped in offline CI.  Run them against a live deployment:

    pytest -m live_aws packages/graphrag/tests/text2sparql/test_live_smoke.py

Expected: Neptune returns an IAM ``AccessDeniedException`` (HTTP 403) for both
``DROP GRAPH`` and ``INSERT DATA`` — confirming ADR-0011 Confirmation gate.
"""

from __future__ import annotations

import os

import pytest

NEPTUNE_ENDPOINT = os.environ.get("NEPTUNE_SPARQL_ENDPOINT", "")


@pytest.mark.live_aws
def test_mcp_lambda_role_blocks_drop_graph() -> None:
    """DROP GRAPH under mcp_lambda_role → Neptune IAM AccessDeniedException."""
    if not NEPTUNE_ENDPOINT:
        pytest.skip("NEPTUNE_SPARQL_ENDPOINT not set")

    import urllib.error
    import urllib.parse
    import urllib.request

    update = "DROP GRAPH <urn:graph:normative>"
    data = urllib.parse.urlencode({"update": update}).encode()
    req = urllib.request.Request(NEPTUNE_ENDPOINT, data=data, method="POST")  # noqa: S310
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
        pytest.fail("Expected IAM AccessDeniedException — Neptune accepted the DROP GRAPH")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403, f"Expected 403 AccessDeniedException, got {exc.code}"
        body = exc.read().decode(errors="replace")
        assert "AccessDeniedException" in body or "Forbidden" in body


@pytest.mark.live_aws
def test_mcp_lambda_role_blocks_insert_data() -> None:
    """INSERT DATA under mcp_lambda_role → Neptune IAM AccessDeniedException."""
    if not NEPTUNE_ENDPOINT:
        pytest.skip("NEPTUNE_SPARQL_ENDPOINT not set")

    import urllib.error
    import urllib.parse
    import urllib.request

    update = "INSERT DATA { GRAPH <urn:graph:normative> { <urn:x> <urn:p> <urn:z> } }"
    data = urllib.parse.urlencode({"update": update}).encode()
    req = urllib.request.Request(NEPTUNE_ENDPOINT, data=data, method="POST")  # noqa: S310
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
        pytest.fail("Expected IAM AccessDeniedException — Neptune accepted the INSERT DATA")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403, f"Expected 403 AccessDeniedException, got {exc.code}"
        body = exc.read().decode(errors="replace")
        assert "AccessDeniedException" in body or "Forbidden" in body
