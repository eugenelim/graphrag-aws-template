"""T4 — OpenSearch adapter against a mocked SigV4/HTTPS endpoint (no live domain) (AC4).

# STUB: AC4
"""

from __future__ import annotations

import json

import pytest
from botocore.credentials import Credentials

from graphrag.chunk import Chunk
from graphrag.store.opensearch import HttpResponse, OpenSearchVectorStore
from graphrag.store.vector_base import EmbeddedChunk


class FakeSession:
    def __init__(self, creds: Credentials | None) -> None:
        self._creds = creds

    def get_credentials(self) -> Credentials | None:
        return self._creds


class RecordingHttp:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, *, data, headers, verify) -> HttpResponse:
        self.calls.append(
            {"method": method, "url": url, "data": data, "headers": headers, "verify": verify}
        )
        return self._responses.pop(0)

    def last_body(self) -> dict:
        return json.loads(self.calls[-1]["data"])


CREDS = Credentials("AKIDEXAMPLE", "secretkey", "token")


def _store(http: RecordingHttp, *, verify: bool = True) -> OpenSearchVectorStore:
    return OpenSearchVectorStore(
        "https://vectors.example.es.amazonaws.com",
        "us-east-1",
        session=FakeSession(CREDS),
        http_client=http,
        verify=verify,
    )


def _embedded() -> EmbeddedChunk:
    return EmbeddedChunk(
        Chunk(
            "kep-1287#0",
            "in-place pod resize",
            "enhancements",
            "k/README.md",
            "Summary",
            ["kep-1287"],
        ),
        [0.1, 0.2, 0.3],
    )


def test_rejects_non_https_endpoint() -> None:
    with pytest.raises(ValueError, match="must be https"):
        OpenSearchVectorStore("http://vectors.example", "us-east-1", session=FakeSession(CREDS))


def test_create_index_puts_knn_vector_mapping() -> None:
    http = RecordingHttp([HttpResponse(200, "{}")])
    _store(http).create_index()
    body = http.last_body()
    assert http.calls[-1]["method"] == "PUT"
    assert body["settings"]["index"]["knn"] is True
    vector = body["mappings"]["properties"]["vector"]
    assert vector["type"] == "knn_vector"
    assert vector["dimension"] == 256


def test_create_index_is_idempotent_on_already_exists() -> None:
    http = RecordingHttp([HttpResponse(400, "resource_already_exists_exception: index exists")])
    _store(http).create_index()  # must not raise


def test_index_chunk_carries_vector_and_metadata_in_body() -> None:
    http = RecordingHttp([HttpResponse(201, "{}")])
    _store(http).index_chunk(_embedded())
    body = http.last_body()
    assert http.calls[-1]["method"] == "POST"
    assert body["vector"] == [0.1, 0.2, 0.3]
    assert body["chunk_id"] == "kep-1287#0"
    assert body["entity_ids"] == ["kep-1287"]
    assert body["text"] == "in-place pod resize"


def test_knn_query_is_body_parameterized_not_interpolated() -> None:
    response = {
        "hits": {
            "hits": [
                {
                    "_score": 0.91,
                    "_source": {
                        "chunk_id": "kep-1287#0",
                        "source": "enhancements",
                        "doc_path": "k/README.md",
                        "heading": "Summary",
                        "entity_ids": ["kep-1287"],
                        "text": "in-place pod resize",
                    },
                }
            ]
        }
    }
    http = RecordingHttp([HttpResponse(200, json.dumps(response))])
    hits = _store(http).knn([0.5, 0.5, 0.5], k=3)

    call = http.calls[-1]
    assert call["method"] == "POST"
    # The vector + k ride the body; nothing is interpolated into the URL path.
    assert "0.5" not in call["url"]
    body = http.last_body()
    assert body["query"]["knn"]["vector"]["vector"] == [0.5, 0.5, 0.5]
    assert body["query"]["knn"]["vector"]["k"] == 3
    assert hits[0].chunk.id == "kep-1287#0"
    assert hits[0].score == 0.91
    assert hits[0].chunk.entity_ids == ["kep-1287"]


def test_requests_are_sigv4_signed_for_es_over_tls() -> None:
    http = RecordingHttp([HttpResponse(200, '{"count": 0}')])
    _store(http, verify=True).count()
    call = http.calls[-1]
    assert call["url"].startswith("https://")
    assert call["verify"] is True
    auth = call["headers"]["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256")
    assert "/es/aws4_request" in auth  # signed for service "es"


def test_missing_credentials_raises() -> None:
    http = RecordingHttp([HttpResponse(200, "{}")])
    store = OpenSearchVectorStore(
        "https://vectors.example", "us-east-1", session=FakeSession(None), http_client=http
    )
    with pytest.raises(RuntimeError, match="no AWS credentials"):
        store.count()


def test_non_2xx_raises_loudly_with_body() -> None:
    http = RecordingHttp([HttpResponse(403, "AuthorizationException: not allowed")])
    with pytest.raises(RuntimeError, match="OpenSearch .* 403: AuthorizationException"):
        _store(http).knn([0.1], k=1)


def test_delete_by_query_is_body_parameterized() -> None:
    http = RecordingHttp([HttpResponse(200, "{}")])
    _store(http).delete(["a", "b"])
    body = http.last_body()
    assert body["query"]["terms"]["chunk_id"] == ["a", "b"]
