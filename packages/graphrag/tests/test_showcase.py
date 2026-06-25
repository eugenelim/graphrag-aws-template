"""T8 — consolidated showcase set + loader (AC10).

The single home for the demo's queries: >=5-6 per mode, each with gold entity/chunk
ids that resolve in the fixture corpus and a non-empty highlight.

# STUB: AC10
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from graphrag.chunk import chunk_corpus
from graphrag.resolve import resolve
from graphrag.showcase import ShowcaseQuery, load_showcase
from graphrag.sources import load_corpus


def test_showcase_parses() -> None:
    queries = load_showcase()
    assert queries
    assert all(isinstance(q, ShowcaseQuery) for q in queries)


def test_at_least_five_per_mode() -> None:
    counts = Counter(q.wins for q in load_showcase())
    for mode in ("vector", "graph", "hybrid"):
        assert counts[mode] >= 5, f"need >=5 {mode} queries, got {counts[mode]}"


def test_every_gold_resolves_in_fixture(community_root: Path, enhancements_root: Path) -> None:
    docs = load_corpus(community_root, enhancements_root)
    graph = resolve(docs)
    chunk_ids = {c.id for c in chunk_corpus(docs)}
    node_ids = set(graph.nodes)

    for q in load_showcase():
        assert q.wins in ("vector", "graph", "hybrid")
        assert q.query.strip()
        assert q.highlight.strip(), f"query {q.id} has an empty highlight"
        assert q.gold, f"query {q.id} names no gold entity/chunk"
        for gold in q.gold:
            assert gold in node_ids or gold in chunk_ids, (
                f"query {q.id} gold {gold!r} resolves to neither a graph node nor a chunk id"
            )


# --- slice 4: permission-filtered showcase queries (AC10) -----------------------------

from graphrag.labels import load_labels  # noqa: E402
from graphrag.showcase import PermissionShowcaseQuery, load_permission_showcase  # noqa: E402
from graphrag.visibility import DEFAULT_VISIBILITY, resolve_clearance  # noqa: E402


def test_permission_showcase_parses() -> None:
    queries = load_permission_showcase()
    assert queries
    assert all(isinstance(q, PermissionShowcaseQuery) for q in queries)


def test_permission_showcase_consistent_with_labels_and_personas(
    community_root: Path, enhancements_root: Path
) -> None:
    docs = load_corpus(community_root, enhancements_root)
    node_ids = set(resolve(docs).nodes)
    labels = load_labels()

    for q in load_permission_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        assert q.visible or q.filtered, f"{q.id} names no visible/filtered split"
        clearance = resolve_clearance(q.persona)  # persona must be a known clearance
        # every named id resolves in the fixture graph, and the visible/filtered split is
        # CONSISTENT with the actual labels + the persona's clearance (no hand-wavy gold).
        for vid in q.visible:
            assert vid in node_ids, f"{q.id} visible {vid!r} missing from fixture"
            assert clearance.allows(labels.get(vid, DEFAULT_VISIBILITY))
        for fid in q.filtered:
            assert fid in node_ids, f"{q.id} filtered {fid!r} missing from fixture"
            assert not clearance.allows(labels.get(fid, DEFAULT_VISIBILITY))


# --- opencypher-templates: governed-path showcase queries (AC10) -----------------------

from graphrag.governed import execute_template  # noqa: E402
from graphrag.params import ParamBinding, extract_params  # noqa: E402
from graphrag.resolve import load_aliases  # noqa: E402
from graphrag.showcase import GovernedShowcaseQuery, load_governed_showcase  # noqa: E402
from graphrag.store import MemoryGraphStore  # noqa: E402
from graphrag.templates import get_template  # noqa: E402


def test_governed_showcase_parses() -> None:
    queries = load_governed_showcase()
    assert len(queries) >= 4
    assert all(isinstance(q, GovernedShowcaseQuery) for q in queries)


def test_governed_showcase_consistent_with_templates_and_fixture(
    community_root: Path, enhancements_root: Path
) -> None:
    store = MemoryGraphStore.from_graph(resolve(load_corpus(community_root, enhancements_root)))
    node_ids = {n.id for n in store.all_nodes()}

    for q in load_governed_showcase():
        assert q.query.strip()
        assert q.highlight.strip(), f"{q.id} has an empty highlight"
        template = get_template(q.template)
        assert template is not None, f"{q.id} names unknown template {q.template!r}"
        assert q.param in node_ids, f"{q.id} param {q.param!r} missing from fixture"
        slot = template.params[0]
        # the showcase QUESTION deterministically extracts to the labeled param (the
        # select-and-extract join AC10's `param` field promises — not just template+gold).
        binding = extract_params(q.query, template, load_aliases(), store)
        assert isinstance(binding, ParamBinding), f"{q.id}: question failed extraction"
        bound = {bp.name: bp.value for bp in binding.bound}
        assert bound.get(slot.name) == q.param, (
            f"{q.id}: question extracts {bound.get(slot.name)!r}, labeled param is {q.param!r}"
        )
        # running the vetted template with the extracted param returns exactly the gold rows
        # (no hand-wavy gold — the showcase is consistent with the real query).
        rows = [n.id for n in execute_template(store, template, {slot.name: q.param})]
        assert rows == sorted(q.gold), f"{q.id}: rows {rows} != gold {sorted(q.gold)}"
        for gid in q.gold:
            assert gid in node_ids, f"{q.id} gold {gid!r} missing from fixture"
