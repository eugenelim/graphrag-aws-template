# Spec: incremental-delta-reingest

- **Status:** Implementing <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Shape:** mixed
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [charter](../../CHARTER.md) (Architecture-pattern 8 — *Incremental delta re-ingest: diff against a corpus snapshot, upsert/delete by a stable key (doc path + content hash) with an explicit orphan-removal pass and a `--rebuild` escape hatch, keeping both stores consistent*; principle 5 — *synthetic stays labeled synthetic*), [design doc](../../architecture/graphrag-aws-architecture/design.md) (the *Incremental sync* paragraph; the *Corpus snapshot strategy* Open Question; the *Incremental drift / orphans* Risk), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (the seed-and-expand stores this keeps fresh), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (the no-NAT / S3-snapshot topology delta detection must stay inside), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python)
- **Brief:** [`docs/product/briefs/graphrag-aws-demo.md`](../../product/briefs/graphrag-aws-demo.md)
- **Contract:** none (internal Python interfaces + new `graphrag` CLI verbs + a `MODE` env on the existing Fargate ingestion task; no repo-root `contracts/` API surface, consistent with slices 1–4)

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> Slice 5 of the brief's Spec map — the second and last enterprise-concern slice.
> It rides the **same two stores and the same single-parse dual-write ingest path**
> the three-mode core ships ([`graph-ingestion-resolution`](../graph-ingestion-resolution/spec.md)
> slice 1, [`vector-rag-baseline`](../vector-rag-baseline/spec.md) slice 2,
> [`hybrid-orchestration`](../hybrid-orchestration/spec.md) slice 3) and the labels
> slice 4 ([`permission-filtered-retrieval`](../permission-filtered-retrieval/spec.md))
> stamps — no parallel pipeline. `Depends on:` slices 1–4 (reuses the
> `GraphStore`/`VectorStore` seams, the `ingest()` dual-write, the chunk→entity-ID
> provenance, the `labels` stamping, and the Fargate ingestion task / S3 corpus
> snapshot).

## Objective

A solution architect evaluating GraphRAG needs to *see* the enterprise concern most
demos quietly dodge — **a corpus that changes** — answered on the same two stores as
the rest of the demo, without a parallel pipeline and without either store drifting
stale. This slice gives the existing Fargate ingestion task a **`--delta` mode**: it
detects the git-delta (**add / change / delete / move**) between the previously-ingested
corpus snapshot and a new one, **re-ingests only the delta** (the costly Bedrock
embedding calls run only for added or changed chunks — never for unchanged ones), and
**updates BOTH stores consistently** through an **explicit orphan-removal pass** that
leaves *no stale graph nodes or edges and no orphan chunks*. The **stable key (doc path
+ content hash)** is what the *delta classification, the chunk operations, and the
node/edge document-provenance* are keyed on; graph node/edge **identity** stays the
slice-1 normalized entity key, carrying a document-provenance set so the same entity can
be contributed by several documents. A node or edge survives a delta **iff at least one
surviving document still contributes it**: the demo's load-bearing teaching beat is
watching a SIG node *survive* the deletion of its README because a KEP still references
it, while a node whose last contributing document is gone is removed. A **`--rebuild`
escape hatch** reingests from scratch as the ground-truth reset.

Delta detection is **content-hash-manifest-based** so it runs entirely inside the
no-NAT, S3-snapshot topology (ADR-0002): each ingest writes a manifest
(`doc id → content hash`) to S3, and a delta diffs the new snapshot's manifest against
the stored one — a **move** is the same content hash appearing at a new path. A
**CLI before/after demo on real git history** drives this from two actual commits of a
local corpus checkout and prints a narratable report — counts before, the classified
add/change/delete/move set, the orphans removed, counts after — so the freshness
mechanism is legible, never a black-box hop (charter principle 1). The slice is
**verified live** on the deployed Neptune + OpenSearch stores before it ships.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule, even under time pressure.

### Always do

- **Keep both stores consistent in one task run.** The graph and vector writes for a
  delta happen in the *same* task invocation reading the *same* new snapshot, so they can
  never diverge (charter pattern 2 / pattern 8). The delta classification, the chunk
  operations, and the node/edge document-provenance are keyed on the stable key (doc path
  + content hash); node/edge identity remains the normalized entity key. The end state
  after a delta is **identical** to a full `--rebuild` of that same snapshot (verified on
  the in-memory stores, AC6) — this equivalence is the consistency contract.
