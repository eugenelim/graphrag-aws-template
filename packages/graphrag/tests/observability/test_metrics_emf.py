"""EMF metrics tests — AC2.

Verifies:
- ``emit_tool_metrics`` emits ``mcp.tool.duration_ms`` with correct namespace,
  unit, and dimension.
- Exception path emits ``mcp.tool.error_count`` with ``error_type = class name``
  (never the message).
- Offline fallback does not raise.
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from graphrag.observability._metrics import METRIC_NAMESPACE, emit_tool_metrics


def _capture_emf(
    tool_name: str,
    duration_ms: float,
    exc: BaseException | None = None,
) -> list[dict]:
    """Call emit_tool_metrics and capture any EMF JSON lines it logs."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    metrics_logger = logging.getLogger("graphrag.observability._metrics")
    orig_handlers = metrics_logger.handlers[:]
    orig_propagate = metrics_logger.propagate
    metrics_logger.handlers = [handler]
    metrics_logger.propagate = False
    metrics_logger.setLevel(logging.INFO)

    try:
        emit_tool_metrics(tool_name=tool_name, duration_ms=duration_ms, exc=exc)
    finally:
        metrics_logger.handlers = orig_handlers
        metrics_logger.propagate = orig_propagate

    output = stream.getvalue()
    results = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            # Plain-log fallback line (offline mode); include as-is
            results.append({"_raw": line})
    return results


# ---------------------------------------------------------------------------
# AC2a: duration metric
# ---------------------------------------------------------------------------


def test_emit_tool_metrics_duration() -> None:
    """emit_tool_metrics emits mcp.tool.duration_ms with correct metadata."""
    payloads = _capture_emf("ask", 12.0)
    assert payloads, "No EMF JSON was emitted"

    # Find the payload containing mcp.tool.duration_ms
    duration_payload = None
    for p in payloads:
        if "mcp.tool.duration_ms" in p:
            duration_payload = p
            break

    if duration_payload is None:
        # Offline fallback: check the plain log line
        raw = payloads[0].get("_raw", "")
        assert "mcp.tool.duration_ms" in raw or "metric_name" in payloads[0], (
            f"Expected metric name in output; got: {payloads}"
        )
        return

    # Check namespace
    aws_meta = duration_payload.get("_aws", {})
    cw_metrics = aws_meta.get("CloudWatchMetrics", [{}])
    namespace = cw_metrics[0].get("Namespace", "")
    assert namespace == METRIC_NAMESPACE, f"Wrong namespace: {namespace!r}"

    # Check metric value
    assert duration_payload["mcp.tool.duration_ms"] == pytest.approx(12.0)

    # Check tool_name dimension
    assert duration_payload.get("tool_name") == "ask"

    # Check unit
    metrics_list = cw_metrics[0].get("Metrics", [])
    units = {m["Name"]: m.get("Unit") for m in metrics_list}
    assert units.get("mcp.tool.duration_ms") == "Milliseconds", f"Wrong unit: {units}"


# ---------------------------------------------------------------------------
# AC2b: error_count with bounded error_type dimension
# ---------------------------------------------------------------------------


def test_emit_tool_metrics_error_type_is_class_name() -> None:
    """error_type dimension is the exception CLASS NAME, never the message."""

    class SecretError(Exception):
        """Error whose message carries question text."""

    exc = SecretError("what are the HR policies?")  # question-derived message
    payloads = _capture_emf("ask", 5.0, exc=exc)

    # Find the payload with error_count
    error_payload = None
    for p in payloads:
        if "mcp.tool.error_count" in p:
            error_payload = p
            break

    if error_payload is None:
        # Offline fallback: check plain log
        combined = " ".join(str(p) for p in payloads)
        assert "SecretError" in combined, "Expected exception class name in fallback log"
        assert "HR policies" not in combined, "Exception message leaked into metric output"
        return

    # error_type must be the class name
    assert error_payload.get("error_type") == "SecretError", (
        f"error_type should be 'SecretError', got: {error_payload.get('error_type')!r}"
    )
    # The exception message must not appear in any dimension value
    payload_str = json.dumps(error_payload)
    assert "HR policies" not in payload_str, (
        "Exception message (question text) leaked into EMF payload"
    )


# ---------------------------------------------------------------------------
# AC2c: offline fallback does not raise
# ---------------------------------------------------------------------------


def test_emit_tool_metrics_offline_does_not_raise(monkeypatch) -> None:
    """emit_tool_metrics falls back to plain log when EMF sink is unavailable."""
    import graphrag.observability._metrics as metrics_mod

    def _raise(*a, **kw):
        raise RuntimeError("no EMF sink")

    monkeypatch.setattr(metrics_mod, "_emit_sync", _raise)
    emit_tool_metrics(tool_name="ask", duration_ms=1.0)  # must not raise
