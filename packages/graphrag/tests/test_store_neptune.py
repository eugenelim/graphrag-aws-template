"""T8 — Neptune adapter against a mocked SigV4/HTTPS endpoint (no live cluster).

# STUB: AC7
"""

from __future__ import annotations

import json

import pytest
from botocore.credentials import Credentials

from graphrag.model import Direction, Edge, EdgeKind, EntityKind, Node
from graphrag.store.neptune import HttpResponse, NeptuneGraphStore


class FakeSession:
    """Stands in for a botocore Session resolving the task-role credential chain."""

    def __init__(self, creds: Credentials | None) -> None:
        self._creds = creds

    def get_credentials(self) -> Credentials | None:
        return self._creds


class RecordingHttp:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def post(self, url, *, data, headers, verify) -> HttpResponse:
        self.calls.append({"url": url, "data": data, "headers": headers, "verify": verify})
        return self._responses.pop(0)

    def last_query(self) -> str:
        body = json.loads(self.calls[-1]["data"])
        return body["query"]

    def last_params(self) -> dict:
        body = json.loads(self.calls[-1]["data"])
        return json.loads(body["parameters"])


CREDS = Credentials("AKIDEXAMPLE", "secretkey", "token")


def _store(http: RecordingHttp, *, verify: bool = True) -> NeptuneGraphStore:
    return NeptuneGraphStore(
        "https://neptune.example:8182",
        "us-east-1",
        session=FakeSession(CREDS),
        http_client=http,
        verify=verify,
    )


def test_rejects_non_https_endpoint() -> None:
    with pytest.raises(ValueError, match="must be https"):
        NeptuneGraphStore("http://neptune.example:8182", "us-east-1", session=FakeSession(CREDS))


def test_upsert_node_emits_parameterized_merge() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    store = _store(http)
    store.upsert_node(Node("person:thockin", EntityKind.PERSON, {"name": "Tim Hockin"}))

    query = http.last_query()
    assert "MERGE" in query
    # Values are bound parameters, never interpolated into the query string.
    assert "$id" in query and "$kind" in query and "$props" in query
    assert "person:thockin" not in query
    params = http.last_params()
    assert params["id"] == "person:thockin"
    assert params["kind"] == "Person"
    assert params["props"] == {"name": "Tim Hockin"}


def test_upsert_edge_uses_single_rel_type_with_kind_param() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    store = _store(http)
    store.upsert_edge(Edge("person:thockin", "sig:sig-network", EdgeKind.TECH_LEADS))
    query = http.last_query()
    assert "MERGE (a)-[r:REL {kind: $kind}]->(b)" in query
    # The edge kind is a bound parameter, not interpolated as a relationship type.
    assert "TECH_LEADS" not in query
    assert http.last_params()["kind"] == "TECH_LEADS"


def test_neighbors_out_parses_into_same_node_shape() -> None:
    response = {
        "results": [
            {"b": {"~entityType": "node", "~properties": {"id": "sig:sig-network", "kind": "SIG"}}}
        ]
    }
    http = RecordingHttp([HttpResponse(200, json.dumps(response))])
    store = _store(http)
    nodes = store.neighbors("person:thockin", EdgeKind.TECH_LEADS, Direction.OUT)

    query = http.last_query()
    assert "MATCH" in query and "->" in query and "$id" in query and "$kind" in query
    assert nodes == [Node("sig:sig-network", EntityKind.SIG, {})]


def test_neighbors_in_uses_incoming_arrow() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    store = _store(http)
    store.neighbors("sig:sig-network", EdgeKind.TECH_LEADS, Direction.IN)
    assert "<-" in http.last_query()


def test_requests_are_sigv4_signed_over_tls() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    store = _store(http, verify=True)
    store.get_node("person:thockin")
    call = http.calls[-1]
    assert call["url"].startswith("https://")
    assert call["verify"] is True
    assert call["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256")


def test_missing_credentials_raises() -> None:
    http = RecordingHttp([HttpResponse(200, "{}")])
    store = NeptuneGraphStore(
        "https://neptune.example:8182", "us-east-1", session=FakeSession(None), http_client=http
    )
    with pytest.raises(RuntimeError, match="no AWS credentials"):
        store.get_node("x")


def test_non_2xx_raises_loudly_with_body() -> None:
    http = RecordingHttp([HttpResponse(400, "BadRequestException: malformed query")])
    store = _store(http)
    with pytest.raises(RuntimeError, match="Neptune openCypher 400: BadRequest"):
        store.get_node("x")
