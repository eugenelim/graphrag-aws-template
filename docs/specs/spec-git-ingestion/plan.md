# Plan: spec-git-ingestion

- **Spec:** [`spec.md`](spec.md)
- **Status:** Executing <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Five tasks. T1 (`GitDeltaReader` + manifest) establishes the delta input — pure Python with a stubbed S3 client, no git binary needed in unit tests. T2 (`NeptuneLoadClient` SPARQL wrappers) and T3 (OpenSearch delete) are parallel after T1 — both depend on the delta result shape but not on each other. T4 (`MedallionOrchestrator`) depends on T1, T2, T3, and on `spec-ingestion-extraction-cleanse`'s `process_document()` entry point being importable (it is stubbed in unit tests). T5 (no-NAT fitness test addition + Terraform task-definition assertions) is a documentation/infra task that can run in parallel with T4.

The riskiest part is the delete path: looking up the partition from the taxonomy graph, then issuing the right `DELETE WHERE` pattern to remove document triples without touching the partition graph itself. Tested with an `rdflib` fixture before any live Neptune call.

No AWS credentials are needed for T1–T4 unit tests. The no-NAT fitness test in T5 runs `terraform plan` with a mock backend — no AWS needed.

## Constraints

- **Prerequisite (resolve before EXECUTE):** Verify the CodePipeline artifact format (git bundle vs. zip) against a live account or authoritative source-action documentation before EXECUTE begins. The `_delta.py` design below assumes a git bundle (`git bundle unbundle`); if CodePipeline's Git source action produces a zip archive instead, the Fargate task setup changes (unzip + `git init` from the extracted tree). See Risks (S3 bundle pattern compatibility).
- ADR-0016: git remote access via CodePipeline/S3-mirror only — no NAT, no live git clone.
- ADR-0016: manifest written after all documents processed; idempotent on same-SHA re-run.
- ADR-0016: `DROP GRAPH` is never issued from the ingestion task — delete uses `DELETE WHERE` scoped to the document URI.
- ADR-0011: `ingestion_task_role` holds `WriteDataViaQuery` + `connect`; `mcp_lambda_role` must never be used for writes.
- ADR-0012: taxonomy graph is updated on both insert and delete paths.
- ADR-0002: no-NAT fitness test must pass (route table assertion for ingestion subnet).
- Ruff + mypy CI gates must stay green.

## Construction tests

**T1 (delta reader + manifest):**
- `--name-status` fixture strings (A/M/D/R100) parse to correct sets.
- Missing manifest → `last_sha = "4b825dc42b"`.
- Unchanged file (not in diff) produces zero store operations.

**T2 (Neptune SPARQL write client):**
- `insert_document(doc_uri, partition, turtle)` produces a SPARQL `INSERT DATA` containing the turtle content and scoped to the partition graph.
- `delete_document(doc_uri)` produces two `DELETE WHERE` queries: one for doc triples, one for chunk triples in the partition graph; one `DELETE WHERE` for the taxonomy entry.
- `lookup_partition(doc_uri)` issues a SPARQL SELECT against `urn:graph:taxonomy` and returns the `biz:inPartition` value.
- No `DROP GRAPH` appears in any query string produced by any method.

**T3 (OpenSearch delete):**
- `delete_by_doc_uri(doc_uri)` issues an OpenSearch `delete_by_query` with `term: {doc_uri: <value>}`.

**T4 (orchestrator):**
- Added file: calls `process_document()`, then `insert_document()`, then `opensearch_upsert()`, then `manifest.write()`.
- Deleted file: calls `lookup_partition()`, then `delete_document()`, then `opensearch_delete()`.
- SHACL quarantine: `process_document()` returns `outcome="quarantined"` → no Neptune partition INSERT; `insert_quarantine_record()` called instead.
- Manifest write is the last call in the run — asserted by call order recording.
- Idempotency: second run with same SHA calls `insert_document()` again (no pre-check skip).

**T5 (no-NAT fitness + infra assertions):**
- `test_plan.py` assertion: ingestion subnet route table has no `0.0.0.0/0` default route.
- Terraform ECS task definition: `cpu=2048`, `memory=8192` (from spec-ingestion-extraction-cleanse constraint, recorded here).

