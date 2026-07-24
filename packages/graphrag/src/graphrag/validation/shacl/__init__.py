"""graphrag.validation.shacl — SHACL gate and CI completeness fixture."""

from graphrag.validation.shacl._completeness import assert_class_shape_completeness
from graphrag.validation.shacl._gate import ShaclGate
from graphrag.validation.shacl._types import GateResult

__all__ = ["GateResult", "ShaclGate", "assert_class_shape_completeness"]
