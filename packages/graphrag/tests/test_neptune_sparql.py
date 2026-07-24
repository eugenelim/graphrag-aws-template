"""SPARQL store adapter tests (live + memory).

T-SPARQL-1  MemorySparqlStore round-trip: load Turtle, SELECT confirms triples.
T-SPARQL-2  Named-graph isolation: triple in urn:graph:normative is NOT visible
            in a urn:graph:descriptive-scoped SELECT (FROM NAMED).
T-SPARQL-3  Denylist: mutation keywords in sparql_select raise ValueError.
T-SPARQL-4  NeptuneSparqlStore: Authorization header is SigV4-shaped, URL ends /sparql.
"""

from __future__ import annotations

import pytest
from botocore.credentials import Credentials

from graphrag.store.neptune_sparql import HttpResponse, NeptuneSparqlStore
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

# ── shared constants ──────────────────────────────────────────────────────

SAMPLE_TTL = """
@prefix ex: <http://example.org/> .
ex:alice ex:knows ex:bob .
"""

NORMATIVE = "urn:graph:normative"
DESCRIPTIVE = "urn:graph:descriptive"

# ── T-SPARQL-1: round-trip ────────────────────────────────────────────────


def test_memory_store_round_trip_load_and_select() -> None:
    store = MemorySparqlStore()
    store.load_turtle(SAMPLE_TTL, NORMATIVE)
    rows = store.sparql_select(
        f"SELECT ?s ?p ?o FROM NAMED <{NORMATIVE}> WHERE {{ GRAPH <{NORMATIVE}> {{ ?s ?p ?o }} }}"
    )
    assert len(rows) == 1
    assert rows[0]["s"] == "http://example.org/alice"
    assert rows[0]["p"] == "http://example.org/knows"
    assert rows[0]["o"] == "http://example.org/bob"


def test_memory_store_construct_returns_graph() -> None:
    from rdflib import Graph

    store = MemorySparqlStore()
    store.load_turtle(SAMPLE_TTL, NORMATIVE)
    g = store.sparql_construct(
        f"CONSTRUCT {{ ?s ?p ?o }} FROM NAMED <{NORMATIVE}> "
        f"WHERE {{ GRAPH <{NORMATIVE}> {{ ?s ?p ?o }} }}"
    )
    assert isinstance(g, Graph)
    assert len(g) == 1


# ── T-SPARQL-2: named-graph isolation ─────────────────────────────────────


def test_memory_store_named_graph_isolation() -> None:
    store = MemorySparqlStore()
    store.load_turtle(SAMPLE_TTL, NORMATIVE)
    # Triple is in normative; descriptive-scoped query must return nothing.
    # Use GRAPH {} without FROM NAMED — rdflib tries to fetch FROM NAMED URIs
    # that do not exist in the dataset; GRAPH {} alone is sufficient to prove
    # named-graph isolation on the offline store.
    rows = store.sparql_select(f"SELECT ?s ?p ?o WHERE {{ GRAPH <{DESCRIPTIVE}> {{ ?s ?p ?o }} }}")
    assert rows == []


# ── T-SPARQL-3: denylist ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_query",
    [
        f"DROP GRAPH <{NORMATIVE}>",
        "INSERT DATA { <http://x.org/a> <http://x.org/b> <http://x.org/c> }",
        "DELETE WHERE { ?s ?p ?o }",
        "CLEAR ALL",
        "LOAD <http://example.org/data.ttl>",
        "CREATE GRAPH <urn:graph:new>",
    ],
)
def test_memory_store_denylist_raises_on_mutation_keyword(bad_query: str) -> None:
    store = MemorySparqlStore()
    with pytest.raises(ValueError, match="mutation keyword"):
        store.sparql_select(bad_query)


def test_neptune_store_denylist_raises_before_http_call() -> None:
    # The denylist check fires before any HTTP interaction.
    http = _no_calls_http()
    store = NeptuneSparqlStore(
        "https://neptune.example:8182",
        "us-east-1",
        session=_fake_session(),
        http_client=http,
    )
    with pytest.raises(ValueError, match="mutation keyword"):
        store.sparql_select(f"DROP GRAPH <{NORMATIVE}>")
    assert http.calls == []  # denylist raised before any HTTP call


# ── T-SPARQL-4: SigV4 signing + URL shape ────────────────────────────────


class FakeSession:
    def __init__(self, creds: Credentials | None) -> None:
        self._creds = creds

    def get_credentials(self) -> Credentials | None:
        return self._creds


class RecordingHttp:
    def __init__(self, response: HttpResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, *, data: bytes, headers: dict[str, str], verify: bool) -> HttpResponse:
        self.calls.append({"url": url, "data": data, "headers": dict(headers), "verify": verify})
        return self._response


