"""T10 — Fargate entrypoint: S3 download + ingest wiring (S3 + store mocked)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from graphrag.store import MemoryGraphStore
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
