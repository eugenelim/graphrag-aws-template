"""Graph store backends: an in-memory store and a Neptune openCypher adapter.

The multi-hop traversal (``graphrag.query``) runs over the ``neighbors()``
primitive both stores implement, so the demo's trace is identical offline and
deployed — the reproducibility contract (ADR-0002 / the spec's Boundaries rail
against pushing traversal into openCypher).
"""

from .base import GraphStore
from .memory import MemoryGraphStore

__all__ = ["GraphStore", "MemoryGraphStore"]
