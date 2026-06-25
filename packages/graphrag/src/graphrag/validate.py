"""Read-only static validation for LLM-authored openCypher — the text2cypher guard's layer 1 (AC1).

The *flexible* (text2cypher) path's first guardrail, and the deliberate contrast with the
governed templates' construction-time guarantee: because the model authors the **whole** query
(structure *and* literal values), there is no parameter map to bind — safety comes from
rejecting any query that is not a bounded, single, read-only statement, and then from the IAM
read-only data-action scope (writes) + the Neptune engine query timeout (runaway reads) that
backstop what this validator cannot catch (ADR-0004).

**This validator is layer 1, NOT the guarantee.** Known classes this text-level check cannot
reliably catch — and which the IAM write-scope and engine timeout backstop:

- a mutating keyword hidden via Unicode / ``\\u``-escaped clause text;
- a write disguised in a back-tick-quoted or dynamically-constructed identifier;
- a ``LIMIT`` *inside a string literal* (e.g. ``WHERE n.note CONTAINS 'LIMIT 5'``) the
  ``LIMIT``-enforcement regex would mistake for the row cap — a false-accept of an
  otherwise-unbounded read (same "a text-level regex can't see string boundaries" root cause).
  The Neptune engine query timeout (ADR-0004) is the backstop for the runaway read.

Conservative by construction: a forbidden keyword *anywhere* in the text — including inside a
string literal — rejects (``WHERE n.title CONTAINS 'how to DELETE a KEP'`` is a false-reject,
the accepted trade-off: safety over recall). The curated demo questions never trip it, and a
rejected query feeds the bounded self-heal loop (``text2cypher.py``).

Pure Python (regex only); PyYAML-free, so it rides the query Lambda's ``Code.from_asset`` bundle.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

# A generous default row ceiling for a demo read: a missing ``LIMIT`` is appended at it, an
# over-bound one rewritten down to it. This bounds rows *returned*; the backstop for rows
# *expanded* (a runaway traversal) is the Neptune engine query timeout (ADR-0004), not LIMIT.
DEFAULT_MAX_LIMIT = 100

# Mutating clauses + **every** procedure call. ``CALL`` is rejected wholesale (read or write):
# the demo needs no procedure, and rejecting all of them removes the read-vs-write-procedure
# ambiguity, so the query-Lambda's two-action Neptune grant (``connect`` + ``ReadDataViaQuery``)
# is provably sufficient (AC9 / ADR-0004).
_FORBIDDEN_KEYWORDS = ("CREATE", "MERGE", "SET", "DELETE", "REMOVE", "DETACH", "DROP", "CALL")
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)
_RETURN_RE = re.compile(r"\bRETURN\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
# A relationship-detail bracket that carries a variable-length ``*`` quantifier.
_VARLEN_BRACKET_RE = re.compile(r"\[[^\]]*\*[^\]]*\]")


@dataclass(frozen=True)
class ValidationResult:
    """The verdict on one model-authored query.

    On accept, ``normalized_query`` is the ``LIMIT``-enforced form that actually executes; on
    reject it is empty and ``violated_rule`` names the rule that failed (fed to self-heal)."""

    ok: bool
    query: str
    normalized_query: str = ""
    violated_rule: str | None = None


def _statements(cypher: str) -> list[str]:
    """Non-empty ``;``-separated statements (a trailing ``;`` is fine; a ``;`` inside a string
    literal conservatively over-splits and rejects — acceptable)."""
    return [s for s in (part.strip() for part in cypher.split(";")) if s]


def _has_unbounded_varlen(cypher: str) -> bool:
    """True if any variable-length relationship has **no upper bound** (``[*]``, ``[*..]``,
    ``[*N..]``). A bounded form (``[*1..3]``, ``[*..5]``, ``[*2]``) is allowed — the read-cost
    guard (``LIMIT`` bounds rows returned, not rows expanded)."""
    for bracket in _VARLEN_BRACKET_RE.findall(cypher):
        after = bracket[bracket.index("*") + 1 :]
        quant = "".join(itertools.takewhile(lambda c: c in "0123456789. ", after)).replace(" ", "")
        if quant == "" or quant.endswith(".."):
            return True
    return False


def _enforce_limit(cypher: str, max_limit: int) -> str:
    """Return the query with a bounded ``LIMIT``: cap an over-bound one, append one if absent.
    Strips a trailing ``;`` so the executed form is a single bare statement."""
    stripped = cypher.rstrip().rstrip(";").rstrip()
    match = _LIMIT_RE.search(stripped)
    if match is None:
        return f"{stripped} LIMIT {max_limit}"
    if int(match.group(1)) > max_limit:
        start, end = match.span(1)
        return stripped[:start] + str(max_limit) + stripped[end:]
    return stripped


def validate_read_only(cypher: str, *, max_limit: int = DEFAULT_MAX_LIMIT) -> ValidationResult:
    """Accept iff ``cypher`` is a single, read-only, ``RETURN``-bearing statement with no
    unbounded variable-length path, normalizing its ``LIMIT`` to ``max_limit`` (AC1).

    Conservative — ambiguous input rejects with the violated rule named; a rejected query is
    never executed (it feeds the bounded self-heal loop)."""
    text = cypher.strip()
    if not text:
        return ValidationResult(ok=False, query=cypher, violated_rule="empty query")
    if len(_statements(text)) != 1:
        return ValidationResult(ok=False, query=cypher, violated_rule="multiple statements")
    forbidden = _FORBIDDEN_RE.search(text)
    if forbidden is not None:
        return ValidationResult(
            ok=False, query=cypher, violated_rule=f"forbidden clause {forbidden.group(1).upper()!r}"
        )
    if len(_RETURN_RE.findall(text)) != 1:
        return ValidationResult(
            ok=False, query=cypher, violated_rule="must contain exactly one RETURN clause"
        )
    if _has_unbounded_varlen(text):
        return ValidationResult(
            ok=False, query=cypher, violated_rule="unbounded variable-length path"
        )
    return ValidationResult(ok=True, query=cypher, normalized_query=_enforce_limit(text, max_limit))
