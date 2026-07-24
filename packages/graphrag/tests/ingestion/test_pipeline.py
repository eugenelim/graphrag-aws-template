"""TDD tests for graphrag.ingestion.pipeline.process_document()."""

from __future__ import annotations

from unittest.mock import MagicMock

from rdflib import Namespace

from graphrag.ingestion._embed import Chunk, ChunkEmbedder
from graphrag.ingestion._extraction import Extractor, FormatRouter
from graphrag.ingestion.pipeline import process_document

BIZ = Namespace("https://graphrag-aws.demo/biz-ops/ontology#")

DOC_URI = "urn:doc:test-repo:sops/ir.md"
SHA = "beefdead"  # pragma: allowlist secret
GIT_REPO = "test-org/test-repo"

# A document that passes all Silver gates and SHACL validation (SOP — fewer required fields)
_VALID_MARKDOWN = (
    "# Incident Response Procedure\n\n"
    "This document describes how to respond to security incidents. "
    "All responders must follow these steps carefully to minimize production impact. "
    "Escalate to management if the incident cannot be resolved within one hour. "
    "Document every action taken throughout the response process."
)

# A document that is too short to pass the min_content gate
_TOO_SHORT = "Hi."

_MOCK_EMBEDDING = [0.1] * 5


def _make_extractor_mock(markdown: str = _VALID_MARKDOWN) -> MagicMock:
    m = MagicMock(spec=Extractor)
    m.extract.return_value = markdown
    return m


def _make_router(markdown: str = _VALID_MARKDOWN) -> FormatRouter:
    return FormatRouter(
        extractors={
            "passthrough": _make_extractor_mock(markdown),
            "pandoc": _make_extractor_mock(markdown),
            "docling": _make_extractor_mock(markdown),
            "markitdown": _make_extractor_mock(markdown),
        }
    )


def _make_embedder(chunks: list[Chunk] | None = None) -> ChunkEmbedder:
    if chunks is None:
        chunks = [
            Chunk(text="chunk text", embedding=_MOCK_EMBEDDING, chunk_index=0, doc_uri=DOC_URI)
        ]
    embedder = MagicMock(spec=ChunkEmbedder)
    embedder.embed.return_value = chunks
    return embedder


# ---------------------------------------------------------------------------
# T5-1: Happy path → outcome="loaded"
# ---------------------------------------------------------------------------


def test_process_document_happy_path() -> None:
    router = _make_router()
    embedder = _make_embedder()

    result = process_document(
        file_bytes=b"content",
        path="sops/ir.md",
        sha=SHA,
        doc_uri=DOC_URI,
        git_repo=GIT_REPO,
        format_router=router,
        chunk_embedder=embedder,
    )

    assert result.outcome == "loaded"
    assert result.silver_artifact_uri is not None
    assert result.gold_artifact_uri is not None
    assert result.vectors_artifact_uri is not None
    assert result.cleansing_report is not None
    assert result.cleansing_report.quarantined is False
    assert result.turtle is not None and len(result.turtle) > 0
    assert result.named_graph == "urn:graph:descriptive"


# ---------------------------------------------------------------------------
# T5-2: Silver gate fail → outcome="quarantined"
# ---------------------------------------------------------------------------


def test_process_document_silver_gate_quarantine() -> None:
    router = _make_router(_TOO_SHORT)

    result = process_document(
        file_bytes=b"too short",
        path="sops/ir.md",
        sha=SHA,
        doc_uri=DOC_URI,
        git_repo=GIT_REPO,
        format_router=router,
    )

    assert result.outcome == "quarantined"
    assert result.quarantine_reason is not None
    assert "Silver gate" in result.quarantine_reason
    assert result.gold_artifact_uri is None
    assert result.cleansing_report is not None


# ---------------------------------------------------------------------------
# T5-3: SHACL gate fail → outcome="quarantined" with SHACL reason
# ---------------------------------------------------------------------------


def test_process_document_shacl_quarantine() -> None:
    # Policy path without front-matter → missing effectiveDate/scope → SHACL fail
    policy_text = (
        "# HR Policy\n\n"
        "This policy describes acceptable use of company computing resources. "
        "All employees must comply with this policy document in full. "
        "Violations will be addressed through the disciplinary process."
    )
    router = _make_router(policy_text)

    result = process_document(
        file_bytes=b"policy content",
        path="policies/hr.md",
        sha=SHA,
        doc_uri="urn:doc:test-repo:policies/hr.md",
        git_repo=GIT_REPO,
        format_router=router,
    )

    assert result.outcome == "quarantined"
    assert result.quarantine_reason is not None
    assert "SHACL" in result.quarantine_reason
    assert result.gold_artifact_uri is None


# ---------------------------------------------------------------------------
# T5-4: cleansing_report present in all outcomes
# ---------------------------------------------------------------------------


