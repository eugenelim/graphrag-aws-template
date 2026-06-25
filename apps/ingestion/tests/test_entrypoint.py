"""T10 — Fargate entrypoint: S3 download + ingest wiring (S3 + store mocked)."""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Any

from graphrag.embed import HashEmbedder
from graphrag.store import MemoryGraphStore, MemoryVectorStore
from ingestion.entrypoint import run

CORPUS = Path(__file__).parents[3] / "packages/graphrag/tests/fixtures/corpus"


class FakeS3:
    """Serves a corpus directory as an S3 snapshot under a prefix, plus an in-memory object
    store for the manifest (slice-5 get_object/put_object)."""

    def __init__(self, root: Path, prefix: str) -> None:
        self._root = root
        self._prefix = prefix
        self._objects: dict[str, bytes] = {}  # put_object/get_object (the manifest)

    def _corpus_files(self) -> dict[str, Path]:
        return {
            f"{self._prefix}{p.relative_to(self._root).as_posix()}": p
            for p in self._root.rglob("*")
            if p.is_file()
        }

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        prefix = kwargs.get("Prefix", "")
        contents = [{"Key": k} for k in self._corpus_files() if k.startswith(prefix)]
        return {"Contents": contents, "IsTruncated": False}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        shutil.copyfile(self._corpus_files()[Key], Filename)

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self._objects:
            raise FileNotFoundError(Key)  # entrypoint treats this as "no prior manifest"
        return {"Body": io.BytesIO(self._objects[Key])}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:  # noqa: N803
        self._objects[Key] = Body
        return {}


def test_download_rejects_keys_that_escape_dest(tmp_path: Path) -> None:
    import pytest

    from ingestion.entrypoint import download_corpus

    class EvilS3:
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            return {"Contents": [{"Key": "snap/../../../../tmp/evil.txt"}], "IsTruncated": False}

        def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
            raise AssertionError("must not download a path-traversal key")

        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            raise AssertionError("unused")

        def put_object(self, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:  # noqa: N803
            raise AssertionError("unused")

    with pytest.raises(ValueError, match="escapes the corpus dir"):
        download_corpus("b", "snap/", tmp_path, EvilS3())


def test_entrypoint_downloads_and_ingests() -> None:
    from graphrag.ingest import IngestReport

    store = MemoryGraphStore()
    report = run(
        {"CORPUS_BUCKET": "demo-bucket", "CORPUS_PREFIX": "snap/", "AWS_REGION": "us-east-1"},
        s3_client=FakeS3(CORPUS, "snap/"),
        store=store,
    )
    # The deployed path runs the same ingest as the CLI: same nodes, same merges.
    assert isinstance(report, IngestReport)
    assert report.nodes == 22
    assert "sig:sig-network" in report.merges
    assert len(store.all_nodes()) == 22


def test_entrypoint_dual_writes_graph_and_vector() -> None:
    # One parse, two stores (charter pattern 2): the graph and vector indices are
    # written from the same corpus read so they can't diverge.
    graph = MemoryGraphStore()
    vectors = MemoryVectorStore()
    run(
        {"CORPUS_BUCKET": "demo-bucket", "CORPUS_PREFIX": "snap/", "AWS_REGION": "us-east-1"},
        s3_client=FakeS3(CORPUS, "snap/"),
        store=graph,
        vector_store=vectors,
        embedder=HashEmbedder(),
    )
    assert graph.all_nodes()  # graph half written
    assert vectors.count() > 0  # vector half written from the same parse


def _env(mode: str = "full") -> dict[str, str]:
    return {
        "CORPUS_BUCKET": "demo-bucket",
        "CORPUS_PREFIX": "snap/",
        "AWS_REGION": "us-east-1",
        "MODE": mode,
    }


def test_full_mode_writes_manifest_last() -> None:
    s3 = FakeS3(CORPUS, "snap/")
    run(_env("full"), s3_client=s3, store=MemoryGraphStore())
    # The manifest is persisted so the next --delta has a baseline (AC8).
    assert "snap/manifest.json" in s3._objects
    assert b'"docs"' in s3._objects["snap/manifest.json"]


def test_delta_mode_reads_manifest_runs_delta_and_rewrites_it(tmp_path: Path) -> None:
    from graphrag.ingest import DeltaReport

    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    s3 = FakeS3(corpus, "snap/")
    graph, vectors = MemoryGraphStore(), MemoryVectorStore()
    # Seed: a full ingest writes the baseline manifest into S3.
    run(_env("full"), s3_client=s3, store=graph, vector_store=vectors, embedder=HashEmbedder())
    baseline = s3._objects["snap/manifest.json"]

    # Mutate the snapshot: add a new KEP, then run MODE=delta.
    new_kep = corpus / "enhancements" / "keps" / "sig-node" / "4242-brand-new"
    new_kep.mkdir(parents=True)
    (new_kep / "kep.yaml").write_text(
        "kep-number: 4242\ntitle: New\nstatus: provisional\nowning-sig: sig-node\n",
        encoding="utf-8",
    )
    (new_kep / "README.md").write_text("# New\n\nProse.\n", encoding="utf-8")

    report = run(
        _env("delta"), s3_client=s3, store=graph, vector_store=vectors, embedder=HashEmbedder()
    )
    assert isinstance(report, DeltaReport)
    assert not report.full_ingest  # a real manifest was read back
    assert graph.get_node("kep-4242") is not None
    assert s3._objects["snap/manifest.json"] != baseline  # manifest rewritten last


def test_rebuild_mode_clears_then_reingests(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    s3 = FakeS3(corpus, "snap/")
    graph, vectors = MemoryGraphStore(), MemoryVectorStore()
    from graphrag.model import EntityKind, Node

    graph.upsert_node(Node("kep-stale", EntityKind.KEP, doc_paths={"gone/x"}))
    run(_env("rebuild"), s3_client=s3, store=graph, vector_store=vectors, embedder=HashEmbedder())
    assert graph.get_node("kep-stale") is None  # cleared
    assert graph.get_node("kep-2086") is not None  # reingested
    assert "snap/manifest.json" in s3._objects


def test_entrypoint_dual_write_labels_chunks() -> None:
    # Slice 4: the same dual-write stamps synthetic visibility on every chunk, so the
    # vector store carries the permission-filter metadata consistent with the graph labels.
    graph = MemoryGraphStore()
    vectors = MemoryVectorStore()
    run(
        {"CORPUS_BUCKET": "demo-bucket", "CORPUS_PREFIX": "snap/", "AWS_REGION": "us-east-1"},
        s3_client=FakeS3(CORPUS, "snap/"),
        store=graph,
        vector_store=vectors,
        embedder=HashEmbedder(),
    )
    visibilities = {ec.chunk.visibility for ec in vectors._items.values()}
    # kep-1287 is labeled restricted in labels.yaml; its README chunks inherit it.
    assert "restricted" in visibilities
    # chunks owned only by public entities stay public.
    assert "public" in visibilities
