"""T3 — entity + edge extraction over the fixture corpus.

# STUB: AC2
# STUB: AC3
"""

from __future__ import annotations

from pathlib import Path

from graphrag.extract import extract
from graphrag.model import EdgeKind, EntityKind
from graphrag.resolve import load_aliases
from graphrag.sources import load_corpus


def _extracted(community_root: Path, enhancements_root: Path):
    docs = load_corpus(community_root, enhancements_root)
    return extract(docs, load_aliases())


def test_extracts_all_four_entity_kinds(community_root: Path, enhancements_root: Path) -> None:
    nodes, _ = _extracted(community_root, enhancements_root)
    ids_by_kind = {k: {n.id for n in nodes if n.kind == k} for k in EntityKind}

    assert "sig:sig-network" in ids_by_kind[EntityKind.SIG]
    assert "sig:sig-node" in ids_by_kind[EntityKind.SIG]
    assert "person:thockin" in ids_by_kind[EntityKind.PERSON]
    assert {"kep-2086", "kep-1880", "kep-1287", "kep-9"} <= ids_by_kind[EntityKind.KEP]
    assert "subproject:sig-network/external-dns" in ids_by_kind[EntityKind.SUBPROJECT]


def test_kep_fields_extracted(community_root: Path, enhancements_root: Path) -> None:
    nodes, _ = _extracted(community_root, enhancements_root)
    kep2086 = next(n for n in nodes if n.id == "kep-2086" and n.props.get("title"))
    assert kep2086.props["title"] == "Service Internal Traffic Policy"
    assert kep2086.props["status"] == "implemented"


def test_extracts_all_six_edge_kinds(community_root: Path, enhancements_root: Path) -> None:
    _, edges = _extracted(community_root, enhancements_root)
    by_kind = {k: {(e.src_id, e.dst_id) for e in edges if e.kind == k} for k in EdgeKind}

    assert ("person:thockin", "sig:sig-network") in by_kind[EdgeKind.TECH_LEADS]
    assert ("person:bowei", "sig:sig-network") in by_kind[EdgeKind.CHAIRS]
    assert ("sig:sig-network", "kep-2086") in by_kind[EdgeKind.OWNS]
    assert ("person:andrewsykim", "kep-2086") in by_kind[EdgeKind.AUTHORS]
    assert ("person:thockin", "kep-2086") in by_kind[EdgeKind.APPROVES]
    assert ("sig:sig-network", "subproject:sig-network/external-dns") in by_kind[
        EdgeKind.HAS_SUBPROJECT
    ]


def test_legacy_prose_author_extracted_via_alias(
    community_root: Path, enhancements_root: Path
) -> None:
    # KEP-0009 (-> kep-9) has no kep.yaml; "Tim Hockin" in prose resolves to thockin.
    _, edges = _extracted(community_root, enhancements_root)
    authors = {(e.src_id, e.dst_id) for e in edges if e.kind == EdgeKind.AUTHORS}
    assert ("person:thockin", "kep-9") in authors
    assert ("sig:sig-node", "kep-9") in {
        (e.src_id, e.dst_id) for e in edges if e.kind == EdgeKind.OWNS
    }
