"""Neptune openCypher adapter — the deployed graph backend.

Security posture (spec AC7):

- **Parameterized queries only.** Values (node ids, edge kinds, property maps) ride
  the openCypher ``parameters`` map, never string-interpolated into the query. Even
  the relationship *type* is not interpolated: every edge is a single ``REL`` type
  carrying a ``kind`` property, so ``kind`` stays a bound parameter (relationship
  types are not parameterizable in openCypher, and interpolating them would be the
  injection vector).
- **HTTPS-enforced with TLS verification on.** A non-``https://`` endpoint is
  rejected; ``verify`` defaults to ``True``.
- **IAM-mediated.** Requests are SigV4-signed with credentials resolved from the
  default botocore provider chain (the Fargate task role) — there is no plaintext
  credential read at the call site.

The HTTP client is injectable so the adapter is testable against a mock without a
live cluster.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

from ..model import Direction, Edge, EdgeKind, EntityKind, Node
from .base import GraphStore, NeighborEdge

NEPTUNE_SERVICE = "neptune-db"
_NODE_LABEL = "Entity"
_REL_TYPE = "REL"


@dataclass
class HttpResponse:
    status: int
    text: str


class HttpClient(Protocol):
    def post(
        self, url: str, *, data: bytes, headers: dict[str, str], verify: bool
    ) -> HttpResponse: ...


class _UrllibClient:
    """Default HTTP client over urllib (TLS verified unless ``verify=False``).

    ``timeout`` is the per-request read timeout in seconds. It defaults to 30 (a
    single Neptune openCypher hop), but a multi-step caller — e.g. the CLI's live
    Function-URL hybrid query — constructs it with a longer timeout."""

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def post(self, url: str, *, data: bytes, headers: dict[str, str], verify: bool) -> HttpResponse:
        # The endpoint scheme is validated as https:// in NeptuneGraphStore.__init__,
        # so this is not an arbitrary-scheme open.
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
        context = ssl.create_default_context()
        if not verify:  # opt-in only; never the default
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=context, timeout=self._timeout) as resp:  # noqa: S310
            return HttpResponse(status=resp.status, text=resp.read().decode("utf-8"))


def _visibility_predicate(params: dict[str, object], allowed_labels: frozenset[str] | None) -> str:
    """The slice-4 permission filter as a bare openCypher predicate, parameterized.

    Returns ``"r.visibility IN $allowed AND b.visibility IN $allowed"`` and binds the tier
    list onto ``params['allowed']`` — the values ride the ``parameters`` map, never
    string-interpolated (the injection vector). Returns ``""`` (binding nothing) when
    ``allowed_labels`` is ``None`` (unfiltered). The caller supplies the ``WHERE``/``AND``
    connector. The edge predicate subsumes the node check (``compose=max`` + downward-closed
    clearance); the ``b.visibility`` conjunct is a defensive guard against a stale edge
    label, matching the in-memory store's predicate exactly.
    """
    if allowed_labels is None:
        return ""
    params["allowed"] = sorted(allowed_labels)
    return "r.visibility IN $allowed AND b.visibility IN $allowed"


# The slice-5 document-provenance set rides a single JSON-string scalar property (Neptune has
# no native set property), so it round-trips losslessly through _scalar_props' string passthrough.
_DOC_PATHS_PROP = "doc_paths"


def _scalar_props(props: dict[str, object]) -> dict[str, object]:
    """Neptune properties must be scalars; drop ``None`` and stringify the rest."""
    out: dict[str, object] = {}
    for key, value in props.items():
        if value is None:
            continue
        out[key] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return out


def _write_props(props: dict[str, object], doc_paths: set[str]) -> dict[str, object]:
    """The scalar property bag written to Neptune, with ``doc_paths`` encoded as a JSON string
    (slice-5 provenance round-trip)."""
    out = _scalar_props(props)
    out[_DOC_PATHS_PROP] = json.dumps(sorted(doc_paths))
    return out


def _decode_doc_paths(props: dict[str, Any]) -> set[str]:
    """Pop and decode the JSON-string ``doc_paths`` property (empty set if absent — a
    pre-slice-5 row)."""
    raw = props.pop(_DOC_PATHS_PROP, None)
    if not raw:
        return set()
    try:
        return {str(p) for p in json.loads(raw)}
    except (TypeError, ValueError):
        return set()


def _node_from_result(obj: dict[str, Any]) -> Node:
    # sources is intentionally not round-tripped from Neptune; doc_paths (slice 5) IS — the
    # delta's orphan reconciliation reads it back on the live path.
    props = dict(obj.get("~properties", {}))
    if "id" not in props or "kind" not in props:
        raise RuntimeError(f"Neptune node result missing id/kind: {obj!r}")
    node_id = str(props.pop("id"))
    kind = EntityKind(str(props.pop("kind")))
    doc_paths = _decode_doc_paths(props)
    return Node(id=node_id, kind=kind, props=props, doc_paths=doc_paths)


class NeptuneGraphStore(GraphStore):
    def __init__(
        self,
        endpoint: str,
        region: str,
        *,
        session: Session | None = None,
        http_client: HttpClient | None = None,
        verify: bool = True,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError(f"Neptune endpoint must be https://, got {endpoint!r}")
        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self.verify = verify
        self._session = session or Session()
        self._http = http_client or _UrllibClient()

    def _run(self, query: str, params: dict[str, object]) -> dict[str, Any]:
        url = f"{self.endpoint}/openCypher"
        body = json.dumps({"query": query, "parameters": json.dumps(params)}).encode("utf-8")
        request = AWSRequest(
            method="POST", url=url, data=body, headers={"Content-Type": "application/json"}
        )
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("no AWS credentials resolved from the default provider chain")
        SigV4Auth(credentials, NEPTUNE_SERVICE, self.region).add_auth(request)
        resp = self._http.post(url, data=body, headers=dict(request.headers), verify=self.verify)
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"Neptune openCypher {resp.status}: {resp.text}")
        return json.loads(resp.text)

    def upsert_node(self, node: Node) -> None:
        self._run(
            f"MERGE (n:{_NODE_LABEL} {{id: $id}}) SET n.kind = $kind, n += $props",
            {
                "id": node.id,
                "kind": node.kind.value,
                "props": _write_props(node.props, node.doc_paths),
            },
        )

    def upsert_edge(self, edge: Edge) -> None:
        self._run(
            f"MATCH (a:{_NODE_LABEL} {{id: $src}}), (b:{_NODE_LABEL} {{id: $dst}}) "
            f"MERGE (a)-[r:{_REL_TYPE} {{kind: $kind}}]->(b) SET r += $props",
            {
                "src": edge.src_id,
                "dst": edge.dst_id,
                "kind": edge.kind.value,
                "props": _write_props(edge.props, edge.doc_paths),
            },
        )

    def get_node(self, node_id: str) -> Node | None:
        res = self._run(f"MATCH (n:{_NODE_LABEL} {{id: $id}}) RETURN n", {"id": node_id})
        rows = res.get("results", [])
        return _node_from_result(rows[0]["n"]) if rows else None

    def neighbors(
        self,
        node_id: str,
        edge_kind: EdgeKind,
        direction: Direction,
        *,
        allowed_labels: frozenset[str] | None = None,
    ) -> list[Node]:
        if direction is Direction.OUT:
            pattern = (
                f"(a:{_NODE_LABEL} {{id: $id}})-[r:{_REL_TYPE} {{kind: $kind}}]->(b:{_NODE_LABEL})"
            )
        else:
            pattern = (
                f"(a:{_NODE_LABEL} {{id: $id}})<-[r:{_REL_TYPE} {{kind: $kind}}]-(b:{_NODE_LABEL})"
            )
        params: dict[str, object] = {"id": node_id, "kind": edge_kind.value}
        pred = _visibility_predicate(params, allowed_labels)
        where = f" WHERE {pred}" if pred else ""
        res = self._run(f"MATCH {pattern}{where} RETURN b", params)
        return [_node_from_result(row["b"]) for row in res.get("results", [])]

    def neighbors_batch(
        self, node_ids: list[str], *, allowed_labels: frozenset[str] | None = None
    ) -> list[NeighborEdge]:
        """Batched neighborhood for the whole frontier in **two** openCypher queries
        (one per direction), instead of the default fan-out's O(nodes x kinds x 2)
        round-trips — the live-perf path for seed-and-expand against Neptune.

        Parameterized exactly like ``neighbors()``: the frontier ids ride the
        ``$ids`` list parameter; the node label and single ``REL`` type are fixed
        constants (never interpolated), so there is no injection surface. The edge's
        ``kind`` is a bound property, returned and re-typed to ``EdgeKind``.
        """
        if not node_ids:
            return []
        out: list[NeighborEdge] = []
        patterns = {
            Direction.OUT: f"(a:{_NODE_LABEL})-[r:{_REL_TYPE}]->(b:{_NODE_LABEL})",
            Direction.IN: f"(a:{_NODE_LABEL})<-[r:{_REL_TYPE}]-(b:{_NODE_LABEL})",
        }
        params: dict[str, object] = {"ids": node_ids}
        # Slice-4 permission filter, applied DURING traversal on the edge (parameterized,
        # never interpolated): a forbidden neighbor is excluded server-side, so it never
        # enters the frontier. Same predicate as the in-memory store.
        pred = _visibility_predicate(params, allowed_labels)
        where_vis = f" AND {pred}" if pred else ""
        for direction, pattern in patterns.items():
            res = self._run(
                f"MATCH {pattern} WHERE a.id IN $ids{where_vis} "
                "RETURN a.id AS src, r.kind AS kind, b AS node",
                params,
            )
            for row in res.get("results", []):
                out.append(
                    NeighborEdge(
                        src_id=str(row["src"]),
                        edge_kind=EdgeKind(str(row["kind"])),
                        direction=direction,
                        neighbor=_node_from_result(row["node"]),
                    )
                )
        return out

    def all_nodes(self) -> list[Node]:
        res = self._run(f"MATCH (n:{_NODE_LABEL}) RETURN n", {})
        return [_node_from_result(row["n"]) for row in res.get("results", [])]

    def all_edges(self) -> list[Edge]:
        res = self._run(
            f"MATCH (a:{_NODE_LABEL})-[r:{_REL_TYPE}]->(b:{_NODE_LABEL}) "
            "RETURN a.id AS src, b.id AS dst, r.kind AS kind, r.doc_paths AS doc_paths",
            {},
        )
        edges: list[Edge] = []
        for row in res.get("results", []):
            edges.append(
                Edge(
                    str(row["src"]),
                    str(row["dst"]),
                    EdgeKind(str(row["kind"])),
                    doc_paths=_decode_doc_paths({_DOC_PATHS_PROP: row.get("doc_paths")}),
                )
            )
        return edges

    def delete_node(self, node_id: str) -> None:
        """Delete a node and its incident edges (slice-5 orphan removal).

        ``DETACH DELETE`` removes the node and every relationship it touches in one statement,
        so no dangling edge survives. Parameterized on ``$id`` (the label/type are fixed
        constants, never interpolated)."""
        self._run(f"MATCH (n:{_NODE_LABEL} {{id: $id}}) DETACH DELETE n", {"id": node_id})

    def delete_edge(self, src_id: str, kind: EdgeKind, dst_id: str) -> None:
        """Delete one edge by its full ``(src, kind, dst)`` identity (slice-5 orphan removal).

        Every leg is bound (``$src``/``$kind``/``$dst``) so exactly the identified edge is
        deleted — never all edges of a kind."""
        self._run(
            f"MATCH (a:{_NODE_LABEL} {{id: $src}})-[r:{_REL_TYPE} {{kind: $kind}}]->"
            f"(b:{_NODE_LABEL} {{id: $dst}}) DELETE r",
            {"src": src_id, "kind": kind.value, "dst": dst_id},
        )

    def clear(self) -> None:
        """Remove every node and edge — the ``--rebuild`` ground-truth reset (slice 5)."""
        self._run(f"MATCH (n:{_NODE_LABEL}) DETACH DELETE n", {})
