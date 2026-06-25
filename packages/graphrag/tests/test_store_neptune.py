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
    # props carries the entity props plus the slice-5 encoded doc_paths (empty here).
    assert params["props"] == {"name": "Tim Hockin", "doc_paths": "[]"}


def test_upsert_edge_uses_single_rel_type_with_kind_param() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    store = _store(http)
    store.upsert_edge(Edge("person:thockin", "sig:sig-network", EdgeKind.TECH_LEADS))
    query = http.last_query()
    assert "MERGE (a)-[r:REL {kind: $kind}]->(b)" in query
    # The edge kind is a bound parameter, not interpolated as a relationship type.
    assert "TECH_LEADS" not in query
    assert http.last_params()["kind"] == "TECH_LEADS"


def test_upsert_node_encodes_doc_paths_as_json_string() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    node = Node("sig:sig-network", EntityKind.SIG, doc_paths={"community/sigs.yaml", "b/README.md"})
    _store(http).upsert_node(node)
    props = http.last_params()["props"]
    # doc_paths rides a single JSON-string scalar property (Neptune can't store a set natively),
    # sorted for determinism.
    assert props["doc_paths"] == json.dumps(["b/README.md", "community/sigs.yaml"])


def test_upsert_edge_encodes_doc_paths_as_json_string() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    edge = Edge("sig:s", "kep-1", EdgeKind.OWNS, doc_paths={"enhancements/k/kep.yaml"})
    _store(http).upsert_edge(edge)
    assert http.last_params()["props"]["doc_paths"] == json.dumps(["enhancements/k/kep.yaml"])


def test_node_round_trips_doc_paths_from_result() -> None:
    response = {
        "results": [
            {
                "n": {
                    "~properties": {
                        "id": "sig:sig-network",
                        "kind": "SIG",
                        "doc_paths": json.dumps(["community/sigs.yaml", "enhancements/k/kep.yaml"]),
                    }
                }
            }
        ]
    }
    http = RecordingHttp([HttpResponse(200, json.dumps(response))])
    node = _store(http).get_node("sig:sig-network")
    assert node is not None
    assert node.doc_paths == {"community/sigs.yaml", "enhancements/k/kep.yaml"}
    assert "doc_paths" not in node.props  # decoded out of the props bag


def test_node_missing_doc_paths_decodes_to_empty_set() -> None:
    # A pre-slice-5 row has no doc_paths property — must decode to an empty set, not crash.
    response = {"results": [{"n": {"~properties": {"id": "x", "kind": "SIG"}}}]}
    http = RecordingHttp([HttpResponse(200, json.dumps(response))])
    node = _store(http).get_node("x")
    assert node is not None and node.doc_paths == set()


def test_all_edges_round_trips_doc_paths() -> None:
    response = {
        "results": [
            {
                "src": "sig:s",
                "dst": "kep-1",
                "kind": "OWNS",
                "doc_paths": json.dumps(["enhancements/k/kep.yaml"]),
            }
        ]
    }
    http = RecordingHttp([HttpResponse(200, json.dumps(response))])
    edges = _store(http).all_edges()
    assert len(edges) == 1
    assert edges[0].doc_paths == {"enhancements/k/kep.yaml"}
    assert "r.doc_paths" in http.last_query()


def test_full_ingest_backfills_doc_paths_through_neptune_upserts(
    community_root: object, enhancements_root: object
) -> None:
    # Offline backfill guard (AC8b): a full ingest() against the Neptune adapter must emit
    # encoded doc_paths on EVERY node/edge upsert payload — so a first --delta on a pre-slice-5
    # stack backfills provenance, verified here without a live cluster.
    from graphrag.ingest import ingest

    class AlwaysEmpty:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        def post(self, url, *, data, headers, verify) -> HttpResponse:
            self.payloads.append(json.loads(json.loads(data)["parameters"]))
            return HttpResponse(200, json.dumps({"results": []}))

    http = AlwaysEmpty()
    store = NeptuneGraphStore(
        "https://neptune.example:8182", "us-east-1", session=FakeSession(CREDS), http_client=http
    )
    ingest(community_root, enhancements_root, store)  # type: ignore[arg-type]
    writes = [p for p in http.payloads if "props" in p]
    assert writes, "expected node/edge upsert writes"
    assert all("doc_paths" in p["props"] for p in writes)


def test_delete_node_emits_parameterized_detach_delete() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    _store(http).delete_node("sig:sig-network")
    query = http.last_query()
    assert "DETACH DELETE" in query
    assert "$id" in query and "sig:sig-network" not in query  # parameterized
    assert http.last_params()["id"] == "sig:sig-network"


def test_delete_edge_binds_full_src_kind_dst_key() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    _store(http).delete_edge("sig:sig-network", EdgeKind.OWNS, "kep-2086")
    query = http.last_query()
    # Every leg bound so exactly one edge is deleted — never all edges of a kind.
    assert "$src" in query and "$kind" in query and "$dst" in query
    assert "DELETE r" in query
    assert "OWNS" not in query and "kep-2086" not in query
    params = http.last_params()
    assert params == {"src": "sig:sig-network", "kind": "OWNS", "dst": "kep-2086"}


def test_clear_emits_detach_delete_all() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    _store(http).clear()
    query = http.last_query()
    assert "MATCH (n:Entity)" in query and "DETACH DELETE n" in query


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


def test_malformed_node_result_raises_with_context() -> None:
    # A row missing id/kind must fail loudly (naming the shape), not KeyError.
    bad = {"results": [{"n": {"~properties": {"name": "no id or kind here"}}}]}
    http = RecordingHttp([HttpResponse(200, json.dumps(bad))])
    store = _store(http)
    with pytest.raises(RuntimeError, match="missing id/kind"):
        store.get_node("x")


