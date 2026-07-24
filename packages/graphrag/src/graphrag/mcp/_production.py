"""Production store initialization for the graphrag MCP tool server.

Reads environment variables to construct the live Neptune SPARQL + OpenSearch/
MemoryVector + Bedrock backend and wires it into ``graphrag.mcp._tools._store``.

Usage::

    # In Lambda cold-start (called by _lambda.py):
    from graphrag.mcp._production import init_production
    init_production()

Design notes:
- ``boto3`` is imported INSIDE ``init_production()`` — never at module level.
  This keeps ``_production.py`` importable in offline environments (e.g., ruff
  and mypy runs) without requiring AWS SDK credentials or connectivity.
- ``NEPTUNE_SPARQL_ENDPOINT`` is the only required env var.  Without it,
  ``init_production()`` raises ``RuntimeError`` with a clear message.
- ``OPENSEARCH_ENDPOINT`` is optional.  When absent, a ``MemoryVectorStore``
  is substituted (empty; kNN results will be empty until documents are indexed).
- ``AWS_REGION`` defaults to ``us-east-1`` when not set.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import graphrag.mcp._tools as _tools
from graphrag.embed import HashEmbedder
from graphrag.store.neptune_sparql import NeptuneSparqlStore

logger = logging.getLogger(__name__)


def init_production() -> None:
    """Initialize production store backends from environment variables.

    Required environment variables:
    - ``NEPTUNE_SPARQL_ENDPOINT``: HTTPS URL of the Neptune analytics SPARQL endpoint.

    Optional environment variables:
    - ``OPENSEARCH_ENDPOINT``: HTTPS URL of the OpenSearch domain for kNN vector search.
      Falls back to an empty in-process ``MemoryVectorStore`` when absent.
    - ``AWS_REGION``: AWS region for SigV4 signing and Bedrock client.  Defaults to
      ``us-east-1``.

    Raises:
        RuntimeError: if ``NEPTUNE_SPARQL_ENDPOINT`` is not set.
    """
    neptune_endpoint = os.environ.get("NEPTUNE_SPARQL_ENDPOINT")
    if not neptune_endpoint:
        raise RuntimeError(
            "NEPTUNE_SPARQL_ENDPOINT environment variable is not set. "
            "Cannot initialize production store.  "
            "Set it to the https:// URL of your Neptune analytics SPARQL endpoint."
        )

    import boto3  # deferred — must not be at module level (offline environments)

    region = os.environ.get("AWS_REGION", "us-east-1")

    neptune = NeptuneSparqlStore(neptune_endpoint, region)
    logger.info(
        "Neptune SPARQL store configured",
        extra={"endpoint": neptune_endpoint, "region": region},
    )

    opensearch_endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
    vector: Any
    if opensearch_endpoint:
        from graphrag.store.opensearch import OpenSearchVectorStore

        vector = OpenSearchVectorStore(opensearch_endpoint, region)
        logger.info(
            "OpenSearch vector store configured",
            extra={"endpoint": opensearch_endpoint},
        )
    else:
        from graphrag.store.vector_memory import MemoryVectorStore

        vector = MemoryVectorStore()
        logger.warning(
            "OPENSEARCH_ENDPOINT not set — falling back to in-process MemoryVectorStore. "
            "Vector search results will be empty until documents are indexed."
        )

    bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    logger.info("Bedrock runtime client constructed", extra={"region": region})

    embedder: Any = HashEmbedder()

    _tools._store = _tools._ProductionStore(
        neptune=neptune,
        vector=vector,
        bedrock_client=bedrock_client,
        embedder=embedder,
    )

    logger.info(
        "Production store initialised",
        extra={
            "vector_backend": type(vector).__name__,
            "neptune_endpoint": neptune_endpoint,
            "region": region,
        },
    )
