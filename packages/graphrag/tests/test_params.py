"""T3 — deterministic parameter extraction + validation (AC3).

Entity slots resolve via ``link_question`` and are confirmed against the store (an
unconfirmed candidate is dropped, not bound); enum slots validate against ``choices``; int
slots parse and bound. A missing/invalid required slot is an ``ExtractionFailure`` — never
a query with a bad parameter.

# STUB: AC3
"""

from __future__ import annotations

from pathlib import Path

from graphrag.model import EntityKind
from graphrag.params import BoundParam, ExtractionFailure, ParamBinding, extract_params
from graphrag.resolve import resolve
from graphrag.sources import load_corpus
from graphrag.store import MemoryGraphStore
from graphrag.templates import ParamSpec, Template, get_template


def _store(community_root: Path, enhancements_root: Path) -> MemoryGraphStore:
    return MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))


def _noop_eval(store: object, params: object) -> list:  # type: ignore[type-arg]
    return []


def test_entity_slot_binds_confirmed_node(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    template = get_template("sig_owned_keps")
    assert template is not None
    result = extract_params("Which KEPs does SIG Network own?", template, {}, store)
    assert isinstance(result, ParamBinding)
    # the first store-confirmed candidate binds (the value is a real graph node id).
    assert result.bound == [BoundParam("sig", "sig:sig-network", "link:slug")]
    # confirmation short-circuits at the first hit, so no unconfirmed candidate was dropped
    # here; the dropped-recording path is covered by the no-confirmed-candidate failure test.
    assert result.dropped == []


def test_required_entity_with_no_confirmed_candidate_fails(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _store(community_root, enhancements_root)
    template = get_template("sig_owned_keps")
    assert template is not None
    # names a SIG that does not exist in the fixture graph -> nothing confirms -> failure.
    result = extract_params("Which KEPs does SIG Nonexistent own?", template, {}, store)
    assert isinstance(result, ExtractionFailure)
    assert "sig" in result.reason
    assert "sig:sig-nonexistent" in result.dropped  # the unconfirmed candidate is recorded


def test_enum_slot_validates_against_choices(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    tmpl = Template(
        id="_enum_probe",
        description="probe",
        params=(ParamSpec("status", "enum", choices=("implementable", "implemented")),),
        cypher="MATCH (n:Entity {status: $status}) RETURN n",
        evaluate=_noop_eval,
    )
    ok = extract_params("show me implementable KEPs", tmpl, {}, store)
    assert isinstance(ok, ParamBinding)
    assert ok.bound == [BoundParam("status", "implementable", "enum-match")]

    bad = extract_params("show me withdrawn KEPs", tmpl, {}, store)  # not a declared choice
    assert isinstance(bad, ExtractionFailure)


def test_int_slot_parses_and_bounds(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    tmpl = Template(
        id="_int_probe",
        description="probe",
        params=(ParamSpec("hops", "int", min=1, max=3),),
        cypher="MATCH (n:Entity) RETURN n LIMIT $hops",
        evaluate=_noop_eval,
    )
    ok = extract_params("expand 2 hops", tmpl, {}, store)
    assert isinstance(ok, ParamBinding)
    assert ok.bound == [BoundParam("hops", 2, "int-parse")]

    out_of_range = extract_params("expand 9 hops", tmpl, {}, store)  # 9 > max
    assert isinstance(out_of_range, ExtractionFailure)


def test_optional_slot_absent_is_not_a_failure(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _store(community_root, enhancements_root)
    tmpl = Template(
        id="_optional_probe",
        description="probe",
        params=(
            ParamSpec("sig", "entity", entity_kind=EntityKind.SIG),
            ParamSpec("status", "enum", choices=("implementable",), required=False),
        ),
        cypher="MATCH (s:Entity {id: $sig}) RETURN s AS n",
        evaluate=_noop_eval,
    )
    result = extract_params("KEPs owned by SIG Network", tmpl, {}, store)
    assert isinstance(result, ParamBinding)
    assert [bp.name for bp in result.bound] == ["sig"]  # optional enum absent, not a failure
