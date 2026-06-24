"""In-VPC OpenSearch vector smoke probe (invoked on demand via `aws lambda invoke`).

The lightest secure way to verify the deployed vector store end-to-end (slice-2 AC7):
a scale-to-zero Lambda in the private subnets that embeds text via Titan v2, indexes a
unique chunk into OpenSearch, reads it back via k-NN through the **same**
``OpenSearchVectorStore`` the CLI uses, cleans up, and returns the trace. No public
endpoint, no NAT, no standing cost; credentials come from the execution role via the
botocore chain. Because it reuses the real adapter + real embeddings, a green result
proves the actual OpenSearch k-NN works against the live domain — not a reimplementation.

Deployed by the CDK stack as ``Code.from_asset`` over this package (pure-Python;
boto3/botocore are in the Lambda runtime).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from .chunk import Chunk
from .embed import BedrockTitanEmbedder
from .store.opensearch import OpenSearchVectorStore
from .store.vector_base import EmbeddedChunk


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    endpoint = os.environ["OPENSEARCH_ENDPOINT"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    embedder = BedrockTitanEmbedder(region=region)
    store = OpenSearchVectorStore(endpoint, region)

    run = uuid.uuid4().hex[:8]
    chunk_id = f"smoke-{run}"
    text = f"smoke probe {run}: kubernetes service networking and in-place pod resize"
    try:
        store.create_index()  # idempotent
        vector = embedder.embed([text])[0]
        chunk = Chunk(
            id=chunk_id,
            text=text,
            source="smoke",
            doc_path="smoke/probe.md",
            heading="Probe",
            entity_ids=["sig:smoke"],
        )
        # refresh=true so the freshly-indexed doc is immediately searchable.
        store.index_chunk(EmbeddedChunk(chunk, vector), refresh=True)
        hits = [hit.chunk.id for hit in store.knn(vector, k=3)]
        ok = chunk_id in hits
        return {
            "ok": ok,
            "run": run,
            "retrieved_id": chunk_id if ok else None,
            "hits": hits,
            "dims": len(vector),
        }
    finally:
        store.delete([chunk_id])
