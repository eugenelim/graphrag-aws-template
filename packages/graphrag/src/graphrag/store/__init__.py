"""Graph store backends: an in-memory store and a Neptune openCypher adapter.

The multi-hop traversal (``graphrag.query``) runs over the ``neighbors()``
primitive both stores implement, so the demo's trace is identical offline and
deployed — the reproducibility contract (ADR-0002 / the spec's Boundaries rail
against pushing traversal into openCypher).
"""

from .base import GraphStore
from .memory import MemoryGraphStore
from .vector_base import EmbeddedChunk, VectorHit, VectorStore
from .vector_memory import MemoryVectorStore

__all__ = [
    "EmbeddedChunk",
    "GraphStore",
    "MemoryGraphStore",
    "MemoryVectorStore",
    "VectorHit",
    "VectorStore",
]
