"""Citation dataclass for graphrag.provenance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Citation:
    """Attribution record returned by CitationResolver alongside retrieval results.

    All optional fields are None when provenance triples are absent (graceful
    partial resolution — no exception raised on missing data).
    """

    uri: str
    title: str | None
    doc_type: str | None
    partition: str | None  # urn:graph:normative or urn:graph:descriptive
    commit_sha: str | None
    git_path: str | None
    git_repo: str | None
    extractor: str | None
    excerpt: str | None  # first 200 chars of biz:chunkText; None for doc URIs
    relevance: float | None  # caller-provided; not resolved from SPARQL
    effective_date: str | None  # ISO date string or None
