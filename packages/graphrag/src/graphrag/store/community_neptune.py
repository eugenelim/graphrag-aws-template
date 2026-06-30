"""Neptune adapter for the community store — the deployed backend (global-community-summary).

Writes/reads ``Community``-labeled nodes on the **existing** Neptune cluster (no new
service — ADR-0002/ADR-0005) and stamps ``communityId`` on member ``Entity`` nodes. It
reuses ``store.neptune``'s SigV4/HTTPS plumbing (``HttpClient``/``HttpResponse``/
``_UrllibClient``/``NEPTUNE_SERVICE`` and the ``Entity`` label constant) so the security
posture is identical to the graph adapter:

- **Parameterized openCypher only.** Community/entity ids, the summary, tier, size, and the
  clearance allow-list ride the ``parameters`` map — never string-interpolated (the
  ``Community`` label is a fixed constant). ``ruff`` ``S`` stays enabled.
- **HTTPS-enforced with TLS verification on**; a non-``https://`` endpoint is rejected.
- **IAM-mediated** via SigV4 from the default botocore chain (the task / Lambda role). The
  ingest task role (read-write) writes communities; the query Lambda role (read-only,
  ADR-0004) only ever calls ``all_communities`` (a read).

``entity_ids`` round-trips as a single JSON-string scalar (Neptune has no native list
property — the ``store.neptune`` ``doc_paths`` precedent).
"""

from __future__ import annotations

import json
from typing import Any

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

from .community_base import Community, CommunityStore
from .neptune import _NODE_LABEL, NEPTUNE_SERVICE, HttpClient, _UrllibClient

_COMMUNITY_LABEL = "Community"


def _decode_entity_ids(raw: object) -> tuple[str, ...]:
    """Decode the JSON-string ``entity_ids`` scalar back to a tuple (empty if absent/garbled)."""
    if not raw:
        return ()
    try:
        return tuple(str(e) for e in json.loads(str(raw)))
    except (TypeError, ValueError):
        return ()


def _community_from_props(props: dict[str, Any]) -> Community:
    return Community(
        id=str(props["id"]),
        title=str(props.get("title", "")),
        summary=str(props.get("summary", "")),
        entity_ids=_decode_entity_ids(props.get("entity_ids")),
        tier=str(props.get("tier", "")),
        size=int(props.get("size", 0)),
        doc_paths=_decode_entity_ids(props.get("doc_paths")),
    )


class NeptuneCommunityStore(CommunityStore):
    """A ``CommunityStore`` over Neptune openCypher — ``Community`` nodes on the live cluster."""

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
        # Mirrors NeptuneGraphStore._run exactly (TLS, SigV4, parameter map) — the same
        # signed-HTTPS openCypher posture, kept self-contained to the community concern.
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

    def upsert_community(self, community: Community) -> None:
        self._run(
            f"MERGE (c:{_COMMUNITY_LABEL} {{id: $id}}) "
            "SET c.title = $title, c.summary = $summary, c.tier = $tier, "
            "c.size = $size, c.entity_ids = $entity_ids, c.doc_paths = $doc_paths",
            {
                "id": community.id,
                "title": community.title,
                "summary": community.summary,
                "tier": community.tier,
                "size": community.size,
                "entity_ids": json.dumps(list(community.entity_ids)),
                "doc_paths": json.dumps(list(community.doc_paths)),
            },
        )

    def set_community_id(self, entity_id: str, community_id: str) -> None:
        # The Entity label is the fixed graph-node constant (never interpolated); only the
        # ids ride the parameter map.
        self._run(
            f"MATCH (n:{_NODE_LABEL} {{id: $id}}) SET n.communityId = $cid",
            {"id": entity_id, "cid": community_id},
        )

    def all_communities(self, *, allowed_labels: frozenset[str] | None = None) -> list[Community]:
        # Clearance gate applied server-side, parameterized: None ⇒ no filter (all); an empty
        # set ⇒ `c.tier IN []` which matches nothing (fail-closed). The summary blends all
        # members, so the gate is the community's composed (most-restrictive) tier.
        params: dict[str, object] = {}
        where = ""
        if allowed_labels is not None:
            params["allowed"] = sorted(allowed_labels)
            where = " WHERE c.tier IN $allowed"
        res = self._run(f"MATCH (c:{_COMMUNITY_LABEL}){where} RETURN c", params)
        communities = [
            _community_from_props(dict(row["c"]["~properties"])) for row in res.get("results", [])
        ]
        communities.sort(key=lambda c: (-c.size, c.id))
        return communities

    def count(self) -> int:
        res = self._run(f"MATCH (c:{_COMMUNITY_LABEL}) RETURN count(c) AS n", {})
        rows = res.get("results", [])
        return int(rows[0]["n"]) if rows else 0

    def clear(self) -> None:
        """Remove every community node **and** the ``communityId`` stamp on every member entity
        (the full-ingest / ``--rebuild`` reset). Both are cleared so a re-detection cannot leave
        a stale ``Community`` node or a stale member stamp — keeping this symmetric with
        ``MemoryCommunityStore.clear`` (the backend-identical invariant; the two cannot disagree
        after a rebuild)."""
        self._run(f"MATCH (c:{_COMMUNITY_LABEL}) DETACH DELETE c", {})
        self._run(f"MATCH (n:{_NODE_LABEL}) REMOVE n.communityId", {})
