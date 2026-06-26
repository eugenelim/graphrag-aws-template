"""Amazon OpenSearch nested parent-child adapter — the deployed backend (parent-child slice).

Each parent document holds its child chunks as a ``nested`` array (each child a
``knn_vector``) plus the parent's full prose in an app-stored ``body`` field. A nested k-NN
query matches a **child** vector during the ANN scan (Lucene HNSW — the engine the flat index
already uses, RFC-0001 §3/§4), scores the parent by its **best** child (``score_mode: max``),
and returns the parent document; ``inner_hits`` surfaces which child matched (the trace). The
parent document *is* the returned unit, so there is no cross-document ``has_child`` join and no
duplicate-parent dedup is needed (RFC-0001 §3).

Security posture mirrors the flat OpenSearch adapter (``store/opensearch.py``) — it reuses that
module's ``HttpClient``/``HttpResponse``/``_UrllibClient`` plumbing and the single
``OPENSEARCH_SERVICE`` SigV4 signing-service constant:

- **Parameterized request bodies only.** The query vector, ``k``, and visibility filter values
  ride the JSON body; nothing caller-supplied is interpolated into the path or query string.
- **HTTPS-enforced, TLS verification on by default.** A non-``https://`` endpoint is rejected;
  ``verify`` defaults to ``True``.
- **IAM-mediated.** Requests are SigV4-signed for service ``es`` with credentials from the
  default botocore provider chain (the task / Lambda role).
"""

from __future__ import annotations

import json
from typing import Any

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

from .opensearch import OPENSEARCH_SERVICE, HttpClient, _UrllibClient
from .parentchild_base import ChildVector, ParentChildStore, ParentDoc, ParentHit

DEFAULT_PARENT_INDEX = "graphrag-parents"
DEFAULT_DIMENSIONS = 256


def _parentchild_mapping(dimensions: int) -> dict[str, Any]:
    return {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "parent_id": {"type": "keyword"},
                "source": {"type": "keyword"},
                "doc_path": {"type": "keyword"},
                "heading": {"type": "text"},
                "entity_ids": {"type": "keyword"},
                # Slice-4 synthetic visibility (a teaching stand-in for an ACL) — keyword so the
                # permission terms-filter is exact-match. Parent-level (a document's chunks share
                # one tier). Lands on a fresh index only.
                "visibility": {"type": "keyword"},
                # The app-stored parent body — the full prose returned for synthesis (RFC-0001 §3;
                # not a has_child join — the app puts it here and reads it back).
                "body": {"type": "text"},
                "children": {
                    "type": "nested",
                    "properties": {
                        "child_id": {"type": "keyword"},
                        "heading": {"type": "text"},
                        "text": {"type": "text"},
                        # Lucene HNSW (same method block as the flat index): the child vector is
                        # matched DURING the nested ANN scan. `cosinesimil` is Lucene-supported on
                        # OpenSearch 2.11 (space unchanged from the flat index, no re-embed).
                        "vector": {
                            "type": "knn_vector",
                            "dimension": dimensions,
                            "method": {
                                "name": "hnsw",
                                "space_type": "cosinesimil",
                                "engine": "lucene",
                            },
                        },
                    },
                },
            }
        },
    }


