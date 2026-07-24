"""EMF metrics helper — ADR-0015 item 3.

``emit_tool_metrics`` wraps ``aws_embedded_metrics`` to emit the five
ADR-0015 metrics with their exact names, types, and dimensions.

Metric namespace: ``"graphrag/mcp"`` (pinned; changing this breaks CloudWatch
Logs Insights queries, EMF metric extraction, and any future alarms).

Offline safety
--------------
``aws_embedded_metrics`` auto-detects the environment (Lambda vs. local).
When no EMF sink is configured the library flushes to stdout; this function
catches import failures and falls back to a plain log line so the offline test
suite never raises.

Content-capture
---------------
``error_type`` is always the **exception class name** (``type(exc).__name__``),
never ``str(exc)`` — exception messages can carry question-derived text.  This
is a bounded-enum dimension: no content leak, no cardinality explosion.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

METRIC_NAMESPACE = "graphrag/mcp"


def emit_tool_metrics(
    *,
    tool_name: str,
    duration_ms: float,
    exc: BaseException | None = None,
) -> None:
    """Emit ``mcp.tool.duration_ms`` (and optionally ``mcp.tool.error_count``).

    Parameters
    ----------
    tool_name:
        Name of the MCP tool (e.g. ``"ask"``).
    duration_ms:
        Tool wall-clock duration in milliseconds.
    exc:
        If the tool raised, pass the exception here.  Emits
        ``mcp.tool.error_count`` with ``error_type = type(exc).__name__`` —
        the class name only, never the message.
    """
    try:
        _emit_sync(tool_name=tool_name, duration_ms=duration_ms, exc=exc)
    except Exception:  # noqa: BLE001
        # Offline / no-sink fallback: log a plain line so the test suite can
        # assert on the metric without requiring a real EMF environment.
        logger.info(
            "metric",
            extra={
                "metric_name": "mcp.tool.duration_ms",
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                **({"error_type": type(exc).__name__} if exc is not None else {}),
            },
        )


def _emit_sync(
    *,
    tool_name: str,
    duration_ms: float,
    exc: BaseException | None,
) -> None:
    """Synchronous emit path using MetricsContext directly (no async needed)."""
    # Defer aws_embedded_metrics import so the module loads without it installed
    from aws_embedded_metrics.logger.metrics_context import MetricsContext  # noqa: PLC0415
    from aws_embedded_metrics.serializers.log_serializer import LogSerializer  # noqa: PLC0415

    ctx = MetricsContext()
    ctx.namespace = METRIC_NAMESPACE
    ctx.put_dimensions({"tool_name": tool_name})
    ctx.put_metric("mcp.tool.duration_ms", duration_ms, "Milliseconds")

    if exc is not None:
        # error_type is the exception class name — bounded enum, no content leak
        ctx.put_dimensions({"tool_name": tool_name, "error_type": type(exc).__name__})
        ctx.put_metric("mcp.tool.error_count", 1, "Count")

    serializer = LogSerializer()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lines = serializer.serialize(ctx)

    for line in lines:
        # EMF output goes to stdout (Lambda log stream); log at INFO for local sinks
        logger.info(line)


def emit_retrieval_metrics(
    *,
    store: str,
    strategy: str,
    duration_ms: float,
) -> None:
    """Emit ``retrieval.<store>.duration_ms`` (Histogram, ``strategy`` dimension).

    Parameters
    ----------
    store:
        ``"neptune"`` or ``"opensearch"``.
    strategy:
        Strategy name (e.g. ``"hybrid"``, ``"vector"``, ``"sparql"``).
    duration_ms:
        Retrieval leg duration in milliseconds.
    """
    metric_name = f"retrieval.{store}.duration_ms"
    try:
        from aws_embedded_metrics.logger.metrics_context import MetricsContext  # noqa: PLC0415
        from aws_embedded_metrics.serializers.log_serializer import LogSerializer  # noqa: PLC0415

        ctx = MetricsContext()
        ctx.namespace = METRIC_NAMESPACE
        ctx.put_dimensions({"strategy": strategy})
        ctx.put_metric(metric_name, duration_ms, "Milliseconds")

        serializer = LogSerializer()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lines = serializer.serialize(ctx)
        for line in lines:
            logger.info(line)
    except Exception:  # noqa: BLE001
        logger.info(
            "metric",
            extra={"metric_name": metric_name, "strategy": strategy, "duration_ms": duration_ms},
        )


def emit_routing_fraction(*, bedrock_fraction: float) -> None:
    """Emit ``routing.decided_by.bedrock.fraction`` (Gauge, no dimensions).

    Parameters
    ----------
    bedrock_fraction:
        Fraction of recent routing decisions made by Bedrock (0.0–1.0).
    """
    try:
        from aws_embedded_metrics.logger.metrics_context import MetricsContext  # noqa: PLC0415
        from aws_embedded_metrics.serializers.log_serializer import LogSerializer  # noqa: PLC0415

        ctx = MetricsContext()
        ctx.namespace = METRIC_NAMESPACE
        ctx.reset_dimensions(use_default=False)
        ctx.put_metric("routing.decided_by.bedrock.fraction", bedrock_fraction, "None")

        serializer = LogSerializer()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lines = serializer.serialize(ctx)
        for line in lines:
            logger.info(line)
    except Exception:  # noqa: BLE001
        logger.info(
            "metric",
            extra={
                "metric_name": "routing.decided_by.bedrock.fraction",
                "bedrock_fraction": bedrock_fraction,
            },
        )
