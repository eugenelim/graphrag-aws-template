"""OTEL bootstrap — ``configure_observability(service_name)``.

Behaviour by environment
------------------------
*In Lambda* — the ADOT layer's ``AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument``
installs the global ``TracerProvider`` and OTLP export pipeline *before* the
handler module is imported.  A subsequent ``set_tracer_provider()`` is a no-op
(OTEL forbids overriding an installed provider), so this module skips provider
installation there and relies on the ADOT collector attribute-processor
(``infra-tf/mcp-otel-lambda``) as the content-capture enforcement point.

*Outside Lambda* (tests, local dev, ingestion) — this module installs a
``TracerProvider`` with every exporter wrapped in
``ContentCaptureFilterExporter``.  No exporter registered here is left
un-wrapped.

Offline safety
--------------
``configure_observability`` never raises when no AWS credentials are set and no
OTLP endpoint is reachable.  The SDK's ``BatchSpanProcessor`` retries then drops
silently; startup always succeeds.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from graphrag.observability._content_filter import ContentCaptureFilterExporter
from graphrag.observability._logging import configure_json_logging

logger = logging.getLogger(__name__)

_CONFIGURED = False


def configure_observability(service_name: str) -> None:
    """Configure structured JSON logging and OTEL tracing for *service_name*.

    Always installs structured JSON logging (``configure_json_logging``).  If no
    non-default ``TracerProvider`` is installed, also installs one with a
    ``ContentCaptureFilterExporter``-wrapped exporter.

    Safe to call multiple times; subsequent calls after the first are no-ops
    for the provider setup (logging is idempotent via ``configure_json_logging``).
    Raises no exception when the OTLP endpoint is unreachable or AWS credentials
    are absent.

    In Lambda the ADOT-installed provider is already active before this runs;
    the function skips provider installation (the ADOT collector processor is
    the enforcement point there) while still configuring the log format.

    Parameters
    ----------
    service_name:
        Value of the ``service.name`` resource attribute (e.g. ``"graphrag-mcp"``).
    """
    global _CONFIGURED  # noqa: PLW0603

    # Detect whether ADOT (or any prior call) has already installed a provider.
    # opentelemetry-api's ProxyTracerProvider is the default until someone calls
    # set_tracer_provider(); once replaced it is no longer the default type.
    current = trace.get_tracer_provider()
    already_configured = not isinstance(
        current,
        trace.ProxyTracerProvider,
    )

    # Always configure structured JSON logging, regardless of provider state.
    # This handles the ingestion-task path (logging-only, no tracer) and the Lambda
    # path (ADOT owns the provider, but this module owns the log format).
    configure_json_logging()

    if already_configured:
        logger.debug(
            "configure_observability: non-default TracerProvider already installed "
            "(ADOT or prior call); skipping provider setup",
            extra={"service_name": service_name},
        )
        _CONFIGURED = True
        return

    if _CONFIGURED:
        logger.debug("configure_observability: already called; skipping")
        return

    try:
        _install_provider(service_name)
    except Exception:  # noqa: BLE001
        logger.warning(
            "configure_observability: provider setup failed (offline/no-op mode)",
            exc_info=True,
        )
    _CONFIGURED = True


def _install_provider(service_name: str) -> None:
    """Install a filtered ``TracerProvider`` for non-ADOT contexts."""
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # In test / local contexts use a no-op exporter by default (the test suite
    # supplies its own InMemorySpanExporter wrapped in ContentCaptureFilterExporter).
    # We register a ConsoleSpanExporter in DEBUG environments so local runs emit spans.
    debug_mode = logger.isEnabledFor(logging.DEBUG)
    if debug_mode:
        console_exporter = ContentCaptureFilterExporter(ConsoleSpanExporter())
        provider.add_span_processor(SimpleSpanProcessor(console_exporter))
    else:
        # Default batch processor: spans are processed but console-export is low-
        # overhead at INFO level (tests supply their own in-memory processors;
        # ingestion uses this path for logging-only and the console sink is silent
        # when no handler is reading stdout).
        console_exporter = ContentCaptureFilterExporter(ConsoleSpanExporter())
        provider.add_span_processor(BatchSpanProcessor(console_exporter))

    trace.set_tracer_provider(provider)
    logger.debug(
        "configure_observability: TracerProvider installed",
        extra={"service_name": service_name},
    )


def _reset_for_testing() -> None:  # pragma: no cover
    """Reset the configured flag and global TracerProvider — **for tests only**.

    Allows tests to call ``configure_observability`` more than once in the same
    process and install their own ``TracerProvider``.  Do not call in production
    code.

    Internals: opentelemetry-api uses a ``Once`` guard (``_TRACER_PROVIDER_SET_ONCE``)
    that prevents overriding an installed provider.  This function resets it so
    the next ``set_tracer_provider`` call succeeds.
    """
    global _CONFIGURED  # noqa: PLW0603
    _CONFIGURED = False
    try:
        import opentelemetry.trace as _trace_mod

        _trace_mod._TRACER_PROVIDER = None
        _trace_mod._PROXY_TRACER_PROVIDER = None  # type: ignore[assignment]
        # Reset the set-once guard so the next set_tracer_provider() succeeds
        _once = getattr(_trace_mod, "_TRACER_PROVIDER_SET_ONCE", None)
        if _once is not None and hasattr(_once, "_done"):
            _once._done = False
    except AttributeError:
        pass  # SDK internals changed; best-effort reset
