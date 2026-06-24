"""T6 — credible-baseline confirmation: curated query set over real frozen vectors (AC6).

# STUB: AC6
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag.chunk import chunk_corpus
from graphrag.sources import load_corpus
from graphrag.vector_eval import (
    evaluate_query_set,
    load_frozen,
    load_query_set,
    max_verbatim_overlap,
)

FIXT = Path(__file__).parent / "fixtures" / "vector"
MAX_VERBATIM = 25  # case-folded; longer means the query echoes the gold chunk (lexical)


@pytest.fixture(scope="module")
def corpus() -> dict:
    docs = load_corpus(FIXT / "corpus/community", FIXT / "corpus/enhancements")
    return {c.id: c for c in chunk_corpus(docs)}


@pytest.fixture(scope="module")
def cases() -> list:
    return load_query_set(FIXT / "query_set.yaml")


@pytest.fixture(scope="module")
def frozen() -> dict:
    return load_frozen(FIXT / "frozen_embeddings.json")


def test_query_set_has_enough_semantic_and_miss_cases(cases: list) -> None:
    semantic = [c for c in cases if not c.expect_miss]
    misses = [c for c in cases if c.expect_miss]
    assert len(semantic) >= 5
    assert len(misses) >= 2


def test_credible_baseline_passes_the_bar(corpus: dict, cases: list, frozen: dict) -> None:
    result = evaluate_query_set(cases, frozen, corpus, k=5)
    # Semantic-led queries all hit@5; entity-led queries are gold-present honest misses.
    assert result.semantic_hit_rate() == 1.0
    for q in result.semantic:
        assert q.hit, f"semantic query {q.case.id} missed (rank={q.rank})"
    for q in result.misses:
        assert q.gold_present, f"miss {q.case.id} gold chunk absent from corpus"
        assert not q.hit, f"miss {q.case.id} unexpectedly retrieved its gold in top-5"
    assert result.passes()


def test_every_gold_chunk_exists_in_the_corpus(corpus: dict, cases: list) -> None:
    for case in cases:
        for gold in case.gold_chunk_ids:
            assert gold in corpus, f"{case.id} gold {gold!r} not a real corpus chunk"


def test_queries_are_semantic_not_lexical(corpus: dict, cases: list) -> None:
    # No query may share a long verbatim run with its gold chunk text — the win must
    # be semantic, not lexical overlap (curation guard).
    for case in cases:
        for gold in case.gold_chunk_ids:
            overlap = max_verbatim_overlap(case.query, corpus[gold].text)
            assert overlap < MAX_VERBATIM, f"{case.id} shares {overlap} verbatim chars with {gold}"


def test_evaluate_rejects_a_frozen_chunk_unknown_to_the_corpus(
    corpus: dict, cases: list, frozen: dict
) -> None:
    poisoned = {
        "chunks": {**frozen["chunks"], "ghost#0": [0.0] * 256},
        "queries": frozen["queries"],
    }
    with pytest.raises(ValueError, match="unknown corpus chunk"):
        evaluate_query_set(cases, poisoned, corpus, k=5)


def test_max_verbatim_overlap_basic() -> None:
    assert max_verbatim_overlap("the quick brown fox", "a quick brown dog") == len(" quick brown ")
    assert max_verbatim_overlap("abc", "xyz") == 0
