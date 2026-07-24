"""graphrag.ingestion._opensearch — OpenSearch chunk operations for the ingestion pipeline.

Provides ``delete_by_doc_uri`` (delete all indexed chunks for a document) and
``upsert_chunks`` (index a list of chunk vectors from the Gold artifact).

Security posture mirrors the existing ``store/opensearch.py``:
- HTTPS-enforced (non-``https://`` endpoint rejected at construction).
- SigV4-signed via ``botocore`` + ``service = "es"`` (the IAM ``es:ESHttp*`` prefix).
- Request bodies are structured dicts; nothing caller-supplied is interpolated into
  the URL path or query string.
- HTTP client is injectable for offline unit tests.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

__all__ = ["IngestOpenSearchClient"]

_OPENSEARCH_SERVICE = "es"
_DEFAULT_INDEX = "biz-ops-chunks"

log = logging.getLogger(__name__)


@dataclass
class HttpResponse:
    status: int
    text: str


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None,
        headers: dict[str, str],
        verify: bool,
    ) -> HttpResponse: ...


class _UrllibClient:
    """Default HTTP client over urllib (TLS-verified unless ``verify=False``)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None,
        headers: dict[str, str],
        verify: bool,
    ) -> HttpResponse:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
        context = ssl.create_default_context()
        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, context=context, timeout=30) as resp:  # noqa: S310
                return HttpResponse(status=resp.status, text=resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return HttpResponse(status=exc.code, text=exc.read().decode("utf-8", errors="replace"))


class IngestOpenSearchClient:
    """OpenSearch client for the biz-ops ingestion pipeline.

    Uses ``doc_uri`` as the document identifier field (distinct from the existing
    ``graphrag.store.opensearch.OpenSearchVectorStore`` which uses ``doc_path``).

    Args:
        endpoint: HTTPS URL of the OpenSearch domain (must start with ``https://``).
        region:   AWS region for SigV4 signing.
        index:    Target index name (default: ``"biz-ops-chunks"``).
        session:  Optional botocore ``Session``; default provider chain if ``None``.
        http_client: Optional ``HttpClient`` for testing (uses urllib by default).
        verify:   TLS verification (default ``True``).
    """

    def __init__(
        self,
        endpoint: str,
        region: str,
        *,
        index: str = _DEFAULT_INDEX,
        session: Session | None = None,
        http_client: HttpClient | None = None,
        verify: bool = True,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError(f"OpenSearch endpoint must be https://, got {endpoint!r}")
        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self.index = index
        self.verify = verify
        self._session = session or Session()
        self._http = http_client or _UrllibClient()

    # ── internal ────────────────────────────────────────────────────────────────

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.endpoint}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = AWSRequest(
            method=method,
            url=url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("no AWS credentials resolved from the default provider chain")
        SigV4Auth(credentials, _OPENSEARCH_SERVICE, self.region).add_auth(request)
        resp = self._http.request(
            method, url, data=data, headers=dict(request.headers), verify=self.verify
        )
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"OpenSearch {method} {path} -> {resp.status}: {resp.text}")
        return json.loads(resp.text) if resp.text else {}

    # ── public API ───────────────────────────────────────────────────────────────

    def delete_by_doc_uri(self, doc_uri: str) -> None:
        """Delete all indexed chunks for ``doc_uri`` via ``_delete_by_query``.

        An OpenSearch 404 (document already absent) is treated as a no-op.

        Args:
            doc_uri: Stable document URI (e.g. ``"urn:doc:repo:path/file.md"``).
        """
        try:
            self._request(
                "POST",
                f"/{self.index}/_delete_by_query",
                {"query": {"term": {"doc_uri": doc_uri}}},
            )
        except RuntimeError as exc:
            if "404" in str(exc):
                log.debug("delete_by_doc_uri: index not found (no-op)", extra={"doc_uri": doc_uri})
                return
            raise
        log.info("deleted chunks from OpenSearch", extra={"doc_uri": doc_uri})

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Index a list of chunk-vector dicts into the biz-ops chunks index.

        Each dict must contain at minimum ``doc_uri``, ``text``, ``embedding``,
        and ``chunk_index`` (as written by ``graphrag.ingestion.pipeline``'s
        vectors artifact).

        Args:
            chunks: List of chunk dicts from the Gold vectors artifact.
        """
        for chunk in chunks:
            self._request(
                "POST",
                f"/{self.index}/_doc",
                {
                    "doc_uri": chunk["doc_uri"],
                    "text": chunk["text"],
                    "vector": chunk["embedding"],
                    "chunk_index": chunk["chunk_index"],
                },
            )
        log.info("upserted chunks to OpenSearch", extra={"count": len(chunks)})
