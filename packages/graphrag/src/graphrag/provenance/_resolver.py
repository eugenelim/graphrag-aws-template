"""CitationResolver — SPARQL-backed citation resolution for graphrag.provenance.

Accepts an injectable ``SparqlStore`` so it is testable offline with
``MemorySparqlStore`` and deployable against live Neptune via
``NeptuneSparqlStore``.  No boto3 or botocore imports.

Resolution strategy (per spec-provenance-citations §Design):
- Run a single SPARQL SELECT that walks the full PROV-O chain from the document
  URI back to the bronze entity to collect git metadata and extractor name.
- Resolve ``biz:chunkText`` separately as the excerpt for chunk URIs.
- For chunk URIs that lack a direct ``biz:effectiveDate``, follow
  ``prov:wasDerivedFrom`` to the parent document and query its date.
- All SPARQL variables are OPTIONAL; a URI with no provenance returns a
  Citation with all optional fields set to None (graceful partial resolution).
"""

from __future__ import annotations

import logging
from typing import Any

from graphrag.provenance._sparql import chunk_parent_date_query, excerpt_query, metadata_query
from graphrag.provenance._types import Citation
from graphrag.store.sparql_base import SparqlStore

_AGENT_PREFIX = "urn:agent:"
_LOG = logging.getLogger(__name__)
_EXCERPT_MAX = 200


class CitationResolver:
    """Resolve retrieval result URIs to Citation dataclasses via SPARQL.

    Args:
        store: An injectable ``SparqlStore`` instance.  Use ``MemorySparqlStore``
            for offline CI; ``NeptuneSparqlStore`` in production.
    """

    def __init__(self, store: SparqlStore) -> None:
        self._store = store

    def resolve(
        self,
        result_uris: list[str],
        *,
        relevance: float | None = None,
    ) -> list[Citation]:
        """Resolve a list of result URIs to Citation dataclasses.

        Args:
            result_uris: Document or chunk URIs from Neptune retrieval results.
            relevance: Optional relevance score to attach to **all** returned
                citations uniformly.  This is a single scalar for the whole call —
                callers ranking N URIs with distinct scores must call ``resolve()``
                once per URI.  The limitation is by design: MCP tools typically
                apply a single retrieval-model score to a batch of results from one
                query.

        Returns:
            One Citation per URI.  URIs with no provenance triples return a
            Citation with ``commit_sha=None`` and all other optional fields None.
            Never raises on missing provenance data.
        """
        return [self._resolve_one(uri, relevance) for uri in result_uris]

    def _resolve_one(self, uri: str, relevance: float | None) -> Citation:
        """Resolve a single URI to a Citation."""
        # ── primary metadata + PROV-O chain ─────────────────────────────────
        rows = self._safe_select(metadata_query(uri))
        if rows:
            row = rows[0]
            title = row.get("title")
            raw_type = row.get("type")
            doc_type = _short_type(raw_type) if raw_type else None
            partition = row.get("partition")
            commit_sha = row.get("sha")
            git_path = row.get("gitPath")
            git_repo = row.get("gitRepo")
            raw_agent = row.get("agentUri")
            extractor = (
                raw_agent[len(_AGENT_PREFIX) :]
                if raw_agent and raw_agent.startswith(_AGENT_PREFIX)
                else raw_agent
            )
            effective_date = row.get("effectiveDate")
        else:
            # No provenance triples — graceful partial resolution
            title = doc_type = partition = commit_sha = git_path = git_repo = extractor = None
            effective_date = None

        # ── chunk excerpt ────────────────────────────────────────────────────
        excerpt = self._resolve_excerpt(uri)

        # ── chunk effective_date via parent doc ──────────────────────────────
        if effective_date is None and excerpt is not None:
            # If we got an excerpt it's a chunk URI; try the parent doc date.
            date_rows = self._safe_select(chunk_parent_date_query(uri))
            if date_rows:
                effective_date = date_rows[0].get("effectiveDate")

        return Citation(
            uri=uri,
            title=title,
            doc_type=doc_type,
            partition=partition,
            commit_sha=commit_sha,
            git_path=git_path,
            git_repo=git_repo,
            extractor=extractor,
            excerpt=excerpt,
            relevance=relevance,
            effective_date=effective_date,
        )

    def _resolve_excerpt(self, uri: str) -> str | None:
        """Return the first 200 chars of biz:chunkText, or None."""
        rows = self._safe_select(excerpt_query(uri))
        if not rows:
            return None
        text = rows[0].get("text")
        if text is None:
            return None
        return text[:_EXCERPT_MAX]

    def _safe_select(self, query: str) -> list[dict[str, Any]]:
        """Run sparql_select; return [] on error rather than propagating."""
        try:
            return self._store.sparql_select(query)
        except Exception as exc:
            _LOG.warning("CitationResolver: SPARQL error: %s", exc, exc_info=True)
            return []


def _short_type(full_uri: str) -> str:
    """Return the local name from a full URI (after # or last /).

    Examples:
        https://graphrag-aws.demo/biz-ops/ontology#Policy  → Policy
        https://schema.org/DigitalDocument                 → DigitalDocument
    """
    if "#" in full_uri:
        return full_uri.rsplit("#", 1)[-1]
    return full_uri.rsplit("/", 1)[-1]
