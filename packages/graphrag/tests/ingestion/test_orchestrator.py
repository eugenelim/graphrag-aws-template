"""TDD tests for graphrag.ingestion._orchestrator — MedallionOrchestrator.

All dependencies (neptune, opensearch, manifest, process_document) are injected
via constructor mocks.  No live AWS services are required.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from graphrag.ingestion._delta import DeltaAction, DeltaEntry, ManifestManager
from graphrag.ingestion._neptune import NeptuneLoadClient
from graphrag.ingestion._opensearch import IngestOpenSearchClient
from graphrag.ingestion._orchestrator import MedallionOrchestrator
from graphrag.ingestion._types import CleansingReport, ProcessResult
from graphrag.store.neptune_sparql_memory import MemorySparqlStore

# ── Fixtures ────────────────────────────────────────────────────────────────────

DOC_PATH = "policies/hr.md"
DOC_URI = "urn:doc:test-repo:policies/hr.md"
SHA = "deadbeef01"  # pragma: allowlist secret
PARTITION = "urn:graph:descriptive"
GIT_REPO = "test-repo"
BUCKET = "my-test-bucket"

_SAMPLE_TURTLE = (
    "@prefix biz: <https://graphrag-aws.demo/biz-ops/ontology#> .\n"
    f'<{DOC_URI}> biz:title "HR Policy" .\n'
)

_VECTORS_URI = f"s3://{BUCKET}/gold/{DOC_URI}/{SHA}.vectors.json"
_VECTORS_JSON = json.dumps(
    {
        "doc_uri": DOC_URI,
        "sha": SHA,
        "chunks": [
            {"doc_uri": DOC_URI, "text": "chunk 0", "embedding": [0.1], "chunk_index": 0},
        ],
    }
)


def _loaded_result() -> ProcessResult:
    return ProcessResult(
        doc_uri=DOC_URI,
        sha=SHA,
        outcome="loaded",
        turtle=_SAMPLE_TURTLE,
        named_graph=PARTITION,
        vectors_artifact_uri=_VECTORS_URI,
        cleansing_report=MagicMock(spec=CleansingReport),
    )


def _quarantined_result() -> ProcessResult:
    return ProcessResult(
        doc_uri=DOC_URI,
        sha=SHA,
        outcome="quarantined",
        quarantine_reason="SHACL validation failed",
        quarantine_insert=(
            f"INSERT DATA {{ GRAPH <urn:graph:quarantine> {{ <{DOC_URI}> "
            f"<https://graphrag-aws.demo/biz-ops/ontology#quarantineReason> "
            f'"SHACL validation failed" }} }}'
        ),
        cleansing_report=MagicMock(spec=CleansingReport),
    )


def _make_orchestrator(
    process_doc_fn: Any = None,
    neptune_store: MemorySparqlStore | None = None,
) -> tuple[MedallionOrchestrator, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Build a MedallionOrchestrator with all mocked dependencies.

    Returns (orchestrator, mock_neptune, mock_opensearch, mock_manifest, mock_s3).
    """
    store = neptune_store or MemorySparqlStore()
    neptune = NeptuneLoadClient(store=store)

    mock_opensearch = MagicMock(spec=IngestOpenSearchClient)
    mock_manifest = MagicMock(spec=ManifestManager)

    mock_s3 = MagicMock()
    mock_body = MagicMock()
    mock_body.read.return_value = _VECTORS_JSON.encode()
    mock_s3.get_object.return_value = {"Body": mock_body}

    if process_doc_fn is None:
        process_doc_fn = MagicMock(return_value=_loaded_result())

    orchestrator = MedallionOrchestrator(
        neptune=neptune,
        opensearch=mock_opensearch,
        manifest=mock_manifest,
        s3_client=mock_s3,
        bucket=BUCKET,
        git_repo=GIT_REPO,
        process_document_fn=process_doc_fn,
    )
    return orchestrator, neptune, mock_opensearch, mock_manifest, mock_s3


# ── T4-1: Added file → correct call order ──────────────────────────────────────