## Design (LLD)

### Design decisions

- **`GitDeltaReader` does not invoke a git binary.** In the Fargate task, the git repository is available as a local clone from S3 (via `aws s3 cp` + `git bundle unbundle`). In unit tests, `git diff` output is a fixture string — no subprocess call. The production path shells out to `git diff` once the bundle is reconstructed locally; this subprocess call is tested only in integration.
- **S3 mirror format: CODE_ZIP (not git bundle).** CodePipeline's CodeStarSourceConnection delivers a `CODE_ZIP` artifact — a history-less source snapshot (no `.git` directory). The ADR's "git bundle" design was aspirational and unverified; this is the resolution of the "resolve before EXECUTE" prerequisite risk in this plan. The Fargate task (T1–T4) adapts: it unzips the snapshot (`latest/repo.zip`), compares file hashes against the previous manifest (snapshot-diff), and obtains the HEAD commit SHA by calling `codepipeline:GetPipelineExecution` with the `CODEPIPELINE_EXECUTION_ID` env var passed by the EventBridge `input_transformer`. The `git diff --name-status` mechanism described in AC1–AC2 becomes a snapshot-diff; those ACs are updated in T1 when the Python code is implemented.
- **`DELETE WHERE` pattern for document deletion.** Two separate statements:
  1. Delete document triples: `DELETE WHERE { GRAPH <partition> { <doc_uri> ?p ?o } }`
  2. Delete chunk triples: `DELETE WHERE { GRAPH <partition> { ?chunk ?p ?o . ?chunk prov:wasDerivedFrom <doc_uri> } }`
  These are issued in order (chunks first is safe because Neptune evaluates the WHERE before deleting). Taxonomy entry deleted last: `DELETE WHERE { GRAPH <urn:graph:taxonomy> { <doc_uri> ?p ?o } }`.
- **Manifest write is a plain S3 `put_object`.** No locking or conditional write — only one Fargate task runs at a time. If a concurrent run somehow fires (EventBridge double-trigger), the second write is also correct (it overwrites with the same HEAD SHA). Losing a manifest write is worse than an idempotent overwrite.
- **Quarantine INSERT uses `ingestion_task_role`.** The same client used for partition INSERTs — no separate role needed. The quarantine triple shape: `<doc_uri> a biz:QuarantinedDocument ; biz:quarantineReason "<reason>" ; biz:quarantineTime "…"^^xsd:dateTime .` inserted into `urn:graph:quarantine`.

### Data & schema

```python
# graphrag/ingestion/_types.py
from dataclasses import dataclass
from enum import StrEnum

class DeltaAction(StrEnum):
    added    = "A"
    modified = "M"
    deleted  = "D"

@dataclass
class DeltaEntry:
    action: DeltaAction
    path: str
    old_path: str | None = None   # populated for renames (R-type)

ProcessResult is defined in `graphrag.ingestion.pipeline` (owned by spec-ingestion-extraction-cleanse).
Import it: `from graphrag.ingestion.pipeline import ProcessResult`.
This spec's code uses it as-is; do not re-declare it.
# ProcessResult shape (for reference — authoritative definition in pipeline.py):
# outcome: "loaded" | "quarantined" | "error"
# silver_artifact_uri, gold_artifact_uri: S3 URIs set by the pipeline; absent on quarantine
# cleansing_report: CleansingReport dataclass (also from pipeline.py)
```

### Component / module decomposition

```
packages/graphrag/src/graphrag/ingestion/
├── __init__.py
├── _types.py            # DeltaAction, DeltaEntry, ProcessResult
├── _delta.py            # GitDeltaReader — parses --name-status; reads/writes manifest
├── _neptune.py          # NeptuneLoadClient — INSERT DATA, DELETE WHERE, lookup_partition
├── _opensearch.py       # delete_by_doc_uri, upsert_chunks
└── _orchestrator.py     # MedallionOrchestrator — drives the per-document pipeline

packages/graphrag/tests/ingestion/
├── test_delta_reader.py
├── test_neptune_client.py
├── test_opensearch_client.py
└── test_orchestrator.py
```

