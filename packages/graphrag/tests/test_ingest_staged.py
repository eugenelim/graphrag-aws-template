"""medallion-staging T4a — the `ingest_staged` Bronze→Silver→Gold driver (integration).

Over in-memory stores + a `MemoryArtifactStore`: a re-ingest of an unchanged corpus makes zero
Bedrock embed calls (served from the persistent Silver cache); a delta re-ingest recomputes only
the changed Silver set and reaches the same end state as a full rebuild; an embedder-fp bump
re-embeds every doc; the schema-guided edges match a full `extract_schema_guided`; and visibility
labels are preserved.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from graphrag.embed import HashEmbedder
from graphrag.extract_llm import RuleTripleExtractor
from graphrag.ingest import ingest_staged
from graphrag.resolve import load_aliases, resolve
from graphrag.schema_extract import extract_schema_guided
from graphrag.silver import MemoryArtifactStore
from graphrag.sources import load_corpus
from graphrag.state import Stage
from graphrag.store.memory import MemoryGraphStore
from graphrag.store.vector_memory import MemoryVectorStore

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "corpus"
_NEW_KEP_YAML = """\
kep-number: 4242
title: A Brand New Enhancement
status: provisional
owning-sig: sig-node
authors:
  - "@thockin"
"""
_NEW_KEP_README = "# A Brand New Enhancement\n\nProse body for the new KEP about node things.\n"


def _snapshot(tmp: Path, name: str) -> tuple[Path, Path]:
    root = tmp / name
    shutil.copytree(FIXTURE_CORPUS, root)
    return root / "community", root / "enhancements"


def _add_kep(enhancements: Path) -> None:
    kep_dir = enhancements / "keps" / "sig-node" / "4242-brand-new"
    kep_dir.mkdir(parents=True)
    (kep_dir / "kep.yaml").write_text(_NEW_KEP_YAML, encoding="utf-8")
    (kep_dir / "README.md").write_text(_NEW_KEP_README, encoding="utf-8")


def _delete_kep(enhancements: Path) -> None:
    shutil.rmtree(enhancements / "keps" / "sig-network" / "1880-multiple-service-cidrs")


class _CountingEmbedder:
    """HashEmbedder that records every text embedded (the AC1/AC2 no-re-embed probe)."""

    def __init__(self) -> None:
        self._inner = HashEmbedder()
        self.embedded: list[str] = []

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def fingerprint(self) -> str:
        return self._inner.fingerprint()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return self._inner.embed(texts)


def _chunk_ids(store: MemoryVectorStore) -> set[str]:
    return set(store._items)


# --- AC1: the Silver cache makes a re-ingest of unchanged content zero-Bedrock ---------


def test_full_reingest_with_warm_cache_makes_zero_embed_calls() -> None:
    artifacts = MemoryArtifactStore()
    c, e = FIXTURE_CORPUS / "community", FIXTURE_CORPUS / "enhancements"

    spy1 = _CountingEmbedder()
    _report, state = ingest_staged(
        None, c, e, MemoryGraphStore(), MemoryVectorStore(), artifacts, spy1
    )
    assert spy1.embedded  # the first (cold-cache) run embedded

    # A fresh graph/vector store but the SAME warm artifacts: every doc is a Silver hit.
    spy2 = _CountingEmbedder()
    ingest_staged(None, c, e, MemoryGraphStore(), MemoryVectorStore(), artifacts, spy2)
    assert spy2.embedded == []  # zero Bedrock embed on the warm-cache re-ingest (AC1)
    assert state.version == 2 and all(d.stage is Stage.GOLD for d in state.docs.values())


def test_unchanged_delta_reingest_makes_zero_embed_calls(tmp_path: Path) -> None:
    c, e = _snapshot(tmp_path, "a")
    artifacts = MemoryArtifactStore()
    _r, state = ingest_staged(
        None, c, e, MemoryGraphStore(), MemoryVectorStore(), artifacts, _CountingEmbedder()
    )
    g, v = MemoryGraphStore(), MemoryVectorStore()
    # Warm the new stores via the cache, then re-ingest unchanged: empty delta, zero embed.
    ingest_staged(state, c, e, g, v, artifacts, _CountingEmbedder())
    spy = _CountingEmbedder()
    report, _state2 = ingest_staged(state, c, e, g, v, artifacts, spy)
    assert report.delta.is_empty
    assert spy.embedded == []


# --- AC2: embedder-fp bump re-embeds every doc -----------------------------------------


def test_embedder_fp_bump_reembeds_every_doc(tmp_path: Path) -> None:
    c, e = _snapshot(tmp_path, "a")
    artifacts = MemoryArtifactStore()
    g, v = MemoryGraphStore(), MemoryVectorStore()
    _r, state = ingest_staged(None, c, e, g, v, artifacts, _CountingEmbedder())  # dims=256

    # Re-ingest the SAME content with a different-dimension embedder → different fingerprint.
    class _Embedder256to128(_CountingEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self._inner = HashEmbedder(dimensions=128)

    spy = _Embedder256to128()
    report, new_state = ingest_staged(state, c, e, g, v, artifacts, spy)
    assert report.delta.is_empty  # no content changed...
    assert spy.embedded  # ...yet every doc's chunks were re-embedded (stale-vector fix, AC2)
    assert new_state.fingerprints["embedder"] != state.fingerprints["embedder"]


# --- delta parity with a full rebuild --------------------------------------------------


def _mutate(community: Path, enhancements: Path) -> None:
    _add_kep(enhancements)
    _delete_kep(enhancements)
    kep_yaml = (
        enhancements / "keps" / "sig-node" / "1287-in-place-update-pod-resources" / "kep.yaml"
    )
    kep_yaml.write_text(kep_yaml.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")


def test_delta_converges_to_full_rebuild(tmp_path: Path) -> None:
    # Path A: full ingest, then delta to the mutated snapshot.
    ca, ea = _snapshot(tmp_path, "a")
    ga, va = MemoryGraphStore(), MemoryVectorStore()
    aa = MemoryArtifactStore()
    _r, state = ingest_staged(None, ca, ea, ga, va, aa, HashEmbedder())
    _mutate(ca, ea)
    ingest_staged(state, ca, ea, ga, va, aa, HashEmbedder())

    # Path B: a from-scratch ingest of the same mutated snapshot.
    cb, eb = _snapshot(tmp_path, "b")
    _mutate(cb, eb)
    gb, vb = MemoryGraphStore(), MemoryVectorStore()
    ingest_staged(None, cb, eb, gb, vb, MemoryArtifactStore(), HashEmbedder())

    a_nodes = {n.id: n for n in ga.all_nodes()}
    b_nodes = {n.id: n for n in gb.all_nodes()}
    assert set(a_nodes) == set(b_nodes)
    for nid, an in a_nodes.items():
        bn = b_nodes[nid]
        assert (an.kind, an.doc_paths, an.sources, an.props) == (
            bn.kind,
            bn.doc_paths,
            bn.sources,
            bn.props,
        ), nid
    a_edges = {e.key(): e for e in ga.all_edges()}
    b_edges = {e.key(): e for e in gb.all_edges()}
    assert set(a_edges) == set(b_edges)
    for key, ae in a_edges.items():
        assert (ae.doc_paths, ae.sources) == (b_edges[key].doc_paths, b_edges[key].sources), key
    assert _chunk_ids(va) == _chunk_ids(vb)


# --- content-only change recomputes only the changed doc -------------------------------


def test_content_only_change_reembeds_only_changed_doc(tmp_path: Path) -> None:
    c, e = _snapshot(tmp_path, "a")
    artifacts = MemoryArtifactStore()
    g, v = MemoryGraphStore(), MemoryVectorStore()
    _r, state = ingest_staged(None, c, e, g, v, artifacts, _CountingEmbedder())

    _add_kep(e)  # one new prose doc
    spy = _CountingEmbedder()
    ingest_staged(state, c, e, g, v, artifacts, spy)
    from graphrag.chunk import chunk_corpus

    new_docs = [d for d in load_corpus(c, e) if "4242-brand-new" in d.path]
    expected = len(chunk_corpus(new_docs))
    assert expected > 0
    assert len(spy.embedded) == expected  # only the new doc's chunks embedded


# --- schema-guided edges + labels ------------------------------------------------------


def test_schema_guided_edges_match_full_extract_schema_guided(tmp_path: Path) -> None:
    c, e = _snapshot(tmp_path, "a")
    g, v = MemoryGraphStore(), MemoryVectorStore()
    report, _state = ingest_staged(
        None, c, e, g, v, MemoryArtifactStore(), HashEmbedder(), extractor=RuleTripleExtractor()
    )
    # Oracle: a one-pass extract_schema_guided over the full resolved graph.
    docs = load_corpus(c, e)
    full_graph = resolve(docs, load_aliases())
    oracle = extract_schema_guided(
        docs, full_graph, extractor=RuleTripleExtractor(), aliases=load_aliases()
    )
    oracle_keys = {edge.key() for edge in oracle.edges}
    assert oracle_keys  # the fixture's "collaborates closely with SIG Node" yields ≥1 edge
    store_schema_keys = {
        edge.key()
        for edge in g.all_edges()
        if edge.props.get("extraction_method") == "schema-guided-llm"
    }
    assert store_schema_keys == oracle_keys
    assert report.extraction is not None and report.extraction.edges


def test_staged_output_preserves_visibility_labels(tmp_path: Path) -> None:
    c, e = _snapshot(tmp_path, "a")
    g, v = MemoryGraphStore(), MemoryVectorStore()
    ingest_staged(None, c, e, g, v, MemoryArtifactStore(), HashEmbedder())
    # Every node carries a visibility prop (label_graph ran inside the staged Gold step).
    assert all("visibility" in n.props for n in g.all_nodes())
    # And every indexed chunk carries a visibility tier (label_chunks ran before indexing).
    assert all(ec.chunk.visibility for ec in v._items.values())


# --- at-least-once retry + state bookkeeping -------------------------------------------


class _FlakyVectorStore(MemoryVectorStore):
    """A vector store that raises on the Nth index_chunk — simulates a crash mid-staged-ingest."""

    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self._fail_on = fail_on
        self._calls = 0

    def index_chunk(self, embedded: object) -> None:  # type: ignore[override]
        self._calls += 1
        if self._calls == self._fail_on:
            raise RuntimeError("simulated mid-staged-ingest crash")
        super().index_chunk(embedded)  # type: ignore[arg-type]


def test_partial_failure_then_retry_converges(tmp_path: Path) -> None:
    # At-least-once: a staged run that crashes mid-index leaves the state UN-persisted (entrypoint
    # writes it last), so re-running from the SAME prev_state must converge to a clean ingest — no
    # duplicate or missing chunks (the delete-then-re-index idempotency the spec claims).
    c, e = _snapshot(tmp_path, "a")
    graph = MemoryGraphStore()
    flaky = _FlakyVectorStore(fail_on=1)  # crash on the first chunk indexed
    artifacts = MemoryArtifactStore()
    with pytest.raises(RuntimeError, match="simulated mid-staged-ingest crash"):
        ingest_staged(None, c, e, graph, flaky, artifacts, HashEmbedder())

    # Retry with the same (unadvanced) prev_state — None, since the first run never returned one.
    flaky._fail_on = -1  # never fail now
    ingest_staged(None, c, e, graph, flaky, artifacts, HashEmbedder())

    # Oracle: a from-scratch staged ingest of the same snapshot.
    gb, vb = MemoryGraphStore(), MemoryVectorStore()
    ingest_staged(None, c, e, gb, vb, MemoryArtifactStore(), HashEmbedder())
    assert {n.id for n in graph.all_nodes()} == {n.id for n in gb.all_nodes()}
    assert _chunk_ids(flaky) == _chunk_ids(vb)  # no duplicate / missing chunks after retry


def test_delta_carries_forward_candidate_keys_for_unchanged_docs(tmp_path: Path) -> None:
    # Nit: _staged_state's candidate-key carry-forward branch — a no-extractor delta over an
    # unchanged doc preserves the silver_candidates key a prior extractor run recorded.
    c, e = _snapshot(tmp_path, "a")
    artifacts = MemoryArtifactStore()
    g, v = MemoryGraphStore(), MemoryVectorStore()
    # A staged run WITH an extractor records a candidates key per doc.
    _r, state = ingest_staged(
        None, c, e, g, v, artifacts, HashEmbedder(), extractor=RuleTripleExtractor()
    )
    prose = "community/sig-network/README.md"
    assert state.docs[prose].silver_candidates is not None  # candidates key set by extractor run

    # A subsequent no-extractor delta over the unchanged corpus carries the key forward.
    _r2, state2 = ingest_staged(state, c, e, g, v, artifacts, HashEmbedder())
    assert state2.docs[prose].silver_candidates == state.docs[prose].silver_candidates
