"""AWS Lambda entrypoint for the graphrag MCP tool server.

Mangum wraps FastMCP's ASGI app for the Lambda execution model.
``lifespan="off"`` skips ASGI startup/shutdown hooks so all store
initialisation must happen in the module body (before ``Mangum(...)``),
ensuring Lambda cold-start wires the stores correctly.

The production store backends (Neptune SPARQL + OpenSearch) are not yet
wired in ini-002 — the mock server (``_mock.py``) is the current runtime.
The Lambda entrypoint wires the mock store for the interim until the full
``mcp-tool-server`` spec implementation lands.

**Fixture path caveat (interim):** ``init_mock()`` loads the fixture corpus
from ``packages/graphrag/tests/fixtures/biz_ops_fixture.ttl`` relative to the
package source tree.  That path exists in the repo checkout but NOT in an
installed package or a stripped Lambda bundle.  If the fixture is absent,
initialisation logs a warning and the handler returns an error response for
every tool call rather than crashing the entire Lambda cold-start.

Handler: ``graphrag.mcp._lambda.handler``
"""

from __future__ import annotations

import logging

from mangum import Mangum

from graphrag.mcp._tools import mcp

logger = logging.getLogger(__name__)

# Initialise the mock store in the module body so it runs during Lambda
# cold-start (before any tool call arrives).  ``lifespan="off"`` means
# Mangum skips FastMCP's ASGI lifespan protocol, so startup hooks never
# fire — all initialisation must be here.
try:
    from graphrag.mcp._mock import init_mock

    init_mock()
    logger.info("graphrag.mcp Lambda handler initialised (mock mode)")
except FileNotFoundError as exc:
    # Fixture corpus not bundled — occurs in an installed package / Lambda zip
    # without the tests/fixtures directory.  Log the warning and let individual
    # tool calls fail with a RuntimeError from _require_store() rather than
    # crashing the entire cold-start.
    logger.warning(
        "graphrag.mcp mock store not initialised — fixture corpus not found: %s. "
        "Tool calls will fail until a production store backend is wired.",
        exc,
    )

# The Mangum-wrapped ASGI handler registered with Lambda.
handler = Mangum(mcp.streamable_http_app(), lifespan="off")
