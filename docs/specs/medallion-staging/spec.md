# Spec: medallion-staging

- **Status:** Draft
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** RFC-0003, RFC-0002, ADR-0007, ADR-0002, ADR-0006
- **Contract:** none
- **Shape:** mixed

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag` ingestion pipeline runs as three decoupled stages — Bronze (raw
parse + content-hash manifest), Silver (per-document chunks+embeddings and
ungrounded candidate triples, cached in S3), Gold (global resolve, ground, and a
transactional store mutation). Silver artifacts are addressed by
`content_hash ⊕ config_fingerprint`, so a re-ingest of an unchanged corpus makes
zero Bedrock calls, a *moved* document reuses its artifact verbatim, and a change
to the embedder model or extraction schema automatically recomputes exactly the
affected artifacts — closing the bug where a config change silently served stale
vectors. Ingest state is a backward-compatible `IngestState` (the v1 manifest
widened with per-document Silver keys, fingerprints, and a stage watermark), and
the graph mutation is an explicit `GraphDelta` planned before it is applied. The
maintainer of this reference template gets correct freshness under change at a
fraction of the Bedrock cost, verifiable end-to-end through the existing query
path.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off
before proceeding; *Never do* is a hard rule, even under time pressure.

### Always do

- Reuse `diff_manifests` unchanged by projecting `IngestState` back to the v1
  `{doc_id: content_hash}` shape via `as_manifest()`.
- Drive unit tests through the offline seams (`HashEmbedder`, `RuleTripleExtractor`)
  with a spy that asserts zero Bedrock calls on a cache hit.
- Give every new S3 artifact prefix its **own** key-scoped `bucket.grant_put` on
  the ingest task role (least privilege), with a matching CDK assertion test.
- Keep this spec the source of truth — if implementation diverges, update the spec
  in the same PR.

### Ask first

- Any new top-level runtime dependency (the corpus is small; prefer the stdlib +
  what `embed.py`/`schema_extract.py` already use).
- Any change to the v1 manifest JSON envelope's on-disk shape (other than the
  additive v2 fields).
- Any change that would make Gold recompute community summaries on a delta
  (out of scope; mirrors microsoft/graphrag #741).

### Never do

- Change query-path / retrieval **code** (`hybrid.py`, `query.py`, the query
  Lambda). Retrieval is a **read-only verification surface** here; a query-path
  change requires amending RFC-0003 first.
- Add Step Functions, a Map state, multi-task Fargate, or any standing billable
  resource (ADR-0002 teardown-first; D4 of RFC-0003 chose the single in-process
  driver).
- Ground or validate schema-guided candidate triples inside Silver — grounding is
  global and belongs in Gold (`extract_schema_guided` needs the resolved graph).
- Persist any Silver artifact outside the teardown-removable corpus-bucket prefix,
  or make it a system of record (it is a cache; `--rebuild` is the reset).
- Import `delta.py`/`silver.py` (PyYAML-bearing ingest path) into the query Lambda.

## Testing Strategy

- **`IngestState` v2 + v1→v2 upgrade** — *TDD*. A compressible invariant: JSON
  round-trip is identity, a v1 envelope upgrades to v2 with Silver cold, and
  `as_manifest()` reproduces the exact v1 dict.
- **`ArtifactStore` + `materialize_silver` cache** — *TDD*, offline. Hit path makes
  zero embed/extract calls (spy); miss path computes once and writes; a fingerprint
  change is a miss.
- **`GraphDelta` plan/apply** — *TDD*. `plan_graph_delta` is pure (no store
  mutation); `apply_graph_delta` leaves the store byte-identical to today's
  `_reconcile_graph` for the same inputs *and* makes the same set of mutating calls —
  unchanged rows are **not** re-written (call-count parity, not just final-state
  equivalence), so the no-op optimization is preserved.
- **Silver key confinement** — *TDD*. The Silver S3 key is built only from the
  server-computed `content_hash` (sha256 hex) and the server-derived
  `config_fingerprint` (hex); a path-shaped `doc_id` containing `../` cannot alter the
  resolved key (mirrors the trace-key CWE-23 confinement pattern).
- **Staged driver (`ingest_staged`)** — *goal-based*, exercised by an **integration**
  test over in-memory stores: a delta re-ingest recomputes only the changed set and
  reconciles orphans, matching a full rebuild's end state; the test counts per-doc
  embed/extract calls so a content-only change is asserted to leave sibling docs as
  hits on **both** Silver artifacts.
- **Fargate/IAM wiring** — *goal-based*, a CDK **assertion** test that the synthesized
  template grants `PutObject` whose Resource is prefix-bounded to `silver/*` (never the
  bucket root or `*`), beside — not replacing — the manifest/trace grants.
- **Live acceptance (run at implementation, not deferred)** — *goal-based/manual QA*
  exercised **end-to-end** against the deployed task: cache-hit skip, fingerprint-bump
  recompute, retrieval reflects the change, and teardown leaves zero residual.

## Acceptance Criteria

- [ ] **AC1** — A re-ingest of an unchanged corpus performs **zero** Bedrock embed and zero
  LLM-extract calls (offline spy test + live AC).
- [ ] **AC2** — An embedder fingerprint change (`model_id` or `dimensions`) recomputes the
  chunks/vectors artifact for every doc; an `EXTRACTION_SCHEMA` change recomputes the
  candidate-triples artifact; a content-only change recomputes neither beyond the
  changed docs.
- [ ] **AC3** — A moved document (same content hash, new path) reuses Silver with zero Bedrock
  calls and is classified a move by `diff_manifests`.
- [ ] **AC4** — `IngestState` v2 round-trips through JSON; a v1 envelope upgrades in with no
  migration script (Silver cold, stage=bronze); `as_manifest()` reproduces the v1
  dict so `diff_manifests` is reused unchanged.
- [ ] **AC5** — `plan_graph_delta` performs no store mutation; `apply_graph_delta` produces a
  store state identical to the pre-refactor `_reconcile_graph` for the same
  `(store, scratch, removed_ids)` **and makes the same set of mutating calls** — an
  unchanged row triggers no `replace_*` (call-count parity test green).
- [ ] **AC6** — The Silver S3 key is built **only** from the server-computed `content_hash`
  (sha256 hex) and the server-derived `config_fingerprint` (hex) — never from
  `doc_id`, a doc path, a span, or model output; a `doc_id` containing `../` cannot
  change the resolved key (confinement unit test, CWE-23).
- [ ] **AC7** — The ingest task role has a `PutObject` grant whose synthesized Resource is
  **prefix-bounded to `silver/*`** (not the bucket root, not `*`), added **beside**
  the existing manifest/trace key grants and widening no other role (CDK assertion
  test).
- [ ] **AC8** — Silver artifacts live under the auto-emptied corpus-bucket prefix; a `destroy`
  leaves **zero residual** Silver objects (live AC).
- [ ] **AC9** — End-to-end, read-only, through the **unmodified** query path: an **embedder**
  fingerprint bump + a staged `MODE=delta` re-index makes a vector query return results from the
  re-embedded vectors; a **schema** fingerprint bump (schema-guided extraction enabled) run via
  `MODE=full`/`rebuild` makes graph traversal reflect the recomputed schema-guided edges (live AC).
  (Delta is the staged path; full/rebuild Silver staging is deferred —
  `medallion-fullrebuild-staging` — so the schema-fp recompute is verified through the existing
  full-path extraction, not the Silver candidate cache, which T2/T4a prove offline.)
- [ ] **AC10** — No query-path/retrieval code changed (diff check); `delta.py`/`silver.py` are
  not imported by the query Lambda (import check). The PR adds no new runtime
  dependency (`pyproject.toml` dependency-list diff check).

## Assumptions

- Technical: runtime is Python ≥3.11 (target 3.11); gates are ruff 0.5 + mypy 1.10 + pytest 8, `pythonpath = packages/graphrag/src, apps, apps/infra` (source: `pyproject.toml`).
- Technical: ingest runs in one on-demand Fargate task; `apps/ingestion/entrypoint.py` reads/writes the manifest to the corpus bucket (source: `apps/ingestion/entrypoint.py:71-86`).
- Technical: an `S3Client` Protocol + `read_manifest`/`write_manifest`/`download_corpus` already exist; the `ArtifactStore` seam extends this pattern (source: `apps/ingestion/entrypoint.py:59-109`).
- Technical: the manifest is a versioned JSON envelope (`MANIFEST_VERSION=1`); v2 extends it and `as_manifest()` preserves `diff_manifests` (source: `delta.py:122-137`).
- Technical: `Embedder` exposes `model_id`+`dimensions` and `EXTRACTION_SCHEMA` is a constant with no version field, so a `schema_fingerprint()` supplies the extraction fingerprint (source: `embed.py:25-36`, `extract_llm.py:114-139`).
- Technical: IAM grants are key-scoped — `grant_read` + `grant_put(MANIFEST_KEY)` + `grant_put(SCHEMA_EXTRACTION_TRACE_KEY)`; the Silver prefix needs its own (source: `apps/infra/stacks/graphrag_stack.py:355-359`).
- Technical: schema-guided extraction runs full/rebuild only, never `--delta`, so Silver caches only its *candidate* extraction; grounding stays in Gold (source: `apps/ingestion/entrypoint.py:289`).
- Process: spec is `Constrained by` RFC-0003 / ADR-0007 / ADR-0002 / ADR-0006; offline-first seams + live-AC run-or-defer are project conventions (source: `docs/CONVENTIONS.md`, `docs/CHARTER.md`).
- Product: scope includes the ingest-path implementation **and** read-only end-to-end retrieval verification; no query-path code change (source: user confirmation 2026-06-28).
- Process: slice (e) live acceptance criteria are **run at implementation time**, not deferred (source: user confirmation 2026-06-28).
- Product: the stack is a reference repo, not a live-in-use system; teardown must leave zero residual and the v1→v2 upgrade needs correctness, not zero-downtime (source: user confirmation 2026-06-28).
- Process: this is a single spec, task-sliced a–e (source: user confirmation 2026-06-28).
