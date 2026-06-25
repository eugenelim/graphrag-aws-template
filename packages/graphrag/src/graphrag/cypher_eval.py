"""A bounded read-subset evaluator for offline text2cypher execution (AC4).

There is **no high-fidelity local Neptune emulator** — AWS ships none, the openCypher-on-local
bridges (cypher-for-gremlin, Kùzu) are abandoned, and Neo4j/Memgraph are both low-fidelity to
Neptune's dialect *and* heavyweight (Docker/JVM), which would break this repo's pure-Python,
laptop-runnable, PyYAML-free posture (see ``docs/architecture/develop-and-test-offline.md`` and
ADR-0004). So the offline path runs model-authored queries through a **small, pure-Python
evaluator over a bounded read grammar**, and **live Neptune is the execution-fidelity oracle**.

This evaluator is explicitly a SUBSET — it is *not* an openCypher engine and never claims
Neptune dialect fidelity. It runs exactly the shapes the offline ``RuleText2CypherGenerator``
emits, over the ``GraphStore`` seam:

- node by id:        ``MATCH (n:Entity {id: 'X'}) RETURN n``
- nodes by kind:     ``MATCH (n:Entity) WHERE n.kind = 'K' RETURN n``
- one hop (out/in):  ``MATCH (a:Entity {id: 'X'})-[:REL {kind: 'EK'}]->(n:Entity) RETURN n``
                     (and the ``<-...-`` in-direction), returning the far node as ``n``.

``ORDER BY`` is ignored (results are sorted by node id regardless — backend-independent), and a
trailing ``LIMIT k`` is honored. Anything outside this grammar raises ``UnsupportedOfflineQuery``,
which the orchestrator surfaces as "runs live, not in the offline subset" — never as false rows.
"""

from __future__ import annotations

import re

from .model import Direction, EdgeKind, EntityKind, Node
from .store.base import GraphStore

_NODE_BY_ID = re.compile(
    r"^MATCH\s*\(\s*n\s*:\s*Entity\s*\{\s*id\s*:\s*'([^']+)'\s*\}\s*\)\s+RETURN\s+n\b",
    re.IGNORECASE,
)
_NODES_BY_KIND = re.compile(
    r"^MATCH\s*\(\s*n\s*:\s*Entity\s*\)\s+WHERE\s+n\.kind\s*=\s*'([^']+)'\s+RETURN\s+n\b",
    re.IGNORECASE,
)
_HOP_OUT = re.compile(
    r"^MATCH\s*\(\s*\w+\s*:\s*Entity\s*\{\s*id\s*:\s*'([^']+)'\s*\}\s*\)\s*-\s*"
    r"\[\s*\w*\s*:\s*REL\s*\{\s*kind\s*:\s*'([^']+)'\s*\}\s*\]\s*->\s*"
    r"\(\s*n\s*:\s*Entity\s*\)\s+RETURN\s+n\b",
    re.IGNORECASE,
)
_HOP_IN = re.compile(
    r"^MATCH\s*\(\s*\w+\s*:\s*Entity\s*\{\s*id\s*:\s*'([^']+)'\s*\}\s*\)\s*<-\s*"
    r"\[\s*\w*\s*:\s*REL\s*\{\s*kind\s*:\s*'([^']+)'\s*\}\s*\]\s*-\s*"
    r"\(\s*n\s*:\s*Entity\s*\)\s+RETURN\s+n\b",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)


class UnsupportedOfflineQuery(Exception):
    """A (read-only, valid) query is outside the offline subset — it runs live on Neptune, not
    against the in-memory evaluator. The orchestrator turns this into a narratable refusal, not
    false rows."""


def _dedupe_sorted(nodes: list[Node]) -> list[Node]:
    by_id: dict[str, Node] = {}
    for node in nodes:
        by_id.setdefault(node.id, node)
    return [by_id[node_id] for node_id in sorted(by_id)]


def _edge_kind(raw: str) -> EdgeKind:
    try:
        return EdgeKind(raw)
    except ValueError as exc:
        raise UnsupportedOfflineQuery(f"unknown edge kind {raw!r}") from exc


def _dispatch(text: str, store: GraphStore) -> list[Node]:
    hop_out = _HOP_OUT.match(text)
    if hop_out is not None:
        return list(store.neighbors(hop_out.group(1), _edge_kind(hop_out.group(2)), Direction.OUT))
    hop_in = _HOP_IN.match(text)
    if hop_in is not None:
        return list(store.neighbors(hop_in.group(1), _edge_kind(hop_in.group(2)), Direction.IN))
    by_kind = _NODES_BY_KIND.match(text)
    if by_kind is not None:
        try:
            kind = EntityKind(by_kind.group(1))
        except ValueError as exc:
            raise UnsupportedOfflineQuery(f"unknown node kind {by_kind.group(1)!r}") from exc
        return [n for n in store.all_nodes() if n.kind is kind]
    by_id = _NODE_BY_ID.match(text)
    if by_id is not None:
        node = store.get_node(by_id.group(1))
        return [node] if node is not None else []
    raise UnsupportedOfflineQuery(
        "query is outside the offline read subset (node-by-id, nodes-by-kind, one-hop REL)"
    )


def eval_read_query(cypher: str, store: GraphStore) -> list[Node]:
    """Run a bounded-subset read query over ``store`` and return its ``RETURN n`` nodes, sorted
    by id (backend-independent) and capped by a trailing ``LIMIT``. Raises
    ``UnsupportedOfflineQuery`` for anything outside the subset (AC4)."""
    text = " ".join(cypher.strip().split())  # collapse newlines/runs of whitespace
    nodes = _dedupe_sorted(_dispatch(text, store))
    limit_match = _LIMIT_RE.search(text)
    if limit_match is not None:
        nodes = nodes[: int(limit_match.group(1))]
    return nodes
