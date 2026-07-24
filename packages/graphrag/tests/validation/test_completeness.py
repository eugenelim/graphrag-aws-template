"""Tests for graphrag.validation.shacl.assert_class_shape_completeness."""

from __future__ import annotations

import pytest
from rdflib import Graph, Namespace
from rdflib.namespace import OWL, RDF

from graphrag.validation.shacl import assert_class_shape_completeness

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")
SH = Namespace("http://www.w3.org/ns/shacl#")

# Full URI for biz:AuditLog — used in ill-formed fixture
BIZ_AUDIT_LOG_URI = str(BIZ.AuditLog)


# ---------------------------------------------------------------------------
# AC5 — bundled well-formed pair returns empty list
# ---------------------------------------------------------------------------


def test_bundled_well_formed_pair_passes() -> None:
    """assert_class_shape_completeness() returns [] for bundled biz_ops.ttl + biz_ops_shapes.ttl."""
    missing = assert_class_shape_completeness()
    assert missing == [], f"Expected no missing shapes for bundled pair, got: {missing}"


# ---------------------------------------------------------------------------
# AC5 — ill-formed fixture: extra OWL class with no shape → non-empty list
# ---------------------------------------------------------------------------


def _ill_formed_ontology() -> Graph:
    """An ontology graph with biz:AuditLog as an owl:Class (no matching sh:NodeShape)."""
    g = Graph()
    g.add((BIZ.AuditLog, RDF.type, OWL.Class))
    return g


def _empty_shapes() -> Graph:
    """An empty SHACL shapes graph — no sh:NodeShape for anything."""
    return Graph()


def test_ill_formed_fixture_returns_non_empty_list() -> None:
    """Extra owl:Class with no matching sh:NodeShape → non-empty list."""
    missing = assert_class_shape_completeness(
        ontology_graph=_ill_formed_ontology(),
        shapes_graph=_empty_shapes(),
    )
    assert len(missing) > 0, "Expected non-empty missing list for ill-formed fixture"
    assert BIZ_AUDIT_LOG_URI in missing, (
        f"Expected {BIZ_AUDIT_LOG_URI!r} in missing list; got {missing}"
    )


def test_bundled_pair_with_extra_class_fails() -> None:
    """CI gate demo: bundled shapes + extra OWL class → non-empty (gate fails)."""
    from graphrag.ontology import load_ontology, load_shapes

    # Start with the bundled (well-formed) ontology and add an extra class.
    ont = load_ontology()
    ont.add((BIZ.AuditLog, RDF.type, OWL.Class))

    missing = assert_class_shape_completeness(
        ontology_graph=ont,
        shapes_graph=load_shapes(),  # bundled shapes: no AuditLog shape
    )
    assert BIZ_AUDIT_LOG_URI in missing, (
        f"Expected biz:AuditLog in missing list after adding unshaped class; got {missing}"
    )


# ---------------------------------------------------------------------------
# Parametrized gate: well-formed → pass; ill-formed → fail (documents CI behaviour)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ontology_graph, shapes_graph, expected_empty",
    [
        pytest.param(None, None, True, id="bundled-well-formed"),
        pytest.param(
            _ill_formed_ontology(),
            _empty_shapes(),
            False,
            id="ill-formed-extra-class",
        ),
    ],
)
def test_completeness_gate_parametrized(
    ontology_graph: Graph | None,
    shapes_graph: Graph | None,
    expected_empty: bool,
) -> None:
    """Parametrized: bundled pair → empty (CI green); ill-formed → non-empty (CI red)."""
    missing = assert_class_shape_completeness(
        ontology_graph=ontology_graph,
        shapes_graph=shapes_graph,
    )
    if expected_empty:
        assert missing == [], f"Expected CI green (empty list); got {missing}"
    else:
        assert len(missing) > 0, "Expected CI red (non-empty list) for ill-formed fixture"
