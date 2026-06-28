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
from graphrag.extract_llm import TripleExtractor
from graphrag.ingest import DeltaReport, IngestReport, ingest, ingest_staged, rebuild
from graphrag.silver import ArtifactStore
from graphrag.state import IngestState
from graphrag.state import from_json as ingest_state_from_json
from graphrag.state import to_json as ingest_state_to_json
from graphrag.store.base import GraphStore
from graphrag.store.community_base import CommunityStore
from graphrag.store.parentchild_base import ParentChildStore
from graphrag.store.vector_base import VectorStore
from graphrag.synthesize import Synthesizer

# The ingest manifest (doc id -> content hash) lives at the corpus prefix root in S3; a --delta
# diffs the new snapshot against it, and every run writes it back **last** (slice 5; AC8).
MANIFEST_FILENAME = "manifest.json"

# The schema-guided extraction trace artifact (the replayable per-triple provenance) is written
# at the corpus prefix root under a CONSTANT filename — a server-side-derived key (CORPUS_PREFIX +
# this constant), never from a doc path / span / model-supplied text, so a poisoned doc cannot
# write outside the corpus prefix (CWE-23 — the write_manifest confinement pattern). AC5/AC7.
SCHEMA_EXTRACTION_TRACE_FILENAME = "schema_extraction_trace.txt"

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


def read_ingest_state(s3_client: S3Client, bucket: str, key: str) -> IngestState | None:
    """Read the stored `IngestState` from S3, or ``None`` when absent (the first staged --delta).

    The object at ``key`` is the same ``manifest.json`` the slice-5 path writes; a **v1** manifest
    envelope upgrades into a v2 `IngestState` transparently (`state.from_json`), so the staged delta
    is backward-compatible with a store that has only ever seen the v1 manifest (medallion AC4)."""
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # a missing state is expected on the first staged delta
        if _is_not_found(exc):
            return None
        raise
    body = resp["Body"].read()
    text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    return ingest_state_from_json(text)


def write_ingest_state(s3_client: S3Client, bucket: str, key: str, state: IngestState) -> None:
    """Persist the v2 `IngestState` to S3 — called **last**, after both stores are updated (AC8)."""
    body = ingest_state_to_json(state).encode("utf-8")
    s3_client.put_object(Bucket=bucket, Key=key, Body=body)


