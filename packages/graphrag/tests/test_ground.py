"""AC2 — the entity-grounding check (the honesty bound: relate known entities, never invent)."""

from __future__ import annotations

import graphrag.ground as ground_mod
from graphrag.extract_llm import EXTRACTION_SCHEMA, CandidateTriple, ExtractionSchema, SchemaEdge
from graphrag.ground import GroundedTriple, ground_triple
from graphrag.model import EdgeKind, EntityKind, Graph, Node


def _graph() -> Graph:
    g = Graph()
    g.upsert_node(Node("sig:sig-network", EntityKind.SIG))
    g.upsert_node(Node("sig:sig-node", EntityKind.SIG))
    g.upsert_node(Node("kep-1880", EntityKind.KEP))
    g.upsert_node(Node("kep-2086", EntityKind.KEP))
    g.upsert_node(Node("person:thockin", EntityKind.PERSON))
    return g


def _cand(subject: str, predicate: str, obj: str) -> CandidateTriple:
    return CandidateTriple(subject, predicate, obj, source_doc="community/x", span="span")


def test_grounds_a_triple_between_known_entities() -> None:
    cand = _cand("SIG Network", "COLLABORATES_WITH", "SIG Node")
    grounded = ground_triple(cand, _graph(), schema=EXTRACTION_SCHEMA)
    assert isinstance(grounded, GroundedTriple)
    # Endpoints resolved to the canonical graph ids via the existing normalizers.
    assert grounded.src_id == "sig:sig-network"
    assert grounded.dst_id == "sig:sig-node"
    assert grounded.kind is EdgeKind.COLLABORATES_WITH
    assert grounded.source_doc == "community/x" and grounded.span == "span"


def test_grounds_a_kep_dependency_with_mixed_id_forms() -> None:
    cand = _cand("kep-2086", "DEPENDS_ON", "KEP-1880")
    grounded = ground_triple(cand, _graph(), schema=EXTRACTION_SCHEMA)
    assert isinstance(grounded, GroundedTriple)
    assert grounded.src_id == "kep-2086" and grounded.dst_id == "kep-1880"


def test_ungrounded_endpoint_is_dropped() -> None:
    # sig-storage is not a node in the graph — the model may not invent it.
    cand = _cand("SIG Network", "COLLABORATES_WITH", "SIG Storage")
    assert ground_triple(cand, _graph(), schema=EXTRACTION_SCHEMA) is None


def test_endpoint_of_wrong_kind_is_dropped() -> None:
    # A COLLABORATES_WITH whose object normalizes to a KEP (wrong endpoint kind) is dropped,
    # never coerced.
    cand = _cand("SIG Network", "COLLABORATES_WITH", "kep-1880")
    assert ground_triple(cand, _graph(), schema=EXTRACTION_SCHEMA) is None


def test_canonical_kep_ids_ground_directly() -> None:
    cand = _cand("kep-1880", "DEPENDS_ON", "kep-2086")  # both already-canonical KEP ids
    grounded = ground_triple(cand, _graph(), schema=EXTRACTION_SCHEMA)
    assert isinstance(grounded, GroundedTriple)


def test_person_endpoint_resolves_via_the_alias_table() -> None:
    # Exercises the PERSON normalizer + the alias table: a prose display-name grounds to the same
    # id the deterministic pass produced. (No shipped edge kind has a PERSON endpoint today, so a
    # one-off PERSON->PERSON schema drives the path.)
    from graphrag.extract_llm import ExtractionSchema, SchemaEdge

    person_schema = ExtractionSchema(
        edges=(SchemaEdge(EdgeKind.COLLABORATES_WITH, EntityKind.PERSON, EntityKind.PERSON, "x"),)
    )
    g = Graph()
    g.upsert_node(Node("person:thockin", EntityKind.PERSON))
    g.upsert_node(Node("person:bowei", EntityKind.PERSON))
    cand = _cand("Tim Hockin", "COLLABORATES_WITH", "bowei")
    grounded = ground_triple(cand, g, schema=person_schema, aliases={"tim hockin": "thockin"})
    assert isinstance(grounded, GroundedTriple)
    assert grounded.src_id == "person:thockin" and grounded.dst_id == "person:bowei"


def test_ambiguous_endpoint_kind_is_dropped_never_guessed() -> None:
    # A malformed schema mapping one predicate to TWO endpoint pairs is ambiguous — drop, never
    # guess which normalizer to use.
    ambiguous = ExtractionSchema(
        edges=(
            SchemaEdge(EdgeKind.COLLABORATES_WITH, EntityKind.SIG, EntityKind.SIG, "x"),
            SchemaEdge(EdgeKind.COLLABORATES_WITH, EntityKind.KEP, EntityKind.KEP, "y"),
        )
    )
    cand = _cand("sig:sig-network", "COLLABORATES_WITH", "sig:sig-node")
    assert ground_triple(cand, _graph(), schema=ambiguous) is None


def test_grounding_reuses_the_existing_normalizers_no_new_resolver() -> None:
    # Pins that grounding calls the existing normalize functions (no new resolution path):
    # the normalizer registry is keyed exactly by the existing module-level functions.
    from graphrag import normalize

    assert ground_mod._NORMALIZERS[EntityKind.SIG] is normalize.sig_id
    assert ground_mod._NORMALIZERS[EntityKind.KEP] is normalize.kep_id
    assert ground_mod._NORMALIZERS[EntityKind.PERSON] is normalize.person_id
