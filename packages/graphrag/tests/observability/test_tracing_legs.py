"""Span vocabulary + traced_leg tests — AC4, AC5 (partial).

Verifies:
- traced_leg produces spans with the correct name and SpanKind.
- DENY_SET attributes set on traced_leg spans are stripped by the filter.
- Offline isolation: configure_observability + mock server + six tools (AC5).
"""

from __future__ import annotations

import asyncio
import logging
import os

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from graphrag.observability import (
    ContentCaptureFilterExporter,
    traced_leg,
)
from graphrag.observability._bootstrap import reset_for_testing


@pytest.fixture(autouse=True)
def reset_provider():
    """Reset the global TracerProvider before and after each test."""
    reset_for_testing()
    yield
    reset_for_testing()


@pytest.fixture()
def tracing_pair() -> tuple[TracerProvider, InMemorySpanExporter]:
    """TracerProvider with ContentCaptureFilterExporter installed as global provider."""
    from opentelemetry import trace

    inner = InMemorySpanExporter()
    wrapped = ContentCaptureFilterExporter(inner)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(wrapped))
    trace.set_tracer_provider(provider)
    return provider, inner


# ---------------------------------------------------------------------------
# AC4: span name and SpanKind
# ---------------------------------------------------------------------------


def test_traced_leg_retrieval_hybrid(tracing_pair) -> None:
    """traced_leg('retrieval', strategy='hybrid') → retrieval.hybrid, CLIENT."""
    provider, inner = tracing_pair
    inner.clear()
    with traced_leg("retrieval", strategy="hybrid"):
        pass
    provider.force_flush()
    spans = inner.get_finished_spans()
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "retrieval.hybrid", f"Expected 'retrieval.hybrid', got {s.name!r}"
    assert s.kind == SpanKind.CLIENT, f"Expected CLIENT, got {s.kind}"


def test_traced_leg_rule_router(tracing_pair) -> None:
    """traced_leg('routing.rule_router') → SpanKind.INTERNAL."""
    provider, inner = tracing_pair
    inner.clear()
    with traced_leg("routing.rule_router"):
        pass
    provider.force_flush()
    spans = inner.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "routing.rule_router"
    assert spans[0].kind == SpanKind.INTERNAL


def test_traced_leg_bedrock_router(tracing_pair) -> None:
    """traced_leg('routing.bedrock_router') → SpanKind.INTERNAL."""
    provider, inner = tracing_pair
    inner.clear()
    with traced_leg("routing.bedrock_router"):
        pass
    provider.force_flush()
    spans = inner.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].kind == SpanKind.INTERNAL


def test_deny_set_attrs_stripped_on_leg_span(tracing_pair) -> None:
    """A DENY_SET attribute set on a traced_leg span is absent from export."""
    provider, inner = tracing_pair
    inner.clear()
    with traced_leg("retrieval", strategy="vector") as span:
        span.set_attribute("question.text", "secret question")
        span.set_attribute("result_count", 5)  # benign
    provider.force_flush()
    spans = inner.get_finished_spans()
    assert len(spans) == 1
    exported_attrs = dict(spans[0].attributes or {})
    assert "question.text" not in exported_attrs
    assert exported_attrs.get("result_count") == 5


# ---------------------------------------------------------------------------
# AC5: offline isolation — configure_observability + in-process mock server
# ---------------------------------------------------------------------------


def test_offline_isolation_mock_server_six_tools(caplog) -> None:
    """configure_observability active + six tool calls via in-process mock → no export ERROR.

    Implements AC5: uses the in-process mcp.call_tool pattern (same as
    test_mock_server.py).  No subprocess, no blocking server start.
    """
    import warnings

    # Clear AWS env vars for a clean offline test
    env_backup = {k: v for k, v in os.environ.items() if k.startswith("AWS_")}
    for k in env_backup:
        del os.environ[k]

    try:
        with caplog.at_level(logging.ERROR, logger="graphrag"):
            from graphrag.observability import configure_observability

            configure_observability("graphrag-mcp-test")

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from graphrag.mcp._mock import init_mock
                from graphrag.mcp._tools import mcp

                init_mock()

            # Drive the six tools via in-process call
            tool_calls = [
                ("ask", {"question": "What are the HR policies?"}),
                ("search", {"query": "HR"}),
                ("search_graph", {"query": "HR", "hops": 1}),
                ("get_policies", {"topic": "HR"}),
                ("query", {"sparql": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"}),
                ("summarize", {"topic": "HR"}),
            ]
            for tool_name, kwargs in tool_calls:
                try:
                    asyncio.run(mcp.call_tool(tool_name, arguments=kwargs))
                except Exception as e:  # noqa: BLE001
                    # Tool failures (e.g. no results) are acceptable;
                    # export errors are not.
                    if "export" in str(e).lower() or "exporter" in str(e).lower():
                        pytest.fail(f"Span export error during {tool_name}: {e}")

        # No ERROR-level span-export logs during the run
        error_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "export" in r.message.lower()
        ]
        assert not error_records, (
            f"Unexpected span-export ERROR logs: {[r.message for r in error_records]}"
        )

    finally:
        os.environ.update(env_backup)
