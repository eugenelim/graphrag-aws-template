"""SPARQL read-only validator — the text2sparql guard layer 1 (belt-and-suspenders).

``SparqlValidator`` is the app-layer mutation denylist + structural checks that screen
every model-authored SPARQL query **before** it can reach Neptune or rdflib.

**This validator is layer 1, NOT the load-bearing guarantee.**  The load-bearing
control is the IAM ``ReadDataViaQuery``-only grant on ``mcp_lambda_role`` (ADR-0011).
This validator is a conservative secondary screen: it catches the easy cases and feeds
the self-heal loop; it does NOT catch mutation keywords inside string literals
(``FILTER(?name = "DROP GRAPH test")`` triggers a false-reject — accepted per ADR-0011).

Denylist (word-boundary, case-insensitive):
  INSERT, DELETE, DROP, CLEAR, LOAD, CREATE, COPY, MOVE, ADD

Structural rejections:
  - Not a SELECT → rule="not_a_select"
  - No FROM NAMED clause → rule="missing_from_named"
  - Unbounded * property path (e.g. ``biz:hasChunk*``) → rule="unbounded_property_path"
  - SERVICE clause (SSRF/federation exfiltration) → rule="service_clause"

**Pure Python, no external dependencies.**  ``from graphrag.text2sparql._validator import
SparqlValidator`` must succeed in an environment without boto3 or rdflib installed.
"""

from __future__ import annotations

import re

from ._types import ValidationResult

# ── Mutation denylist ──────────────────────────────────────────────────────────
# All nine SPARQL Update keywords; word-boundary + case-insensitive so they match
# as tokens, not as substrings of URIs or property names.
# Note: COPY, MOVE, ADD are SPARQL 1.1 Update graph management forms.
_MUTATION_KEYWORDS = (
    "INSERT",
    "DELETE",
    "DROP",
    "CLEAR",
    "LOAD",
    "CREATE",
    "COPY",
    "MOVE",
    "ADD",
)
_MUTATION_RE = re.compile(
    r"\b(" + "|".join(_MUTATION_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# ── Structural patterns ────────────────────────────────────────────────────────
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_FROM_NAMED_RE = re.compile(r"\bFROM\s+NAMED\b", re.IGNORECASE)
_SERVICE_RE = re.compile(r"\bSERVICE\b", re.IGNORECASE)

# Unbounded property path: ``*`` or ``+`` quantifiers directly on a property
# token (e.g. ``biz:hasChunk*``, ``biz:knows+``) or a grouped path
# (e.g. ``(biz:a|biz:b)*``).  Bounded forms (``{0,5}``, ``{2,10}``) are not
# flagged.  Two alternatives: (1) word/colon token; (2) closing paren.
_UNBOUNDED_PATH_RE = re.compile(r"(?:[\w:]+|\))[*+](?!\s*\{)")


class SparqlValidator:
    """Stateless SPARQL read-only validator.

    Call ``validate(query)`` to get a ``ValidationResult``.  All checks are
    applied in priority order; the first failure is returned immediately.

    No external imports — importable without boto3 or rdflib.
    """

    def validate(self, query: str) -> ValidationResult:
        """Validate ``query`` against the mutation denylist and structural rules.

        Returns ``ValidationResult(valid=True)`` if the query passes all checks.
        Returns ``ValidationResult(valid=False, rule=<rule_name>)`` on the first
        failing check, where ``rule_name`` is one of:
          - ``"mutation_keyword"`` — a SPARQL Update keyword found
          - ``"service_clause"`` — a SERVICE federation/SSRF clause found
          - ``"not_a_select"`` — the query is not a SELECT
          - ``"missing_from_named"`` — no FROM NAMED dataset clause
          - ``"unbounded_property_path"`` — an unbound ``*`` path quantifier
        """
        text = query.strip()

        # 1. Mutation denylist (highest priority — any Update keyword is an instant reject)
        if _MUTATION_RE.search(text):
            return ValidationResult(valid=False, rule="mutation_keyword")

        # 2. SERVICE clause (SSRF / federation exfiltration vector)
        if _SERVICE_RE.search(text):
            return ValidationResult(valid=False, rule="service_clause")

        # 3. Must be a SELECT (not CONSTRUCT, ASK, DESCRIBE, or Update)
        if not _SELECT_RE.search(text):
            return ValidationResult(valid=False, rule="not_a_select")

        # 4. Must include a FROM NAMED dataset clause (partition scope required)
        if not _FROM_NAMED_RE.search(text):
            return ValidationResult(valid=False, rule="missing_from_named")

        # 5. No unbounded property path quantifiers
        if _UNBOUNDED_PATH_RE.search(text):
            return ValidationResult(valid=False, rule="unbounded_property_path")

        return ValidationResult(valid=True)