### Failure, edge cases & resilience

- **Partial delta run failure.** If `process_document()` raises (not quarantines) for a document mid-run, the exception is caught, the document is logged as `outcome="error"`, and the run continues to the next document. The manifest is still written with the new HEAD SHA at the end — the failing document will not be retried on the next run (it is "past" the new SHA). The operator must manually re-trigger with `last_commit_sha` reset to the previous SHA to retry.
- **Renamed file.** `git diff --name-status` produces `R100 <old-path> <new-path>`. The orchestrator treats this as: delete `old_path` (taxonomy lookup + DELETE WHERE) + add `new_path` (full pipeline). This is correct because the document URI (`urn:doc:{repo}:{path}`) changes when the path changes.
- **Missing taxonomy entry for deleted file.** If `lookup_partition()` returns no result (the document was never successfully loaded into Neptune), the delete path logs a WARNING and skips the Neptune DELETE (there is nothing to delete). The OpenSearch delete is still attempted.
- **Empty delta run.** Zero added/modified/deleted files. The manifest is updated to the new HEAD SHA (no-op commit), but no documents are processed. This is correct — the manifest tracks HEAD, not the last processed document.

### Quality attributes (NFRs)

- **No `DROP GRAPH` in any method** — confirmed by a string search across `_neptune.py` in CI.
- **Idempotent S3 writes** — Gold artifact S3 key is deterministic (`doc_uri + sha`); a second write is a safe overwrite.
- **Offline CI** — all unit tests run with `rdflib` + mock S3 client + mock OpenSearch; no AWS credentials.

## Tasks

### T1: `GitDeltaReader` + `ManifestManager`

**Depends on:** none

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_delta.py`
- `packages/graphrag/src/graphrag/ingestion/_types.py`
- `packages/graphrag/tests/ingestion/test_delta_reader.py`

**Tests (TDD):**
1. `A path/to/file.docx` → `DeltaEntry(action=DeltaAction.added, path="path/to/file.docx")`.
2. `M path/to/file.docx` → modified.
3. `D path/to/file.docx` → deleted.
4. `R100 old.docx new.docx` → two entries: deleted `old.docx`, added `new.docx`.
5. S3 raises `NoSuchKey` on manifest read → `last_sha = "4b825dc42b"`.
6. Full delta string (multiple lines) → correct sets.

**Done when:** 6 tests pass; `ruff check` and `mypy` clean.

---

### T2: `NeptuneLoadClient` SPARQL wrappers

**Depends on:** T1 (uses `DeltaEntry` type)

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_neptune.py`
- `packages/graphrag/tests/ingestion/test_neptune_client.py`

**Tests (TDD, rdflib offline substitute):**
1. `insert_document()` inserts triples into the correct named graph; SPARQL SELECT confirms.
2. `delete_document()` removes doc triples + chunk triples; taxonomy entry removed.
3. `lookup_partition()` returns the `biz:inPartition` URI from the taxonomy graph.
4. `insert_quarantine_record()` inserts a `biz:QuarantinedDocument` triple into `urn:graph:quarantine`.
5. No `DROP GRAPH` in any query string (string assertion on all methods).
6. Missing taxonomy entry → `lookup_partition()` returns `None`; no exception.

**Done when:** 6 tests pass; `ruff check` and `mypy` clean.

---

### T3: OpenSearch delete helper

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_opensearch.py`
- `packages/graphrag/tests/ingestion/test_opensearch_client.py`

**Tests (TDD, mock OpenSearch client):**
1. `delete_by_doc_uri("urn:doc:…")` issues `delete_by_query` with `term: {doc_uri: "urn:doc:…"}`.
2. OpenSearch raises `NotFoundError` → treated as a no-op (document already absent); no exception.

**Done when:** 2 tests pass; `ruff check` and `mypy` clean.

---

### T4: `MedallionOrchestrator` — pipeline dispatch

**Depends on:** T1, T2, T3; `spec-ingestion-extraction-cleanse`'s `process_document()` (stubbed in unit tests)

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_orchestrator.py`
- `packages/graphrag/tests/ingestion/test_orchestrator.py`