- **Re-ingest only the delta.** Re-extract, re-chunk, and **re-embed** only added / changed /
  moved-to documents; **unchanged documents are never re-embedded** — that Bedrock call is the
  cost the "incremental" claim is about (AC2). (Parsing the corpus to build the new manifest is
  cheap and in-process, so it is *not* restricted; only the network-bound embedding is.)
- **Run an explicit orphan-removal pass.** After applying the delta, no graph node or
  edge and no chunk may remain whose every contributing document is gone (design's
  *Incremental drift / orphans* Risk). Orphan removal is computed as **net reconciliation
  over a provenance set**: each node/edge carries the set of document ids that contribute
  it (the reference count); a delta strips the changed documents' ids and re-adds their
  new contributions, and a node/edge is deleted **iff its provenance set becomes empty**.
- **Compute surviving provenance before deleting.** A *changed* document both removes its
  old contribution and adds its new one; reconcile the net surviving provenance first, so
  a node still referenced by the changed (or any other) document is never transiently
  deleted (no over-deletion).
- **Detect and report move distinctly.** A document whose content hash is unchanged but
  whose path changed is a **move**, classified separately from add/change/delete; its
  chunks and node/edge provenance migrate to the new path and nothing is orphaned by the
  move.
- **Persist the manifest to S3 after every successful run** (full *or* delta). The
  manifest (`doc id → content hash`) is the record of "what is ingested" that the next
  delta diffs against — the design's "the ingested commit."
- **Keep delta detection inside the no-NAT topology.** Detection reads the manifest from
  S3 (a gateway endpoint already provisioned, ADR-0002); the deployed path never performs
  a live `git clone` from inside the VPC (it would need NAT and break the cost posture).
  Git is the *demo driver* on the laptop, not an in-VPC dependency.
- **Stamp document-path provenance from the same single parse pass** (charter pattern 2),
  alongside the existing `sources` tag and visibility labels — one pass, no second read.
- **Keep the query Lambda's PyYAML-free import invariant green.** Delta lives entirely on
  the ingest path (Fargate / CLI); nothing it adds may be imported by the query Lambda
  (`packages/graphrag/AGENTS.md`; `test_query_lambda_import_graph_is_pyyaml_free`).
- **Present the construct as synthetic where it surfaces.** The before/after demo and docs
  carry the same teaching-stand-in framing the rest of the demo uses for visibility labels
  (charter principle 5) — the delta is over a public corpus, narrated, not dressed up as a
  production CDC pipeline.

### Ask first

- **Changing the `GraphStore` / `VectorStore` public interface beyond** adding the delete
  primitives and the document-provenance this slice needs.
- **Changing the S3 corpus layout or the manifest schema** once a first version ships
  (it becomes the cross-run compatibility surface a deployed stack reads back).
- **Re-running the live AC against the deployed stores** (it incurs Neptune/OpenSearch +
  Bedrock cost and must be torn down after — same posture as slices 2–4).

### Never do

- **Never introduce a NAT gateway or a live in-VPC `git clone`** (breaks ADR-0002's
  no-NAT cost posture; the S3 snapshot + manifest is the only no-NAT-consistent source).
