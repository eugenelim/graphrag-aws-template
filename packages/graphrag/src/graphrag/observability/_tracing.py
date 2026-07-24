"""Span vocabulary and ``traced_leg`` context manager — ADR-0015 item 5.

Canonical span names and ``SpanKind`` values for the ``ask`` trace:

=========================  =================  ========
Span name                  SpanKind           Notes
=========================  =================  ========
``mcp.ask``                ``SERVER``         Lambda handler root (auto by ADOT)
``routing.rule_router``    ``INTERNAL``       manual
``routing.bedrock_router`` ``INTERNAL``       manual (fires only on ambiguous route)
``retrieval.<strategy>``   ``CLIENT``         manual; name = ``retrieval.<strategy>``
=========================  =================  ========

Usage::

    from graphrag.observability import traced_leg

    with traced_leg("retrieval", strategy="hybrid") as span:
        span.set_attribute("result_count", len(results))
        ...  # Neptune + OpenSearch calls happen here

    with traced_leg("routing.rule_router") as span:
        ...

The helper is the **contract** between this module and the router/orchestrator.
Adopting it in ``spec-multi-strategy-routing`` is a seam owned by that spec;
this spec verifies the helper directly (AC4) so the contract holds regardless.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical span-kind mapping (ADR-0015 item 5)
# ---------------------------------------------------------------------------

#: Mapping from *prefix* span name to ``SpanKind``.
#: ``retrieval.*`` spans use ``CLIENT``; routing spans use ``INTERNAL``.
#: ``mcp.ask`` is the Lambda handler root — auto-created by ADOT, listed here
#: for documentation.
SPAN_KINDS: dict[str, SpanKind] = {
    "mcp.ask": SpanKind.SERVER,
    "routing.rule_router": SpanKind.INTERNAL,
    "routing.bedrock_router": SpanKind.INTERNAL,
    "retrieval": SpanKind.CLIENT,  # name formatted as retrieval.<strategy>
}


def _resolve_kind(name: str) -> SpanKind:
    """Return the ``SpanKind`` for *name*, defaulting to ``INTERNAL``."""
    if name in SPAN_KINDS:
        return SPAN_KINDS[name]
    # Handle prefixes: "retrieval.hybrid" → "retrieval" → CLIENT
    prefix = name.split(".")[0]
    return SPAN_KINDS.get(prefix, SpanKind.INTERNAL)


# ---------------------------------------------------------------------------
# Context-manager helper
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def traced_leg(
    name: str,
    *,
    strategy: str | None = None,
    **attrs: Any,
) -> Iterator[Span]:
    """Open a manual OTEL span for a routing or retrieval leg.

    The span name is ``name`` when ``strategy`` is ``None``, or
    ``f"{name}.{strategy}"`` when ``strategy`` is provided.  The ``SpanKind``
    is resolved from :data:`SPAN_KINDS`.

    All *attrs* are set on the span unconditionally; the ``ContentCaptureFilterExporter``
    strips any ``DENY_SET ∪ AUTO_CAPTURE_KEYS`` keys at export time as the runtime backstop.
    Do not pass ``DENY_SET`` keys here — they will be stripped before export.

    Parameters
    ----------
    name:
        Span name or prefix (e.g. ``"retrieval"``, ``"routing.rule_router"``).
    strategy:
        Optional strategy suffix appended as ``name.strategy`` (e.g.
        ``"hybrid"`` → ``"retrieval.hybrid"``).
    **attrs:
        Arbitrary span attributes.  Do not pass ``DENY_SET`` keys here.

    Yields
    ------
    Span
        The live OTEL span.  Set additional attributes inside the ``with``
        block; the span is ended automatically on exit.
    """
    full_name = f"{name}.{strategy}" if strategy is not None else name
    kind = _resolve_kind(name)
    tracer = trace.get_tracer(__name__)
    # record_exception=False / set_status_on_exception=False: OTEL's defaults
    # auto-attach exception.message + exception.stacktrace to a span-event and
    # copy str(exc) into the status description — both can carry question-derived
    # text (content-capture violation, ADR-0015).  We record a content-free error
    # status manually (error.type = class name only, a bounded enum).
    with tracer.start_as_current_span(
        full_name,
        kind=kind,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)
        try:
            yield span
        except Exception as exc:
            # Content-free error signal: class name only, never str(exc)
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", type(exc).__name__)
            raise
