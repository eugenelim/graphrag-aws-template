"""graphrag.mcp — MCP tool server with 6 typed tools + offline mock.

Exports the ``FastMCP`` instance so importers can reference the shared
``mcp`` object without importing from the private ``_tools`` module.

OTEL observability (ADR-0015) is bootstrapped here — once at module import —
so tracing and structured logging are active before any tool is served.
In Lambda, ``configure_observability`` detects the ADOT-installed provider
and skips installing a second provider while still configuring logging.
In offline/test contexts it installs a filtered ``TracerProvider`` with no
AWS credentials or OTLP endpoint required.
"""

from graphrag.mcp._tools import mcp
from graphrag.observability import configure_observability

configure_observability("graphrag-mcp")

__all__ = ["mcp"]
