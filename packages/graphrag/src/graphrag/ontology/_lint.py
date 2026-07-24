"""Class-shape completeness lint for graphrag.ontology."""

from __future__ import annotations

import rdflib
from rdflib.namespace import OWL, RDF

_SH_TARGET_CLASS = rdflib.URIRef("http://www.w3.org/ns/shacl#targetClass")


def check_class_shape_completeness(
    ontology_graph: rdflib.Graph,
    shapes_graph: rdflib.Graph,
) -> list[str]:
    """Return OWL class URIs that lack a matching sh:NodeShape in shapes_graph.

    An empty list means every owl:Class has a paired sh:NodeShape — the CI green state.
    A non-empty list names the unshaped classes; adding a class without a shape fails CI.
    """
    owl_classes = {str(c) for c in ontology_graph.subjects(RDF.type, OWL.Class)}
    shaped_classes = {str(c) for _, _, c in shapes_graph.triples((None, _SH_TARGET_CLASS, None))}
    return sorted(owl_classes - shaped_classes)
