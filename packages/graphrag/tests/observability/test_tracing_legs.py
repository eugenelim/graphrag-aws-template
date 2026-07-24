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
from graphrag.observability._bootstrap import _reset_for_testing


@pytest.fixture(autouse=True)
def reset_provider():
    """Reset the global TracerProvider before and after each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


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


def test_traced_leg_exception_does_not_leak_message(tracing_pair) -> None:
    """When an exception propagates through traced_leg, exception.message is absent.

    ADR-0015 content-capture policy: str(exc) may contain question-derived text;
    only the bounded class name (error.type) is recorded.
    """
    provider, inner = tracing_pair
    inner.clear()

    class _TestError(RuntimeError):
        pass

    with pytest.raises(_TestError):
        with traced_leg("retrieval", strategy="hybrid"):
            raise _TestError("sensitive question text goes here")

    provider.force_flush()
    spans = inner.get_finished_spans()
    assert len(spans) == 1
    s = spans[0]

    # error.type is the class name only — bounded, no content leak
    attrs = dict(s.attributes or {})
    assert attrs.get("error.type") == "_TestError"

    # exception.message must NOT be present (would carry str(exc))
    event_attr_keys: set[str] = set()
    for ev in s.events or []:
        event_attr_keys.update(ev.attributes or {})
    assert "exception.message" not in event_attr_keys, (
        "exception.message found in span events — content-capture bypass"
    )
    assert "exception.stacktrace" not in event_attr_keys, (
        "exception.stacktrace found in span events — content-capture bypass"
    )


# ---------------------------------------------------------------------------
# AC5: offline isolation — configure_observability + in-process mock server
# ---------------------------------------------------------------------------


def test_offline_isolation_mock_server_six_tools() -> None:
    """configure_observability active + six tool calls via in-process mock → no export ERROR.

    Implements AC5: uses the in-process mcp.call_tool pattern (same as
    test_mock_server.py).  No subprocess, no blocking server start.

    Note: configure_observability calls configure_json_logging(), which removes
    ALL root handlers (including pytest's LogCaptureHandler).  We therefore add
    our own capture handler AFTER setup so the assertion can actually go red on
    a real export error.
    """
    import warnings

    # Clear AWS env vars for a clean offline test
    env_backup = {k: v for k, v in os.environ.items() if k.startswith("AWS_")}
    for k in env_backup:
        del os.environ[k]

    try:
        from graphrag.observability import configure_observability

        configure_observability("graphrag-mcp-test")

        # Add capture handler AFTER configure_json_logging removed pytest's handler
        export_errors: list[logging.LogRecord] = []

        class _ExportErrorCapture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.levelno >= logging.ERROR and "export" in record.getMessage().lower():
                    export_errors.append(record)

        cap = _ExportErrorCapture()
        cap.setLevel(logging.ERROR)
        logging.getLogger().addHandler(cap)

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from graphrag.mcp._mock import init_mock
                from graphrag.mcp._tools import mcp

                init_mock()

            # Drive the six tools via in-process call
            tool_calls = [
                ("ask", {"question": "What are the HR policies?"}),
                ("search", {"question": "HR"}),
                ("search_graph", {"uri": "urn:biz:policy:hr-leave", "hops": 1}),
                ("get_policies", {"context": "HR"}),
                ("query", {"template_name": "policies_by_domain", "params": {"domain": "hr"}}),
                ("summarize", {"topic": "HR"}),
            ]
            for tool_name, kwargs in tool_calls:
                try:
                    asyncio.run(mcp.call_tool(tool_name, arguments=kwargs))
                except Exception as e:  # noqa: BLE001
                    # Tool failures (e.g. no results) are acceptable;
                    # export errors are not (they bypass the content filter).
                    if "export" in str(e).lower() or "exporter" in str(e).lower():
                        pytest.fail(f"Span export error during {tool_name}: {e}")
        finally:
            logging.getLogger().removeHandler(cap)

        # No ERROR-level span-export logs during the run
        assert not export_errors, (
            f"Unexpected span-export ERROR logs: {[r.getMessage() for r in export_errors]}"
        )

    finally:
        os.environ.update(env_backup)
