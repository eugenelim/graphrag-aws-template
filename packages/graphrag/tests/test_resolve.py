"""T4 — cross-source resolution into single nodes, with negatives.

# STUB: AC4
"""

from __future__ import annotations

from pathlib import Path

from graphrag.model import EntityKind
from graphrag.resolve import cross_source_merges, load_aliases, resolve
from graphrag.sources import COMMUNITY, ENHANCEMENTS, load_corpus


def _graph(community_root: Path, enhancements_root: Path):
    return resolve(load_corpus(community_root, enhancements_root))


def test_shared_sig_slug_is_one_node(community_root: Path, enhancements_root: Path) -> None:
    g = _graph(community_root, enhancements_root)
    sig = g.get_node("sig:sig-network")
    assert sig is not None
    assert [n.id for n in g.nodes.values()].count("sig:sig-network") == 1
    # sig-network is defined in community and referenced as owning-sig in enhancements.
    assert sig.sources == {COMMUNITY, ENHANCEMENTS}


def test_shared_handle_is_one_person(community_root: Path, enhancements_root: Path) -> None:
    g = _graph(community_root, enhancements_root)
    thockin = g.get_node("person:thockin")
    assert thockin is not None
    # tech_lead (community, bare "thockin") + approver (enhancements, "@thockin").
    assert thockin.sources == {COMMUNITY, ENHANCEMENTS}
    assert thockin.props.get("name") == "Tim Hockin"  # name kept from community
    persons = [n for n in g.nodes.values() if n.kind == EntityKind.PERSON]
    assert sum(1 for n in persons if n.id == "person:thockin") == 1


def test_at_prefix_and_case_normalization_merge(
    community_root: Path, enhancements_root: Path
) -> None:
    g = _graph(community_root, enhancements_root)
    # @aojea (KEP-1880 author) == aojea (sig-network tech_lead) -> one node.
    aojea = g.get_node("person:aojea")
    assert aojea is not None and aojea.sources == {COMMUNITY, ENHANCEMENTS}
    # mixed-case sigs.yaml handle is lowercased.
    assert g.get_node("person:sergeykanzhelev") is not None


def test_alias_merges_prose_name(community_root: Path, enhancements_root: Path) -> None:
    g = _graph(community_root, enhancements_root)
    # "Tim Hockin" (prose author of legacy KEP-9) resolves into person:thockin,
    # not a separate "person:tim hockin" node.
    assert g.get_node("person:tim hockin") is None
    assert "person:thockin" in cross_source_merges(g)


def test_negatives_distinct_handles_do_not_merge(
    community_root: Path, enhancements_root: Path
) -> None:
    g = _graph(community_root, enhancements_root)
    assert g.get_node("person:bowei") is not None
    assert g.get_node("person:thockin") is not None
    assert g.get_node("person:bowei") is not g.get_node("person:thockin")


def test_negative_unaliased_name_stays_split() -> None:
    # A display name absent from the alias table must NOT be force-merged onto a
    # handle — it normalizes to its own id.
    aliases = load_aliases()
    from graphrag.normalize import person_id

    assert person_id("Some Contributor", aliases) == "person:some contributor"
    assert person_id("Some Contributor", aliases) != "person:thockin"
