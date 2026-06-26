"""T2 — Community node + CommunityStore seam (Neptune adapter + in-memory, backend-identical).

# STUB: AC2
"""

from __future__ import annotations

import json

import pytest
from botocore.credentials import Credentials

from graphrag.store.community_base import Community
from graphrag.store.community_memory import MemoryCommunityStore
from graphrag.store.community_neptune import NeptuneCommunityStore
from graphrag.store.neptune import HttpResponse
from graphrag.visibility import Visibility


class FakeSession:
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
        return json.loads(self.calls[-1]["data"])["query"]

    def last_params(self) -> dict:
        return json.loads(json.loads(self.calls[-1]["data"])["parameters"])


CREDS = Credentials("AKIDEXAMPLE", "secretkey", "token")


def _store(http: RecordingHttp) -> NeptuneCommunityStore:
    return NeptuneCommunityStore(
        "https://neptune.example:8182", "us-east-1", session=FakeSession(CREDS), http_client=http
    )


def _community(cid: str, tier: str, *, size: int = 2) -> Community:
    return Community(
        id=cid,
        title=f"{cid} title",
        summary=f"summary of {cid}",
        entity_ids=tuple(f"{cid}-e{i}" for i in range(size)),
        tier=tier,
        size=size,
    )


# --- Neptune adapter: parameterized writes ----------------------------------------------


def test_rejects_non_https_endpoint() -> None:
    with pytest.raises(ValueError, match="must be https"):
        NeptuneCommunityStore(
            "http://neptune.example:8182", "us-east-1", session=FakeSession(CREDS)
        )


def test_upsert_community_is_parameterized() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    c = _community("community-0", Visibility.PUBLIC.value)
    _store(http).upsert_community(c)

    query, params = http.last_query(), http.last_params()
    assert "MERGE (c:Community {id: $id})" in query
    # every value rides the parameter map, nothing interpolated into the query string
    assert params["id"] == "community-0"
    assert params["summary"] == "summary of community-0"
    assert params["tier"] == Visibility.PUBLIC.value
    assert params["size"] == 2
    assert json.loads(params["entity_ids"]) == list(c.entity_ids)
    assert "community-0" not in query and "summary of" not in query


def test_set_community_id_is_parameterized_on_entity_label() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    _store(http).set_community_id("sig:sig-network", "community-3")

    query, params = http.last_query(), http.last_params()
    assert "MATCH (n:Entity {id: $id})" in query and "SET n.communityId = $cid" in query
    assert params == {"id": "sig:sig-network", "cid": "community-3"}
    assert "sig:sig-network" not in query and "community-3" not in query


def test_all_communities_unrestricted_has_no_where() -> None:
    rows = {"results": [{"c": {"~properties": _props("community-0", "public", 2)}}]}
    http = RecordingHttp([HttpResponse(200, json.dumps(rows))])
    out = _store(http).all_communities(allowed_labels=None)

    assert "WHERE" not in http.last_query()
    assert [c.id for c in out] == ["community-0"]
    assert out[0].entity_ids == ("community-0-e0", "community-0-e1")


def test_all_communities_clearance_filters_server_side() -> None:
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    _store(http).all_communities(allowed_labels=frozenset({"public", "internal"}))

    assert "WHERE c.tier IN $allowed" in http.last_query()
    assert http.last_params()["allowed"] == ["internal", "public"]  # sorted


def test_all_communities_empty_clearance_is_fail_closed() -> None:
    # an empty allow-set still issues the IN [] filter (matches nothing) — never "no filter"
    http = RecordingHttp([HttpResponse(200, json.dumps({"results": []}))])
    _store(http).all_communities(allowed_labels=frozenset())

    assert "WHERE c.tier IN $allowed" in http.last_query()
    assert http.last_params()["allowed"] == []


def _props(cid: str, tier: str, size: int) -> dict:
    return {
        "id": cid,
        "title": f"{cid} title",
        "summary": f"summary of {cid}",
        "tier": tier,
        "size": size,
        "entity_ids": json.dumps([f"{cid}-e{i}" for i in range(size)]),
    }


# --- backend-identical: in-memory mirrors the Neptune clearance gate ---------------------


def _seed_memory() -> MemoryCommunityStore:
    s = MemoryCommunityStore()
    s.upsert_community(_community("community-0", Visibility.PUBLIC.value, size=3))
    s.upsert_community(_community("community-1", Visibility.INTERNAL.value, size=2))
    s.upsert_community(_community("community-2", Visibility.RESTRICTED.value, size=1))
    return s


def test_memory_unrestricted_returns_all_largest_first() -> None:
    s = _seed_memory()
    assert [c.id for c in s.all_communities()] == ["community-0", "community-1", "community-2"]


def test_memory_clearance_gates_by_composed_tier() -> None:
    s = _seed_memory()
    # public-reader clearance (public only) sees only the public community
    out = s.all_communities(allowed_labels=frozenset({"public"}))
    assert [c.id for c in out] == ["community-0"]
    # member clearance (public+internal) sees two, not the restricted one
    out2 = s.all_communities(allowed_labels=frozenset({"public", "internal"}))
    assert [c.id for c in out2] == ["community-0", "community-1"]


def test_memory_empty_clearance_is_fail_closed() -> None:
    assert _seed_memory().all_communities(allowed_labels=frozenset()) == []


def test_memory_set_and_lookup_community_id() -> None:
    s = MemoryCommunityStore()
    s.set_community_id("sig:sig-network", "community-0")
    assert s.community_of("sig:sig-network") == "community-0"
    assert s.community_of("missing") is None


def test_memory_clear_and_count() -> None:
    s = _seed_memory()
    assert s.count() == 3
    s.clear()
    assert s.count() == 0 and s.all_communities() == []
