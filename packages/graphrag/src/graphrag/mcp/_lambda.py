"""AWS Lambda entrypoint for the graphrag MCP tool server.

Mangum wraps FastMCP's ASGI app for the Lambda execution model.
``lifespan="off"`` skips ASGI startup/shutdown hooks so all store
initialisation must happen in the module body (before ``Mangum(...)``),
ensuring Lambda cold-start wires the stores correctly.

Production routing:
- When ``NEPTUNE_SPARQL_ENDPOINT`` is set → ``init_production()`` (NeptuneSparqlStore +
  OpenSearch/MemoryVector fallback + Bedrock client).
- When ``NEPTUNE_SPARQL_ENDPOINT`` is absent → ``init_mock()`` with a WARNING log.
  The mock uses the fixture corpus; it will fail if the fixture is not bundled.

Handler: ``graphrag.mcp._lambda.handler``
"""

from __future__ import annotations

import logging
import os

from mangum import Mangum

from graphrag.mcp._tools import mcp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Store initialisation — runs in the module body during Lambda cold-start.
# ``lifespan="off"`` means Mangum skips FastMCP's ASGI lifespan protocol,
# so startup hooks never fire.  All wiring must be done here.
# ---------------------------------------------------------------------------

if os.environ.get("NEPTUNE_SPARQL_ENDPOINT"):
    # Production mode: real Neptune SPARQL + OpenSearch/Bedrock backends.
    try:
        from graphrag.mcp._production import init_production

        init_production()
        logger.info("graphrag.mcp Lambda handler initialised (production mode)")
    except Exception as exc:  # noqa: BLE001 — surface any init failure clearly
        logger.error(
            "graphrag.mcp production store init failed: %s. "
            "Tool calls will fail until the store is wired.",
            exc,
        )
else:
    # Mock mode: fixture corpus backed in-process store (WARNING — not for production).
    logger.warning(
        "NEPTUNE_SPARQL_ENDPOINT not set — initialising in mock mode. "
        "Set NEPTUNE_SPARQL_ENDPOINT to enable the production Neptune backend."
    )
    try:
        from graphrag.mcp._mock import init_mock

        init_mock()
        logger.info("graphrag.mcp Lambda handler initialised (mock mode)")
    except FileNotFoundError as exc:
        # Fixture corpus not bundled — occurs in an installed package / Lambda zip
        # without the tests/fixtures directory.
        logger.warning(
            "graphrag.mcp mock store not initialised — fixture corpus not found: %s. "
            "Tool calls will fail until a production store backend is wired.",
            exc,
        )

# The Mangum-wrapped ASGI handler registered with Lambda.
handler = Mangum(mcp.streamable_http_app(), lifespan="off")
