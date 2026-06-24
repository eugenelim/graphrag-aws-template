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
- ``AWS_REGION`` (optional, default ``us-east-1``) — region for SigV4 signing.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from graphrag.ingest import IngestReport, ingest
from graphrag.store.base import GraphStore


class S3Client(Protocol):
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...  # noqa: N803


def download_corpus(bucket: str, prefix: str, dest: Path, s3_client: S3Client) -> tuple[Path, Path]:
    """Download every object under ``prefix`` into ``dest``, preserving layout."""
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
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(bucket, key, str(target))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return dest / "community", dest / "enhancements"


def _build_store(endpoint: str, region: str) -> GraphStore:
    from graphrag.store.neptune import NeptuneGraphStore  # lazy: deploy-only path

    return NeptuneGraphStore(endpoint, region)


def run(
    env: Mapping[str, str],
    *,
    s3_client: S3Client | None = None,
    store: GraphStore | None = None,
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

    print(report.render())
    return report


def main() -> int:  # pragma: no cover - container entrypoint
    run(os.environ)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
