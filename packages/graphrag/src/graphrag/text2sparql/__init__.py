"""graphrag.text2sparql — guarded natural language → SPARQL SELECT translation.

Public surface:
  - ``text2sparql_query`` — orchestrator: generate → validate → self-heal → execute.
  - ``Text2SparqlResult`` — full audit trace (no question text — ADR-0014 policy).
  - ``SparqlValidator`` — pure-Python validator; importable without boto3 or rdflib.
  - ``BedrockText2SparqlGenerator`` — Bedrock Converse generator.

Import isolation: ``SparqlValidator`` and the type-only exports (``ValidationResult``,
``GeneratedQuery``, ``Text2SparqlResult``) are importable without boto3 or rdflib.
``BedrockText2SparqlGenerator`` and ``text2sparql_query`` are lazy-loaded on first
access and require rdflib + boto3 (they depend on the store layer).
"""

from __future__ import annotations

from ._types import GeneratedQuery, Text2SparqlResult, ValidationResult
from ._validator import SparqlValidator

__all__ = [
    "BedrockText2SparqlGenerator",
    "GeneratedQuery",
    "SparqlValidator",
    "Text2SparqlResult",
    "ValidationResult",
    "text2sparql_query",
]


def __getattr__(name: str) -> object:
    """Lazy-load the store-dependent components on first access."""
    if name == "text2sparql_query":
        from ._orchestrator import text2sparql_query

        return text2sparql_query
    if name == "BedrockText2SparqlGenerator":
        from ._generator import BedrockText2SparqlGenerator

        return BedrockText2SparqlGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
