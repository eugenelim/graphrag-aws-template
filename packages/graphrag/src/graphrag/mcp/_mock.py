"""Mock server initialisation for offline-first development and CI.

Sets up ``graphrag.mcp._tools._store`` with:
- ``rdflib.Dataset`` seeded from the fixture TriG corpus
- ``MemoryVectorStore`` populated with HashEmbedder vectors for all entities
- ``HashEmbedder`` for deterministic, offline-safe embedding

No boto3, no Bedrock, no Neptune — the mock starts without AWS credentials.

Usage::

    python -m graphrag.mcp --mock        # streamable-http on localhost:8000
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import graphrag.mcp._tools as _tools
from graphrag.chunk import Chunk
from graphrag.embed import HashEmbedder
from graphrag.store.vector_base import EmbeddedChunk
from graphrag.store.vector_memory import MemoryVectorStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------


# Resolve to tests/fixtures/ relative to the package location when running
# from an installed package.
def _fixture_path() -> Path:
    # packages/graphrag/src/graphrag/mcp/_mock.py
    # → packages/graphrag/src/graphrag/mcp/ (this file's dir)
    # → packages/graphrag/src/graphrag/
    # → packages/graphrag/src/
    # → packages/graphrag/
    # → packages/graphrag/tests/fixtures/biz_ops_fixture.ttl
    candidate = Path(__file__).parent.parent.parent.parent / "tests/fixtures/biz_ops_fixture.ttl"
    if candidate.exists():
        return candidate
    # Fallback for installed package: look adjacent to the package dir
    raise FileNotFoundError(
        f"Fixture corpus not found: {candidate}\n"
        "Run from the repo root or set the GRAPHRAG_FIXTURE_TTL env var."
    )


# ---------------------------------------------------------------------------
# Mock store query helpers
# ---------------------------------------------------------------------------

_ALL_DOCS_SPARQL = """
PREFIX biz:    <https://graphrag-aws.demo/biz-ops/ontology#>
PREFIX schema: <https://schema.org/>
PREFIX skos:   <http://www.w3.org/2004/02/skos/core#>
SELECT ?doc ?type ?name ?partition WHERE {
    {
        GRAPH <urn:graph:normative> {
            ?doc a ?type ;
                 schema:name ?name .
            BIND("normative" AS ?partition)
        }
    }
    UNION
    {
        GRAPH <urn:graph:descriptive> {
            ?doc a ?type .
            OPTIONAL { ?doc schema:name ?name . }
            OPTIONAL { ?doc skos:prefLabel ?name . }
            BIND("descriptive" AS ?partition)
        }
    }
}
"""


def _seed_vector_store(
    graph: Any, embedder: HashEmbedder
) -> tuple[MemoryVectorStore, dict[str, tuple[str, str]]]:
    """Index fixture entities into a MemoryVectorStore; return store + URI metadata."""
    store = MemoryVectorStore()
    uri_meta: dict[str, tuple[str, str]] = {}

    rows = list(graph.query(_ALL_DOCS_SPARQL))
    texts: list[str] = []
    entities: list[tuple[str, str, str, str]] = []  # (uri, type, name, partition)

    for row in rows:
        uri = str(row.doc)
        type_uri = str(row.type)
        name = str(row.name) if row.name else uri.split(":")[-1]
        partition = str(row.partition)
        # Deduplicate by URI
        if uri in uri_meta:
            continue
        uri_meta[uri] = (type_uri, partition)
        texts.append(name)
        entities.append((uri, type_uri, name, partition))

    if texts:
        vectors = embedder.embed(texts)
        for (uri, _type_uri, name, _partition), vec in zip(entities, vectors, strict=False):
            chunk = Chunk(
                id=uri,
                text=name,
                source="fixture",
                doc_path=uri,
                heading="",
            )
            store.index_chunk(EmbeddedChunk(chunk=chunk, vector=vec))

    return store, uri_meta


# ---------------------------------------------------------------------------
# Public initialisation entry point
# ---------------------------------------------------------------------------


def init_mock() -> None:
    """Load the fixture corpus and initialise ``_tools._store``.

    Idempotent — calling it twice is safe; the second call re-initialises the
    store (allowing tests to reset state if needed).
    """
    import warnings

    with warnings.catch_warnings():
        # rdflib deprecation warnings for ConjunctiveGraph / Dataset are noisy
        warnings.simplefilter("ignore", DeprecationWarning)
        import rdflib

    fixture = _fixture_path()
    logger.info("Loading fixture corpus", extra={"path": str(fixture)})

    # Use Dataset (modern API) to avoid ConjunctiveGraph deprecation warning
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        graph = rdflib.Dataset()
        graph.parse(str(fixture), format="trig")

    embedder = HashEmbedder()
    vector_store, uri_meta = _seed_vector_store(graph, embedder)

    _tools._store = _tools._MockStore(
        graph=graph,
        vector=vector_store,
        embedder=embedder,
        uri_meta=uri_meta,
    )
    logger.info(
        "Mock store initialised",
        extra={
            "entity_count": vector_store.count(),
            "uri_meta_count": len(uri_meta),
        },
    )


def run_mock_server(host: str = "localhost", port: int = 8000) -> None:
    """Start FastMCP in streamable-http mode on ``host:port``."""
    import asyncio

    from graphrag.mcp._tools import mcp

    # Configure host/port on the FastMCP settings object (constructor params)
    mcp.settings.host = host
    mcp.settings.port = port

    asyncio.run(mcp.run_streamable_http_async())
