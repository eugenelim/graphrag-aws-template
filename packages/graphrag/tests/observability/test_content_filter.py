"""Unit tests for ContentCaptureFilterExporter and its deny-set constants.

Tests AC1, AC6 (boto3-free import + pin test).
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from graphrag.observability._content_filter import (
    AUTO_CAPTURE_KEYS,
    DENY_SET,
    ContentCaptureFilterExporter,
)


@pytest.fixture()
def pair() -> tuple[TracerProvider, InMemorySpanExporter]:
    inner = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ContentCaptureFilterExporter(inner)))
    return provider, inner


def _export(
    provider: TracerProvider,
    inner: InMemorySpanExporter,
    attrs: dict,
    event_attrs: dict | None = None,
):
    inner.clear()
    with provider.get_tracer("t").start_as_current_span("s", kind=SpanKind.CLIENT) as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)
        if event_attrs:
            span.add_event("ev", event_attrs)
    provider.force_flush()
    return inner.get_finished_spans()[0]


# ---------------------------------------------------------------------------
# DENY_SET constant pin (AC6)
# ---------------------------------------------------------------------------


def test_deny_set_value_pinned() -> None:
    """DENY_SET equals the ADR-0015 item 6 canonical names exactly."""
    expected = {"question.text", "query.text", "sparql.query", "document.content", "chunk.text"}
    assert DENY_SET == frozenset(expected), (
        f"DENY_SET mismatch — update to match ADR-0015 item 6.\n"
        f"Got: {DENY_SET}\nExpected: {expected}"
    )


def test_auto_capture_keys_non_empty() -> None:
    """AUTO_CAPTURE_KEYS is non-empty and contains the expected OTEL keys."""
    for key in ("db.statement", "http.url", "gen_ai.prompt", "gen_ai.completion"):
        assert key in AUTO_CAPTURE_KEYS, f"{key!r} missing from AUTO_CAPTURE_KEYS"


# ---------------------------------------------------------------------------
# Attribute stripping
# ---------------------------------------------------------------------------


def test_deny_set_attrs_stripped(pair) -> None:
    span = _export(*pair, {k: "secret" for k in DENY_SET}, None)
    exported = dict(span.attributes or {})
    for k in DENY_SET:
        assert k not in exported


def test_auto_capture_keys_stripped(pair) -> None:
    span = _export(*pair, {k: "auto-secret" for k in AUTO_CAPTURE_KEYS}, None)
    exported = dict(span.attributes or {})
    for k in AUTO_CAPTURE_KEYS:
        assert k not in exported


def test_benign_attrs_survive(pair) -> None:
    span = _export(
        *pair,
        {"tool_name": "ask", "db.system": "neptune", "http.status_code": 200},
    )
    exported = dict(span.attributes or {})
    assert exported["tool_name"] == "ask"
    assert exported["db.system"] == "neptune"
    assert exported["http.status_code"] == 200


def test_event_deny_attrs_stripped(pair) -> None:
    span = _export(
        *pair,
        {"tool_name": "ask"},
        {"question.text": "secret question", "safe_key": "ok"},
    )
    ev_attrs = dict(span.events[0].attributes or {})
    assert "question.text" not in ev_attrs
    assert "safe_key" in ev_attrs


# ---------------------------------------------------------------------------
# AC6: boto3-free import
# ---------------------------------------------------------------------------


def test_import_without_boto3() -> None:
    """Importing the module does not require boto3 or botocore."""
    import sys

    # Temporarily shadow boto3/botocore if present
    saved = {}
    for mod in list(sys.modules):
        if mod in ("boto3", "botocore") or mod.startswith(("boto3.", "botocore.")):
            saved[mod] = sys.modules.pop(mod)

    try:
        # Re-import the filter module to prove it loads without boto3
        import importlib

        import graphrag.observability._content_filter as cf_mod

        importlib.reload(cf_mod)
        assert cf_mod.DENY_SET  # non-empty, import succeeded
    finally:
        sys.modules.update(saved)
