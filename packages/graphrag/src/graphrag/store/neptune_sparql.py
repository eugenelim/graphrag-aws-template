"""Neptune SPARQL 1.1 adapter — the deployed RDF graph backend.

Security posture (ADR-0011):

- **Mutation denylist (layer 1, belt-and-suspenders).** Before any SELECT
  or CONSTRUCT query is executed the string is checked for SPARQL Update
  keywords (INSERT, DELETE, DROP, CLEAR, LOAD, CREATE). Failure raises
  ``ValueError``. The IAM ``ReadDataViaQuery``-only scope on
  ``mcp_lambda_role`` is the load-bearing control.
- **HTTPS-enforced with TLS verification on.** A non-``https://`` endpoint
  is rejected; ``verify`` defaults to ``True``.
- **IAM-mediated.** Requests are SigV4-signed with
  ``service = "neptune-db"``, credentials resolved from the default
  botocore provider chain (the Fargate / Lambda task role).

The HTTP client is injectable so the adapter is testable against a mock
without a live cluster.
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session
from rdflib import Graph

from .sparql_base import SparqlStore, check_read_only

NEPTUNE_SERVICE = "neptune-db"

# Denylist imported from sparql_base — single canonical definition.


@dataclass
class HttpResponse:
    status: int
    text: str


class HttpClient(Protocol):
    def post(
        self, url: str, *, data: bytes, headers: dict[str, str], verify: bool
    ) -> HttpResponse: ...


class _UrllibClient:
    """Default HTTP client over urllib (TLS-verified unless ``verify=False``).

    ``timeout`` is the per-request read timeout in seconds; defaults to 30.
    """

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def post(self, url: str, *, data: bytes, headers: dict[str, str], verify: bool) -> HttpResponse:
        # The endpoint scheme is validated as https:// in
        # NeptuneSparqlStore.__init__, so this is not an arbitrary-scheme open.
        req = urllib.request.Request(  # noqa: S310
            url, data=data, headers=headers, method="POST"
        )
        context = ssl.create_default_context()
        if not verify:  # opt-in only; never the default
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(  # noqa: S310
            req, context=context, timeout=self._timeout
        ) as resp:
            return HttpResponse(status=resp.status, text=resp.read().decode("utf-8"))


class NeptuneSparqlStore(SparqlStore):
    """Live Neptune SPARQL 1.1 client (SigV4-signed POST to /sparql).

    ``sparql_select`` and ``sparql_construct`` enforce the ADR-0011 mutation
    denylist before issuing any HTTP request. ``sparql_update`` and
    ``load_turtle`` are the ingestion-role-only write path — callers are
    responsible for using them only under ``ingestion_task_role``.
    """

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

    def _credentials(self) -> Any:
        creds = self._session.get_credentials()
        if creds is None:
            raise RuntimeError("no AWS credentials resolved from the default provider chain")
        return creds

    def _post_signed(self, body: bytes, headers: dict[str, str]) -> HttpResponse:
        url = f"{self.endpoint}/sparql"
        request = AWSRequest(method="POST", url=url, data=body, headers=headers)
        SigV4Auth(self._credentials(), NEPTUNE_SERVICE, self.region).add_auth(request)
        resp = self._http.post(url, data=body, headers=dict(request.headers), verify=self.verify)
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"Neptune SPARQL {resp.status}: {resp.text}")
        return resp

    def sparql_select(self, query: str) -> list[dict[str, Any]]:
        """Execute a SPARQL SELECT query; return a list of binding dicts.

        Raises ``ValueError`` if the query contains a mutation keyword.
        """
        check_read_only(query)
        body = urllib.parse.urlencode({"query": query}).encode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/sparql-results+json",
        }
        resp = self._post_signed(body, headers)
        data = json.loads(resp.text)
        bindings: list[dict[str, Any]] = data.get("results", {}).get("bindings", [])
        return [{k: v["value"] for k, v in row.items()} for row in bindings]

    def sparql_construct(self, query: str) -> Graph:
        """Execute a SPARQL CONSTRUCT query; return an rdflib Graph.

        Raises ``ValueError`` if the query contains a mutation keyword.
        """
        check_read_only(query)
        body = urllib.parse.urlencode({"query": query}).encode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/turtle",
        }
        resp = self._post_signed(body, headers)
        g = Graph()
        g.parse(data=resp.text, format="turtle")
        return g

    def sparql_update(self, update: str) -> None:
        """Execute a SPARQL Update statement (ingestion role only)."""
        body = urllib.parse.urlencode({"update": update}).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        self._post_signed(body, headers)

    def load_turtle(self, ttl: str, named_graph: str) -> None:
        """Parse Turtle and insert all triples into ``named_graph`` via SPARQL Update.

        Triples are re-serialized as N-Triples before embedding in
        ``INSERT DATA { GRAPH <named_graph> { ... } }`` — N-Triples values
        are fully escaped so there is no injection surface from the Turtle
        content.  ``named_graph`` must be a well-formed absolute URI
        (no ``>`` or whitespace); this is validated before interpolation.
        """
        if ">" in named_graph or any(c in named_graph for c in " \t\n\r"):
            raise ValueError(f"invalid named_graph URI: {named_graph!r}")
        g = Graph()
        g.parse(data=ttl, format="turtle")
        ntriples = g.serialize(format="nt")
        update = f"INSERT DATA {{ GRAPH <{named_graph}> {{ {ntriples} }} }}"
        self.sparql_update(update)