**Tests (TDD):**
1. Added file → call order: `process_document()` → `insert_document()` → `opensearch_upsert()` → `manifest.write_sha()`.
2. Deleted file → `lookup_partition()` → `delete_document()` → `opensearch_delete()` → `manifest.write_sha()`.
3. Quarantined file → `process_document()` returns `outcome="quarantined"` → `insert_quarantine_record()` → NO `insert_document()` → `manifest.write_sha()`.
4. `manifest.write_sha()` is called exactly once, after all documents, even when one is quarantined.
5. Idempotency: second call with same SHA → `process_document()` and `insert_document()` called again; no exception.
6. Missing taxonomy for deleted file → WARNING logged; `delete_document()` skipped; `opensearch_delete()` still called.

**Done when:** 6 tests pass; full test suite green; `ruff check` and `mypy` clean.

---

### T5: No-NAT fitness test + Terraform task-definition assertions

**Depends on:** none (independent Terraform/test assertion)

**Touches:**
- `apps/infra-tf/tests/test_plan.py` (add ingestion-subnet route-table assertion)

**Tests (goal-based):**
- `terraform plan` output for `apps/infra-tf/` confirms no `0.0.0.0/0` default route in the ingestion subnet route table.
- ECS task definition resource has `cpu = 2048` and `memory = 8192`.
- ECS task definition environment block contains `TRANSFORMERS_OFFLINE=1` and `HF_DATASETS_OFFLINE=1`.

**Approach:**
1. Add three `assert` statements to the existing `test_plan.py` fixture, following the existing pattern for IAM grant assertions.
2. These assertions run offline (`terraform plan` with mock backend; no AWS credentials).

**Done when:** assertions pass in `pytest apps/infra-tf/tests/`; `ruff check` clean.

## Rollout

- **Delivery:** no flag — `graphrag.ingestion` is a new module; the Fargate task is a new Fargate task definition (separate from any existing task).
- **Infrastructure:** CodePipeline source action, S3 mirror bucket, EventBridge rule, and Fargate task definition are provisioned in `apps/infra-tf/git-ingestion-trigger` (work queue item).
- **Deployment sequencing:** `spec-git-ingestion` depends on `packages/graphrag/neptune-sparql-store` (work queue) and `packages/graphrag/ingestion-extraction-cleanse` (this spec's companion).

## Risks

- **S3 bundle pattern compatibility (resolve before EXECUTE — see Prerequisite above).** CodePipeline's Git source action may produce a zip archive of the repo tree rather than a git bundle. If it does, the Fargate task must unzip and `git init` from the extracted tree rather than running `git bundle unbundle`. The design currently assumes bundle; this assumption must be confirmed (documentation or live account check) before `_delta.py` is implemented.
- **Manifest write race condition.** EventBridge double-trigger (unlikely but possible on webhook delivery) causes two Fargate tasks to process the same delta concurrently. Both write the same HEAD SHA to the manifest — harmless. Both attempt Neptune INSERTs — idempotent. OpenSearch upserts — idempotent. Accepted risk for the demo; a production system would use SQS FIFO for deduplication.
- **Chunk DELETE pattern correctness.** The `DELETE WHERE { GRAPH <partition> { ?chunk ?p ?o . ?chunk prov:wasDerivedFrom <doc_uri> } }` pattern must be verified against Neptune SPARQL 1.1 semantics — Neptune may not support graph-scoped DELETE WHERE with a property-path filter. If not, fallback: SELECT chunk URIs first, then DELETE by URI list.

## Changelog

- 2026-07-23: initial plan
- 2026-07-24: T5 executing — Terraform infra shipped (CodePipeline, EventBridge, git_mirror S3 bucket, IAM roles). Resolved prerequisite risk: CodeStarSourceConnection delivers CODE_ZIP (history-less snapshot), not a git bundle. Updated S3 mirror format note; added EventBridge `input_transformer` to propagate `CODEPIPELINE_EXECUTION_ID`; added `codepipeline:GetPipelineExecution` IAM grant. AC1–AC2 `git diff` parsing will be adapted in T1 to snapshot-diff.