class OpenSearchParentChildStore(ParentChildStore):
    def __init__(
        self,
        endpoint: str,
        region: str,
        *,
        index: str = DEFAULT_PARENT_INDEX,
        dimensions: int = DEFAULT_DIMENSIONS,
        session: Session | None = None,
        http_client: HttpClient | None = None,
        verify: bool = True,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError(f"OpenSearch endpoint must be https://, got {endpoint!r}")
        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self.index = index
        self.dimensions = dimensions
        self.verify = verify
        self._session = session or Session()
        self._http = http_client or _UrllibClient()

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.endpoint}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = AWSRequest(
            method=method, url=url, data=data, headers={"Content-Type": "application/json"}
        )
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("no AWS credentials resolved from the default provider chain")
        SigV4Auth(credentials, OPENSEARCH_SERVICE, self.region).add_auth(request)
        resp = self._http.request(
            method, url, data=data, headers=dict(request.headers), verify=self.verify
        )
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"OpenSearch {method} {path} -> {resp.status}: {resp.text}")
        return json.loads(resp.text) if resp.text else {}

    def create_index(self) -> None:
        """Create the nested parent-child index; idempotent (an already-exists 400 is fine)."""
        try:
            self._request("PUT", f"/{self.index}", _parentchild_mapping(self.dimensions))
        except RuntimeError as exc:
            if "resource_already_exists" not in str(exc):
                raise

    def index_parent(self, parent: ParentDoc, *, refresh: bool = False) -> None:
        path = f"/{self.index}/_doc"
        if refresh:  # make the doc immediately searchable
            path += "?refresh=true"
        self._request(
            "POST",
            path,
            {
                "parent_id": parent.parent_id,
                "source": parent.source,
                "doc_path": parent.doc_path,
                "heading": parent.heading,
                "entity_ids": list(parent.entity_ids),
                "visibility": parent.visibility,
                "body": parent.body,
                "children": [
                    {
                        "child_id": child.child_id,
                        "heading": child.heading,
                        "text": child.text,
                        "vector": child.vector,
                    }
                    for child in parent.children
                ],
            },
        )

    def search(
        self,
        vector: list[float],
        k: int,
        *,
        allowed_labels: frozenset[str] | None = None,
    ) -> list[ParentHit]:
        # The nested child match: a `knn` over `children.vector`, scoring the parent by its best
        # child (`score_mode: max`); `inner_hits` returns which child matched (for the trace).
        # The child vectors are excluded from `_source` (the parent body + child text/heading are
        # what synthesis + the trace need — the vectors would just bloat the response).
        nested: dict[str, Any] = {
            "nested": {
                "path": "children",
                "query": {"knn": {"children.vector": {"vector": vector, "k": k}}},
                "score_mode": "max",
                "inner_hits": {"_source": {"excludes": ["children.vector"]}, "size": 1},
            }
        }
        # The slice-4 permission filter is a parent-level `terms` clause composed AND with the
        # nested child match (a sibling clause, on the parent's `visibility` field). It rides the
        # request body, never interpolated. `allowed_labels=None` ⇒ no clause (unrestricted); an
        # EMPTY set ⇒ a terms clause matching nothing (the fail-closed permission semantics).
        if allowed_labels is not None:
            query: dict[str, Any] = {
                "bool": {
                    "must": [nested],
                    "filter": [{"terms": {"visibility": sorted(allowed_labels)}}],
                }
            }
        else:
            query = nested
        body = {"size": k, "_source": {"excludes": ["children.vector"]}, "query": query}
        res = self._request("POST", f"/{self.index}/_search", body)
        return [self._hit(h) for h in res.get("hits", {}).get("hits", [])]

    @staticmethod
    def _child_from_source(src: dict[str, Any]) -> ChildVector:
        return ChildVector(
            child_id=str(src.get("child_id", "")),
            heading=str(src.get("heading", "")),
            text=str(src.get("text", "")),
            # The query response excludes `children.vector`; the returned child carries no vector
            # (it isn't needed for synthesis or the trace).
            vector=list(src.get("vector") or []),
        )

    @classmethod
    def _hit(cls, hit: dict[str, Any]) -> ParentHit:
        src = hit.get("_source", {})
        children = tuple(cls._child_from_source(c) for c in src.get("children") or [])
        parent = ParentDoc(
            parent_id=str(src.get("parent_id", "")),
            source=str(src.get("source", "")),
            doc_path=str(src.get("doc_path", "")),
            heading=str(src.get("heading", "")),
            entity_ids=tuple(src.get("entity_ids") or []),
            visibility=str(src.get("visibility", "public")),
            body=str(src.get("body", "")),
            children=children,
        )
        # The matched child is the top `inner_hits` entry (the precise match); absent on a
        # malformed response, the trace simply shows "(none)".
        matched: ChildVector | None = None
        inner = hit.get("inner_hits", {}).get("children", {}).get("hits", {}).get("hits", [])
        if inner:
            matched = cls._child_from_source(inner[0].get("_source", {}))
        return ParentHit(parent=parent, score=float(hit.get("_score", 0.0)), matched_child=matched)

    def count(self) -> int:
        res = self._request("GET", f"/{self.index}/_count")
        return int(res.get("count", 0))

    def clear(self, *, refresh: bool = True) -> None:
        """Remove every parent (keep the index) — the ``--rebuild`` reset."""
        path = f"/{self.index}/_delete_by_query"
        if refresh:
            path += "?refresh=true"
        self._request("POST", path, {"query": {"match_all": {}}})
