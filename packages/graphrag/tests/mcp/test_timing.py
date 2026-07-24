"""AC4 — ask() timing gate.

Calls ``ask()`` against the mock fixture and asserts it completes in < 30 s
(API Gateway hard timeout).  A WARNING log is emitted if > 20 s (from
``_tools.ask``).

Wall-clock timing in CI is generous — the mock path has no runaway loop or
blocking call, so it should complete in milliseconds.
"""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture(scope="module", autouse=True)
def mock_store_timing() -> None:
    """Ensure mock store is initialised for timing tests."""
    from graphrag.mcp._mock import init_mock

    init_mock()


def test_ask_completes_within_30s() -> None:
    """ask() with the mock fixture completes in < 30 s (AC4)."""
    from graphrag.mcp._tools import mcp

    start = time.monotonic()
    _, result = asyncio.run(
        mcp.call_tool("ask", arguments={"question": "What are the HR policies?"})
    )
    elapsed = time.monotonic() - start

    assert elapsed < 30.0, f"ask() took {elapsed:.2f}s — exceeds API Gateway 30s hard timeout"
    assert "answer" in result


def test_ask_completes_within_generous_ci_budget() -> None:
    """ask() mock completes well under 5 s — confirms no blocking call."""
    from graphrag.mcp._tools import mcp

    start = time.monotonic()
    asyncio.run(mcp.call_tool("ask", arguments={"question": "HR governance overview"}))
    elapsed = time.monotonic() - start

    # Mock path is purely in-memory; > 5 s would indicate a bug (blocking I/O, loop, etc.)
    assert elapsed < 5.0, (
        f"ask() took {elapsed:.2f}s in mock mode — check for blocking call or loop"
    )
