"""Nested parent-child store: mapping + nested query body + hit parse (AC2), and the
in-memory backend's best-child ranking + fail-closed visibility semantics.

# STUB: AC2

Backend parity note: full ANN-rank parity between the in-memory (exact cosine) and OpenSearch
(approximate HNSW) backends is proven LIVE in AC9 on the fixture-sized corpus (HNSW ≈ exact);
offline, this module pins (a) the memory store's real ranking + visibility predicate and (b)
the OpenSearch adapter's request body + hit parse against a mock HTTP client.
"""

from __future__ import annotations

import json

from botocore.credentials import Credentials

from graphrag.store.opensearch import HttpResponse
from graphrag.store.parentchild_base import ChildVector, ParentDoc
from graphrag.store.parentchild_memory import MemoryParentChildStore
from graphrag.store.parentchild_opensearch import (
    OpenSearchParentChildStore,
    _parentchild_mapping,
)

CREDS = Credentials("AKIDEXAMPLE", "secretkey", "token")


class FakeSession:
    def get_credentials(self) -> Credentials:
        return CREDS


class RecordingHttp:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, *, data, headers, verify) -> HttpResponse:
        self.calls.append({"method": method, "url": url, "data": data, "verify": verify})
        return self._responses.pop(0)

    def last_body(self) -> dict:
        return json.loads(self.calls[-1]["data"])


def _store(responses: list[HttpResponse]) -> OpenSearchParentChildStore:
    return OpenSearchParentChildStore(
        "https://vectors.example.es.amazonaws.com",
        "us-east-1",
        session=FakeSession(),
        http_client=RecordingHttp(responses),
    )


# --- AC2: nested mapping --------------------------------------------------------------


def test_mapping_declares_nested_children_with_lucene_knn_vector() -> None:
    mapping = _parentchild_mapping(256)
    props = mapping["mappings"]["properties"]
    assert props["children"]["type"] == "nested"
    method = props["children"]["properties"]["vector"]["method"]
    assert method["engine"] == "lucene"
    assert method["name"] == "hnsw"
    assert method["space_type"] == "cosinesimil"
    assert props["children"]["properties"]["vector"]["dimension"] == 256
    # the app-stored parent body + parent-level filterables are present
    assert props["body"]["type"] == "text"
    assert props["source"]["type"] == "keyword"
    assert props["visibility"]["type"] == "keyword"


# --- AC2: nested query body -----------------------------------------------------------


def _empty_search_response() -> HttpResponse:
    return HttpResponse(status=200, text=json.dumps({"hits": {"hits": []}}))


def test_search_issues_nested_knn_with_inner_hits_and_score_mode_max() -> None:
    http = RecordingHttp([_empty_search_response()])
    store = OpenSearchParentChildStore(
        "https://x.es.amazonaws.com", "us-east-1", session=FakeSession(), http_client=http
    )
    store.search([0.1, 0.2, 0.3], 5)
    body = http.last_body()
    # no clearance ⇒ the query is the bare nested clause (no bool/filter)
    nested = body["query"]["nested"]
    assert nested["path"] == "children"
    assert nested["score_mode"] == "max"
    assert "inner_hits" in nested
    assert nested["query"]["knn"]["children.vector"]["vector"] == [0.1, 0.2, 0.3]
    assert nested["query"]["knn"]["children.vector"]["k"] == 5
    # child vectors excluded from the response _source (top-level and inner_hits)
    assert body["_source"]["excludes"] == ["children.vector"]
    assert nested["inner_hits"]["_source"]["excludes"] == ["children.vector"]


def test_search_composes_visibility_terms_filter_when_clearance_applied() -> None:
    http = RecordingHttp([_empty_search_response()])
    store = OpenSearchParentChildStore(
        "https://x.es.amazonaws.com", "us-east-1", session=FakeSession(), http_client=http
    )
    store.search([0.1], 3, allowed_labels=frozenset({"public", "restricted"}))
    body = http.last_body()
    bool_q = body["query"]["bool"]
    assert "nested" in bool_q["must"][0]
    # parent-level terms clause: sorted values in the body, never path-interpolated
    assert bool_q["filter"] == [{"terms": {"visibility": ["public", "restricted"]}}]


def test_search_empty_allowed_labels_still_emits_a_terms_clause_matching_nothing() -> None:
    """Fail-closed: an EMPTY clearance must still produce a visibility clause (matching nothing),
    never drop the filter and fall through to unrestricted."""
    http = RecordingHttp([_empty_search_response()])
    store = OpenSearchParentChildStore(
        "https://x.es.amazonaws.com", "us-east-1", session=FakeSession(), http_client=http
    )
    store.search([0.1], 3, allowed_labels=frozenset())
    body = http.last_body()
    assert body["query"]["bool"]["filter"] == [{"terms": {"visibility": []}}]