def test_cleansing_report_present_on_happy_path() -> None:
    router = _make_router()
    embedder = _make_embedder()
    result = process_document(
        file_bytes=b"content",
        path="sops/ir.md",
        sha=SHA,
        doc_uri=DOC_URI,
        git_repo=GIT_REPO,
        format_router=router,
        chunk_embedder=embedder,
    )
    assert result.cleansing_report is not None
    assert result.cleansing_report.doc_uri == DOC_URI
    assert result.cleansing_report.sha == SHA


def test_cleansing_report_present_on_quarantine() -> None:
    router = _make_router(_TOO_SHORT)
    result = process_document(
        file_bytes=b"too short",
        path="sops/ir.md",
        sha=SHA,
        doc_uri=DOC_URI,
        git_repo=GIT_REPO,
        format_router=router,
    )
    assert result.outcome == "quarantined"
    assert result.cleansing_report is not None
    assert result.cleansing_report.quarantined is True


# ---------------------------------------------------------------------------
# T5-5: Embedding throttle quarantine
# ---------------------------------------------------------------------------


def test_embedding_throttle_quarantine() -> None:
    router = _make_router()
    embedder = MagicMock(spec=ChunkEmbedder)
    embedder.embed.side_effect = RuntimeError("embedding_throttle")

    result = process_document(
        file_bytes=b"content",
        path="sops/ir.md",
        sha=SHA,
        doc_uri=DOC_URI,
        git_repo=GIT_REPO,
        format_router=router,
        chunk_embedder=embedder,
    )

    assert result.outcome == "quarantined"
    assert result.quarantine_reason == "embedding_throttle"


# ---------------------------------------------------------------------------
# T5-6: quarantine_insert carries biz:quarantineReason (AC7)
# ---------------------------------------------------------------------------


def test_shacl_quarantine_insert_contains_reason_and_sha() -> None:
    """The quarantine_insert payload carries biz:quarantineReason and biz:gitCommitSHA."""
    policy_text = (
        "# HR Policy\n\n"
        "This policy describes acceptable use of company computing resources. "
        "All employees must comply with this policy document in full. "
        "Violations will be addressed through the disciplinary process."
    )
    router = _make_router(policy_text)

    result = process_document(
        file_bytes=b"policy content",
        path="policies/hr.md",
        sha=SHA,
        doc_uri="urn:doc:test-repo:policies/hr.md",
        git_repo=GIT_REPO,
        format_router=router,
    )

    assert result.outcome == "quarantined"
    assert result.quarantine_insert is not None
    assert "quarantineReason" in result.quarantine_insert
    # The SHACL violation path should reference the failing constraint
    assert "SHACL" in (result.quarantine_reason or "")
    assert SHA in result.quarantine_insert
    assert "urn:graph:quarantine" in result.quarantine_insert


# ---------------------------------------------------------------------------
# T5-7: S3 writes — cleansing report for both loaded and quarantined (AC12)
# ---------------------------------------------------------------------------


def test_s3_report_written_for_quarantined_outcome() -> None:
    """The cleansing report is written to S3 even when the document is quarantined (AC12)."""
    router = _make_router(_TOO_SHORT)
    mock_s3 = MagicMock()

    process_document(
        file_bytes=b"too short",
        path="sops/ir.md",
        sha=SHA,
        doc_uri=DOC_URI,
        git_repo=GIT_REPO,
        s3_client=mock_s3,
        bucket="my-bucket",
        format_router=router,
    )

    # At least one put_object call should be for the .report.json key
    calls = mock_s3.put_object.call_args_list
    keys = [c.kwargs.get("Key", "") for c in calls]
    assert any(".report.json" in k for k in keys), f"No report.json put_object found. Keys: {keys}"


def test_s3_report_written_for_loaded_outcome() -> None:
    """All four S3 artifacts (silver .md, report .json, gold .ttl, vectors .json) are written
    for a loaded outcome (AC12 + happy path)."""
    router = _make_router()
    embedder = _make_embedder()
    mock_s3 = MagicMock()

    process_document(
        file_bytes=b"content",
        path="sops/ir.md",
        sha=SHA,
        doc_uri=DOC_URI,
        git_repo=GIT_REPO,
        s3_client=mock_s3,
        bucket="my-bucket",
        format_router=router,
        chunk_embedder=embedder,
    )

    calls = mock_s3.put_object.call_args_list
    assert mock_s3.put_object.call_count == 4  # silver.md, report.json, gold.ttl, vectors.json
    keys = [c.kwargs.get("Key", "") for c in calls]
    assert any(".report.json" in k for k in keys)
    assert any(".md" in k and "silver" in k for k in keys)
    assert any(".ttl" in k and "gold" in k for k in keys)
    assert any(".vectors.json" in k for k in keys)
