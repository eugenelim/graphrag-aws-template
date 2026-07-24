"""SPARQL leg for graphrag.normative.

Executes an exhaustive SELECT against ``urn:graph:normative`` using the
``MemorySparqlStore`` (offline) or ``NeptuneSparqlStore`` (live).

Design decisions:
- ``FROM NAMED <urn:graph:normative>`` is mandatory — never omitted (ADR-0012).
- No ``LIMIT`` clause — exhaustive recall, no top-k (ADR-0012).
- PII filter is applied in Python (not SPARQL) so that ``pii_withheld_count``
  can be computed without a second COUNT query — see :mod:`graphrag.normative._retriever`.
- Effective-date filter IS in SPARQL — smaller result set, no count needed.
- Domain parameter is validated against ``KNOWN_DOMAINS`` before substitution
  to prevent SPARQL injection (all known domain names are alphanumeric).
- ``schema:name`` and ``biz:gitCommitSHA`` are OPTIONAL in the query so that
  provenance-incomplete documents are still returned (exhaustive-recall guarantee).
  Results are deduplicated by ``?doc`` URI after retrieval to handle multi-valued
  OPTIONAL properties (e.g. multiple ``biz:inDomain`` values producing cartesian rows).
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

from ..store.sparql_base import SparqlStore
from ._types import NormativeResult, NormativeUnavailable

log = logging.getLogger(__name__)

# Normative named-graph URI (ADR-0012).
NORMATIVE_GRAPH = "urn:graph:normative"

# Known business domain local names — validated before SPARQL substitution.
# Each value is an alphanumeric identifier that maps to ``biz:<Name>`` in the
# SPARQL query (e.g. ``Finance`` ->
# ``https://graphrag-aws.demo/biz-ops/ontology#Finance``).
# Extend as the biz-ops corpus grows; raising ``ValueError`` on an unknown
# domain is preferable to an empty/unexpected result set.
KNOWN_DOMAINS: frozenset[str] = frozenset(
    {
        "Finance",
        "HR",
        "IT",
        "Legal",
        "Operations",
        "Marketing",
        "Sales",
        "Engineering",
        "Compliance",
        "Risk",
        "Procurement",
        "CustomerService",
        "Security",
        "Audit",
    }
)

# Base SPARQL SELECT — always scoped to urn:graph:normative.
# ``{extra_filters}`` is replaced by zero or more validated FILTER clauses.
_QUERY_TEMPLATE = """PREFIX biz:    <https://graphrag-aws.demo/biz-ops/ontology#>
PREFIX schema: <https://schema.org/>
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>

SELECT ?doc ?title ?type ?domain ?effectiveDate ?scope ?hasPII ?sha ?path
FROM NAMED <urn:graph:normative>
WHERE {{
  GRAPH <urn:graph:normative> {{
    ?doc a ?type .
    OPTIONAL {{ ?doc schema:name ?title }}
    OPTIONAL {{ ?doc biz:gitCommitSHA ?sha }}
    OPTIONAL {{ ?doc biz:gitPath ?path }}
    OPTIONAL {{ ?doc biz:hasPII ?hasPII }}
    OPTIONAL {{ ?doc biz:inDomain ?domain }}
    OPTIONAL {{ ?doc biz:effectiveDate ?effectiveDate }}
    OPTIONAL {{ ?doc biz:scope ?scope }}
    FILTER(?type IN (biz:Policy, biz:Standard, biz:Guideline)){extra_filters}
  }}
}}
"""


def build_query(
    *,
    domain: str | None,
    include_future: bool,
    today: str,
) -> str:
    """Return the SPARQL SELECT string with optional domain and date filters.

    Raises ``ValueError`` for an unknown domain (prevents injection; all known
    domain local names are alphanumeric so they are safe to interpolate as
    ``biz:<Name>`` in the query prefix context).
    """
    if domain is not None and domain not in KNOWN_DOMAINS:
        raise ValueError(f"Unknown domain {domain!r}; must be one of: {sorted(KNOWN_DOMAINS)}")

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", today):
        raise ValueError(f"today must be ISO date YYYY-MM-DD, got {today!r}")

    extra: list[str] = []
    if domain is not None:
        extra.append(f"\n    FILTER(?domain = biz:{domain})")
    if not include_future:
        date_clause = (
            '\n    FILTER(!bound(?effectiveDate) || ?effectiveDate <= "' + today + '"^^xsd:date)'
        )
        extra.append(date_clause)

    return _QUERY_TEMPLATE.format(extra_filters="".join(extra))


def _local_name(uri: str) -> str | None:
    """Extract the local name from a URI (after ``#`` or last ``/``)."""
    if not uri:
        return None
    if "#" in uri:
        return uri.rsplit("#", 1)[-1] or None
    if "/" in uri:
        return uri.rsplit("/", 1)[-1] or None
    return uri or None


def _row_to_result(row: dict[str, Any]) -> NormativeResult:
    """Map a SPARQL result binding dict to a :class:`NormativeResult`."""
    type_uri = row.get("type", "")
    doc_type = _local_name(type_uri) or "Unknown"

    domain_uri = row.get("domain", "")
    domain_str = _local_name(domain_uri) if domain_uri else None

    haspii_val = row.get("hasPII", "")
    pii_flagged = haspii_val.lower() == "true"

    return NormativeResult(
        uri=row.get("doc", ""),
        title=row.get("title", ""),
        doc_type=doc_type,
        domain=domain_str,
        effective_date=row.get("effectiveDate") or None,
        scope=row.get("scope") or None,
        pii_flagged=pii_flagged,
        relevance=None,  # SPARQL leg items carry no vector score
        git_commit=row.get("sha") or None,
        git_path=row.get("path") or None,
    )


def sparql_leg(
    store: SparqlStore,
    *,
    domain: str | None = None,
    include_future: bool = False,
    today: str | None = None,
) -> list[NormativeResult]:
    """Execute the SPARQL leg; return all matching normative documents.

    Hard-fails with :class:`NormativeUnavailable` (logged at ERROR then
    re-raised) if the underlying store raises any exception — Neptune
    unavailability must never silently degrade to a partial result.

    ``today`` is a testing seam (ISO date string); ``None`` defaults to
    ``datetime.date.today().isoformat()``.

    Note: PII filtering is *not* applied here — it is applied in Python by
    :class:`NormativeRetriever` so that ``pii_withheld_count`` can be computed
    without a second query.
    """
    if today is None:
        today = datetime.date.today().isoformat()

    query = build_query(domain=domain, include_future=include_future, today=today)

    try:
        rows = store.sparql_select(query)
    except Exception as exc:
        log.error("Neptune SPARQL normative leg failed: %s", exc, exc_info=True)
        raise NormativeUnavailable("Neptune normative partition unreachable") from exc

    # Deduplicate by document URI — multiple SPARQL rows per document occur when
    # OPTIONAL patterns bind to multiple values (e.g. a policy with two
    # biz:inDomain values produces one row per domain via cartesian join).
    # Keep the first row encountered for each URI (insertion order is stable).
    seen: dict[str, NormativeResult] = {}
    for row in rows:
        result = _row_to_result(row)
        if result.uri not in seen:
            seen[result.uri] = result
    return list(seen.values())