# --- AC2: hit parse -------------------------------------------------------------------


def test_search_parses_parent_body_and_matched_child_from_inner_hits() -> None:
    pid = "enhancements/keps/sig-node/1287-x/README.md"
    child0 = {"child_id": f"{pid}#0", "heading": "Summary", "text": "child 0"}
    child1 = {"child_id": f"{pid}#1", "heading": "Design", "text": "child 1"}
    hit_doc = {
        "_score": 0.87,
        "_source": {
            "parent_id": pid,
            "source": "enhancements",
            "doc_path": "keps/sig-node/1287-x/README.md",
            "heading": "Summary",
            "entity_ids": ["kep-1287", "sig:sig-node"],
            "visibility": "public",
            "body": "FULL PARENT BODY",
            "children": [child0, child1],
        },
        # the best-matching child surfaced via inner_hits (child1, not child0)
        "inner_hits": {"children": {"hits": {"hits": [{"_source": child1}]}}},
    }
    response = {"hits": {"hits": [hit_doc]}}
    store = _store([HttpResponse(status=200, text=json.dumps(response))])
    hits = store.search([0.1], 1)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.score == 0.87
    assert hit.parent.body == "FULL PARENT BODY"
    assert hit.parent.parent_id == pid
    assert hit.parent.entity_ids == ("kep-1287", "sig:sig-node")
    assert len(hit.parent.children) == 2
    # the matched child is the inner_hits top entry (the precise match)
    assert hit.matched_child is not None
    assert hit.matched_child.child_id == f"{pid}#1"


def test_index_parent_posts_children_as_nested_array_with_vectors() -> None:
    http = RecordingHttp([HttpResponse(status=201, text="{}")])
    store = OpenSearchParentChildStore(
        "https://x.es.amazonaws.com", "us-east-1", session=FakeSession(), http_client=http
    )
    parent = ParentDoc(
        parent_id="community/sig-x/README.md",
        source="community",
        doc_path="sig-x/README.md",
        heading="Charter",
        entity_ids=("sig:sig-x",),
        visibility="public",
        body="BODY",
        children=(ChildVector("community/sig-x/README.md#0", "Charter", "t", [0.5, 0.5]),),
    )
    store.index_parent(parent)
    body = http.last_body()
    assert body["parent_id"] == "community/sig-x/README.md"
    assert body["body"] == "BODY"
    assert body["children"][0]["child_id"] == "community/sig-x/README.md#0"
    assert body["children"][0]["vector"] == [0.5, 0.5]


# --- AC2: in-memory ranking + visibility ---------------------------------------------


def _memory_store() -> MemoryParentChildStore:
    store = MemoryParentChildStore()
    store.index_parent(
        ParentDoc(
            parent_id="a",
            source="enhancements",
            doc_path="a",
            heading="H",
            entity_ids=(),
            visibility="restricted",
            body="A",
            children=(
                ChildVector("a#0", "H", "t", [1.0, 0.0]),
                ChildVector("a#1", "H", "t", [0.6, 0.0]),  # worse child; best-child wins
            ),
        )
    )
    store.index_parent(
        ParentDoc(
            parent_id="b",
            source="community",
            doc_path="b",
            heading="H",
            entity_ids=(),
            visibility="public",
            body="B",
            children=(ChildVector("b#0", "H", "t", [0.0, 1.0]),),
        )
    )
    return store


def test_memory_search_ranks_by_best_child_and_returns_matched_child() -> None:
    store = _memory_store()
    hits = store.search([1.0, 0.0], 5)
    assert hits[0].parent.parent_id == "a"  # best child [1,0] aligns with the query
    assert hits[0].matched_child is not None
    assert hits[0].matched_child.child_id == "a#0"  # the best child, not a#1
    assert hits[0].score > hits[1].score


def test_memory_search_visibility_none_vs_empty() -> None:
    store = _memory_store()
    # None ⇒ unrestricted (both parents eligible)
    assert {h.parent.parent_id for h in store.search([1.0, 0.0], 5)} == {"a", "b"}
    # a public-only clearance excludes the restricted parent
    assert {
        h.parent.parent_id
        for h in store.search([1.0, 0.0], 5, allowed_labels=frozenset({"public"}))
    } == {"b"}
    # empty ⇒ fail-closed (zero hits)
    assert store.search([1.0, 0.0], 5, allowed_labels=frozenset()) == []
