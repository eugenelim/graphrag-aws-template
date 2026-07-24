from graphrag.ontology._lint import check_class_shape_completeness
from graphrag.ontology._resources import BIZ, load_ontology, load_shapes
from graphrag.ontology._validate import ShapeViolation, ValidationResult, validate_graph

__all__ = [
    "BIZ",
    "ShapeViolation",
    "ValidationResult",
    "load_ontology",
    "load_shapes",
    "validate_graph",
    "check_class_shape_completeness",
]
