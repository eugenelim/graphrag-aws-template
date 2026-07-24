"""CI completeness gate — every OWL class must have a paired sh:NodeShape."""

from __future__ import annotations

import rdflib

from graphrag.ontology import check_class_shape_completeness, load_ontology, load_shapes


def assert_class_shape_completeness(
    ontology_graph: rdflib.Graph | None = None,
    shapes_graph: rdflib.Graph | None = None,
) -> list[str]:
    """Return OWL class URIs that lack a matching sh:NodeShape.

    Wraps graphrag.ontology.check_class_shape_completeness().  When called
    with no arguments, validates the bundled biz_ops.ttl against the bundled
    biz_ops_shapes.ttl.  Both graphs can be supplied explicitly for fixture-
    based testing (e.g. to check a modified ontology graph).

    Returns:
        An empty list if every owl:Class is covered — CI green state.
        A non-empty list of absolute class URIs that lack a shape — CI red.

    CI usage::

        from graphrag.validation.shacl import assert_class_shape_completeness

        def test_completeness() -> None:
            assert assert_class_shape_completeness() == []
    """
    if ontology_graph is None:
        ontology_graph = load_ontology()
    if shapes_graph is None:
        shapes_graph = load_shapes()
    return check_class_shape_completeness(ontology_graph, shapes_graph)
