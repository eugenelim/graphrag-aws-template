"""T2 — dual-form execution identity: openCypher (Neptune) == app-layer (in-memory) (AC2).

For a given bound parameter set, the template's parameterized openCypher (run on the
mocked Neptune backend) and its paired app-layer ``evaluate`` (run on the in-memory store)
return the **same sorted node set** — the ``neighbors_batch`` invariant. Pinned on the
``sig:sig-network -OWNS-> {kep-1880, kep-2086}`` exemplar.

# STUB: AC2
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from botocore.credentials import Credentials

from graphrag.governed import execute_template
from graphrag.resolve import resolve
from graphrag.sources import load_corpus
from graphrag.store import MemoryGraphStore
from graphrag.store.neptune import HttpResponse, NeptuneGraphStore
from graphrag.templates import get_template


class _FakeSession:
    def get_credentials(self) -> Credentials:
        return Credentials("AKIDEXAMPLE", "secretkey", "token")


class _RecordingHttp:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def post(self, url, *, data, headers, verify) -> HttpResponse:
        self.calls.append({"data": data})
        return self._responses.pop(0)

    def last_query(self) -> str:
        return json.loads(self.calls[-1]["data"])["query"]

    def last_params(self) -> dict:
        return json.loads(json.loads(self.calls[-1]["data"])["parameters"])


def _mem_store(community_root: Path, enhancements_root: Path) -> MemoryGraphStore:
    return MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))


def test_app_layer_evaluator_returns_sorted_owned_keps(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _mem_store(community_root, enhancements_root)
    template = get_template("sig_owned_keps")
    assert template is not None
    rows = execute_template(store, template, {"sig": "sig:sig-network"})
    ids = [n.id for n in rows]
    assert ids == ["kep-1880", "kep-2086"]  # sorted, deduped


# Every template, with a param that has rows in the fixture — the four most likely to drift
# between cypher and evaluator (incoming edge, two edge kinds + a node-kind filter, inbound).
_TEMPLATE_CASES = [
    ("sig_owned_keps", {"sig": "sig:sig-network"}),
    ("sig_tech_leads", {"sig": "sig:sig-network"}),
    ("person_led_sigs", {"person": "person:thockin"}),
    ("kep_owning_sig", {"kep": "kep-2086"}),
]


@pytest.mark.parametrize(("template_id", "params"), _TEMPLATE_CASES)
def test_neptune_path_matches_app_layer_for_every_template(
    community_root: Path, enhancements_root: Path, template_id: str, params: dict[str, str]
) -> None:
    template = get_template(template_id)
    assert template is not None
    # The app-layer result is the ground truth the openCypher form must equal (the dual-form
    # identity, AC2 — covers each evaluator's edge direction / kind filter, not just one).
    app_rows = execute_template(_mem_store(community_root, enhancements_root), template, params)
    app_ids = [n.id for n in app_rows]
    assert app_ids, f"{template_id} produced no rows for {params} — bad test fixture"

    # Neptune returns the same nodes under alias ``n``, unsorted on the wire; execute_template
    # sorts both backends so the result is byte-identical. Feed the rows reversed to prove the sort.
    neptune_rows = {
        "results": [
            {"n": {"~properties": {"id": n.id, "kind": n.kind.value}}} for n in reversed(app_rows)
        ]
    }
    http = _RecordingHttp([HttpResponse(200, json.dumps(neptune_rows))])
    store = NeptuneGraphStore(
        "https://neptune.example:8182", "us-east-1", session=_FakeSession(), http_client=http
    )
    rows = execute_template(store, template, params)

    assert [n.id for n in rows] == app_ids  # identical sorted set across backends
    # the governed (parameterized) cypher ran: the value is bound, not interpolated.
    assert http.last_query() == template.cypher
    assert http.last_params() == params
    for value in params.values():
        assert value not in http.last_query()