def test_neighbors_batch_two_queries_parameterized_no_injection() -> None:
    # OUT result then IN result — one query per direction for the whole frontier.
    out_resp = HttpResponse(
        200,
        json.dumps(
            {
                "results": [
                    {
                        "src": "sig:sig-network",
                        "kind": "OWNS",
                        "node": {"~properties": {"id": "kep-2086", "kind": "KEP"}},
                    }
                ]
            }
        ),
    )
    in_resp = HttpResponse(
        200,
        json.dumps(
            {
                "results": [
                    {
                        "src": "sig:sig-network",
                        "kind": "TECH_LEADS",
                        "node": {"~properties": {"id": "person:thockin", "kind": "Person"}},
                    }
                ]
            }
        ),
    )
    http = RecordingHttp([out_resp, in_resp])
    edges = _store(http).neighbors_batch(["sig:sig-network"])

    # exactly two HTTP calls (one per direction), both parameterized via $ids.
    assert len(http.calls) == 2
    for call in http.calls:
        body = json.loads(call["data"])
        assert "WHERE a.id IN $ids" in body["query"]
        assert json.loads(body["parameters"]) == {"ids": ["sig:sig-network"]}
        # the frontier id is never string-interpolated into the query text.
        assert "sig:sig-network" not in body["query"]

    by_dir = {(e.direction, e.edge_kind, e.neighbor.id) for e in edges}
    assert (Direction.OUT, EdgeKind.OWNS, "kep-2086") in by_dir
    assert (Direction.IN, EdgeKind.TECH_LEADS, "person:thockin") in by_dir


def test_neighbors_batch_empty_frontier_makes_no_call() -> None:
    http = RecordingHttp([])
    assert _store(http).neighbors_batch([]) == []
    assert http.calls == []


# --- slice-4: during-traversal permission filter is parameterized (shape check, AC3) ---
# These assert the WHERE clause + $allowed parameterization (the security posture). They are
# NOT the leak-correctness proof — a mock returns author-supplied rows, so it cannot prove
# server-side exclusion; the leak proof runs against the in-memory store in test_query.py.


def test_neighbors_batch_visibility_filter_is_parameterized() -> None:
    # The mock returns a RESTRICTED row; the override must REQUEST it be filtered (the WHERE
    # names r/b visibility), proving the predicate is sent — not that the mock excluded it.
    restricted_row = {
        "src": "sig:sig-node",
        "kind": "OWNS",
        "node": {"~properties": {"id": "kep-1287", "kind": "KEP", "visibility": "restricted"}},
    }
    http = RecordingHttp(
        [
            HttpResponse(200, json.dumps({"results": [restricted_row]})),
            HttpResponse(200, json.dumps({"results": []})),
        ]
    )
    _store(http).neighbors_batch(["sig:sig-node"], allowed_labels=frozenset({"public"}))

    assert len(http.calls) == 2
    for call in http.calls:
        body = json.loads(call["data"])
        query = body["query"]
        assert "r.visibility IN $allowed" in query
        assert "b.visibility IN $allowed" in query
        params = json.loads(body["parameters"])
        assert params["allowed"] == ["public"]
        # the allowed tier rides the params map, never interpolated into the query text.
        assert "public" not in query


def test_neighbors_batch_without_clearance_omits_filter() -> None:
    http = RecordingHttp([HttpResponse(200, "{}"), HttpResponse(200, "{}")])
    _store(http).neighbors_batch(["sig:sig-network"])
    for call in http.calls:
        body = json.loads(call["data"])
        assert "visibility" not in body["query"]
        assert "allowed" not in json.loads(body["parameters"])


def test_neighbors_single_visibility_filter_parameterized() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    _store(http).neighbors(
        "person:thockin",
        EdgeKind.TECH_LEADS,
        Direction.OUT,
        allowed_labels=frozenset({"public", "internal"}),
    )
    query = http.last_query()
    assert "WHERE r.visibility IN $allowed AND b.visibility IN $allowed" in query
    assert sorted(http.last_params()["allowed"]) == ["internal", "public"]


# --- opencypher-templates: run_template_query decode (AC2) -----------------------------
def test_run_template_query_decodes_n_rows() -> None:
    rows = {"results": [{"n": {"~properties": {"id": "kep-2086", "kind": "KEP"}}}]}
    http = RecordingHttp([HttpResponse(200, json.dumps(rows))])
    store = _store(http)
    nodes = store.run_template_query("MATCH (n:Entity) RETURN n", {"sig": "sig:sig-network"})
    assert [n.id for n in nodes] == ["kep-2086"]
    # the value is bound, not interpolated.
    assert http.last_params() == {"sig": "sig:sig-network"}


def test_run_template_query_empty_results_is_empty_list() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    assert _store(http).run_template_query("MATCH (n:Entity) RETURN n", {}) == []


def test_run_template_query_missing_alias_raises_diagnosable_error() -> None:
    # a row that doesn't carry the expected `n` alias fails with a message naming the alias,
    # not a bare KeyError that surfaces as an opaque sanitized envelope.
    rows = {"results": [{"x": {"~properties": {"id": "kep-2086", "kind": "KEP"}}}]}
    http = RecordingHttp([HttpResponse(200, json.dumps(rows))])
    with pytest.raises(RuntimeError, match="'n' alias"):
        _store(http).run_template_query("MATCH (n:Entity) RETURN n", {})
