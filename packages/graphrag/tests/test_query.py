"""T7 — multi-hop traversal + trace, on the entity-led exemplar.

# STUB: AC6
# STUB: AC10
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag.model import Direction, EdgeKind
from graphrag.query import traverse
from graphrag.resolve import resolve
from graphrag.sources import load_corpus
from graphrag.store import MemoryGraphStore


def _store(community_root: Path, enhancements_root: Path) -> MemoryGraphStore:
    return MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))


def test_entity_led_exemplar_scopes_correctly(
    community_root: Path, enhancements_root: Path
) -> None:
    # "KEPs owned by the SIG @thockin tech-leads":
    #   @thockin -TECH_LEADS-> sig-network -OWNS-> {2086, 1880}
    store = _store(community_root, enhancements_root)
    result = traverse(
        store,
        ["person:thockin"],
        [(EdgeKind.TECH_LEADS, Direction.OUT), (EdgeKind.OWNS, Direction.OUT)],
    )
    assert set(result.result_ids) == {"kep-2086", "kep-1880"}
    # sig-node's KEP-1287 must NOT appear — thockin only *approves* it, not owns it.
    assert "kep-1287" not in result.result_ids


def test_trace_structure_is_ordered_seed_hop_result(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _store(community_root, enhancements_root)
    result = traverse(
        store,
        ["person:thockin"],
        [(EdgeKind.TECH_LEADS, Direction.OUT), (EdgeKind.OWNS, Direction.OUT)],
    )
    # AC10: the trace is an ordered seed -> per-hop -> result structure.
    assert result.seed_ids == ["person:thockin"]
    assert [(t.hop, t.edge_kind, t.direction) for t in result.trace] == [
        (1, EdgeKind.TECH_LEADS, Direction.OUT),
        (2, EdgeKind.OWNS, Direction.OUT),
    ]
    assert result.trace[0].to_ids == ["sig:sig-network"]
    assert set(result.trace[1].to_ids) == {"kep-2086", "kep-1880"}

    rendered = result.render()
    # The narration names each seed, each hop (edge kind + direction), each result.
    assert "seeds: person:thockin" in rendered
    assert "hop 1: TECH_LEADS OUT" in rendered
    assert "hop 2: OWNS OUT" in rendered
    assert rendered.index("hop 1") < rendered.index("hop 2") < rendered.index("result:")


def test_hop_cap_enforced(community_root: Path, enhancements_root: Path) -> None:
    store = _store(community_root, enhancements_root)
    with pytest.raises(ValueError, match="exceeds max_hops"):
        traverse(
            store,
            ["person:thockin"],
            [
                (EdgeKind.TECH_LEADS, Direction.OUT),
                (EdgeKind.OWNS, Direction.OUT),
                (EdgeKind.OWNS, Direction.IN),
            ],
            max_hops=2,
        )
