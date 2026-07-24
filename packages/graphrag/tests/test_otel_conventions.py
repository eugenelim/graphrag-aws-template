"""ADR-0015 content-capture conventions test — the canonical location.

ADR-0015 item 6 names this file as the programmatic content-capture gate:
``packages/graphrag/tests/test_otel_conventions.py``.

This test constructs a span, sets both ``DENY_SET`` and ``AUTO_CAPTURE_KEYS``
attributes, and asserts none of those keys are present in the exported span —
confirming that ``ContentCaptureFilterExporter`` is the load-bearing runtime
control on module-owned export paths (tests, local, ingestion).

No AWS credentials required.  See also ``tests/observability/`` for
per-module tests.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from graphrag.observability import (
    AUTO_CAPTURE_KEYS,
    DENY_SET,
    ContentCaptureFilterExporter,
)


@pytest.fixture()
def filtered_exporter() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Return a (provider, inner_exporter) pair with the content filter wired."""
    inner = InMemorySpanExporter()
    wrapped = ContentCaptureFilterExporter(inner)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(wrapped))
    return provider, inner


def _make_span(
    provider: TracerProvider,
    inner: InMemorySpanExporter,
    attrs: dict,
    event_attrs: dict | None = None,
) -> dict:
    """Create a span with *attrs*, force-flush, return the exported attributes."""
    inner.clear()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("test-span", kind=SpanKind.CLIENT) as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)
        if event_attrs:
            span.add_event("test-event", event_attrs)
    provider.force_flush()
    spans = inner.get_finished_spans()
    assert len(spans) == 1
    return spans[0]


# ---------------------------------------------------------------------------
# AC1: all DENY_SET keys are stripped
# ---------------------------------------------------------------------------


def test_all_deny_set_keys_stripped(
    filtered_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """A span with all five DENY_SET keys exports with none of them present."""
    provider, inner = filtered_exporter
    attrs = {k: f"secret-{k}" for k in DENY_SET}
    attrs["tool_name"] = "ask"  # benign
    span = _make_span(provider, inner, attrs)

    exported_attrs = dict(span.attributes or {})
    for key in DENY_SET:
        assert key not in exported_attrs, f"DENY_SET key {key!r} leaked into export"
    assert "tool_name" in exported_attrs, "benign key 'tool_name' was stripped"


def test_each_deny_set_key_individually(
    filtered_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Each DENY_SET key individually is stripped; benign key survives."""
    provider, inner = filtered_exporter
    for key in DENY_SET:
        span = _make_span(
            provider, inner, {key: "secret", "tool_name": "ask", "db.system": "neptune"}
        )
        exported_attrs = dict(span.attributes or {})
        assert key not in exported_attrs, f"DENY_SET key {key!r} not stripped"
        assert "tool_name" in exported_attrs
        assert "db.system" in exported_attrs


# ---------------------------------------------------------------------------
# AC1: all AUTO_CAPTURE_KEYS are stripped
# ---------------------------------------------------------------------------


def test_all_auto_capture_keys_stripped(
    filtered_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """AUTO_CAPTURE_KEYS (auto-instrumentation content keys) are stripped."""
    provider, inner = filtered_exporter
    attrs = {k: f"auto-content-{k}" for k in AUTO_CAPTURE_KEYS}
    attrs["db.system"] = "neptune"  # benign auto key
    attrs["http.status_code"] = 200  # benign auto key (int)
    span = _make_span(provider, inner, attrs)

    exported_attrs = dict(span.attributes or {})
    for key in AUTO_CAPTURE_KEYS:
        assert key not in exported_attrs, f"AUTO_CAPTURE_KEY {key!r} leaked"
    assert "db.system" in exported_attrs, "benign 'db.system' was stripped"
    assert "http.status_code" in exported_attrs, "benign 'http.status_code' was stripped"


def test_each_auto_capture_key_individually(
    filtered_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Each AUTO_CAPTURE_KEY individually is stripped; benign keys survive."""
    provider, inner = filtered_exporter
    for key in AUTO_CAPTURE_KEYS:
        span = _make_span(
            provider,
            inner,
            {key: "auto-content", "db.system": "neptune", "http.status_code": 200},
        )
        exported_attrs = dict(span.attributes or {})
        assert key not in exported_attrs, f"AUTO_CAPTURE_KEY {key!r} not stripped"
        assert "db.system" in exported_attrs
        assert "http.status_code" in exported_attrs


# ---------------------------------------------------------------------------
# Span-event attribute filtering (Blocker 3 fix)
# ---------------------------------------------------------------------------


def test_deny_set_keys_stripped_from_span_events(
    filtered_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """DENY_SET keys in span-event attributes are also stripped."""
    provider, inner = filtered_exporter
    span = _make_span(
        provider,
        inner,
        {"tool_name": "ask"},
        event_attrs={"question.text": "what are the HR policies?", "safe_key": "safe"},
    )
    exported_events = span.events
    assert len(exported_events) == 1
    event_attrs = dict(exported_events[0].attributes or {})
    assert "question.text" not in event_attrs, "DENY_SET key in event was not stripped"
    assert "safe_key" in event_attrs, "benign event key was stripped"
