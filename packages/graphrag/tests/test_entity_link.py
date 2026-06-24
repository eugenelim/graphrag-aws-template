"""T1 — question entity-linking on the controlled vocabulary (AC1).

Every candidate's ``entity_id`` is asserted **byte-equal** to the slice-1
``normalize`` output, so a misseed is legible against the resolver.

# STUB: AC1
"""

from __future__ import annotations

from graphrag.entity_link import link_question
from graphrag.normalize import kep_id, normalize_handle, person_id, sig_id
from graphrag.resolve import load_aliases


def test_handle_links_to_person_via_handle() -> None:
    cands = link_question("the KEPs @thockin tech-leads owns", {})
    person = [c for c in cands if c.entity_id == "person:thockin"]
    assert person, f"expected a person:thockin candidate, got {cands}"
    c = person[0]
    assert c.kind == "person"
    assert c.via == "handle"
    assert c.surface in ("@thockin", "thockin")
    # byte-equal to the slice-1 normalizer output.
    assert c.entity_id == person_id("thockin", {})
    assert c.entity_id == f"person:{normalize_handle('@thockin')}"


def test_sig_mention_links_to_sig_via_slug() -> None:
    for q in ("what does SIG Network own", "what does sig-network own", "the Network sig"):
        cands = link_question(q, {})
        sigs = [c for c in cands if c.entity_id == "sig:sig-network"]
        assert sigs, f"expected sig:sig-network from {q!r}, got {cands}"
        assert sigs[0].via == "slug"
        assert sigs[0].entity_id == sig_id("sig-network")


def test_kep_reference_links_via_kep_number() -> None:
    for q in ("risks in KEP-1287", "what about KEP 1287"):
        cands = link_question(q, {})
        keps = [c for c in cands if c.entity_id == "kep-1287"]
        assert keps, f"expected kep-1287 from {q!r}, got {cands}"
        assert keps[0].via == "kep-number"
        assert keps[0].entity_id == kep_id(1287)


def test_display_name_links_via_alias() -> None:
    aliases = load_aliases()
    cands = link_question("what does Tim Hockin approve", aliases)
    person = [c for c in cands if c.entity_id == "person:thockin"]
    assert person, f"expected person:thockin via alias, got {cands}"
    assert person[0].via == "alias"
    assert person[0].entity_id == person_id("Tim Hockin", aliases)


def test_no_known_vocabulary_yields_empty() -> None:
    assert link_question("how do I configure a load balancer", {}) == []


def test_unknown_handle_still_links_mechanically() -> None:
    # A bare @handle is a person candidate even if it is not in the graph; the
    # hybrid layer drops it as an unconfirmed candidate (AC4), not entity_link.
    cands = link_question("ask @nobody about it", {})
    assert any(c.entity_id == "person:nobody" and c.via == "handle" for c in cands)
