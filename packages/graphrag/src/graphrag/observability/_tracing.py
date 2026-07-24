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
from opentelemetry.trace import Span, SpanKind

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

    Attributes in *attrs* that are NOT in ``DENY_SET`` are set on the span;
    the ``ContentCaptureFilterExporter`` strips any ``DENY_SET`` keys at export
    time as the runtime backstop.

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
    with tracer.start_as_current_span(full_name, kind=kind) as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)
        yield span
