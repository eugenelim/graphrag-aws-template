"""In-VPC Neptune smoke probe (invoked on demand via `aws lambda invoke`).

The lightest secure way to verify the deployed graph store end-to-end: a
scale-to-zero Lambda in the private subnets that upserts a unique node + edge into
Neptune, reads them back through the **same** ``NeptuneGraphStore`` the CLI uses,
cleans up, and returns the trace. No public endpoint, no NAT, no standing cost;
credentials come from the execution role via the botocore chain. Because it reuses
the real adapter, a green result proves the actual openCypher works against
Neptune -- not a reimplementation.

Deployed by the CDK stack as ``Code.from_asset`` over this package (pure-Python;
boto3/botocore are in the Lambda runtime). Only the model/store/query modules are
imported here, none of which pull in PyYAML.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from .model import Direction, Edge, EdgeKind, EntityKind, Node
from .store.neptune import NeptuneGraphStore


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    endpoint = os.environ["NEPTUNE_ENDPOINT"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    store = NeptuneGraphStore(endpoint, region)

    run = uuid.uuid4().hex[:8]
    person, sig = f"person:smoke-{run}", f"sig:smoke-{run}"
    try:
        # Insert.
        store.upsert_node(Node(person, EntityKind.PERSON, {"name": "smoke"}))
        store.upsert_node(Node(sig, EntityKind.SIG))
        store.upsert_edge(Edge(person, sig, EdgeKind.TECH_LEADS))
        # Retrieve, two ways: direct lookup and a real one-hop traversal.
        got = store.get_node(person)
        neighbors = [n.id for n in store.neighbors(person, EdgeKind.TECH_LEADS, Direction.OUT)]
        ok = got is not None and got.props.get("name") == "smoke" and neighbors == [sig]
        return {
            "ok": ok,
            "run": run,
            "retrieved_node": got.id if got else None,
            "neighbors": neighbors,
        }
    finally:
        # Clean up just this run's probe nodes (parameterized).
        store._run("MATCH (n:Entity) WHERE n.id IN $ids DETACH DELETE n", {"ids": [person, sig]})
