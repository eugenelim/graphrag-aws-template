"""Credible-baseline confirmation — the curated query set, mechanized (slice-2 AC6).

Charter principle 2 (honest comparison) lives or dies on query selection, so the
fairness bar is a test, not a sentence. A curated query set holds natural
architect-style questions in two classes:

- **semantic-led** — questions vector should answer well; the bar is **hit@5 = 1.0**.
- **entity-led** — scoping questions vector honestly *misses* (the answer is a set the
  prose never enumerates); each gold chunk **exists in the corpus** but is **not** in
  top-5, so the baseline is credible, not a strawman in either direction.

To be honest *and* CI-reproducible the eval runs over **frozen real Titan v2 vectors**
(committed), not the offline ``HashEmbedder``. ``freeze_embeddings`` regenerates them
against live Titan (the opt-in ``--bedrock`` path).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from .chunk import Chunk
from .embed import Embedder
from .store.vector_base import EmbeddedChunk
from .store.vector_memory import MemoryVectorStore

DEFAULT_K = 5


@dataclass
class QueryCase:
    id: str
    query: str
    gold_chunk_ids: list[str]
    expect_miss: bool
    klass: str  # "semantic" | "entity"


@dataclass
class QueryEval:
    case: QueryCase
    rank: int | None  # 1-based rank of the first gold chunk in top-k; None if absent
    gold_present: bool  # every gold chunk id resolves to a real corpus chunk

    @property
    def hit(self) -> bool:
        return self.rank is not None


@dataclass
class VectorEvalResult:
    per_query: list[QueryEval]
    k: int

    @property
    def semantic(self) -> list[QueryEval]:
        return [q for q in self.per_query if not q.case.expect_miss]

    @property
    def misses(self) -> list[QueryEval]:
        return [q for q in self.per_query if q.case.expect_miss]

    def semantic_hit_rate(self) -> float:
        sem = self.semantic
        return sum(1 for q in sem if q.hit) / len(sem) if sem else 1.0

    def mrr(self) -> float:
        sem = self.semantic
        if not sem:
            return 1.0
        return sum((1.0 / q.rank) if q.rank else 0.0 for q in sem) / len(sem)

    def passes(self) -> bool:
        """Semantic queries all hit@k; entity-led misses all gold-present-but-unretrieved."""
        semantic_ok = all(q.hit for q in self.semantic)
        misses_ok = all(q.gold_present and not q.hit for q in self.misses)
        return semantic_ok and misses_ok

    def render(self) -> str:
        lines = [f"== vector-eval (credible-baseline confirmation, k={self.k}) =="]
        for q in self.per_query:
            tag = "MISS-expected" if q.case.expect_miss else "semantic"
            rank = f"rank={q.rank}" if q.rank else "rank=>k"
            present = "" if q.gold_present else " [GOLD ABSENT FROM CORPUS]"
            lines.append(f"  [{tag}] {q.case.id}: {rank} hit={q.hit}{present}  {q.case.query!r}")
        lines.append(
            f"semantic hit@{self.k}: {self.semantic_hit_rate():.3f}   MRR: {self.mrr():.3f}   "
            f"misses honored: {all(q.gold_present and not q.hit for q in self.misses)}"
        )
        lines.append("PASS" if self.passes() else "FAIL")
        return "\n".join(lines)


def load_query_set(path: Path) -> list[QueryCase]:
    """Load the curated query set, validating required keys with row-numbered errors."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases: list[QueryCase] = []
    for i, row in enumerate(data.get("queries", [])):
        missing = {"id", "query", "gold_chunk_ids", "class"} - set(row)
        if missing:
            raise ValueError(f"query-set row {i} missing {sorted(missing)}: {row!r}")
        klass = str(row["class"])
        expect_miss = bool(row.get("expect_miss", False))
        # The class label must be one of the two, and it must agree with expect_miss:
        # semantic-led queries are hits, entity-led queries are the honest misses.
        if klass not in ("semantic", "entity"):
            raise ValueError(f"query-set row {i} class must be semantic|entity, got {klass!r}")
        if (klass == "entity") != expect_miss:
            raise ValueError(
                f"query-set row {i}: class={klass!r} disagrees with expect_miss={expect_miss} "
                "(entity ⟺ expect_miss)"
            )
        cases.append(
            QueryCase(
                id=str(row["id"]),
                query=str(row["query"]),
                gold_chunk_ids=[str(g) for g in row["gold_chunk_ids"]],
                expect_miss=expect_miss,
                klass=klass,
            )
        )
    return cases


def load_frozen(path: Path) -> dict[str, dict[str, list[float]]]:
    """Load committed frozen vectors: ``{"chunks": {id: vec}, "queries": {id: vec}}``."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "chunks": {k: [float(x) for x in v] for k, v in raw.get("chunks", {}).items()},
        "queries": {k: [float(x) for x in v] for k, v in raw.get("queries", {}).items()},
    }


def evaluate_query_set(
    cases: list[QueryCase],
    frozen: dict[str, dict[str, list[float]]],
    corpus_chunks: dict[str, Chunk],
    k: int = DEFAULT_K,
) -> VectorEvalResult:
    """Score the curated set: k-NN each query's frozen vector over the frozen corpus."""
    store = MemoryVectorStore()
    for chunk_id, vector in frozen["chunks"].items():
        chunk = corpus_chunks.get(chunk_id)
        if chunk is None:
            raise ValueError(f"frozen embedding for unknown corpus chunk {chunk_id!r}")
        store.index_chunk(EmbeddedChunk(chunk, vector))

    per_query: list[QueryEval] = []
    for case in cases:
        gold_present = all(g in corpus_chunks for g in case.gold_chunk_ids)
        qvec = frozen["queries"].get(case.id)
        if qvec is None:
            raise ValueError(f"no frozen embedding for query {case.id!r}")
        ranked = [hit.chunk.id for hit in store.knn(qvec, k)]
        rank = next(
            (i for i, cid in enumerate(ranked, start=1) if cid in case.gold_chunk_ids), None
        )
        per_query.append(QueryEval(case=case, rank=rank, gold_present=gold_present))
    return VectorEvalResult(per_query=per_query, k=k)


def freeze_embeddings(
    corpus_chunks: dict[str, Chunk], cases: list[QueryCase], embedder: Embedder
) -> dict[str, dict[str, list[float]]]:
    """Embed every corpus chunk + every query (the ``--bedrock`` regeneration step)."""
    chunk_ids = list(corpus_chunks)
    chunk_vecs = embedder.embed([corpus_chunks[c].text for c in chunk_ids])
    query_ids = [c.id for c in cases]
    query_vecs = embedder.embed([c.query for c in cases])
    return {
        "chunks": dict(zip(chunk_ids, chunk_vecs, strict=True)),
        "queries": dict(zip(query_ids, query_vecs, strict=True)),
    }


def max_verbatim_overlap(query: str, text: str) -> int:
    """Longest common verbatim substring length (case-folded) — the curation guard.

    A large overlap means the query echoes the gold chunk's wording, so a "win" would
    be lexical, not semantic. The eval test asserts this stays under a small bound.
    """
    a, b = query.lower(), text.lower()
    if not a or not b:
        return 0
    # Classic DP longest-common-substring; corpus + queries are tiny, so O(n*m) is fine.
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best
