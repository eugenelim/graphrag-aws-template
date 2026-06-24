"""T7 — multi-hop traversal + trace, on the entity-led exemplar.

# STUB: AC6
# STUB: AC10
# STUB: AC3
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag.model import Direction, EdgeKind
from graphrag.query import expand_neighborhood, traverse
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


def test_frontier_cap_truncates_and_records_it() -> None:
    from graphrag.model import Edge, EntityKind, Node
    from graphrag.store import MemoryGraphStore

    store = MemoryGraphStore()
    store.upsert_node(Node("sig:x", EntityKind.SIG))
    for i in range(5):
        store.upsert_node(Node(f"kep-{i}", EntityKind.KEP))
        store.upsert_edge(Edge("sig:x", f"kep-{i}", EdgeKind.OWNS))

    result = traverse(store, ["sig:x"], [(EdgeKind.OWNS, Direction.OUT)], frontier_cap=3)
    assert len(result.result_ids) == 3
    assert result.trace[-1].truncated is True
    assert "[frontier truncated]" in result.render()


def test_missing_seed_yields_legible_empty_result(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _store(community_root, enhancements_root)
    result = traverse(store, ["person:nobody"], [(EdgeKind.TECH_LEADS, Direction.OUT)])
    assert result.result_ids == []
    rendered = result.render()
    assert "seeds: person:nobody" in rendered
    assert "result: (none)" in rendered  # nothing-matched stays legible, not blank


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


# --- T3 / AC3: bounded neighborhood expansion over all edge kinds + directions ----


def test_expand_one_hop_reaches_owned_keps_and_leadership(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _store(community_root, enhancements_root)
    result = expand_neighborhood(store, ["sig:sig-network"], max_hops=1)
    reached = set(result.result_ids)
    # one hop over ALL edge kinds/directions: owned KEPs (OWNS OUT), the leaders
    # (TECH_LEADS/CHAIRS IN), and the subprojects (HAS_SUBPROJECT OUT).
    assert {"kep-1880", "kep-2086"} <= reached
    assert "person:thockin" in reached  # TECH_LEADS IN
    # the trace names the contributing edge kinds at this hop.
    edge_kinds = {ek for entry in result.trace for ek in entry.edge_kinds}
    assert EdgeKind.OWNS in edge_kinds
    assert EdgeKind.TECH_LEADS in edge_kinds


def test_thockin_two_hop_reaches_owned_keps_via_sig(
    community_root: Path, enhancements_root: Path
) -> None:
    store = _store(community_root, enhancements_root)
    # The entity-led exemplar path the graph-win depends on:
    #   person:thockin -TECH_LEADS-> sig:sig-network -OWNS-> {kep-2086, kep-1880}
    two = expand_neighborhood(store, ["person:thockin"], max_hops=2)
    assert "sig:sig-network" in two.result_ids  # reached at hop 1 (TECH_LEADS)
    assert {"kep-1880", "kep-2086"} <= set(two.result_ids)  # reached at hop 2

    # The sig:sig-network -OWNS-> KEP edge is what surfaces the owned set at hop 2;
    # this OWNS path only exists at hop 2, never hop 1 (the SIG itself is not in the
    # hop-1 frontier-in). Assert the hop-2 trace carries OWNS from the SIG.
    hop2 = [e for e in two.trace if e.hop == 2]
    assert hop2, "expected a hop-2 trace entry"
    assert EdgeKind.OWNS in {ek for e in hop2 for ek in e.edge_kinds}
    assert "sig:sig-network" in {fid for e in hop2 for fid in e.frontier_in}

    # max_hops=1 reaches the SIG but the SIG's OWNS edges are not yet followed (the
    # SIG only enters the frontier at hop 1), so the owned set arrives via OWNS only
    # at hop 2 — the win requires max_hops >= 2.
    one = expand_neighborhood(store, ["person:thockin"], max_hops=1)
    assert "sig:sig-network" in one.result_ids
    one_owns_from_sig = [
        e for e in one.trace if "sig:sig-network" in e.frontier_in and EdgeKind.OWNS in e.edge_kinds
    ]
    assert not one_owns_from_sig, "OWNS-from-SIG must not fire at max_hops=1"


def test_expand_frontier_cap_truncates_and_records() -> None:
    from graphrag.model import Edge, EntityKind, Node

    store = MemoryGraphStore()
    store.upsert_node(Node("sig:x", EntityKind.SIG))
    for i in range(5):
        store.upsert_node(Node(f"kep-{i}", EntityKind.KEP))
        store.upsert_edge(Edge("sig:x", f"kep-{i}", EdgeKind.OWNS))
    result = expand_neighborhood(store, ["sig:x"], max_hops=1, frontier_cap=3)
    assert len(result.result_ids) == 3
    assert any(entry.truncated for entry in result.trace)
    assert "[frontier truncated]" in result.render()


def test_expand_empty_seed_yields_nothing() -> None:
    store = MemoryGraphStore()
    result = expand_neighborhood(store, [], max_hops=2)
    assert result.result_ids == []
    assert result.trace == []
