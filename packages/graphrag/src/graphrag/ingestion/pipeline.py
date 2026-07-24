"""graphrag.ingestion.pipeline — per-document Bronze → Silver → Gold pipeline.

Public API
----------
process_document(file_bytes, path, sha, doc_uri, *, git_repo, s3_client, bucket)
    → ProcessResult

The caller (MedallionOrchestrator from spec-git-ingestion) drives Neptune INSERT,
OpenSearch upsert, and S3 artifact delivery based on ProcessResult.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from graphrag.ingestion._cleansing import CleansingPipeline
from graphrag.ingestion._embed import ChunkEmbedder
from graphrag.ingestion._extraction import FormatRouter
from graphrag.ingestion._rdf import RDFEmitter
from graphrag.ingestion._types import CleansingReport, ProcessResult

__all__ = ["process_document"]

_QUARANTINE_GRAPH = "urn:graph:quarantine"
_BIZ = "https://graphrag-aws.demo/biz-ops/ontology#"


def _s3_key(prefix: str, doc_uri: str, sha: str, ext: str) -> str:
    """Build an S3 key with URL-encoded doc_uri as the path component."""
    encoded = urllib.parse.quote(doc_uri, safe="")
    return f"{prefix}/{encoded}/{sha}{ext}"


def _quarantine_insert(doc_uri: str, sha: str, reason: str) -> str:
    """Emit a minimal SPARQL INSERT for the quarantine graph.

    The caller (MedallionOrchestrator) executes this INSERT against Neptune.
    Returned as ``ProcessResult.quarantine_insert`` for quarantined outcomes.
    """
    escaped_reason = reason.replace('"', '\\"')
    return (
        "INSERT DATA { GRAPH <"
        + _QUARANTINE_GRAPH
        + "> { "
        + "<"
        + doc_uri
        + "> <"
        + _BIZ
        + 'quarantineReason> "'
        + escaped_reason
        + '" . '
        + "<"
        + doc_uri
        + "> <"
        + _BIZ
        + 'gitCommitSHA> "'
        + sha
        + '" . '
        + "} }"
    )


def _write_report_if_s3(
    s3_client: Any | None,
    bucket: str,
    doc_uri: str,
    sha: str,
    report: CleansingReport,
) -> None:
    """Write the JSON cleansing report to S3 when a client is provided.

    Called for every processed document regardless of outcome (AC12).
    """
    if s3_client is not None and bucket:
        key = _s3_key("silver", doc_uri, sha, ".report.json")
        body = CleansingPipeline.report_to_json(report).encode()
        _s3_put(s3_client, bucket, key, body, "application/json")


def process_document(
    file_bytes: bytes,
    path: str,
    sha: str,
    doc_uri: str,
    *,
    git_repo: str = "unknown",
    s3_client: Any | None = None,
    bucket: str = "",
    format_router: FormatRouter | None = None,
    rdf_emitter: RDFEmitter | None = None,
    chunk_embedder: ChunkEmbedder | None = None,
) -> ProcessResult:
    """Transform raw file bytes (Bronze) into Silver + Gold artifacts.

    The caller is responsible for:
    - Neptune INSERT of the returned turtle into the returned named_graph.
    - Neptune INSERT of quarantine_insert (when outcome=="quarantined") — carries
      biz:quarantineReason and biz:gitCommitSHA in urn:graph:quarantine.
    - OpenSearch upsert of each chunk vector.
    - S3 upload of the Silver Markdown, Gold Turtle, and vectors.
      (Pass ``s3_client`` and ``bucket`` to enable in-pipeline S3 writes.)
    - The JSON cleansing report (silver/<doc_uri>/<sha>.report.json) is always
      written in-pipeline when s3_client is provided, regardless of outcome (AC12).

    Args:
        file_bytes: Raw Bronze document bytes.
        path: Repository-relative file path (e.g. ``"policies/hr.md"``).
        sha: 40-char git commit SHA.
        doc_uri: Stable document URI (e.g. ``"urn:doc:my-repo:policies/hr.md"``).
        git_repo: Git repository identifier recorded in provenance triples.
        s3_client: Optional boto3 S3 client.  If ``None``, S3 writes are skipped
            and ``silver_artifact_uri`` / ``gold_artifact_uri`` are returned as
            the would-be S3 URIs for the caller to use.
        bucket: S3 bucket name for artifact writes.
        format_router: Optional injected FormatRouter (for testing).
        rdf_emitter: Optional injected RDFEmitter (for testing).
        chunk_embedder: Optional injected ChunkEmbedder (for testing).

    Returns:
        ProcessResult with outcome="loaded" on success or outcome="quarantined"
        on Silver/SHACL gate failure.  ``quarantine_insert`` is populated on
        every quarantined outcome so the caller can execute the Neptune INSERT.
    """
    router = format_router or FormatRouter()
    emitter = rdf_emitter or RDFEmitter()
    embedder = chunk_embedder or ChunkEmbedder()

    # -- T1/T2: Extract -> Cleanse ---------------------------------------------
    try:
        markdown, ext_key = router.route(file_bytes, path)
    except Exception as exc:  # noqa: BLE001
        report = CleansingReport(
            doc_uri=doc_uri,
            sha=sha,
            extractor="unknown",
            char_count_raw=len(file_bytes),
            char_count_clean=0,
            gates_failed=["extraction_error"],
            quarantined=True,
        )
        _write_report_if_s3(s3_client, bucket, doc_uri, sha, report)
        qreason = f"extraction error: {exc}"
        return ProcessResult(
            doc_uri=doc_uri,
            sha=sha,
            outcome="error",
            quarantine_reason=qreason,
            quarantine_insert=_quarantine_insert(doc_uri, sha, qreason),
            cleansing_report=report,
        )

    pipeline = CleansingPipeline(extractor=ext_key)
    clean_text, report = pipeline.run(markdown, doc_uri=doc_uri, sha=sha)

    # Write the cleansing report for every outcome, including quarantined (AC12).
    _write_report_if_s3(s3_client, bucket, doc_uri, sha, report)

    if report.quarantined:
        gate_reason = report.gates_failed[0] if report.gates_failed else "unknown"
        qreason = f"Silver gate failed: {gate_reason}"
        return ProcessResult(
            doc_uri=doc_uri,
            sha=sha,
            outcome="quarantined",
            quarantine_reason=qreason,
            quarantine_insert=_quarantine_insert(doc_uri, sha, qreason),
            cleansing_report=report,
        )

    # -- T3: Emit RDF + SHACL gate --------------------------------------------
    emit_result = emitter.emit(
        doc_uri=doc_uri,
        path=path,
        sha=sha,
        git_repo=git_repo,
        extractor=ext_key,
        clean_text=clean_text,
        pii_flagged=report.pii_flagged,
    )

    if not emit_result.conforms:
        qreason = emit_result.quarantine_reason or "SHACL validation failed"
        return ProcessResult(
            doc_uri=doc_uri,
            sha=sha,
            outcome="quarantined",
            quarantine_reason=qreason,
            quarantine_insert=_quarantine_insert(doc_uri, sha, qreason),
            cleansing_report=report,
            named_graph=_QUARANTINE_GRAPH,
        )

    # -- T4: Chunk + Embed ----------------------------------------------------
    try:
        chunks = embedder.embed(clean_text, doc_uri=doc_uri)
    except RuntimeError as exc:
        if "embedding_throttle" in str(exc):
            qreason = "embedding_throttle"
            return ProcessResult(
                doc_uri=doc_uri,
                sha=sha,
                outcome="quarantined",
                quarantine_reason=qreason,
                quarantine_insert=_quarantine_insert(doc_uri, sha, qreason),
                cleansing_report=report,
            )
        raise

    vectors_payload = json.dumps(
        {
            "doc_uri": doc_uri,
            "sha": sha,
            "chunks": [
                {
                    "text": c.text,
                    "embedding": c.embedding,
                    "chunk_index": c.chunk_index,
                    "doc_uri": c.doc_uri,
                }
                for c in chunks
            ],
        },
        indent=2,
    )

    # -- Build S3 URIs --------------------------------------------------------
    silver_key = _s3_key("silver", doc_uri, sha, ".md")
    gold_key = _s3_key("gold", doc_uri, sha, ".ttl")
    vectors_key = _s3_key("gold", doc_uri, sha, ".vectors.json")

    silver_uri = f"s3://{bucket}/{silver_key}" if bucket else silver_key
    gold_uri = f"s3://{bucket}/{gold_key}" if bucket else gold_key
    vectors_uri = f"s3://{bucket}/{vectors_key}" if bucket else vectors_key

    # Optionally write remaining artifacts to S3 (cleansing report already written above).
    if s3_client is not None and bucket:
        _s3_put(s3_client, bucket, silver_key, clean_text.encode(), "text/markdown")
        _s3_put(s3_client, bucket, gold_key, emit_result.turtle.encode(), "text/turtle")
        _s3_put(s3_client, bucket, vectors_key, vectors_payload.encode(), "application/json")

    return ProcessResult(
        doc_uri=doc_uri,
        sha=sha,
        outcome="loaded",
        silver_artifact_uri=silver_uri,
        gold_artifact_uri=gold_uri,
        vectors_artifact_uri=vectors_uri,
        cleansing_report=report,
        turtle=emit_result.turtle,
        named_graph=emit_result.named_graph,
    )


def _s3_put(
    client: Any,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
