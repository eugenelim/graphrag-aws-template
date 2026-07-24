"""Content-capture filter exporter — ADR-0015 item 6.

``ContentCaptureFilterExporter`` is a ``SpanExporter`` decorator that strips
every attribute whose key is in ``DENY_SET ∪ AUTO_CAPTURE_KEYS`` from span
attributes **and** from span-event attributes before delegating to the wrapped
exporter.  It is the load-bearing runtime control on exporters this module
registers (tests, local/console, ingestion).

Design note — exporter, not processor
--------------------------------------
``SpanProcessor.on_end(span)`` receives an immutable ``ReadableSpan``.
Attribute stripping there is a silent no-op.  The correct mechanism is a
``SpanExporter`` decorator that builds a filtered ``ReadableSpan`` in
``export()`` — which is what this class does.  This matches the ADR-0015
Confirmation test exactly (which asserts on *exported* span data).

On the ADOT-owned Lambda pipeline this module does not register the exporter;
the ADOT collector's ``attributes`` delete processor (owned by
``infra-tf/mcp-otel-lambda``) is the enforcement point there.
"""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

# ---------------------------------------------------------------------------
# Canonical deny-sets (ADR-0015 item 6)
# ---------------------------------------------------------------------------

#: Keys that must never leave an exporter this module registers.
#: Pinned to the ADR-0015 item 6 canonical names; AC6 asserts this exact set.
#: Also pinned by ``spec-mcp-tool-server`` AC5's static linter against the same
#: ADR literals (the linter cannot import this constant because it ships first;
#: both are held equal by separate pin tests, not a shared import).
DENY_SET: frozenset[str] = frozenset(
    {
        "question.text",
        "query.text",
        "sparql.query",
        "document.content",
        "chunk.text",
    }
)

#: OTEL semantic-convention keys that ADOT's boto3/urllib3 auto-instrumentation
#: can populate with request content.  Config-level capture-off is the primary
#: control (infra-tf/mcp-otel-lambda AC7); this set is the in-process backstop.
#: Version-confirmed against opentelemetry-sdk 1.x at T1; re-confirm on upgrade.
AUTO_CAPTURE_KEYS: frozenset[str] = frozenset(
    {
        "db.statement",
        "db.query.text",
        "http.url",
        "url.full",
        "url.query",
        "http.request.body",
        "gen_ai.prompt",
        "gen_ai.completion",
    }
)

_STRIP: frozenset[str] = DENY_SET | AUTO_CAPTURE_KEYS


# ---------------------------------------------------------------------------
# Exporter decorator
# ---------------------------------------------------------------------------


class ContentCaptureFilterExporter(SpanExporter):
    """Strips ``DENY_SET ∪ AUTO_CAPTURE_KEYS`` from span and event attributes.

    Wraps any ``SpanExporter``; every exporter this module registers MUST be
    wrapped in this class.  Direct registration of an un-wrapped exporter from
    this module is a content-capture bypass.

    Parameters
    ----------
    inner:
        The underlying exporter to delegate to after filtering.
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Strip sensitive keys then delegate to the inner exporter."""
        return self._inner.export([self._strip(s) for s in spans])

    def shutdown(self) -> None:  # pragma: no branch
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip(span: ReadableSpan) -> ReadableSpan:
        """Return a new ``ReadableSpan`` with all ``_STRIP`` keys removed.

        Filters both top-level span attributes and per-event attributes.
        ``ReadableSpan`` is reconstructed via its public constructor (stable in
        the 1.x SDK series; version-confirmed at T1).
        """
        filtered_attrs = {k: v for k, v in (span.attributes or {}).items() if k not in _STRIP}
        filtered_events = [
            Event(
                name=ev.name,
                attributes={k: v for k, v in (ev.attributes or {}).items() if k not in _STRIP},
                timestamp=ev.timestamp,
            )
            for ev in (span.events or ())
        ]
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=filtered_attrs,
            events=filtered_events,
            links=span.links,
            kind=span.kind,
            instrumentation_scope=span.instrumentation_scope,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
        )
