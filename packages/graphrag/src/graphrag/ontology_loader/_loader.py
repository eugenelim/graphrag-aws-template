"""OntologyLoader — loads the biz-ops OWL ontology into a SPARQL store.

Uses ``graphrag.ontology.load_ontology()`` to retrieve the bundled ``biz_ops.ttl``
and ``SparqlStore.load_turtle()`` to insert the triples into a named graph.  A
minimal PROV-O activity record is emitted alongside the ontology triples so the
load event is auditable.

Designed for offline use (``MemorySparqlStore``) and live Neptune use
(``NeptuneSparqlStore``); no boto3 or botocore imports.

Named graph convention:
    Ontology triples go into ``urn:graph:ontology`` by default — a dedicated
    schema-vocabulary partition separate from ``urn:graph:normative`` and
    ``urn:graph:descriptive``.  Override with the ``named_graph`` parameter.
"""

from __future__ import annotations

import datetime
import logging

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import PROV, RDF, XSD

from graphrag.ontology import load_ontology
from graphrag.store.sparql_base import SparqlStore

_DEFAULT_GRAPH = "urn:graph:ontology"
_ONTOLOGY_ENTITY = URIRef("urn:entity:ontology:biz-ops")
_LOADER_AGENT = URIRef("urn:agent:ontology-loader")


class OntologyLoader:
    """Load the bundled biz-ops OWL ontology into a SPARQL store.

    Args:
        store: An injectable ``SparqlStore``.  Use ``MemorySparqlStore`` for
            offline CI; ``NeptuneSparqlStore`` in production.
    """

    def __init__(self, store: SparqlStore) -> None:
        self._store = store

    def load(self, named_graph: str = _DEFAULT_GRAPH) -> None:
        """Load the ontology and PROV-O provenance into ``named_graph``.

        Calls ``load_ontology()`` once, serialises the graph to Turtle, and
        inserts the triples via ``store.load_turtle()``.  A PROV-O activity
        record (load time, agent) is then inserted into the same graph.

        Args:
            named_graph: Target named graph URI.  Defaults to
                ``"urn:graph:ontology"``.  Must be a valid absolute URI.
        """
        started_at = datetime.datetime.now(tz=datetime.UTC)

        # 1. Load ontology triples
        ontology: Graph = load_ontology()
        ttl = ontology.serialize(format="turtle")
        self._store.load_turtle(ttl, named_graph)

        ended_at = datetime.datetime.now(tz=datetime.UTC)

        # 2. Emit PROV-O provenance for the load activity.
        # Per spec "Never do": must not raise if provenance emission fails — the
        # ontology triples are the primary load; provenance is best-effort.
        try:
            prov_ttl = _build_load_provenance(started_at, ended_at).serialize(format="turtle")
            self._store.load_turtle(prov_ttl, named_graph)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "OntologyLoader: PROV-O provenance emission failed (non-fatal): %s",
                exc,
                exc_info=True,
            )


def _build_load_provenance(
    started_at: datetime.datetime,
    ended_at: datetime.datetime,
) -> Graph:
    """Return a minimal PROV-O graph recording the ontology load activity."""
    g = Graph()
    g.bind("prov", PROV)

    act_uri = URIRef(f"urn:activity:load-ontology:{started_at.isoformat()}")

    # Ontology entity
    g.add((_ONTOLOGY_ENTITY, RDF.type, PROV.Entity))

    # Load activity
    g.add((act_uri, RDF.type, PROV.Activity))
    g.add((act_uri, PROV.used, _ONTOLOGY_ENTITY))
    g.add((act_uri, PROV.wasAssociatedWith, _LOADER_AGENT))
    g.add(
        (
            act_uri,
            PROV.startedAtTime,
            Literal(started_at.isoformat(), datatype=XSD.dateTime),
        )
    )
    g.add(
        (
            act_uri,
            PROV.endedAtTime,
            Literal(ended_at.isoformat(), datatype=XSD.dateTime),
        )
    )

    # Loader agent
    g.add((_LOADER_AGENT, RDF.type, PROV.SoftwareAgent))

    return g
