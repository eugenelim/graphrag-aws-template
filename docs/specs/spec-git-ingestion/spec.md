# Spec: spec-git-ingestion

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0016](../../adr/0016-git-ingestion-commit-sha-delta-medallion.md) (git commit-SHA delta + medallion — primary decision this spec implements); [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (no-NAT egress posture — CodePipeline/S3-mirror is a hard constraint, not a preference); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (`ingestion_task_role` WriteDataViaQuery grant; SPARQL DROP logic on delete); [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition the Gold layer loads into; `biz:gitCommitSHA` required by SHACL shapes); `spec-ingestion-extraction-cleanse` (Silver + Gold production is out of scope for this spec)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** pipeline

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.ingestion.git` module implements the Fargate ingestion task orchestrator that drives the Bronze → Silver → Gold medallion pipeline from a git commit-SHA delta signal. It provides:

1. **`GitDeltaReader`** — reads `last_commit_sha` from the S3 manifest; runs `git diff <last_sha>..HEAD --name-status`; parses the output into add, modify, and delete sets. Falls back to a full rescan (empty-tree SHA `4b825dc642cb6eb9a060e54bf8d69288fbee4904`) when the manifest is absent or corrupt.

2. **`MedallionOrchestrator`** — dispatches each added/modified file to the extraction and cleansing pipeline (`spec-ingestion-extraction-cleanse`) and each deleted file to the Neptune DELETE + OpenSearch delete path. Drives the pipeline document by document; the Gold SHACL gate routes validation failures to `urn:graph:quarantine` without stopping the run.

3. **`NeptuneLoadClient`** — wraps the SPARQL `INSERT DATA` and `DELETE WHERE` calls using `ingestion_task_role` (WriteDataViaQuery). Handles the taxonomy graph updates (partition membership) on both insert and delete paths.

4. **`ManifestManager`** — reads the current manifest SHA from S3; writes the new HEAD SHA atomically after all documents in the run have been processed (loaded or quarantined — not rolled back on failure).

This module owns the orchestration loop and the Neptune/OpenSearch coordination. The pipeline (`spec-ingestion-extraction-cleanse`) owns all S3 writes (Silver + Gold artifacts) and returns a `ProcessResult` describing what was written. The orchestrator consumes `ProcessResult` to decide whether to issue a Neptune partition INSERT or a quarantine INSERT, and to drive the OpenSearch upsert.

## Boundaries

### Always do

- Read git remote content exclusively from the S3 mirror produced by CodePipeline — never attempt a direct `git clone` from a remote URL. The Fargate task has no NAT gateway and cannot reach the public internet from the ingestion subnet.
- Treat the delta run as atomic with respect to the manifest: write the new HEAD SHA to the manifest **only after all documents in the delta have been processed** (loaded or quarantined — not rolled back on failure). A partial-write manifest would cause the next run to skip documents whose processing failed.
- Preserve idempotency: if a Gold artifact already exists for `doc_uri + commit_sha`, the Neptune INSERT and OpenSearch upsert are still issued (SPARQL `INSERT DATA` for existing triples is a no-op in Neptune; OpenSearch upsert is idempotent). Do not add a pre-check that skips the INSERT — the cost of a no-op is lower than the risk of silent non-idempotency.
- Use `ingestion_task_role` credentials for all Neptune SPARQL writes — `mcp_lambda_role` is read-only and must not be used in the ingestion path.
- Update the taxonomy graph (`urn:graph:taxonomy`) with `biz:inPartition` triples on insert, and delete taxonomy entries on the delete path. The taxonomy index is the lookup key for the delete path's partition resolution.
- Log each document's outcome (added to partition, quarantined, deleted) at INFO level with `doc_uri`, `sha`, and outcome. Do not log raw document content.

### Ask first

- Changing the manifest S3 key path (`manifest/last_commit_sha`) — downstream monitoring may watch this key.
- Changing the Gold artifact S3 key scheme (`gold/<doc_uri>/<sha>.ttl`, `gold/<doc_uri>/<sha>.vectors.json`) — the key scheme is also the provenance artifact URI format; changes break PROV-O triples for existing artifacts.
- Adding a concurrency model (parallel document processing within a delta run) — the current design is sequential per document to keep the failure model simple; concurrency would require per-document failure isolation and a different manifest model.

### Never do

- Attempt a direct git clone from a public remote URL (NAT gateway is out of bounds per ADR-0002 and ADR-0016 §Security constraint).
- Write the HEAD SHA to the manifest before all documents in the delta are processed.
- Issue SPARQL `DROP GRAPH` from the ingestion task — delete is implemented via `DELETE WHERE` scoped to the document URI, not by dropping the entire partition graph. `DROP GRAPH` on `urn:graph:normative` would destroy the entire normative partition.
- Skip the taxonomy graph update on delete — without deleting the `biz:inPartition` entry, a future re-add of the same document would fail the partition-lookup step with a stale entry.
- Write Gold artifacts to S3 for documents that failed the SHACL gate — quarantined documents go to `urn:graph:quarantine`; their Gold path is not written.

## Testing Strategy

- **TDD** — `GitDeltaReader` delta parsing (AC1–AC2): fixture `--name-status` output strings for add (`A`), modify (`M`), rename (`R100`), and delete (`D`); assert correct set membership. Rename is treated as delete-old + add-new.
- **TDD** — `GitDeltaReader` manifest fallback (AC2): stub S3 client that raises `NoSuchKey`; assert `last_sha = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"` (empty-tree SHA) is used and `git diff 4b825dc642cb6eb9a060e54bf8d69288fbee4904..HEAD` is the effective delta command.
- **TDD** — no-op for unchanged files (AC3): a delta with zero added/modified/deleted files produces zero S3 writes, zero Neptune operations, and zero embedding calls — confirmed via mock assertions.
- **TDD** — delete path (AC5): fixture taxonomy graph with `<urn:doc:repo:path> biz:inPartition <urn:graph:descriptive>`; assert the orchestrator issues a `DELETE WHERE` scoped to `urn:graph:descriptive` for the document's triples and chunks, then a `DELETE WHERE` on the taxonomy entry, then an OpenSearch delete by `doc_uri`.
- **TDD** — manifest write timing (AC8): assert `ManifestManager.write_sha()` is called exactly once, after `MedallionOrchestrator.process_all()` completes, even when some documents are quarantined.
- **TDD** — idempotency (AC9): running the orchestrator twice with the same commit SHA produces a second Neptune INSERT (which is a no-op in Neptune SPARQL) and a second OpenSearch upsert (idempotent) — no exception raised, no skip logic; the same Gold S3 artifact key is written again.
- **Goal-based check** — no-NAT fitness (AC11): `terraform plan` assertion in `apps/infra-tf/tests/test_plan.py` — the ingestion subnet route table has no default route (`0.0.0.0/0`) to an internet gateway or NAT gateway. Fires in CI on any infra change.
- **Goal-based check** — Gold artifact not written for SHACL failure (AC12): patch the SHACL validator to return a violation; assert no `gold/` S3 key is written and a `urn:graph:quarantine` INSERT is issued.

## Acceptance Criteria

- [x] `GitDeltaReader` correctly classifies `git diff --name-status` output lines: `A <path>` → added; `M <path>` → modified; `D <path>` → deleted; `R100 <old> <new>` → old path deleted + new path added.
- [x] When `manifest/last_commit_sha` is absent from S3 (key does not exist), `GitDeltaReader` uses `last_sha = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"` (the empty-tree SHA), producing a full-corpus rescan from the initial commit.
- [x] Unchanged files (files present in HEAD but not in the `git diff` output) produce zero S3 writes, zero Neptune SPARQL calls, and zero Bedrock embedding calls.
- [x] An added file produces, in order: `process_document()` (pipeline owns Silver + Gold S3 writes) → Neptune SPARQL `INSERT DATA` into the partition graph (if outcome=`loaded`) or `INSERT` into quarantine (if outcome=`quarantined`) → taxonomy graph `INSERT DATA` (loaded only) → OpenSearch upsert (loaded only).
- [x] A modified file (action `M`) is treated as delete-old-version then add-new-version: the orchestrator first issues `DELETE WHERE` for the old document's triples and chunks from the resolved partition graph, then `DELETE WHERE` on the taxonomy entry, then calls `process_document()` and follows the add path. After a modify run, the Neptune partition graph contains only the new SHA's triples — no old-SHA triples survive.
- [x] A modified file produces no orphaned old triples: a SPARQL SELECT for the document URI after a modify run returns only triples emitted from the new commit SHA.
- [x] A deleted file produces, in order: taxonomy lookup (`SELECT … FROM NAMED <urn:graph:taxonomy>` for the `biz:inPartition` value) → `DELETE WHERE` for the document's triples and chunk triples from the resolved partition graph → `DELETE WHERE` on the taxonomy entry → OpenSearch delete by `doc_uri`.
- [x] Gold S3 artifact key follows the scheme `gold/<doc_uri>/<sha>.ttl` for the Turtle graph and `gold/<doc_uri>/<sha>.vectors.json` for the embedding vectors; Silver artifact key follows `silver/<doc_uri>/<sha>.md` and `silver/<doc_uri>/<sha>.report.json`.
- [x] `ManifestManager.write_sha(new_sha)` is called exactly once per run, after all documents in the delta are processed, with the new HEAD SHA (not the last ingested SHA).
- [x] Re-running the orchestrator with the same commit SHA (same delta set) produces no exception and no incorrect state: Neptune INSERT for an existing triple is a no-op; OpenSearch upsert is idempotent; the manifest is overwritten with the same SHA.
- [x] All SPARQL writes use `ingestion_task_role` credentials (`WriteDataViaQuery` + `connect`). No SPARQL write is attempted from a context that uses `mcp_lambda_role`.
- [x] The delete path does not issue `DROP GRAPH` — it uses `DELETE WHERE { GRAPH <partition> { <doc_uri> ?p ?o } }` and a separate `DELETE WHERE` for chunks.
- [x] `terraform plan` output for `apps/infra-tf/` confirms the ingestion subnet route table has no `0.0.0.0/0` route to an internet gateway or NAT gateway (no-NAT fitness test — offline CI, no AWS credentials needed to run `terraform plan` on a mock backend).
- [x] A document failing the SHACL gate (e.g. `biz:Policy` missing `biz:effectiveDate`) routes to `urn:graph:quarantine` with a structured `biz:quarantineReason` triple; no Gold S3 artifact is written; no Neptune partition INSERT is issued.
- [x] When `last_commit_sha` is `"4b825dc642cb6eb9a060e54bf8d69288fbee4904"` (empty-tree SHA), every file in `HEAD` is in the added set — a full rescan with no skips.
- [x] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/ingestion/` with zero errors.

## Assumptions

- Technical: `graphrag.ingestion.git` lives in `packages/graphrag/src/graphrag/ingestion/`; tests in `packages/graphrag/tests/ingestion/`.
- Technical: The Fargate task reads the git repo from an S3 bucket populated by the CodePipeline source action (not a live git clone). The S3 bucket name is passed as an environment variable (`GIT_MIRROR_BUCKET`).
- Technical: The offline test substitute for the git diff is a fixture `--name-status` string; the S3 mirror interaction is stubbed (a fixture dict of `{path: bytes}` representing the file tree). No live git binary is required for unit tests.
- Technical: `ingestion_task_role` credentials are available via the ECS task metadata credential provider (standard boto3 credential chain in a Fargate task — no explicit key injection).
- Technical: The manifest is a single S3 object at `manifest/last_commit_sha` containing the raw SHA hex string (no JSON wrapper). `ManifestManager` reads with `get_object` and writes with `put_object`; there is no locking — only one Fargate task runs at a time (EventBridge does not trigger concurrent runs for the same repo).
- Technical: The Silver/Gold production (extraction, SHACL validation, RDF emission, embedding, and all S3 artifact writes) is executed via the `graphrag.ingestion.pipeline.process_document(file_bytes, path, sha, doc_uri)` entry point from `spec-ingestion-extraction-cleanse`. This spec's scope ends at invoking `process_document()` and handling its `ProcessResult` (`loaded`, `quarantined`, or `error`). The 4-arg signature is canonical; the `doc_uri` is required by the pipeline to key S3 artifacts.
- Infra: The CodePipeline source action mirrors the full repo on each push; the Fargate task does not need to handle partial mirrors. The S3 mirror is complete and consistent with the HEAD SHA at the time the EventBridge trigger fires.
- Infra: The no-NAT fitness test (`test_plan.py`) already exists for the Terraform infra tier (from `spec-infra-terraform-verification`) — this spec adds an assertion for the ingestion-subnet route table specifically.