class S3ArtifactStore:
    """An `ArtifactStore` backed by S3 over the task's existing `S3Client` seam (medallion T4b).

    Silver artifact keys (`silver.silver_key`, e.g. ``silver/<fp>/<hash>/chunks.json``) are written
    under ``CORPUS_PREFIX`` in the corpus bucket, so a `destroy` of the auto-emptied bucket leaves
    zero residual (AC8). ``has`` probes with ``get_object`` (the `S3Client` seam has no
    ``head_object``); the corpus is small, so the extra read on a hit is acceptable."""

    def __init__(self, s3_client: S3Client, bucket: str, prefix: str = "") -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = prefix

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def has(self, key: str) -> bool:
        try:
            self._s3.get_object(Bucket=self._bucket, Key=self._full_key(key))
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    def load(self, key: str) -> str:
        resp = self._s3.get_object(Bucket=self._bucket, Key=self._full_key(key))
        body = resp["Body"].read()
        return body.decode("utf-8") if isinstance(body, bytes) else str(body)

    def write(self, key: str, body: str) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=self._full_key(key), Body=body.encode("utf-8"))


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
    parentchild_store: ParentChildStore | None = None,
) -> int:
    """Write the vector half from the same corpus read. Returns the chunk count.

    The same Fargate run reads one immutable S3 snapshot, so the graph and vector
    writes can't diverge (charter pattern 2). A no-op when neither an injected store
    (tests) nor ``OPENSEARCH_ENDPOINT`` (deploy) is present.

    The **parent-child** nested index (the Parent-Child Retriever slice) rides the *same*
    parse + embed pass: the chunks are embedded **once**, written to the flat index, then
    grouped into parents (a document's chunks, ordered) and written to the nested index — the
    child vectors are the chunk vectors, reused (no second embed pass, no extra Bedrock cost).
    The parent-child index is built only on this full-ingest path; it lands on the same
    OpenSearch domain. Skipped when neither an injected parent-child store nor
    ``OPENSEARCH_ENDPOINT`` is present (a flat-only / graph-only deploy is unchanged).
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
    if parentchild_store is None and endpoint:  # pragma: no cover - deployed task only
        from graphrag.store.parentchild_opensearch import OpenSearchParentChildStore

        parentchild_store = OpenSearchParentChildStore(endpoint, region)

    from graphrag.chunk import chunk_corpus
    from graphrag.labels import label_chunks, load_labels
    from graphrag.sources import load_corpus
    from graphrag.store.vector_base import EmbeddedChunk

    vector_store.create_index()  # no-op for in-memory; creates the k-NN index on OpenSearch
    docs = load_corpus(community, enhancements)
    chunks = chunk_corpus(docs)
    # Stamp synthetic visibility on every chunk from the same parse (slice 4) so the vector
    # store carries the permission-filter metadata, consistent with the graph's labels.
    label_chunks(chunks, load_labels())
    # Embed ONCE; both indexes consume the same EmbeddedChunk list (no re-embed).
    vectors = embedder.embed([c.text for c in chunks])
    embedded = [EmbeddedChunk(chunk, vector) for chunk, vector in zip(chunks, vectors, strict=True)]
    for ec in embedded:
        vector_store.index_chunk(ec)
    print(f"vector dual-write: indexed {len(embedded)} chunks")

    if parentchild_store is not None:
        from graphrag.parentchild import group_into_parents

        parentchild_store.create_index()  # no-op in-memory; creates the nested index on OpenSearch
        # The parent body is the document's full prose (app-stored, read back from the hit —
        # RFC-0001 §3, not a has_child join). Built from the SAME parsed docs this pass read.
        bodies = {d.doc_id: d.markdown.body for d in docs if d.markdown is not None}
        parents = group_into_parents(embedded, bodies)
        for parent in parents:
            parentchild_store.index_parent(parent)
        print(f"parent-child dual-write: indexed {len(parents)} parents")

    return len(embedded)


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


def _community_writeback(
    env: Mapping[str, str],
    store: GraphStore,
    community_store: CommunityStore | None,
    synthesizer: Synthesizer | None,
) -> int:
    """Detect + summarize communities and write them back to Neptune. Returns the count.

    The Global Community Summary slice (ADR-0005): community detection runs **here**, in the
    on-demand Fargate ingest task (Louvain via networkx, seeded) — not a standing Neptune
    Analytics service. It reads the just-written entity graph back from the ``GraphStore``,
    partitions it, summarizes each community via the ``Synthesizer`` seam, and writes
    ``Community`` nodes (+ a ``communityId`` stamp on each member ``Entity``) to the existing
    cluster. Recomputes from scratch (``clear`` first) on every call — communities are rebuilt
    on **full ingest / ``--rebuild`` only**; delta does not call this (a member visibility
    change therefore needs a full re-ingest to refresh community tiers — spec Never-do +
    ``global-community-summary-delta-tier-refresh``).

    The live trigger is **``NEPTUNE_ENDPOINT`` set** (mirroring how ``_vector_dual_write`` keys
    off ``OPENSEARCH_ENDPOINT``); tests inject ``community_store`` (+ a synthesizer). A no-op
    when neither is present.
    """
    endpoint = env.get("NEPTUNE_ENDPOINT")
    if community_store is None and not endpoint:
        return 0
    region = env.get("AWS_REGION", "us-east-1")
    if community_store is None:  # pragma: no cover - exercised only in the deployed task
        from graphrag.store.community_neptune import NeptuneCommunityStore

        community_store = NeptuneCommunityStore(endpoint or "", region)
    if synthesizer is None:  # pragma: no cover - exercised only in the deployed task
        from graphrag.synthesize import BedrockClaudeSynthesizer

        synthesizer = BedrockClaudeSynthesizer(region=region)

    from graphrag.community_detect import detect_communities, summarize_communities

    community_store.create()  # no-op in-memory; schema-less Neptune label
    nodes = store.all_nodes()
    edges = store.all_edges()
    specs = detect_communities(nodes, edges)
    communities = summarize_communities(specs, nodes, edges, synthesizer)
    # Recompute from scratch — full ingest / rebuild rebuilds the partition (delta never gets
    # here), so stale communities from a prior run never linger.
    community_store.clear()
    for community in communities:
        community_store.upsert_community(community)
        for entity_id in community.entity_ids:
            community_store.set_community_id(entity_id, community.id)
    print(f"community write-back: {len(communities)} communities (Louvain, in-task)")
    return len(communities)


def _flag_on(env: Mapping[str, str], name: str) -> bool:
    """Whether a default-off boolean env flag is set (``1``/``true``/``yes``/``on``)."""
    return env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _schema_extraction_writeback(
    env: Mapping[str, str],
    store: GraphStore,
    community: Path,
    enhancements: Path,
    *,
    s3_client: S3Client,
    bucket: str,
    prefix: str,
    extractor: TripleExtractor | None = None,
) -> int:
    """Schema-guided LLM extraction over the prose bodies, written back additively (AC5).

    The additive, **default-off** counterpart to ``_community_writeback`` (ADR-0006). Runs only
    when the ``SCHEMA_EXTRACTION`` flag is set — unlike community detection, which keys off
    ``NEPTUNE_ENDPOINT`` and runs unconditionally, this pass must be **no-op-by-default even on a
    deployed task** (the default-off contract). Called on ``MODE=full``/``rebuild`` only, **after**
    the deterministic graph write; ``MODE=delta`` never reaches here.

    Reads the just-written graph back from the store, extracts triples from the prose bodies via
    the injected ``extractor`` (offline ``RuleTripleExtractor`` in tests; live Bedrock deployed),
    validates + grounds them, upserts the accepted ``schema-guided-llm`` edges, and persists the
    per-triple ``ExtractionResult`` trace to the corpus bucket under a server-side key. Returns the
    number of edges written.

    **Additive resilience:** a raising extractor logs and leaves the deterministic graph intact — a
    failed LLM pass must never corrupt the deterministic graph (the pass is additive)."""
    if not _flag_on(env, "SCHEMA_EXTRACTION"):
        return 0

    region = env.get("AWS_REGION", "us-east-1")
    if extractor is None:  # pragma: no cover - exercised only in the deployed task
        from graphrag.extract_llm import BedrockTripleExtractor

        extractor = BedrockTripleExtractor(region=region)

    from graphrag.extract_llm import EXTRACTION_SCHEMA
    from graphrag.model import Graph
    from graphrag.resolve import load_aliases
    from graphrag.schema_extract import extract_schema_guided
    from graphrag.sources import load_corpus

    try:
        # Reconstruct an in-memory view of the just-written graph for grounding (membership +
        # kind checks). Read-only — the deterministic store is the source of truth.
        graph = Graph()
        for node in store.all_nodes():
            graph.upsert_node(node)
        for edge in store.all_edges():
            graph.upsert_edge(edge)

        docs = load_corpus(community, enhancements)
        result = extract_schema_guided(
            docs, graph, extractor=extractor, schema=EXTRACTION_SCHEMA, aliases=load_aliases()
        )
    except Exception:  # additive resilience: a failed LLM pass must not corrupt the graph
        logger.exception("schema-guided extraction failed; deterministic graph left intact")
        # Positively visible in the smoke output too (not inferred from the absence of the
        # success line below) so a failed live pass is debuggable from task stdout.
        print("schema-guided extraction: SKIPPED (extractor error; deterministic graph intact)")
        return 0

    for edge in result.edges:
        store.upsert_edge(edge)

    # Persist the replayable trace under a server-side-derived key (never doc/span/model text).
    trace_key = f"{prefix}{SCHEMA_EXTRACTION_TRACE_FILENAME}"
    s3_client.put_object(Bucket=bucket, Key=trace_key, Body=result.render().encode("utf-8"))
    print(
        f"schema-guided extraction: +{len(result.edges)} edges "
        f"({result.off_schema_count} off-schema-rejected; {result.dropped_count} "
        f"dropped-ungrounded); trace at s3://{bucket}/{trace_key}"
    )
    return len(result.edges)


def run(
    env: Mapping[str, str],
    *,
    s3_client: S3Client | None = None,
    store: GraphStore | None = None,
    vector_store: VectorStore | None = None,
    embedder: Embedder | None = None,
    parentchild_store: ParentChildStore | None = None,
    community_store: CommunityStore | None = None,
    synthesizer: Synthesizer | None = None,
    extractor: TripleExtractor | None = None,
    artifacts: ArtifactStore | None = None,
) -> IngestReport | DeltaReport:
    """Run the ingestion task. ``MODE`` selects ``full`` (default — the slice-1–4 dual-write,
    unchanged), ``delta`` (the medallion-staged incremental re-ingest through the Silver cache,
    against the stored `IngestState`), or ``rebuild`` (clear both stores + full ingest). Full and
    rebuild write the v1 ``manifest.json``; delta reads/writes the v2 `IngestState` at the same key
    (a v1 manifest upgrades in) — written **last**, after both stores are updated (AC8)."""
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
            _vector_dual_write(
                env, community, enhancements, vector_store, embedder, parentchild_store
            )
            # Detect + summarize communities from the just-written graph (ADR-0005), in-task.
            _community_writeback(env, store, community_store, synthesizer)
            # Schema-guided LLM extraction (ADR-0006) — additive, default-off; after the graph
            # write so it grounds against the resolved entities. No-op unless SCHEMA_EXTRACTION.
            _schema_extraction_writeback(
                env, store, community, enhancements,
                s3_client=s3_client, bucket=bucket, prefix=prefix, extractor=extractor,
            )
            new_manifest = build_manifest(community, enhancements)
        elif mode == "rebuild":
            vstore, emb = _resolve_vector(env, vector_store, embedder)
            report = rebuild(community, enhancements, store, vstore, emb)
            # Rebuild recomputes communities too (a fresh partition over the rebuilt graph).
            _community_writeback(env, store, community_store, synthesizer)
            _schema_extraction_writeback(
                env, store, community, enhancements,
                s3_client=s3_client, bucket=bucket, prefix=prefix, extractor=extractor,
            )
            new_manifest = report.new_manifest
        elif mode == "delta":
            vstore, emb = _resolve_vector(env, vector_store, embedder)
            art_store = artifacts or S3ArtifactStore(s3_client, bucket, prefix)
            prev_state = read_ingest_state(s3_client, bucket, manifest_key)
            if prev_state is None:
                # Loud, not silent: an operator expecting an incremental delta should see that the
                # baseline was missing and the run fell back to a full staged ingest (warms Silver).
                logger.warning(
                    "MODE=delta but no state at s3://%s/%s — falling back to a FULL staged ingest",
                    bucket,
                    manifest_key,
                )
            # Delta passes no extractor: schema-guided extraction is full/rebuild-only (ADR-0006).
            report, new_state = ingest_staged(
                prev_state, community, enhancements, store, vstore,
                artifacts=art_store, embedder=emb,
            )
            print(report.render())
            # Written last, only after both stores are updated: a crash leaves the old state, so the
            # next --delta re-attempts the same delta (at-least-once, idempotent).
            write_ingest_state(s3_client, bucket, manifest_key, new_state)
            return report
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
