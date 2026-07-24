"""graphrag.normative — exhaustive retrieval for the ``get_policies`` MCP tool.

Public surface:

- :class:`NormativeRetriever` — two-leg retrieval (SPARQL + vector-threshold)
  over ``urn:graph:normative``.
- :class:`NormativeUnavailable` — raised (not caught) when Neptune is
  unreachable; propagates to the MCP tool handler as a structured error.
"""

from ._retriever import NormativeRetriever
from ._types import NormativeUnavailable

__all__ = ["NormativeRetriever", "NormativeUnavailable"]
