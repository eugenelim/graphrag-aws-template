"""graphrag.ingestion._orchestrator — MedallionOrchestrator.

Drives the Bronze → Silver → Gold ingestion loop for a list of
:class:`~graphrag.ingestion._delta.DeltaEntry` items produced by
:class:`~graphrag.ingestion._delta.GitDeltaReader`.

Processing contract (per spec AC3–AC10):
- **Added file**: ``process_document()`` → Neptune INSERT (loaded) or quarantine INSERT
  (quarantined) → taxonomy INSERT (loaded only) → OpenSearch upsert (loaded only).
- **Modified file**: treated as delete-old then add-new (AC5).
- **Deleted file**: taxonomy lookup → Neptune DELETE → OpenSearch delete.
- **Manifest**: written once, after ALL documents are processed (loaded or quarantined).
- **Errors**: logged at ERROR; run continues; manifest is still written.
- **Idempotency**: a second run with the same SHA re-issues Neptune INSERT (which is a
  no-op in Neptune SPARQL for existing triples) and re-calls OpenSearch upsert
  (idempotent). No pre-check skip logic.

Offline unit-test contract: inject mock callables for ``process_document``,
``neptune``, ``opensearch``, and ``manifest``; assert on call order and arguments.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from graphrag.ingestion._delta import DeltaAction, DeltaEntry, ManifestManager
from graphrag.ingestion._neptune import NeptuneLoadClient
from graphrag.ingestion._opensearch import IngestOpenSearchClient
from graphrag.ingestion._types import ProcessResult
from graphrag.ingestion.pipeline import process_document as _default_process_document

__all__ = ["MedallionOrchestrator"]

log = logging.getLogger(__name__)

# Type alias for the injected process_document callable.
ProcessDocFn = Callable[..., ProcessResult]


class MedallionOrchestrator:
    """Orchestrate the per-document medallion pipeline for a delta run.

    All dependencies are constructor-injected so the orchestrator is fully
    testable without live AWS services.

    Args:
        neptune:            Injected :class:`~graphrag.ingestion._neptune.NeptuneLoadClient`.
        opensearch:         Injected
                            :class:`~graphrag.ingestion._opensearch.IngestOpenSearchClient`.
        manifest:           Injected :class:`~graphrag.ingestion._delta.ManifestManager`.
        s3_client:          boto3 S3 client used to read Gold vectors artifacts.
        bucket:             S3 bucket that stores Silver + Gold artifacts.
        git_repo:           Git repository identifier recorded in PROV-O triples.
        process_document_fn: Override for ``process_document()`` (inject in tests).
    """

    def __init__(
        self,
        neptune: NeptuneLoadClient,
        opensearch: IngestOpenSearchClient,
        manifest: ManifestManager,
        s3_client: Any,
        bucket: str,
        git_repo: str,
        process_document_fn: ProcessDocFn | None = None,
    ) -> None:
        self._neptune = neptune
        self._opensearch = opensearch
        self._manifest = manifest
        self._s3 = s3_client
        self._bucket = bucket
        self._git_repo = git_repo
        self._process_doc = process_document_fn or _default_process_document

    # ── public entry point ───────────────────────────────────────────────────────

    def run(
        self,
        delta_entries: list[DeltaEntry],
        head_sha: str,
        file_tree: dict[str, bytes] | None = None,
    ) -> None:
        """Process all delta entries then write the manifest SHA.

        For each modified file, the old version is deleted first (via ``_handle_delete``)
        before the new version is processed (via ``_handle_add``).  The manifest is written
        exactly once at the end, regardless of individual document outcomes.

        Args:
            delta_entries: List of :class:`DeltaEntry` items from :class:`GitDeltaReader`.
            head_sha:      New HEAD commit SHA — written to the manifest after the run.
            file_tree:     Optional ``{path: bytes}`` fixture dict used in unit tests
                           instead of a live S3 file-tree lookup.
        """
        for entry in delta_entries:
            try:
                if entry.action == DeltaAction.added:
                    self._handle_add(entry.path, head_sha, file_tree)
                elif entry.action == DeltaAction.modified:
                    # Modified = delete old version + add new version (AC5).
                    self._handle_delete(entry.path)
                    self._handle_add(entry.path, head_sha, file_tree)
                elif entry.action == DeltaAction.deleted:
                    self._handle_delete(entry.path)
            except Exception:  # noqa: BLE001
                doc_uri = self._doc_uri(entry.path)
                log.error(
                    "document processing failed; run continues",
                    extra={"doc_uri": doc_uri, "path": entry.path, "action": entry.action},
                    exc_info=True,
                )

        # Manifest is written once, after ALL documents, regardless of failures (AC8).
        self._manifest.write_sha(head_sha)

    # ── private helpers ──────────────────────────────────────────────────────────

    def _doc_uri(self, path: str) -> str:
        """Derive the stable document URI from the repo and file path."""
        return f"urn:doc:{self._git_repo}:{path}"

    def _handle_add(
        self,
        path: str,
        sha: str,
        file_tree: dict[str, bytes] | None,
    ) -> None:
        doc_uri = self._doc_uri(path)
        file_bytes = self._read_file(path, file_tree)
        if file_bytes is None:
            log.error(
                "skipping add: file not found in tree",
                extra={"doc_uri": doc_uri, "path": path},
            )
            return

        result: ProcessResult = self._process_doc(
            file_bytes,
            path,
            sha,
            doc_uri,
            git_repo=self._git_repo,
            s3_client=self._s3,
            bucket=self._bucket,
        )

        if result.outcome == "loaded":
            # Neptune partition INSERT + taxonomy INSERT (inside insert_document).
            self._neptune.insert_document(
                doc_uri=doc_uri,
                partition_graph=result.named_graph or "urn:graph:descriptive",
                turtle=result.turtle or "",
            )
            # OpenSearch upsert from Gold vectors artifact.
            self._upsert_opensearch(result)
            log.info(
                "document loaded",
                extra={"doc_uri": doc_uri, "sha": sha, "outcome": "loaded"},
            )
        elif result.outcome in ("quarantined", "error"):
            # Always route through insert_quarantine_record (guarded SPARQL builder).
            # Never execute result.quarantine_insert directly — it is an unguarded
            # string from pipeline.py and would be a SPARQL injection surface.
            self._neptune.insert_quarantine_record(
                doc_uri=doc_uri,
                sha=sha,
                reason=result.quarantine_reason or "unknown",
            )
            log.warning(
                "document not loaded",
                extra={
                    "doc_uri": doc_uri,
                    "sha": sha,
                    "outcome": result.outcome,
                    "reason": result.quarantine_reason,
                },
            )

    def _handle_delete(self, path: str) -> None:
        doc_uri = self._doc_uri(path)
        partition = self._neptune.lookup_partition(doc_uri)
        if partition is None:
            log.warning(
                "delete skipped: no taxonomy entry for document",
                extra={"doc_uri": doc_uri},
            )
            # OpenSearch delete is still attempted even without a taxonomy entry.
            self._opensearch.delete_by_doc_uri(doc_uri)
            return

        self._neptune.delete_document(doc_uri=doc_uri, partition_graph=partition)
        self._opensearch.delete_by_doc_uri(doc_uri)
        log.info("document deleted", extra={"doc_uri": doc_uri, "partition": partition})

    def _upsert_opensearch(self, result: ProcessResult) -> None:
        """Read the vectors artifact from S3 and upsert chunks to OpenSearch."""
        vectors_uri = result.vectors_artifact_uri
        if not vectors_uri:
            return
        # Parse S3 URI: "s3://<bucket>/<key>" or bare key.
        if vectors_uri.startswith("s3://"):
            _prefix, rest = vectors_uri[5:].split("/", 1)
            key = rest
        else:
            key = vectors_uri

        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            payload = json.loads(resp["Body"].read())
            self._opensearch.upsert_chunks(payload.get("chunks", []))
        except Exception:  # noqa: BLE001
            log.warning(
                "failed to upsert vectors to OpenSearch",
                extra={"vectors_uri": vectors_uri},
                exc_info=True,
            )

    def _read_file(
        self,
        path: str,
        file_tree: dict[str, bytes] | None,
    ) -> bytes | None:
        """Read a file from the fixture tree or from S3 git mirror."""
        if file_tree is not None:
            return file_tree.get(path)
        # Production path: read from the S3 git mirror (populated by CodePipeline).
        git_mirror_key = path  # path is relative to the repo root
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=git_mirror_key)
            return resp["Body"].read()
        except Exception:  # noqa: BLE001
            log.error(
                "failed to read file from S3 git mirror",
                extra={"path": path},
                exc_info=True,
            )
            return None
