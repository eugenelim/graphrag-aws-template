"""Fargate ingestion entrypoint.

Resolves the corpus snapshot from S3 into a temp dir, builds a Neptune-backed
graph store from the task's environment, and runs the same ``graphrag.ingest``
the CLI runs — so the deployed path and the local path share one code path
(reproducibility). Configuration is environment-only (the Fargate task
definition), and AWS credentials come from the task role via the default botocore
chain — never from this code.

Env:
- ``CORPUS_BUCKET`` (required) — S3 bucket holding the corpus snapshot.
- ``CORPUS_PREFIX`` (optional) — key prefix; the snapshot must contain
  ``community/`` and ``enhancements/`` trees.
- ``NEPTUNE_ENDPOINT`` (required) — ``https://`` Neptune cluster endpoint.
- ``OPENSEARCH_ENDPOINT`` (optional) — ``https://`` OpenSearch domain endpoint; when
  set, the same run **dual-writes** the vector index (chunk -> embed -> index) so the
  graph and vector stores never diverge (charter pattern 2). Absent, only the graph
  is written (a slice-1-only deploy).
- ``AWS_REGION`` (optional, default ``us-east-1``) — region for SigV4 signing.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from graphrag.embed import Embedder
from graphrag.ingest import IngestReport, ingest
from graphrag.store.base import GraphStore
from graphrag.store.vector_base import VectorStore


class S3Client(Protocol):
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...  # noqa: N803


def download_corpus(bucket: str, prefix: str, dest: Path, s3_client: S3Client) -> tuple[Path, Path]:
    """Download every object under ``prefix`` into ``dest``, preserving layout."""
    dest_root = dest.resolve()
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3_client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(prefix) :].lstrip("/")
            # Confine the write to dest: a poisoned snapshot key like
            # "snap/../../etc/x" must not escape the temp dir (CWE-22/CWE-23).
            target = (dest_root / rel).resolve()
            if not rel or not target.is_relative_to(dest_root):
                raise ValueError(f"refusing S3 key that escapes the corpus dir: {key!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(bucket, key, str(target))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return dest / "community", dest / "enhancements"


def _build_store(endpoint: str, region: str) -> GraphStore:
    from graphrag.store.neptune import NeptuneGraphStore  # lazy: deploy-only path

    return NeptuneGraphStore(endpoint, region)


def _vector_dual_write(
    env: Mapping[str, str],
    community: Path,
    enhancements: Path,
    vector_store: VectorStore | None,
    embedder: Embedder | None,
) -> int:
    """Write the vector half from the same corpus read. Returns the chunk count.

    The same Fargate run reads one immutable S3 snapshot, so the graph and vector
    writes can't diverge (charter pattern 2). A no-op when neither an injected store
    (tests) nor ``OPENSEARCH_ENDPOINT`` (deploy) is present.
    """
    endpoint = env.get("OPENSEARCH_ENDPOINT")
    if vector_store is None and not endpoint:
        return 0
    region = env.get("AWS_REGION", "us-east-1")
    if embedder is None:  # pragma: no cover - exercised only in the deployed task
        from graphrag.embed import BedrockTitanEmbedder

        embedder = BedrockTitanEmbedder(region=region)
    if vector_store is None:  # pragma: no cover - exercised only in the deployed task
        from graphrag.store.opensearch import OpenSearchVectorStore

        vector_store = OpenSearchVectorStore(endpoint or "", region)

    from graphrag.chunk import chunk_corpus
    from graphrag.labels import label_chunks, load_labels
    from graphrag.sources import load_corpus
    from graphrag.store.vector_base import EmbeddedChunk

    vector_store.create_index()  # no-op for in-memory; creates the k-NN index on OpenSearch
    chunks = chunk_corpus(load_corpus(community, enhancements))
    # Stamp synthetic visibility on every chunk from the same parse (slice 4) so the vector
    # store carries the permission-filter metadata, consistent with the graph's labels.
    label_chunks(chunks, load_labels())
    vectors = embedder.embed([c.text for c in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        vector_store.index_chunk(EmbeddedChunk(chunk, vector))
    print(f"vector dual-write: indexed {len(chunks)} chunks")
    return len(chunks)


def run(
    env: Mapping[str, str],
    *,
    s3_client: S3Client | None = None,
    store: GraphStore | None = None,
    vector_store: VectorStore | None = None,
    embedder: Embedder | None = None,
) -> IngestReport:
    bucket = env["CORPUS_BUCKET"]
    prefix = env.get("CORPUS_PREFIX", "")
    region = env.get("AWS_REGION", "us-east-1")

    if s3_client is None:  # pragma: no cover - exercised only in the deployed task
        import boto3

        s3_client = boto3.client("s3", region_name=region)
    if store is None:  # pragma: no cover - exercised only in the deployed task
        store = _build_store(env["NEPTUNE_ENDPOINT"], region)

    with tempfile.TemporaryDirectory() as tmp:
        community, enhancements = download_corpus(bucket, prefix, Path(tmp), s3_client)
        report = ingest(community, enhancements, store)
        _vector_dual_write(env, community, enhancements, vector_store, embedder)

    print(report.render())
    return report


def main() -> int:  # pragma: no cover - container entrypoint
    run(os.environ)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
