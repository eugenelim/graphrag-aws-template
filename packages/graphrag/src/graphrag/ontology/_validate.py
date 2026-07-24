"""SHACL validation API for graphrag.ontology."""

from __future__ import annotations

from dataclasses import dataclass, field

import pyshacl
import rdflib

from graphrag.ontology._resources import _load_shapes

# sh:resultMessage and sh:sourceShape are optional per SHACL core; move them to OPTIONAL
# so that violations from non-property shapes (no sh:resultPath) are never silently dropped.
_VIOLATION_SPARQL = """\
PREFIX sh: <http://www.w3.org/ns/shacl#>
SELECT ?fn ?path ?msg ?shape WHERE {
  ?r a sh:ValidationResult ;
     sh:focusNode ?fn .
  OPTIONAL { ?r sh:resultPath    ?path  }
  OPTIONAL { ?r sh:resultMessage ?msg   }
  OPTIONAL { ?r sh:sourceShape   ?shape }
}
"""


@dataclass
class ShapeViolation:
    focus_node: str
    path: str
    message: str
    source_shape: str
    # NOTE: source_shape may be a blank-node id (e.g. "Nab12…") when shapes use
    # inline blank-node sh:property blocks; callers must not assume it is a named
    # shape URI. Assertions should pin on `path`, not `source_shape`.


@dataclass
class ValidationResult:
    conforms: bool
    violations: list[ShapeViolation] = field(default_factory=list)


def validate_graph(
    data_graph: rdflib.Graph,
    shapes_graph: rdflib.Graph | None = None,
) -> ValidationResult:
    """Run SHACL validation with inference='none'; never raises on constraint failure.

    Args:
        data_graph: The RDF graph to validate.
        shapes_graph: SHACL shapes graph; defaults to the bundled biz_ops_shapes.ttl.

    Returns:
        ValidationResult with conforms=True/False and a list of ShapeViolation instances.

    Raises:
        Exception: Only if data_graph or shapes_graph contain unparseable RDF — the
            caller is responsible for constructing parseable graphs.
    """
    if shapes_graph is None:
        shapes_graph = _load_shapes()

    conforms, report_graph, _report_text = pyshacl.validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="none",
        abort_on_first=False,
    )

    violations: list[ShapeViolation] = []
    for row in report_graph.query(_VIOLATION_SPARQL):
        fn, path, msg, shape = row
        violations.append(
            ShapeViolation(
                focus_node=str(fn),
                path=str(path) if path is not None else "",
                message=str(msg) if msg is not None else "",
                source_shape=str(shape) if shape is not None else "",
            )
        )

    return ValidationResult(conforms=bool(conforms), violations=violations)