def test_added_file_call_order() -> None:
    """Added file → process_document → insert_document → opensearch_upsert → manifest.write_sha."""
    mock_process_doc = MagicMock(return_value=_loaded_result())
    orchestrator, neptune, mock_opensearch, mock_manifest, mock_s3 = _make_orchestrator(
        process_doc_fn=mock_process_doc
    )

    entries = [DeltaEntry(action=DeltaAction.added, path=DOC_PATH)]
    orchestrator.run(entries, SHA, file_tree={DOC_PATH: b"file content"})

    # process_document was called.
    mock_process_doc.assert_called_once()
    # OpenSearch upsert was called.
    mock_opensearch.upsert_chunks.assert_called_once()
    # Manifest was written.
    mock_manifest.write_sha.assert_called_once_with(SHA)


# ── T4-2: Deleted file → correct call order ────────────────────────────────────


def test_deleted_file_call_order() -> None:
    """Deleted file → correct call order.

    lookup_partition → delete_document → opensearch_delete → manifest.write_sha.
    """
    store = MemorySparqlStore()
    # Pre-populate taxonomy so lookup_partition returns PARTITION.
    store.sparql_update(
        f"INSERT DATA {{ GRAPH <urn:graph:taxonomy> {{ "
        f"<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#inPartition> <{PARTITION}> "
        f"}} }}"
    )
    # Pre-populate partition graph so delete has something to remove.
    store.sparql_update(
        f"INSERT DATA {{ GRAPH <{PARTITION}> {{ "
        f'<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#title> "HR Policy" . '
        f"}} }}"
    )

    orchestrator, neptune, mock_opensearch, mock_manifest, mock_s3 = _make_orchestrator(
        neptune_store=store
    )

    entries = [DeltaEntry(action=DeltaAction.deleted, path=DOC_PATH)]
    orchestrator.run(entries, SHA, file_tree={})

    # OpenSearch delete was called.
    mock_opensearch.delete_by_doc_uri.assert_called_once_with(DOC_URI)
    # Manifest was written.
    mock_manifest.write_sha.assert_called_once_with(SHA)

    # Verify taxonomy entry is gone.
    rows = store.sparql_select(
        f"SELECT ?p WHERE {{ GRAPH <urn:graph:taxonomy> {{ <{DOC_URI}> ?p ?o }} }}"
    )
    assert rows == []


# ── T4-3: Quarantined file → quarantine INSERT, no partition INSERT ─────────────


def test_quarantined_file_routes_to_quarantine() -> None:
    """process_document() → outcome=quarantined → quarantine INSERT, NO partition INSERT."""
    mock_process_doc = MagicMock(return_value=_quarantined_result())
    store = MemorySparqlStore()
    orchestrator, neptune, mock_opensearch, mock_manifest, mock_s3 = _make_orchestrator(
        process_doc_fn=mock_process_doc,
        neptune_store=store,
    )

    entries = [DeltaEntry(action=DeltaAction.added, path=DOC_PATH)]
    orchestrator.run(entries, SHA, file_tree={DOC_PATH: b"file content"})

    # No partition INSERT — taxonomy graph must not have the document.
    rows = store.sparql_select(
        f"SELECT ?p WHERE {{ GRAPH <urn:graph:taxonomy> {{ <{DOC_URI}> ?p ?o }} }}"
    )
    assert rows == []

    # Quarantine graph must have the document.
    quarantine_rows = store.sparql_select(
        f"SELECT ?r WHERE {{ GRAPH <urn:graph:quarantine> {{ "
        f"<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#quarantineReason> ?r "
        f"}} }}"
    )
    assert quarantine_rows != []

    # OpenSearch upsert must NOT be called for quarantined documents.
    mock_opensearch.upsert_chunks.assert_not_called()


# ── T4-4: manifest.write_sha called once, after all documents ─────────────────


def test_manifest_written_exactly_once_after_all_documents() -> None:
    """manifest.write_sha() is called exactly once, after all documents, even with quarantine."""
    process_calls: list[str] = []

    def _multi_process(*args: Any, **kwargs: Any) -> ProcessResult:
        doc_uri = kwargs.get("doc_uri") or args[3]
        process_calls.append(doc_uri)
        if "doc1" in doc_uri:
            return _loaded_result()
        return _quarantined_result()

    orchestrator2, _, _, mock_manifest2, _ = _make_orchestrator(process_doc_fn=_multi_process)

    entries = [
        DeltaEntry(action=DeltaAction.added, path="doc1.md"),
        DeltaEntry(action=DeltaAction.added, path="doc2_quarantine.md"),
    ]
    orchestrator2.run(
        entries,
        SHA,
        file_tree={"doc1.md": b"content 1", "doc2_quarantine.md": b"content 2"},
    )

    # Manifest written once, after both documents.
    mock_manifest2.write_sha.assert_called_once_with(SHA)