- **Never re-embed unchanged chunks** (it silently defeats "re-ingest only the delta" and
  the cost claim — assert it, don't assume it).
- **Never over-delete or under-delete:** never delete a node/edge/chunk a surviving
  document still contributes, and never leave one whose contributing documents are all
  gone. Both directions are correctness failures.
- **Structural — no parallel ingestion pipeline.** `--delta` and `--rebuild` are **modes
  of the existing `ingest()` path and Fargate task**, not a forked second pipeline.
- **Structural — no new top-level runtime dependency** (content hashing is stdlib
  `hashlib`; the manifest is stdlib `json`; git is invoked only by the offline demo
  driver, never imported into the package).

## Testing Strategy

- **Delta classification (add/change/delete/move from two manifests):** TDD — a
  compressible invariant over a pure function; covers AC1, AC5.
- **Provenance-set orphan reconciliation (survives-iff-referenced; deletes-when-empty;
  no over-deletion on a changed doc):** TDD — the load-bearing correctness logic, against
  the in-memory `GraphStore`; covers AC4.
- **Vector chunk delta (delete a removed doc's chunks; index an added doc's chunks):**
  TDD against the in-memory `VectorStore`; covers AC3.
- **No-re-embed guarantee:** TDD — a spy/counting embedder asserts zero embed calls for
  unchanged documents; covers AC2.
- **Delta-equals-rebuild equivalence:** goal-based, exercised by an **integration** test
  over both in-memory stores — applying a delta yields byte-identical store contents to a
  full `--rebuild` of the new snapshot; covers AC6 (the consistency contract).
- **`--rebuild` clears prior state:** TDD / goal-based; covers AC7.
- **Manifest round-trip persistence (write to S3, read back, diff):** TDD with an
  in-memory S3 fake (the existing `S3Client` Protocol seam in
  `apps/ingestion/entrypoint.py`); covers AC8.
- **CLI before/after demo on real git history:** goal-based, exercised end-to-end by a
  test that builds a tiny throwaway git repo of corpus files, commits two states, and
  asserts the printed report classifies the delta and lists the orphans removed; covers
  AC10.
- **Live deployed delta sync:** manual QA / goal-based — run `--delta` against the
  deployed Neptune + OpenSearch on a real git-history delta, observe an orphaned
  node/chunk gone and an added document retrievable in the trace, then tear down; covers
  AC9.

## Acceptance Criteria

- [x] **AC1 — `--delta` detects the git-delta.** The Fargate ingestion task (and the CLI)
  run in `--delta` mode and classify every changed document between the stored manifest
  and a new snapshot as exactly one of add / change / delete / move.
- [x] **AC2 — re-ingest is delta-only.** A delta re-parses and re-embeds only added /
  changed / moved-to documents; a test proves zero embedding calls are made for unchanged
  documents.
- [x] **AC3 — both stores reflect the new state.** After a delta: added documents'
  nodes/edges/chunks are present, changed documents' content is updated, and deleted
  documents' content is absent — in **both** the graph store and the vector store. The
  chunk and document-provenance operations are addressed by the (doc path + content hash)
  key; node/edge identity remains the normalized entity key.
- [x] **AC4 — explicit orphan-removal, no stale nodes / no orphan chunks.** After a delta,
  no graph node/edge and no chunk remains whose contributing documents are all gone; **and**
  a node/edge still contributed by a surviving document is *not* deleted (the
  README-deleted-but-KEP-still-references-the-SIG case is covered by a test).
- [x] **AC5 — move is classified and migrated distinctly.** A same-hash-new-path document
  is reported as a move (not delete+add) in the trace, its chunks and node/edge provenance
  carry the new path, and nothing is orphaned by the move.
- [x] **AC6 — delta equals rebuild (in-memory).** Applying a delta converges the
  in-memory graph and vector stores to the same **node set** (by id+kind), **edge set** (by
  key), **`doc_paths` provenance**, **`sources`**, and **chunk set** (by id) as a full
  `--rebuild` of the new snapshot, and the same **props** on every delta-touched node — the
  consistency contract, proven offline (the live path is AC9). The in-memory stores are the
  equivalence oracle because the deployed backends do not round-trip every field
  byte-identically (Neptune prop encoding; OpenSearch per-op `_id`). *Documented limit:* a
  multiply-contributed prop (a KEP's `title`, set by both `kep.yaml` and its README) reconciles
  last-writer-wins, so a README-only prose edit that changes its H1 while `kep.yaml` is
  unchanged is the one case the incremental path and a rebuild may differ on — out of the
  equivalence scope (deferred: incremental-delta-multicontributed-prop).
- [x] **AC7 — `--rebuild` escape hatch.** `--rebuild` reingests from scratch, clearing
  prior state in both stores, and produces the same end state as a clean first ingest.
- [x] **AC8 — manifest persisted and diffed.** Each run writes the manifest
  (`doc id → content hash`) to S3 **last**, only after both stores are updated; the next
  `--delta` reads it back and diffs against it.
- [x] **AC8b — no-prior-manifest fallback.** A `--delta` against a stack with no stored
  manifest (the state of every already-deployed slice-1–4 stack on its first `--delta`)
  falls back to a full ingest and writes the manifest — it never crashes, and it
  backfills the graph's document-provenance so the next `--delta` reconciles correctly.
- [ ] **AC9 — verified live.** `--delta` runs against the deployed Neptune + OpenSearch on
  a real git-history delta; an orphaned node/chunk from a deleted document is gone and an
  added document's content is retrievable, observed in the trace, then the stack is torn
  down.
- [x] **AC10 — narratable before/after demo on real git history.** A CLI demo driven from
  two real commits of a local corpus checkout prints a legible report — counts before, the
  classified add/change/delete/move set, the orphans removed, counts after — with no
  black-box hop (charter principle 1) and the synthetic/teaching framing surfaced.

## Assumptions

- Technical: chunk IDs are already doc-path-scoped (`{source}/{doc_path}#{ordinal}`), so
  vector delta is per-document by construction (source: `packages/graphrag/src/graphrag/chunk.py:123`).
- Technical: graph `Node`/`Edge` carry no per-document provenance today — only a coarse
  `sources={community,enhancements}` tag, and IDs are normalized keys shared across
  documents — so document-path provenance is added by this slice (source:
  `packages/graphrag/src/graphrag/model.py`, `…/extract.py`).
- Technical: the OpenSearch chunk index **already** indexes a `doc_path` keyword field and
  every chunk already writes it (source: `…/store/opensearch.py:93,166`), so vector
  delete-by-document needs only a new query — no field add, no backfill on slice-2–4
  indices.
- Technical: `VectorStore` has `delete(ids)` (OpenSearch deletes by `chunk_id` terms) but
  `GraphStore` has no delete primitive, so node/edge delete + a delete-by-document on the
  vector side are added here (source: `packages/graphrag/src/graphrag/store/base.py`,
  `…/store/opensearch.py:216`, `…/store/neptune.py`).
- Technical: Neptune does **not** round-trip the graph's set-valued fields — `all_edges()`
  returns only src/dst/kind and `_node_from_result` stringifies non-scalar props (source:
  `…/store/neptune.py:92-110,237-246`), so the new document-provenance must be encoded for
  Neptune to read back (the live reconciliation depends on it; T-Neptune below).
- Technical: the Fargate entrypoint does a full re-ingest only — no manifest, no
  `--delta`/`--rebuild` — and downloads the snapshot from S3 via an injectable `S3Client`
  Protocol (source: `apps/ingestion/entrypoint.py`).
- Technical: git (any recent version; 2.50.1 here) is available for the offline demo
  driver; the real-K8s-excerpt corpus lives at
  `packages/graphrag/tests/fixtures/corpus/{community,enhancements}` (source:
  `git --version`; repo tree).
- Technical: no new top-level runtime dependency — content hashing is stdlib `hashlib`,
  the manifest is stdlib `json`, git is invoked only by the offline demo driver (source:
  `pyproject.toml` dependencies).
- Process: this is slice 5 of brief `graphrag-aws-demo`, constrained by charter
  Architecture-pattern 8, the design doc's *Incremental sync* paragraph and *Corpus
  snapshot strategy* open question, and ADR-0001/0002/0003 (source: `CHARTER.md:166-169`,
  `docs/architecture/graphrag-aws-architecture/design.md:215-219,323-328`).
- Product: delta detection is content-hash-manifest-based (NAT-free, S3-consistent), with
  **move = same-hash-new-path**, and the "real git history" demo drives the CLI from real
  commits of a local corpus checkout rather than introducing an in-VPC git clone (source:
  user confirmation 2026-06-24, reconciling the design's *Corpus snapshot strategy* open
  question).
- Product: orphan removal uses **provenance-set reference counting** (a `doc_paths` set on
  each node/edge) rather than a full graph recompute per delta, to honor "re-ingest only
  the delta" (source: user confirmation 2026-06-24).
- Process: this slice requires a **live deployed** before/after delta verification (AC9),
  not a deferral — the environment has deploy access (source: user confirmation
  2026-06-24).
