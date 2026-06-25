"""Tests for ingest_delta / rebuild — provenance-set orphan reconciliation + dual-store delta.

The corpus snapshots are copies of the bundled fixture corpus, mutated in a temp dir so the
delta runs over realistic real-excerpt documents (AC2–AC8b).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from graphrag.delta import build_manifest
from graphrag.embed import HashEmbedder
from graphrag.ingest import ingest_delta, rebuild
from graphrag.store.memory import MemoryGraphStore
from graphrag.store.vector_memory import MemoryVectorStore


class CountingEmbedder:
    """Wraps HashEmbedder and records every text it embeds — the AC2 no-re-embed probe."""

    def __init__(self) -> None:
        self._inner = HashEmbedder()
        self.embedded: list[str] = []

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return self._inner.embed(texts)


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
    """Copy the fixture corpus into ``tmp/name`` and return (community_root, enhancements_root)."""
    root = tmp / name
    shutil.copytree(FIXTURE_CORPUS, root)
    return root / "community", root / "enhancements"


def _chunk_ids(store: MemoryVectorStore) -> set[str]:
    return set(store._items)  # white-box: the in-memory chunk identity set


def _kep_yaml(enhancements: Path, sig: str, kep_dir: str) -> Path:
    return enhancements / "keps" / sig / kep_dir / "kep.yaml"


def _add_kep(enhancements: Path) -> None:
    kep_dir = enhancements / "keps" / "sig-node" / "4242-brand-new"
    kep_dir.mkdir(parents=True)
    (kep_dir / "kep.yaml").write_text(_NEW_KEP_YAML, encoding="utf-8")
    (kep_dir / "README.md").write_text(_NEW_KEP_README, encoding="utf-8")


def _delete_kep(enhancements: Path) -> None:
    shutil.rmtree(enhancements / "keps" / "sig-network" / "1880-multiple-service-cidrs")


def _full_ingest(community: Path, enhancements: Path) -> tuple[MemoryGraphStore, MemoryVectorStore]:
    g, v = MemoryGraphStore(), MemoryVectorStore()
    ingest_delta(None, community, enhancements, g, v, HashEmbedder())  # prev=None -> full
    return g, v


# --- AC8b: no-prior-manifest fallback + backfill --------------------------------------


def test_prev_none_runs_full_ingest(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = MemoryGraphStore(), MemoryVectorStore()
    report = ingest_delta(None, community, enhancements, g, v, HashEmbedder())
    assert report.full_ingest
    assert g.all_nodes() and v.count() > 0


def test_full_ingest_backfills_doc_paths(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, _ = _full_ingest(community, enhancements)
    # Every node/edge carries a non-empty provenance set after a full ingest — the property the
    # next --delta's reconciliation reads back (AC8b backfill).
    assert all(n.doc_paths for n in g.all_nodes())
    assert all(e.doc_paths for e in g.all_edges())


# --- AC2: re-ingest is delta-only -----------------------------------------------------


def test_delta_does_not_reembed_unchanged_docs(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = MemoryGraphStore(), MemoryVectorStore()
    spy = CountingEmbedder()
    ingest_delta(None, community, enhancements, g, v, spy)  # full ingest
    full_count = len(spy.embedded)
    manifest = build_manifest(community, enhancements)

    _add_kep(enhancements)  # change exactly one document set (a new KEP)
    spy.embedded.clear()
    ingest_delta(manifest, community, enhancements, g, v, spy)
    # Only the new KEP's chunks were embedded — exactly the delta doc's chunk count, far fewer
    # than a full re-embed (pins "only delta docs embedded" without coupling to prose wording).
    from graphrag.chunk import chunk_corpus
    from graphrag.sources import load_corpus

    new_kep_docs = [d for d in load_corpus(community, enhancements) if "4242-brand-new" in d.path]
    expected = len(chunk_corpus(new_kep_docs))
    assert expected > 0
    assert len(spy.embedded) == expected < full_count


# --- AC3 + AC4: both stores updated; orphan removal keeps referenced nodes -------------


def test_delta_add_then_kep_present_in_both_stores(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = _full_ingest(community, enhancements)
    manifest = build_manifest(community, enhancements)
    _add_kep(enhancements)
    report = ingest_delta(manifest, community, enhancements, g, v, HashEmbedder())

    assert any(d.endswith("4242-brand-new/kep.yaml") for d in report.delta.added)
    assert g.get_node("kep-4242") is not None  # graph
    assert any(cid.endswith("README.md#0") and "4242" in cid for cid in _chunk_ids(v))  # vector


def test_delete_removes_orphan_kep_but_keeps_referenced_sig(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = _full_ingest(community, enhancements)
    manifest = build_manifest(community, enhancements)
    assert g.get_node("kep-1880") is not None

    _delete_kep(enhancements)
    report = ingest_delta(manifest, community, enhancements, g, v, HashEmbedder())

    # The KEP node and its chunks are gone (orphan removal)...
    assert g.get_node("kep-1880") is None
    assert not any("1880" in cid for cid in _chunk_ids(v))
    # ...but its owning SIG survives — still contributed by sigs.yaml and other KEPs (AC4).
    sig = g.get_node("sig:sig-network")
    assert sig is not None and sig.doc_paths
    assert report.orphans_removed >= 1


def test_changed_kep_yaml_status_updates_graph(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = _full_ingest(community, enhancements)
    manifest = build_manifest(community, enhancements)
    kep_yaml = _kep_yaml(enhancements, "sig-network", "2086-service-internal-traffic-policy")
    text = kep_yaml.read_text(encoding="utf-8")
    assert "status: implemented" in text
    kep_yaml.write_text(text.replace("status: implemented", "status: withdrawn"), encoding="utf-8")

    ingest_delta(manifest, community, enhancements, g, v, HashEmbedder())
    node = g.get_node("kep-2086")
    assert node is not None and node.props.get("status") == "withdrawn"  # AC3


# --- AC5: move ------------------------------------------------------------------------


def test_move_migrates_provenance_and_chunks(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = _full_ingest(community, enhancements)
    manifest = build_manifest(community, enhancements)
    src = enhancements / "keps" / "sig-node" / "1287-in-place-update-pod-resources"
    dst = enhancements / "keps" / "sig-node" / "1287-in-place-pod-resize"
    src.rename(dst)

    report = ingest_delta(manifest, community, enhancements, g, v, HashEmbedder())
    assert report.delta.moved  # classified as a move, not delete+add
    # The KEP node still exists; its chunks now live under the new path, none orphaned.
    assert g.get_node("kep-1287") is not None
    assert any("1287-in-place-pod-resize" in cid for cid in _chunk_ids(v))
    assert not any("1287-in-place-update-pod-resources" in cid for cid in _chunk_ids(v))


# --- AC6: delta equals rebuild (in-memory oracle) -------------------------------------


def _mutate(community: Path, enhancements: Path) -> None:
    """A combined add + delete + kep.yaml-change delta that reconciles exactly (no
    multiply-contributed-prop edge case — see backlog incremental-delta-multicontributed-prop)."""
    _add_kep(enhancements)
    _delete_kep(enhancements)
    # A real content change to a kep.yaml so it classifies as "changed".
    kep_yaml = _kep_yaml(enhancements, "sig-node", "1287-in-place-update-pod-resources")
    kep_yaml.write_text(kep_yaml.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")


def test_delta_converges_to_rebuild_for_structural_delta(tmp_path: Path) -> None:
    # AC6 oracle for a STRUCTURAL delta (add + delete + kep.yaml change) — these reconcile
    # exactly. The one out-of-scope case (a README-H1 edit / single-co-contributor delete that
    # diverges on a multiply-contributed prop) is backlog incremental-delta-multicontributed-prop.
    # Path A: ingest snapshot, then delta to the mutated snapshot.
    ca, ea = _snapshot(tmp_path, "a")
    ga, va = _full_ingest(ca, ea)
    manifest = build_manifest(ca, ea)
    _mutate(ca, ea)
    ingest_delta(manifest, ca, ea, ga, va, HashEmbedder())

    # Path B: rebuild the same mutated snapshot from scratch.
    cb, eb = _snapshot(tmp_path, "b")
    _mutate(cb, eb)
    gb, vb = MemoryGraphStore(), MemoryVectorStore()
    rebuild(cb, eb, gb, vb, HashEmbedder())

    # Nodes: id+kind, provenance, sources, and props all match.
    a_nodes = {n.id: n for n in ga.all_nodes()}
    b_nodes = {n.id: n for n in gb.all_nodes()}
    assert set(a_nodes) == set(b_nodes)
    for nid, an in a_nodes.items():
        bn = b_nodes[nid]
        assert an.kind == bn.kind
        assert an.doc_paths == bn.doc_paths, nid
        assert an.sources == bn.sources, nid
        assert an.props == bn.props, nid
    # Edges: key + provenance + sources.
    a_edges = {e.key(): e for e in ga.all_edges()}
    b_edges = {e.key(): e for e in gb.all_edges()}
    assert set(a_edges) == set(b_edges)
    for key, ae in a_edges.items():
        assert ae.doc_paths == b_edges[key].doc_paths, key
        assert ae.sources == b_edges[key].sources, key
    # Chunks: identical id set.
    assert _chunk_ids(va) == _chunk_ids(vb)


# --- AC7: rebuild + AC8b idempotency --------------------------------------------------


class _FlakyVectorStore(MemoryVectorStore):
    """A vector store that raises on the Nth index_chunk — to simulate a crash mid-delta."""

    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self._fail_on = fail_on
        self._calls = 0

    def index_chunk(self, embedded: object) -> None:  # type: ignore[override]
        self._calls += 1
        if self._calls == self._fail_on:
            raise RuntimeError("simulated mid-delta crash")
        super().index_chunk(embedded)  # type: ignore[arg-type]


def test_partial_failure_then_retry_converges_to_rebuild(tmp_path: Path) -> None:
    # The at-least-once posture (manifest written last): a delta that crashes mid-index leaves the
    # stores partly mutated and the manifest unchanged; re-running with the SAME prev_manifest must
    # converge to the rebuild oracle (proving the claim, not just asserting it in a docstring).
    ca, ea = _snapshot(tmp_path, "a")
    graph = MemoryGraphStore()
    flaky = _FlakyVectorStore(fail_on=-1)  # never fail during the base full ingest
    ingest_delta(None, ca, ea, graph, flaky, HashEmbedder())
    manifest = build_manifest(ca, ea)
    _add_kep(ea)

    # First attempt crashes partway through indexing the delta chunks.
    flaky._calls = 0
    flaky._fail_on = 1  # fail on the first delta chunk
    with pytest.raises(RuntimeError, match="simulated mid-delta crash"):
        ingest_delta(manifest, ca, ea, graph, flaky, HashEmbedder())

    # Retry with the same (unadvanced) manifest, now non-flaky.
    flaky._fail_on = -1  # never fail
    ingest_delta(manifest, ca, ea, graph, flaky, HashEmbedder())

    # Oracle: a clean rebuild of the same snapshot.
    cb, eb = _snapshot(tmp_path, "b")
    _add_kep(eb)
    gb, vb = MemoryGraphStore(), MemoryVectorStore()
    rebuild(cb, eb, gb, vb, HashEmbedder())

    assert {n.id for n in graph.all_nodes()} == {n.id for n in gb.all_nodes()}
    assert _chunk_ids(flaky) == _chunk_ids(vb)  # no duplicate / missing chunks after retry


def test_rebuild_clears_then_reingests(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = _full_ingest(community, enhancements)
    # Pollute with a stale node that no document contributes; rebuild must drop it.
    from graphrag.model import EntityKind, Node

    g.upsert_node(Node("kep-stale", EntityKind.KEP, doc_paths={"gone/x"}))
    rebuild(community, enhancements, g, v, HashEmbedder())
    assert g.get_node("kep-stale") is None
    assert g.get_node("kep-2086") is not None


def test_idempotent_rerun_is_noop(tmp_path: Path) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = _full_ingest(community, enhancements)
    manifest = build_manifest(community, enhancements)
    before = ({n.id for n in g.all_nodes()}, _chunk_ids(v))
    report = ingest_delta(manifest, community, enhancements, g, v, HashEmbedder())
    assert report.delta.is_empty
    assert report.orphans_removed == 0
    assert ({n.id for n in g.all_nodes()}, _chunk_ids(v)) == before


@pytest.mark.parametrize("ordinal", [0])
def test_changed_kep_yaml_classified_as_changed(tmp_path: Path, ordinal: int) -> None:
    community, enhancements = _snapshot(tmp_path, "a")
    g, v = _full_ingest(community, enhancements)
    manifest = build_manifest(community, enhancements)
    kep_yaml = _kep_yaml(enhancements, "sig-node", "1287-in-place-update-pod-resources")
    kep_yaml.write_text(kep_yaml.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    report = ingest_delta(manifest, community, enhancements, g, v, HashEmbedder())
    assert any(
        d.endswith("1287-in-place-update-pod-resources/kep.yaml") for d in report.delta.changed
    )
