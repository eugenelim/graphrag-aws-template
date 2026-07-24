"""graphrag.ontology_loader — loads the biz-ops OWL ontology into Neptune.

Public API:

    OntologyLoader(store)
        .load(named_graph="urn:graph:ontology") -> None

Importable without boto3 or botocore; the live ``NeptuneSparqlStore`` is
injected by the caller.
"""

from graphrag.ontology_loader._loader import OntologyLoader

__all__ = ["OntologyLoader"]