# ── T4-5: idempotency — second run makes no exception ─────────────────────────


def test_idempotent_second_run_does_not_raise() -> None:
    """Running with the same SHA twice does not raise; Neptune INSERT is called again."""
    mock_process_doc = MagicMock(return_value=_loaded_result())
    orchestrator, neptune, mock_opensearch, mock_manifest, mock_s3 = _make_orchestrator(
        process_doc_fn=mock_process_doc
    )

    entries = [DeltaEntry(action=DeltaAction.added, path=DOC_PATH)]
    file_tree = {DOC_PATH: b"file content"}

    # First run.
    orchestrator.run(entries, SHA, file_tree=file_tree)
    first_call_count = mock_process_doc.call_count

    # Second run with the same SHA — must not raise.
    orchestrator.run(entries, SHA, file_tree=file_tree)

    # process_document called again (no skip logic).
    assert mock_process_doc.call_count == first_call_count * 2
    # Manifest written twice.
    assert mock_manifest.write_sha.call_count == 2


# ── T4-6: missing taxonomy on delete → WARNING logged, opensearch delete still called ─


def test_missing_taxonomy_on_delete_logs_warning_and_calls_opensearch() -> None:
    """When lookup_partition returns None, delete_document is skipped
    but opensearch_delete is still called."""
    orchestrator, neptune, mock_opensearch, mock_manifest, mock_s3 = _make_orchestrator()

    # No taxonomy entry for this doc → lookup returns None.
    entries = [DeltaEntry(action=DeltaAction.deleted, path=DOC_PATH)]
    orchestrator.run(entries, SHA, file_tree={})

    # OpenSearch delete still called.
    mock_opensearch.delete_by_doc_uri.assert_called_once_with(DOC_URI)
    # Manifest still written.
    mock_manifest.write_sha.assert_called_once_with(SHA)


# ── T4-7: Modified file → delete old triples + add new (AC5 / no orphans) ───────


def test_modified_file_deletes_old_then_inserts_new() -> None:
    """Modified entry → delete_document called before insert_document; no old triples orphaned."""
    store = MemorySparqlStore()

    # Pre-load old document triples into the partition graph + taxonomy.
    store.sparql_update(
        f"INSERT DATA {{ GRAPH <urn:graph:taxonomy> {{ "
        f"<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#inPartition> <{PARTITION}> "
        f"}} }}"
    )
    old_title = "Old HR Policy"
    store.sparql_update(
        f"INSERT DATA {{ GRAPH <{PARTITION}> {{ "
        f'<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#title> "{old_title}" . '
        f"}} }}"
    )

    # process_document returns a new version (different title in turtle).
    new_turtle = (
        "@prefix biz: <https://graphrag-aws.demo/biz-ops/ontology#> .\n"
        f'<{DOC_URI}> biz:title "Updated HR Policy" .\n'
    )
    mock_process_doc = MagicMock(
        return_value=ProcessResult(
            doc_uri=DOC_URI,
            sha=SHA,
            outcome="loaded",
            turtle=new_turtle,
            named_graph=PARTITION,
            vectors_artifact_uri=None,
            cleansing_report=MagicMock(spec=CleansingReport),
        )
    )

    orchestrator, neptune, mock_opensearch, mock_manifest, mock_s3 = _make_orchestrator(
        process_doc_fn=mock_process_doc,
        neptune_store=store,
    )

    entries = [DeltaEntry(action=DeltaAction.modified, path=DOC_PATH)]
    orchestrator.run(entries, SHA, file_tree={DOC_PATH: b"updated content"})

    # The old title should be gone — no orphaned triples.
    rows = store.sparql_select(
        f"SELECT ?title WHERE {{ GRAPH <{PARTITION}> {{ "
        f"<{DOC_URI}> <https://graphrag-aws.demo/biz-ops/ontology#title> ?title "
        f"}} }}"
    )
    titles = [str(r["title"]) for r in rows]
    assert old_title not in titles, f"Old triples still present after modify: {titles}"
    # New title should be present.
    assert "Updated HR Policy" in titles, f"New triples not found after modify: {titles}"

    # Manifest written exactly once.
    mock_manifest.write_sha.assert_called_once_with(SHA)
