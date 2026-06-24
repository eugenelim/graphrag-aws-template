"""graphrag — graph ingestion + cross-source entity resolution (demo slice 1).

The graph half of the GraphRAG-on-AWS reference demo: parse Markdown + YAML from
the Kubernetes ``community`` and ``enhancements`` repos, extract organizational
entities and edges, resolve shared entities across the two sources into single
graph nodes (normalized match + a small alias table, no trained model), and serve
a bounded multi-hop graph query with a visible trace.

See ``docs/specs/graph-ingestion-resolution/spec.md`` for the contract.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
