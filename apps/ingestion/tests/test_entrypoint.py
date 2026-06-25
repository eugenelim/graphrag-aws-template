"""T10 — Fargate entrypoint: S3 download + ingest wiring (S3 + store mocked)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from graphrag.embed import HashEmbedder
from graphrag.store import MemoryGraphStore, MemoryVectorStore
from ingestion.entrypoint import run

CORPUS = Path(__file__).parents[3] / "packages/graphrag/tests/fixtures/corpus"


class FakeS3:
    """Serves the fixture corpus as if it were an S3 snapshot under a prefix."""

    def __init__(self, root: Path, prefix: str) -> None:
        self._files = {
            f"{prefix}{p.relative_to(root).as_posix()}": p for p in root.rglob("*") if p.is_file()
        }

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        prefix = kwargs.get("Prefix", "")
        contents = [{"Key": k} for k in self._files if k.startswith(prefix)]
        return {"Contents": contents, "IsTruncated": False}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        shutil.copyfile(self._files[Key], Filename)


def test_download_rejects_keys_that_escape_dest(tmp_path: Path) -> None:
    import pytest

    from ingestion.entrypoint import download_corpus

    class EvilS3:
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            return {"Contents": [{"Key": "snap/../../../../tmp/evil.txt"}], "IsTruncated": False}

        def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
            raise AssertionError("must not download a path-traversal key")

    with pytest.raises(ValueError, match="escapes the corpus dir"):
        download_corpus("b", "snap/", tmp_path, EvilS3())


def test_entrypoint_downloads_and_ingests() -> None:
    store = MemoryGraphStore()
    report = run(
        {"CORPUS_BUCKET": "demo-bucket", "CORPUS_PREFIX": "snap/", "AWS_REGION": "us-east-1"},
        s3_client=FakeS3(CORPUS, "snap/"),
        store=store,
    )
    # The deployed path runs the same ingest as the CLI: same nodes, same merges.
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
