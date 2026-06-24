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
