"""Amazon OpenSearch Service k-NN adapter — the deployed vector backend (slice-2 AC4).

Security posture mirrors the Neptune adapter (``store/neptune.py``):

- **Parameterized request bodies only.** The query vector, ``k``, and document ids
  ride the JSON body; nothing caller-supplied is interpolated into the path or a
  query string (the only query-string params used are fixed constants like
  ``refresh=true``).
- **HTTPS-enforced, TLS verification on by default.** A non-``https://`` endpoint is
  rejected; ``verify`` defaults to ``True``.
- **IAM-mediated.** Requests are SigV4-signed for service ``es`` with credentials from
  the default botocore provider chain (the task / Lambda role) — no plaintext
  credential read at the call site.

``OPENSEARCH_SERVICE`` is the single source for the SigV4 signing service *and* the
IAM ``es:ESHttp*`` action prefix the IaC scopes, so the two can't drift apart. The
HTTP client is injectable so the adapter is testable against a mock without a live
domain.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

from ..chunk import Chunk
from .vector_base import EmbeddedChunk, VectorHit, VectorStore

OPENSEARCH_SERVICE = "es"  # SigV4 signing service AND the IAM es:ESHttp* action prefix
DEFAULT_INDEX = "graphrag-chunks"
DEFAULT_DIMENSIONS = 256


@dataclass
class HttpResponse:
    status: int
    text: str


class HttpClient(Protocol):
    def request(
        self, method: str, url: str, *, data: bytes | None, headers: dict[str, str], verify: bool
    ) -> HttpResponse: ...


class _UrllibClient:
    """Default HTTP client over urllib (TLS verified unless ``verify=False``)."""

    def request(
        self, method: str, url: str, *, data: bytes | None, headers: dict[str, str], verify: bool
    ) -> HttpResponse:
        # The endpoint scheme is validated as https:// in __init__, so this is not an
        # arbitrary-scheme open.
        req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
        context = ssl.create_default_context()
        if not verify:  # opt-in only; never the default
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=context, timeout=30) as resp:  # noqa: S310
            return HttpResponse(status=resp.status, text=resp.read().decode("utf-8"))


def _knn_mapping(dimensions: int) -> dict[str, Any]:
    return {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "vector": {
                    "type": "knn_vector",
                    "dimension": dimensions,
                    "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "nmslib"},
                },
                "chunk_id": {"type": "keyword"},
                "source": {"type": "keyword"},
                "doc_path": {"type": "keyword"},
                "heading": {"type": "text"},
                "entity_ids": {"type": "keyword"},
                "text": {"type": "text"},
            }
        },
    }


class OpenSearchVectorStore(VectorStore):
    def __init__(
        self,
        endpoint: str,
        region: str,
        *,
        index: str = DEFAULT_INDEX,
        dimensions: int = DEFAULT_DIMENSIONS,
        session: Session | None = None,
        http_client: HttpClient | None = None,
        verify: bool = True,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError(f"OpenSearch endpoint must be https://, got {endpoint!r}")
        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self.index = index
        self.dimensions = dimensions
        self.verify = verify
        self._session = session or Session()
        self._http = http_client or _UrllibClient()

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.endpoint}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = AWSRequest(
            method=method, url=url, data=data, headers={"Content-Type": "application/json"}
        )
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("no AWS credentials resolved from the default provider chain")
        SigV4Auth(credentials, OPENSEARCH_SERVICE, self.region).add_auth(request)
        resp = self._http.request(
            method, url, data=data, headers=dict(request.headers), verify=self.verify
        )
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"OpenSearch {method} {path} -> {resp.status}: {resp.text}")
        return json.loads(resp.text) if resp.text else {}

    def create_index(self) -> None:
        """Create the k-NN index; idempotent (an already-exists 400 is fine)."""
        try:
            self._request("PUT", f"/{self.index}", _knn_mapping(self.dimensions))
        except RuntimeError as exc:
            if "resource_already_exists" not in str(exc):
                raise

    def index_chunk(self, embedded: EmbeddedChunk, *, refresh: bool = False) -> None:
        path = f"/{self.index}/_doc"
        if refresh:  # make the doc immediately searchable (the probe needs this)
            path += "?refresh=true"
        chunk = embedded.chunk
        self._request(
            "POST",
            path,
            {
                "chunk_id": chunk.id,
                "vector": embedded.vector,
                "source": chunk.source,
                "doc_path": chunk.doc_path,
                "heading": chunk.heading,
                "entity_ids": chunk.entity_ids,
                "text": chunk.text,
            },
        )

    def knn(self, vector: list[float], k: int) -> list[VectorHit]:
        body = {
            "size": k,
            "_source": {"excludes": ["vector"]},
            "query": {"knn": {"vector": {"vector": vector, "k": k}}},
        }
        res = self._request("POST", f"/{self.index}/_search", body)
        return [self._hit(h) for h in res.get("hits", {}).get("hits", [])]

    @staticmethod
    def _hit(hit: dict[str, Any]) -> VectorHit:
        src = hit.get("_source", {})
        chunk = Chunk(
            id=str(src.get("chunk_id", "")),
            text=str(src.get("text", "")),
            source=str(src.get("source", "")),
            doc_path=str(src.get("doc_path", "")),
            heading=str(src.get("heading", "")),
            entity_ids=list(src.get("entity_ids") or []),
        )
        return VectorHit(chunk, float(hit.get("_score", 0.0)))

    def count(self) -> int:
        res = self._request("GET", f"/{self.index}/_count")
        return int(res.get("count", 0))

    def delete(self, ids: list[str], *, refresh: bool = True) -> None:
        path = f"/{self.index}/_delete_by_query"
        if refresh:
            path += "?refresh=true"
        self._request("POST", path, {"query": {"terms": {"chunk_id": ids}}})
