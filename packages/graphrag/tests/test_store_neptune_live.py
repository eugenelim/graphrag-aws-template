"""Live Neptune round-trip — the real insert-then-retrieve the mock can't prove.

Gated: runs ONLY when `GRAPHRAG_NEPTUNE_ENDPOINT` is set (an https Neptune
endpoint reachable from the runner — i.e. in-VPC / CI, not a laptop, since the
cluster has no public endpoint). Skipped everywhere else.

This closes the narcissistic-mock gap in `test_store_neptune.py`: it upserts real
nodes + an edge into the deployed cluster, reads them back via `neighbors()`, and
asserts the round-trip — then cleans up after itself (idempotent, unique ids).
"""

from __future__ import annotations

import os
import uuid

import pytest

from graphrag.model import Direction, Edge, EdgeKind, EntityKind, Node

ENDPOINT = os.environ.get("GRAPHRAG_NEPTUNE_ENDPOINT")
REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

pytestmark = pytest.mark.skipif(
    not ENDPOINT,
    reason="set GRAPHRAG_NEPTUNE_ENDPOINT (reachable in-VPC) to run the live round-trip",
)


@pytest.fixture
def store():
    from graphrag.store.neptune import NeptuneGraphStore

    s = NeptuneGraphStore(ENDPOINT, REGION)  # type: ignore[arg-type]
    run = uuid.uuid4().hex[:8]
    person = f"person:livetest-{run}"
    sig = f"sig:livetest-{run}"
    yield s, person, sig
    # Cleanup: remove just this run's test nodes (parameterized DETACH DELETE).
    s._run(
        "MATCH (n:Entity) WHERE n.id IN $ids DETACH DELETE n",
        {"ids": [person, sig]},
    )


def test_live_insert_then_retrieve(store) -> None:
    s, person, sig = store
    s.upsert_node(Node(person, EntityKind.PERSON, {"name": "Live Test"}))
    s.upsert_node(Node(sig, EntityKind.SIG))
    s.upsert_edge(Edge(person, sig, EdgeKind.TECH_LEADS))

    # Retrieve the node directly.
    got = s.get_node(person)
    assert got is not None and got.kind is EntityKind.PERSON
    assert got.props.get("name") == "Live Test"

    # Retrieve via a real one-hop traversal — the same primitive the CLI uses.
    neighbors = s.neighbors(person, EdgeKind.TECH_LEADS, Direction.OUT)
    assert [n.id for n in neighbors] == [sig]
