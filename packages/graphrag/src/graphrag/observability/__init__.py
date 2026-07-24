"""graphrag.observability — OTEL instrumentation primitives for graphrag.mcp.

Public API
----------
``configure_observability(service_name)``
    Bootstrap the ``TracerProvider`` (non-ADOT contexts) and structured
    JSON logging.  Call once at module import.

``ContentCaptureFilterExporter``
    ``SpanExporter`` decorator; strips ``DENY_SET ∪ AUTO_CAPTURE_KEYS`` from
    span attributes *and* span-event attributes.

``DENY_SET``
    Frozenset of the five ADR-0015 item 6 canonical content-bearing keys.

``AUTO_CAPTURE_KEYS``
    Frozenset of OTEL semantic-convention keys ADOT auto-instrumentation can
    populate with request content.

``emit_tool_metrics(tool_name, duration_ms, exc=None)``
    Emit ``mcp.tool.duration_ms`` (and optionally ``mcp.tool.error_count``)
    via ``aws_embedded_metrics``.

``emit_retrieval_metrics(store, strategy, duration_ms)``
    Emit ``retrieval.<store>.duration_ms`` (Histogram, ``strategy`` dimension).

``emit_routing_fraction(bedrock_fraction)``
    Emit ``routing.decided_by.bedrock.fraction`` (Gauge, no dimensions).

``configure_json_logging()``
    Install ``pythonjsonlogger`` structured JSON formatter on the root logger.

``traced_leg(name, *, strategy=None, **attrs)``
    Context manager for manual routing/retrieval leg spans.

``SPAN_KINDS``
    Canonical span-name → ``SpanKind`` mapping (ADR-0015 item 5).

AWS-free core
-------------
``ContentCaptureFilterExporter``, ``DENY_SET``, ``AUTO_CAPTURE_KEYS``,
``configure_json_logging``, and ``traced_leg`` import without boto3/botocore.
Only ``_metrics.py`` and the OTLP branch of ``_bootstrap.py`` touch
AWS-adjacent libraries; none import boto3/botocore (AC6).
"""

from graphrag.observability._bootstrap import configure_observability
from graphrag.observability._content_filter import (
    AUTO_CAPTURE_KEYS,
    DENY_SET,
    ContentCaptureFilterExporter,
)
from graphrag.observability._logging import configure_json_logging
from graphrag.observability._metrics import (
    emit_retrieval_metrics,
    emit_routing_fraction,
    emit_tool_metrics,
)
from graphrag.observability._tracing import SPAN_KINDS, traced_leg

__all__ = [
    "AUTO_CAPTURE_KEYS",
    "ContentCaptureFilterExporter",
    "DENY_SET",
    "SPAN_KINDS",
    "configure_json_logging",
    "configure_observability",
    "emit_retrieval_metrics",
    "emit_routing_fraction",
    "emit_tool_metrics",
    "traced_leg",
]