def _fake_session() -> FakeSession:
    return FakeSession(Credentials("AKIDEXAMPLE", "secretkey", "token"))


def _no_calls_http() -> RecordingHttp:
    return RecordingHttp(HttpResponse(200, '{"results":{"bindings":[]}}'))


def _select_store(http: RecordingHttp) -> NeptuneSparqlStore:
    return NeptuneSparqlStore(
        "https://neptune.example:8182",
        "us-east-1",
        session=_fake_session(),
        http_client=http,
    )


def test_neptune_store_signs_request_with_sigv4() -> None:
    http = RecordingHttp(HttpResponse(200, '{"results":{"bindings":[]}}'))
    store = _select_store(http)
    store.sparql_select("SELECT ?s WHERE { ?s ?p ?o } LIMIT 1")

    assert len(http.calls) == 1
    call = http.calls[0]
    assert str(call["url"]).endswith("/sparql")
    auth = str(call["headers"]["Authorization"])  # type: ignore[index]
    assert auth.startswith("AWS4-HMAC-SHA256")


def test_neptune_store_rejects_non_https_endpoint() -> None:
    with pytest.raises(ValueError, match="must be https"):
        NeptuneSparqlStore("http://neptune.example:8182", "us-east-1")


def test_neptune_store_missing_credentials_raises() -> None:
    http = RecordingHttp(HttpResponse(200, '{"results":{"bindings":[]}}'))
    store = NeptuneSparqlStore(
        "https://neptune.example:8182",
        "us-east-1",
        session=FakeSession(None),
        http_client=http,
    )
    with pytest.raises(RuntimeError, match="no AWS credentials"):
        store.sparql_select("SELECT ?s WHERE { ?s ?p ?o } LIMIT 1")


def test_neptune_store_non_2xx_raises_with_status() -> None:
    http = RecordingHttp(HttpResponse(400, "MalformedQueryException: bad query"))
    store = _select_store(http)
    with pytest.raises(RuntimeError, match="Neptune SPARQL 400"):
        store.sparql_select("SELECT ?s WHERE { ?s ?p ?o }")


def test_neptune_store_sparql_select_parses_bindings() -> None:
    response_body = (
        '{"results":{"bindings":[{"s":{"type":"uri","value":"http://example.org/alice"}}]}}'
    )
    http = RecordingHttp(HttpResponse(200, response_body))
    rows = _select_store(http).sparql_select("SELECT ?s WHERE { ?s ?p ?o }")
    assert rows == [{"s": "http://example.org/alice"}]


# ── T-SPARQL-5: sparql_update and load_turtle on Neptune path ────────────────


def test_neptune_store_sparql_update_posts_update_param() -> None:
    # sparql_update must POST update=<stmt> to /sparql (not query=).
    http = RecordingHttp(HttpResponse(200, ""))
    store = _select_store(http)
    store.sparql_update("INSERT DATA { <http://x.org/a> <http://x.org/b> <http://x.org/c> }")

    assert len(http.calls) == 1
    call = http.calls[0]
    assert str(call["url"]).endswith("/sparql")
    body = call["data"]
    assert isinstance(body, bytes)
    import urllib.parse

    params = dict(urllib.parse.parse_qsl(body.decode()))
    assert "update" in params
    assert "query" not in params


def test_neptune_store_load_turtle_embeds_graph_uri() -> None:
    # load_turtle must produce an INSERT DATA { GRAPH <named_graph> { ... } } body.
    http = RecordingHttp(HttpResponse(200, ""))
    store = _select_store(http)
    store.load_turtle(
        "@prefix ex: <http://example.org/> . ex:a ex:b ex:c .",
        "urn:graph:normative",
    )

    call = http.calls[0]
    body = call["data"]
    assert isinstance(body, bytes)
    import urllib.parse

    params = dict(urllib.parse.parse_qsl(body.decode()))
    update_stmt = params["update"]
    assert "INSERT DATA" in update_stmt
    assert "GRAPH <urn:graph:normative>" in update_stmt


def test_neptune_store_load_turtle_rejects_invalid_named_graph() -> None:
    http = RecordingHttp(HttpResponse(200, ""))
    store = _select_store(http)
    with pytest.raises(ValueError, match="invalid named_graph"):
        store.load_turtle("", "urn:graph:bad>injection")


def test_memory_store_sparql_update_mutates_graph() -> None:
    store = MemorySparqlStore()
    store.sparql_update(
        "INSERT DATA { GRAPH <urn:graph:normative> "
        "{ <http://x.org/a> <http://x.org/b> <http://x.org/c> } }"
    )
    rows = store.sparql_select("SELECT ?s WHERE { GRAPH <urn:graph:normative> { ?s ?p ?o } }")
    assert rows == [{"s": "http://x.org/a"}]
