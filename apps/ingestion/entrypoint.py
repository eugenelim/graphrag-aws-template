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

import logging
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from graphrag.delta import Manifest, build_manifest, manifest_from_json, manifest_to_json
from graphrag.embed import Embedder
from graphrag.ingest import DeltaReport, IngestReport, ingest, ingest_delta, rebuild
from graphrag.store.base import GraphStore
from graphrag.store.vector_base import VectorStore

# The ingest manifest (doc id -> content hash) lives at the corpus prefix root in S3; a --delta
# diffs the new snapshot against it, and every run writes it back **last** (slice 5; AC8).
MANIFEST_FILENAME = "manifest.json"

logger = logging.getLogger("ingestion.entrypoint")


class S3Client(Protocol):
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...  # noqa: N803
    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]: ...  # noqa: N803
    def put_object(self, Bucket: str, Key: str, Body: bytes) -> Any: ...  # noqa: N803


def _is_not_found(exc: Exception) -> bool:
    """Whether an S3 ``get_object`` error means "no such key" (the first-delta case, AC8b)."""
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code in {"NoSuchKey", "404", "NotFound"}


def read_manifest(s3_client: S3Client, bucket: str, key: str) -> Manifest | None:
    """Read the stored manifest from S3, or ``None`` when it does not exist yet (first --delta)."""
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # a missing manifest is expected on the first delta (AC8b)
        if _is_not_found(exc):
            return None
        raise
    body = resp["Body"].read()
    text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    return manifest_from_json(text)


def write_manifest(s3_client: S3Client, bucket: str, key: str, manifest: Manifest) -> None:
    """Persist the manifest to S3 — called **last**, after both stores are updated (AC8)."""
    s3_client.put_object(Bucket=bucket, Key=key, Body=manifest_to_json(manifest).encode("utf-8"))


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


def _resolve_vector(
    env: Mapping[str, str], vector_store: VectorStore | None, embedder: Embedder | None
) -> tuple[VectorStore, Embedder]:
    """Resolve the vector store + embedder for the delta/rebuild dual-write (deploy-or-injected)."""
    region = env.get("AWS_REGION", "us-east-1")
    if embedder is None:  # pragma: no cover - exercised only in the deployed task
        from graphrag.embed import BedrockTitanEmbedder

        embedder = BedrockTitanEmbedder(region=region)
    if vector_store is None:  # pragma: no cover - exercised only in the deployed task
        endpoint = env.get("OPENSEARCH_ENDPOINT")
        if not endpoint:
            raise RuntimeError("MODE=delta/rebuild requires a vector store (OPENSEARCH_ENDPOINT)")
        from graphrag.store.opensearch import OpenSearchVectorStore

        vector_store = OpenSearchVectorStore(endpoint, region)
    vector_store.create_index()  # no-op for in-memory; creates the k-NN index on OpenSearch
    return vector_store, embedder


def run(
    env: Mapping[str, str],
    *,
    s3_client: S3Client | None = None,
    store: GraphStore | None = None,
    vector_store: VectorStore | None = None,
    embedder: Embedder | None = None,
) -> IngestReport | DeltaReport:
    """Run the ingestion task. ``MODE`` selects ``full`` (default — the slice-1–4 dual-write,
    unchanged), ``delta`` (slice-5 incremental re-ingest against the stored manifest), or
    ``rebuild`` (clear both stores + full ingest). Every mode writes the manifest to S3 **last**,
    after both stores are updated, so the next ``--delta`` has a baseline (AC8)."""
    bucket = env["CORPUS_BUCKET"]
    prefix = env.get("CORPUS_PREFIX", "")
    region = env.get("AWS_REGION", "us-east-1")
    mode = env.get("MODE", "full").lower()
    manifest_key = f"{prefix}{MANIFEST_FILENAME}"

    if s3_client is None:  # pragma: no cover - exercised only in the deployed task
        import boto3

        s3_client = boto3.client("s3", region_name=region)
    if store is None:  # pragma: no cover - exercised only in the deployed task
        store = _build_store(env["NEPTUNE_ENDPOINT"], region)

    report: IngestReport | DeltaReport
    with tempfile.TemporaryDirectory() as tmp:
        community, enhancements = download_corpus(bucket, prefix, Path(tmp), s3_client)
        if mode == "full":
            report = ingest(community, enhancements, store)
            _vector_dual_write(env, community, enhancements, vector_store, embedder)
            new_manifest = build_manifest(community, enhancements)
        elif mode == "rebuild":
            vstore, emb = _resolve_vector(env, vector_store, embedder)
            report = rebuild(community, enhancements, store, vstore, emb)
            new_manifest = report.new_manifest
        elif mode == "delta":
            vstore, emb = _resolve_vector(env, vector_store, embedder)
            prev = read_manifest(s3_client, bucket, manifest_key)
            if prev is None:
                # Loud, not silent: an operator expecting an incremental delta should see that the
                # baseline was missing and the run fell back to a full re-ingest (re-embeds all).
                logger.warning(
                    "MODE=delta but no manifest at s3://%s/%s — falling back to a FULL ingest",
                    bucket,
                    manifest_key,
                )
            report = ingest_delta(prev, community, enhancements, store, vstore, emb)
            new_manifest = report.new_manifest
        else:
            raise ValueError(f"unknown MODE {mode!r}: expected full | delta | rebuild")

        print(report.render())
        # Written last, only after both stores are updated: a crash leaves the old manifest, so
        # the next --delta re-attempts the same delta (at-least-once, idempotent).
        write_manifest(s3_client, bucket, manifest_key, new_manifest)

    return report


def main() -> int:  # pragma: no cover - container entrypoint
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(os.environ)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
