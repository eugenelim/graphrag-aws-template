"""TDD tests for graphrag.ingestion._opensearch — IngestOpenSearchClient."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from graphrag.ingestion._opensearch import HttpResponse, IngestOpenSearchClient

DOC_URI = "urn:doc:test-repo:policies/hr.md"


def _make_client(captured_requests: list[dict[str, Any]]) -> IngestOpenSearchClient:
    """Build an IngestOpenSearchClient with a capturing mock HTTP client."""

    class _MockHttp:
        def request(
            self,
            method: str,
            url: str,
            *,
            data: bytes | None,
            headers: dict[str, str],
            verify: bool,
        ) -> HttpResponse:
            body_dict = json.loads(data) if data else {}
            captured_requests.append({"method": method, "url": url, "body": body_dict})
            return HttpResponse(status=200, text="{}")

    from botocore.session import Session

    mock_session = MagicMock(spec=Session)
    mock_creds = MagicMock()
    mock_creds.access_key = "AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret
    mock_creds.secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # pragma: allowlist secret
    mock_creds.token = None
    mock_session.get_credentials.return_value = mock_creds

    return IngestOpenSearchClient(
        endpoint="https://search.example.com",
        region="us-east-1",
        session=mock_session,
        http_client=_MockHttp(),
    )


# ── T3-1: delete_by_doc_uri issues delete_by_query ────────────────────────────


def test_delete_by_doc_uri_issues_delete_by_query() -> None:
    """delete_by_doc_uri() posts to _delete_by_query with a term filter on doc_uri."""
    captured: list[dict[str, Any]] = []
    client = _make_client(captured)

    client.delete_by_doc_uri(DOC_URI)

    assert len(captured) == 1
    req = captured[0]
    assert req["method"] == "POST"
    assert "_delete_by_query" in req["url"]
    # The body must contain a term filter on doc_uri.
    query = req["body"].get("query", {})
    term = query.get("term", {})
    assert "doc_uri" in term
    assert term["doc_uri"] == DOC_URI


# ── T3-2: 404 from OpenSearch is a no-op ──────────────────────────────────────


def test_delete_by_doc_uri_404_is_noop() -> None:
    """A 404 from OpenSearch (index absent) is treated as a no-op — no exception raised."""
    from botocore.session import Session

    class _NotFoundHttp:
        def request(
            self,
            method: str,
            url: str,
            *,
            data: bytes | None,
            headers: dict[str, str],
            verify: bool,
        ) -> HttpResponse:
            return HttpResponse(status=404, text='{"error":"index_not_found"}')

    mock_session = MagicMock(spec=Session)
    _creds = MagicMock()
    _creds.access_key = "AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret
    _creds.secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # pragma: allowlist secret
    _creds.token = None
    mock_session.get_credentials.return_value = _creds

    client = IngestOpenSearchClient(
        endpoint="https://search.example.com",
        region="us-east-1",
        session=mock_session,
        http_client=_NotFoundHttp(),
    )
    # Should not raise.
    client.delete_by_doc_uri(DOC_URI)


# ── extra: upsert_chunks indexes each chunk ────────────────────────────────────


def test_upsert_chunks_posts_each_chunk() -> None:
    """upsert_chunks() makes one POST per chunk to the _doc endpoint."""
    captured: list[dict[str, Any]] = []
    client = _make_client(captured)

    chunks = [
        {"doc_uri": DOC_URI, "text": "chunk 0", "embedding": [0.1, 0.2], "chunk_index": 0},
        {"doc_uri": DOC_URI, "text": "chunk 1", "embedding": [0.3, 0.4], "chunk_index": 1},
    ]
    client.upsert_chunks(chunks)

    assert len(captured) == 2
    for req in captured:
        assert req["method"] == "POST"
        assert "/_doc" in req["url"]
        assert req["body"]["doc_uri"] == DOC_URI


def test_upsert_chunks_empty_list_makes_no_requests() -> None:
    """upsert_chunks([]) makes no HTTP requests."""
    captured: list[dict[str, Any]] = []
    client = _make_client(captured)
    client.upsert_chunks([])
    assert captured == []
