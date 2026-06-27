"""AC1 — the closed-schema triple validator + the load-bearing disjointness invariant."""

from __future__ import annotations

import pytest

from graphrag.extract_llm import EXTRACTION_SCHEMA, CandidateTriple, ExtractionSchema, SchemaEdge
from graphrag.model import (
    DETERMINISTIC_EDGE_KINDS,
    LLM_EXTRACTABLE_EDGE_KINDS,
    EdgeKind,
    EntityKind,
    extraction_method_for_kind,
)
from graphrag.validate_triple import TripleValidation, validate_triple


def _cand(subject: str, predicate: str, obj: str) -> CandidateTriple:
    return CandidateTriple(subject, predicate, obj, source_doc="community/x/README.md", span="s")


# --- The disjointness invariant (load-bearing for AC4 stamping + AC11 read-side) ----------


def test_llm_and_deterministic_edge_kinds_are_disjoint() -> None:
    # Asserted DIRECTLY (not inferred from a count): an LLM edge's kind can never equal a
    # deterministic edge's kind, so an LLM edge can never share a (src, kind, dst) key with a
    # deterministic one under merge-on-upsert.
    assert LLM_EXTRACTABLE_EDGE_KINDS & DETERMINISTIC_EDGE_KINDS == frozenset()


def test_every_edge_kind_is_classified_exactly_once() -> None:
    # Exhaustive: a new EdgeKind added to neither set (or both) is caught here.
    assert LLM_EXTRACTABLE_EDGE_KINDS | DETERMINISTIC_EDGE_KINDS == frozenset(EdgeKind)


def test_llm_extractable_set_is_pinned() -> None:
    assert LLM_EXTRACTABLE_EDGE_KINDS == frozenset(
        {EdgeKind.COLLABORATES_WITH, EdgeKind.SUPERSEDES, EdgeKind.DEPENDS_ON}
    )


def test_extraction_method_is_a_pure_function_of_kind() -> None:
    assert extraction_method_for_kind(EdgeKind.COLLABORATES_WITH) == "schema-guided-llm"
    assert extraction_method_for_kind(EdgeKind.SUPERSEDES) == "schema-guided-llm"
    assert extraction_method_for_kind(EdgeKind.OWNS) == "deterministic"
    assert extraction_method_for_kind(EdgeKind.AUTHORS) == "deterministic"


def test_schema_kinds_match_the_llm_extractable_set() -> None:
    assert EXTRACTION_SCHEMA.kinds() == LLM_EXTRACTABLE_EDGE_KINDS


# --- Accept table -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "predicate",
    [EdgeKind.COLLABORATES_WITH.value, EdgeKind.SUPERSEDES.value, EdgeKind.DEPENDS_ON.value],
)
def test_in_schema_triples_are_accepted(predicate: str) -> None:
    cand = _cand("sig:sig-network", predicate, "sig:sig-node")
    result = validate_triple(cand, schema=EXTRACTION_SCHEMA)
    assert isinstance(result, TripleValidation)
    assert result.ok
    assert result.violated_rule is None


# --- Reject table (conservative: ambiguous ⇒ reject, the rule named) ----------------------


def test_unknown_predicate_is_rejected() -> None:
    result = validate_triple(_cand("a", "FRIENDS_WITH", "b"), schema=EXTRACTION_SCHEMA)
    assert not result.ok
    assert result.violated_rule == "off-schema-predicate"


def test_deterministic_only_predicate_is_rejected() -> None:
    # AUTHORS is a real EdgeKind but a DETERMINISTIC one — it must never be LLM-extracted.
    result = validate_triple(_cand("person:thockin", "AUTHORS", "kep-9"), schema=EXTRACTION_SCHEMA)
    assert not result.ok
    assert result.violated_rule == "off-schema-predicate"


def test_empty_subject_is_rejected() -> None:
    cand = _cand("  ", "COLLABORATES_WITH", "sig:sig-node")
    result = validate_triple(cand, schema=EXTRACTION_SCHEMA)
    assert not result.ok
    assert result.violated_rule == "empty-subject"


def test_empty_object_is_rejected() -> None:
    cand = _cand("sig:sig-network", "COLLABORATES_WITH", "")
    result = validate_triple(cand, schema=EXTRACTION_SCHEMA)
    assert not result.ok
    assert result.violated_rule == "empty-object"


def test_malformed_predicate_is_rejected() -> None:
    # A predicate carrying internal whitespace is not a single token — malformed.
    result = validate_triple(_cand("a", "COLLABORATES WITH", "b"), schema=EXTRACTION_SCHEMA)
    assert not result.ok
    assert result.violated_rule == "malformed-predicate"


def test_unknown_endpoint_kind_is_rejected() -> None:
    # A malformed schema whose endpoint kind is not an EntityKind fails closed (defensive).
    bad_schema = ExtractionSchema(
        edges=(SchemaEdge(EdgeKind.COLLABORATES_WITH, "NOT_A_KIND", EntityKind.SIG, "x"),)  # type: ignore[arg-type]
    )
    result = validate_triple(_cand("a", "COLLABORATES_WITH", "b"), schema=bad_schema)
    assert not result.ok
    assert result.violated_rule == "unknown-endpoint-kind"
