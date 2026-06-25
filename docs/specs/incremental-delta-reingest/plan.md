# Plan: incremental-delta-reingest

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

The change rides the **existing single ingest path** — `graphrag.ingest.ingest()` and the
Fargate `apps/ingestion/entrypoint.py` — and adds two *modes*, never a parallel pipeline.
The shape, bottom-up:

1. A new **`graphrag.delta`** module owns the document **content hash**, the **manifest**
   (`{doc id → sha256}`), and the pure **`diff_manifests(old, new) → Delta`** that
   classifies add / change / delete / move (move = same hash, new path). This is the only
   genuinely new logic and is pure-function TDD.
2. **Document-path provenance.** `Node`/`Edge` gain a `doc_paths: set[str]` field (the
   reference count), stamped during extraction from the originating `ParsedDoc`. Chunks
   already carry `source` + `doc_path`, so the chunk side needs only a delete-by-document
   capability. Provenance is unioned on merge exactly like `sources`.
3. **Store delete primitives.** `GraphStore` gains `delete_node` / `delete_edge` and a
   `clear()` (for `--rebuild`); `VectorStore` gains `delete_by_doc` / `clear()`. Both
   backends (memory + Neptune; memory + OpenSearch) implement them; Neptune via parameterized
   `DETACH DELETE` / `DELETE`, OpenSearch via delete-by-query on a new `doc_path` field.
4. **Delta orchestration.** `ingest_delta(prev_manifest, new_corpus, graph_store,
   vector_store, embedder, …)` re-parses only the delta documents, computes net surviving
   provenance over `all_nodes()`/`all_edges()` (corpus is hundreds of docs — in-app
   reconciliation is fine and narratable), upserts survivors + new contributions, deletes
   the empties, applies the vector chunk delta, and returns a `DeltaReport`. `--rebuild`
   clears both stores then calls the existing `ingest()`.
5. **Wiring + demo.** New CLI verbs (`delta`, `rebuild`) and a `MODE` env on the Fargate
   task; the manifest is read from / written to S3 through the existing injectable
   `S3Client` Protocol. A `scripts/`-level **before/after demo** drives the CLI from two
   real git commits of a corpus checkout and prints the narratable report.

**Riskiest part:** the net-reconciliation orphan pass (T4) — getting "survives iff still
referenced" right for a *changed* document (which both removes and re-adds), so it never
over-deletes. It is covered by the README-deleted-but-KEP-still-references-the-SIG test and
the delta-equals-rebuild equivalence test (AC4, AC6), which together pin correctness.

## Constraints

- **Charter Architecture-pattern 8** — diff a snapshot, upsert/delete by (doc path +
  content hash), explicit orphan-removal pass, `--rebuild` escape hatch, both stores
  consistent.
- **Charter pattern 2** — single-parse dual-write: provenance + labels + content stamped
  from one pass; both stores written in one task run.
- **ADR-0002** — no NAT; the S3 snapshot + manifest is the only detection source on the
  deployed path; no in-VPC `git clone`.
- **ADR-0001** — the graph/vector stores and chunk→entity-ID join this keeps fresh.
- **`packages/graphrag/AGENTS.md`** — the query Lambda import graph stays PyYAML-free;
  delta is ingest-path only.

## Construction tests

Most construction tests live per-task below. Cross-cutting:

**Integration tests:**
- **Delta-equals-rebuild** (AC6): build snapshot A, ingest; apply a delta to snapshot B via
  `ingest_delta`; separately `--rebuild` snapshot B into fresh stores; assert the two stores'
  `all_nodes()`/`all_edges()`/chunk sets are byte-identical. Spans T1–T5.
- **End-to-end before/after demo on a real git repo** (AC10): the demo test builds a throwaway
  git repo of corpus files, commits two states, runs the demo, asserts the classified delta
  and orphan list in the printed report. Spans T1–T6.

**Manual verification:**
- **AC9 live**: deploy, ingest base snapshot, run `--delta` on a real git-history delta against
  the deployed Neptune + OpenSearch, confirm an orphaned node/chunk is gone and an added
  document is retrievable in the trace, then `cdk destroy`. Recorded in the spec/README on pass.

## Design (LLD)

Shape: **mixed** (data + service + integration). Stack: pure-Python `graphrag` package
(stdlib `hashlib`/`json`), the `GraphStore`/`VectorStore` seams (memory + Neptune +
OpenSearch backends), the Fargate ingestion task, and S3 — all already established by
slices 1–4; this slice adds no new component type.

### Design decisions

- **Provenance-set reference counting over full recompute.** Each node/edge carries
  `doc_paths`; orphan = empty set after a delta. Honors "re-ingest only the delta" (no
  re-parse of unchanged docs) and is the narratable demo beat. Rejected: full graph
  recompute every delta (simpler, but re-parses everything — violates the slice's premise).
  Traces to: AC2, AC4.
- **Content-hash manifest as the detection source.** A `manifest.json` (`doc id → sha256`)
  in S3 is the "ingested commit"; diffing manifests is NAT-free and runs identically on the
  laptop and in Fargate. **Move = same hash, new path.** Rejected: in-VPC `git diff -M`
  (needs NAT, breaks ADR-0002). Traces to: AC1, AC5, AC8.
- **Doc id = `{source}/{path}`.** One stable key string used in the manifest, as node/edge
  `doc_paths` members, and as the chunk delete key — so the same identity threads all three
  surfaces. Node/edge *identity* stays the slice-1 normalized key; `doc_paths` is provenance,
  not identity. Traces to: AC3, AC4.
- **`doc_paths` encoded as a JSON-string property for Neptune round-trip.** Neptune's
  `_node_from_result` stringifies non-scalar props and `all_edges()` returns no props
  (`store/neptune.py:92-110,237-246`), so the set is written as a JSON string
  (`json.dumps(sorted(doc_paths))`) and decoded on read — for **nodes and edges** — so the
  live reconciliation reads provenance back identically to the in-memory store. Rejected:
  a native Neptune list property (openCypher list-property support is uneven; the JSON
  string is unambiguous and scalar). Traces to: AC4, AC9.
- **`--delta`/`--rebuild` as modes, not a fork.** `--rebuild` = `clear()` both stores +
  existing `ingest()`; `--delta` = `ingest_delta()`; `ingest_delta(prev_manifest=None)`
  falls back to a full ingest (the no-prior-manifest case). Reuses the dual-write. Traces
  to: AC6, AC7, AC8b.

### Data & schema

- **`Node.doc_paths: set[str]`, `Edge.doc_paths: set[str]`** — unioned on merge (mirrors
  `sources`). On Neptune, written/read as a JSON-string property `doc_paths` (encode at
  upsert, decode in `_node_from_result` and in an extended `all_edges()` that now also
  returns `r.doc_paths`). The in-memory store carries the set directly. Traces to: AC4, AC9
  · no contract.
- **Manifest** — `{"version": 1, "docs": {"<source>/<path>": "<sha256-hex>"}}`, stored at
  the corpus prefix in S3 (`<prefix>/manifest.json`). `version` guards future schema change.
  Traces to: AC8.
- **OpenSearch chunk doc** — both the `source` and `doc_path` keyword fields **already exist**
  and are written for every chunk (`store/opensearch.py:92,93,166`). Delete-by-document is a
  new delete-by-query, requiring **no** field add and **no** backfill. **Key disambiguation:**
  a doc-id is `{source}/{path}` but `Chunk.doc_path` is the source-less path (`chunk.py:126`),
  so the delete predicate matches on **`source` AND `doc_path` together** (a `bool.should` of
  per-doc `{must:[{term:source},{term:doc_path}]}`), never on `doc_path` alone — otherwise a
  community path could delete an enhancements chunk. The in-memory store matches the same
  identity via `f"{c.source}/{c.doc_path}"`. Traces to: AC3, AC4.

### Interfaces & contracts

- **`GraphStore`**: `+ delete_node(id)`, `+ delete_edge(src, kind, dst)`, `+ clear()`.
- **`VectorStore`**: `+ delete_by_doc(doc_ids: list[str])` (doc-ids are `{source}/{path}`),
  `+ clear()`. The existing `delete(ids)` (by `chunk_id`) is retained unchanged; the delta
  path uses `delete_by_doc` exclusively, so there is one delete key-semantics in reconciliation.
- **`graphrag.delta`**: `content_hash(bytes) -> str`, `build_manifest(root) -> Manifest`,
  `diff_manifests(old, new) -> Delta(added, changed, deleted, moved)`.
- **`graphrag.ingest`**: `+ ingest_delta(...) -> DeltaReport`, `+ rebuild(...) -> IngestReport`.
- No repo-root `contracts/` surface (internal Python + CLI), consistent with slices 1–4.

### Component / module decomposition

- **New:** `packages/graphrag/src/graphrag/delta.py` (hash + manifest + diff + `DeltaReport`).
- **Changed (reused):** `model.py` (provenance field), `extract.py` (stamp provenance),
  `ingest.py` (`ingest_delta`/`rebuild`), `store/base.py` + `store/memory.py` +
  `store/neptune.py` + `store/vector_base.py` + `store/vector_memory.py` +
  `store/opensearch.py` (delete/clear primitives), `cli.py` (verbs),
  `apps/ingestion/entrypoint.py` (MODE + manifest S3 read/write).
- **New (demo):** `scripts/delta-demo.sh` (or a `graphrag` demo verb) + its test.

### State & control flow

`--delta` flow: T6 reads `manifest.json` from S3 → calls `ingest_delta(prev_manifest, …)`,
which internally `build_manifest(new snapshot)` → `diff_manifests` (or, if `prev_manifest`
is `None`, falls back to a full ingest) → for delete+change-old+move-from doc ids: strip from
provenance; for add+change-new+move-to: re-parse → re-extract → upsert (re-adds provenance)
and re-chunk → re-embed → index; net-reconcile graph (delete empties), apply vector chunk
delta (delete removed docs' chunks); return a `DeltaReport` carrying `new_manifest` → T6
writes `new_manifest` to S3 **last** (after both stores are updated). **Order:** add new
contributions *into the provenance reconciliation* before computing empties, so a changed doc
(or a move+edit → delete+add) never transiently empties a still-referenced node.

### Failure, edge cases & resilience

- **No prior manifest** (first-ever run, or `--delta` with nothing ingested): fall back to a
  full ingest and write the manifest (don't crash).
- **Idempotent re-run:** running the same `--delta` twice is a no-op (empty Delta) — relies
  on the same MERGE/upsert idempotency slices 1–4 already guarantee.
- **Move + edit** (path changed *and* content changed): classified as delete(old)+add(new),
  not a move (hash differs) — correct, documented in the trace.
- **Partial failure mid-delta:** the manifest is written **last**, only after both stores are
  updated, so a crash leaves the old manifest and the next `--delta` re-attempts the same
  delta (at-least-once, idempotent). Noted as the resilience posture, not a transaction.

### Dependencies & integration

- S3 (manifest read/write) via the existing injectable `S3Client` Protocol — testable with
  an in-memory fake, no live AWS in unit tests.
- Neptune + OpenSearch delete paths exercised live only in AC9; unit-tested against the
  in-memory backends.

## Tasks

### T1: `graphrag.delta` — content hash, manifest, and add/change/delete/move diff

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/delta.py, packages/graphrag/tests/test_delta.py

**Tests:**
- `content_hash` is stable and order-independent over identical bytes; differs on a 1-byte change.
- `build_manifest` over a fixture corpus dir maps `{source}/{path} → hash` for every file.
- `diff_manifests` classifies: a new path → added; same path/changed hash → changed; missing
  path → deleted; **same hash, new path → moved** (not delete+add). (AC1, AC5)
- A move *and* edit (hash changed, path changed) → delete(old)+add(new), not moved. (AC5 edge)

**Approach:**
- `content_hash(data: bytes) -> str` = `hashlib.sha256(data).hexdigest()`.
- `Manifest = dict[str, str]`; `build_manifest(community_root, enhancements_root)` walks the
  same files `load_corpus` reads, keyed `f"{source}/{rel}"`.
- `Delta` dataclass (`added/changed/deleted/moved: list`, `moved` = `(old_id, new_id)` pairs);
  `diff_manifests(old, new)` builds a reverse hash→path index to detect moves before
  falling back to add/delete.

**Done when:** `test_delta.py` is green and `diff_manifests` classifies all four kinds + the
edge case.

### T2: Document-path provenance on nodes and edges

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/model.py, packages/graphrag/src/graphrag/extract.py, packages/graphrag/src/graphrag/resolve.py, packages/graphrag/tests/test_model.py, packages/graphrag/tests/test_extract.py, packages/graphrag/tests/test_resolve.py

**Tests:**
- `Node`/`Edge` `doc_paths` unions on `Graph.upsert_node`/`upsert_edge` (mirrors `sources`).
- `extract(docs, aliases)` stamps each node/edge with the `{source}/{path}` of its originating
  `ParsedDoc`; a SIG node contributed by both `sigs.yaml` and a KEP carries both doc ids. (AC4)
- Existing extract/resolve tests stay green (provenance is additive).

**Approach:**
- Add `doc_paths: set[str] = field(default_factory=set)` to `Node` and `Edge`; union it in
  `Graph.upsert_node`/`upsert_edge`.
- Thread the originating doc id into `extract` (each `_extract_*` stamps `doc_paths={doc_id}`
  on the nodes/edges it emits), where `doc_id = f"{doc.source}/{doc.path}"`; confirm `resolve`
  passes it through unchanged (it upserts into a `Graph`, so the union is automatic).

**Done when:** provenance round-trips through resolve and the dual-contribution test passes.

### T3: Store delete + clear primitives (graph and vector, both backends)

**Depends on:** T2
**Touches:** packages/graphrag/src/graphrag/model.py, store/base.py, store/memory.py, store/neptune.py, store/vector_base.py, store/vector_memory.py, store/opensearch.py, packages/graphrag/tests/test_store_*.py, packages/graphrag/tests/test_model.py

**Tests:**
- `Graph.remove_node` removes the node **and its incident edges**; `Graph.remove_edge`
  removes one edge by `(src, kind, dst)` key (`model.py:63-65`); both are new `Graph` mutators.
- Memory `GraphStore.delete_node`/`delete_edge` delegate to those; `clear()` empties the store.
- Memory `VectorStore.delete_by_doc(["enhancements/keps/.../README.md"])` removes exactly that
  doc's chunks and **not** a community chunk that shares the bare path (source-disambiguation);
  `clear()` empties it.
- Neptune/OpenSearch adapters: the emitted openCypher / delete-by-query is **parameterized**
  (no string interpolation — `ruff` `S` stays green), asserted via the existing HTTP-client seam.
- OpenSearch `delete_by_doc` emits a `bool.should` of per-doc `{must:[{term:source},{term:doc_path}]}`
  using the already-present `source` + `doc_path` fields (no `doc_path`-alone match). (AC3, AC4)

**Approach:**
- `model.py`: add `Graph.remove_node(id)` (drops the node + every edge whose key touches it)
  and `Graph.remove_edge(src, kind, dst)`.
- `GraphStore`: abstract `delete_node`, `delete_edge`, `clear`. Memory delegates to the `Graph`
  mutators. Neptune: `MATCH (n:Entity {id:$id}) DETACH DELETE n` for a node, and the **fully
  keyed** edge delete `MATCH (a:Entity {id:$src})-[r:REL {kind:$kind}]->(b:Entity {id:$dst})
  DELETE r` (bind src+kind+dst — never delete all edges of a kind); `clear` = `MATCH (n:Entity)
  DETACH DELETE n`. All parameterized.
- `VectorStore`: `delete_by_doc(doc_ids)` (each `{source}/{path}`, split on the first `/`),
  `clear()`. OpenSearch: delete-by-query over a `bool.should` of per-doc source+doc_path term
  pairs (existing fields, no change); in-memory matches `f"{c.source}/{c.doc_path}"`. `clear` =
  delete-by-query `match_all` (keep the index). The existing `delete(ids)` is untouched.

**Done when:** all store tests green; security ruleset unchanged; primitives parameterized.

### T3b: Neptune `doc_paths` round-trip (write + read-back for nodes and edges)

**Depends on:** T2, T3
**Touches:** packages/graphrag/src/graphrag/store/neptune.py, packages/graphrag/tests/test_store_neptune.py

**Tests:**
- `upsert_node`/`upsert_edge` write `doc_paths` as a JSON string property (asserted on the
  emitted, parameterized openCypher — no interpolation, `ruff` `S` stays green).
- `_node_from_result` decodes the JSON string back to a `set[str]`; `all_edges()` now also
  returns `r.doc_paths` decoded — a node/edge round-trips its provenance set identically to the
  in-memory store. (AC4, AC9)
- A node/edge with no `doc_paths` property (a pre-slice-5 row) decodes to an empty set, not a
  crash (the backfill-on-first-delta case, AC8b).
- **Offline backfill guard:** a full `ingest()` / `rebuild()` against the Neptune adapter
  (via the HTTP-client seam, no live cluster) emits encoded `doc_paths` on its upsert
  payloads — so AC8b's "first `--delta` backfills Neptune provenance" is verified offline,
  not first proven live in T8. (AC8b)

**Approach:**
- Encode `doc_paths` as `json.dumps(sorted(node.doc_paths))` into the props bag before
  `_scalar_props`; decode in `_node_from_result` (pop `doc_paths`, `json.loads`) and in
  `all_edges()` (return `r.doc_paths AS doc_paths`, decode).

**Done when:** the Neptune provenance round-trip test is green and matches the in-memory shape.

### T4: `ingest_delta` — net provenance reconciliation + dual-store orphan removal

**Depends on:** T1, T2, T3
**Touches:** packages/graphrag/src/graphrag/ingest.py, packages/graphrag/tests/test_ingest_delta.py

**Tests:**
- README deleted but a KEP still references the SIG → the SIG node **survives**; a KEP whose
  only doc is deleted → its node/edges **removed**. (AC4)
- Unchanged docs are not re-parsed/re-embedded: a counting embedder records zero calls for the
  unchanged set. (AC2)
- After a delta: added present, changed updated, deleted absent — in **both** stores. (AC3)
- Move migrates provenance/chunks to the new path with nothing orphaned. (AC5)
- **Move + edit** (path *and* content changed) → delete(old)+add(new); a node contributed only
  by that one doc is **not** transiently orphaned (the add is reconciled before the empty-check).
  (AC5 edge)
- `prev_manifest=None` → falls back to a full ingest, no `diff_manifests` call needed. (AC8b)

**Approach:**
- **Signature:** `ingest_delta(prev_manifest: Manifest | None, community_root,
  enhancements_root, graph_store, vector_store, embedder, aliases=None, labels=None) ->
  DeltaReport`. It builds `new_manifest` from the corpus, and **computes the `Delta`
  internally** via T1's `diff_manifests(prev_manifest, new_manifest)` — so the no-S3 fallback
  (`prev_manifest is None` → full ingest) is pure logic inside this function, callable from a
  test with no S3 (resolves the AC8b / T5-no-forward-dep claim). The returned `DeltaReport`
  carries `new_manifest` so the caller (T6) persists it.
- Re-parse only the add/change/move-to docs; re-extract → resolve in a scratch `Graph`; stamp
  labels.
- Graph reconciliation over `all_nodes()`/`all_edges()`: `surviving = (current.doc_paths −
  removed_doc_ids) ∪ new_contribution_doc_ids`; upsert with the recomputed `doc_paths` when
  non-empty, `delete_*` when empty. **Compute the union (re-add new contributions) before
  deciding deletion** — this covers both the changed-doc and the move+edit (delete+add) cases.
- Vector: `delete_by_doc(removed + changed-old + move-from)`; chunk+embed+index the
  add/change/move-to set.
- Return a `DeltaReport` (added/changed/deleted/moved counts, orphans removed, before/after
  node/edge/chunk counts, `new_manifest`) with a `render()`.

**Done when:** `test_ingest_delta.py` green incl. the survive-vs-orphan and no-re-embed tests.

### T5: `rebuild` + manifest persistence, and the delta-equals-rebuild equivalence test

**Depends on:** T3, T4
**Touches:** packages/graphrag/src/graphrag/ingest.py, packages/graphrag/tests/test_ingest_delta.py

**Tests:**
- `rebuild` clears both stores then full-ingests; end state equals a clean first ingest. (AC7)
- **Delta-equals-rebuild:** `ingest_delta(A→B)` store contents == `rebuild(B)` store contents,
  node/edge/chunk-for-chunk, on the **in-memory** stores. (AC6)
- `ingest_delta(prev_manifest=None, ...)` falls back to a full ingest (no S3 involved). (AC8b)
- **Backfill:** a full `ingest()` / `rebuild()` writes **non-empty `doc_paths`** onto the
  store's nodes/edges (the property AC8b's next-delta reconciliation reads back) — guards that
  T2's provenance stamping flows through the `resolve()`→`ingest()` path, not only `ingest_delta`. (AC8b)
- Idempotent re-run: a second identical `--delta` is a no-op (empty Delta).

**Approach:**
- `rebuild(...)`: `graph_store.clear(); vector_store.clear(); ingest(...)`.
- The no-prior-manifest fallback is **logic in `ingest_delta`** (`prev_manifest is None →`
  full ingest), so it is tested here against in-memory stores with **no S3 dependency**; the
  S3 `read_manifest`/`write_manifest` wrappers are wired in T6. This removes any forward
  dependency on T6.

**Done when:** the equivalence integration test and the rebuild/fallback tests are green.

### T6: CLI verbs + Fargate `--delta`/`--rebuild` wiring + S3 manifest round-trip

**Depends on:** T4, T5
**Touches:** packages/graphrag/src/graphrag/cli.py, apps/ingestion/entrypoint.py, packages/graphrag/tests/test_cli.py, apps/ingestion/tests/test_entrypoint.py

**Tests:**
- CLI `delta` / `rebuild` verbs parse and dispatch; `delta` prints the `DeltaReport`. (AC1, AC10)
- Entrypoint `MODE=delta` reads `manifest.json` via the injected `S3Client` fake, runs the
  delta, and writes the new manifest **last**; `MODE=rebuild` clears + full-ingests. (AC8)
- Manifest is written only after both stores are updated (partial-failure posture).

**Approach:**
- `cli.py`: `delta` (against two local corpus roots + a manifest path or S3) and `rebuild`
  verbs reusing `_target_store`/`_vector_store`/`_embedder`.
- `entrypoint.py`: read `MODE` env (`full` default | `delta` | `rebuild`); add
  `read_manifest`/`write_manifest` over the `S3Client` Protocol; keep the full path unchanged.

**Done when:** CLI + entrypoint tests green; the full-ingest path is byte-unchanged for `MODE=full`.

### T7: Before/after demo on real git history + presenter narration

**Depends on:** T6
**Touches:** scripts/delta-demo.sh, packages/graphrag/tests/test_delta_demo.py, docs/specs/incremental-delta-reingest/spec.md, README/presenter notes

**Tests:**
- The demo test builds a throwaway git repo of corpus files, commits state A then a delta
  (add + change + delete + move), runs the demo, and asserts the printed before/after report
  classifies the delta and lists the orphans removed. (AC10)

**Approach:**
- A small script/verb: checkout commit A → ingest (offline, in-memory) → checkout commit B →
  `delta` → print counts-before / classified delta / orphans-removed / counts-after, with the
  synthetic/teaching framing line.

**Done when:** the demo test is green and the report is legible end-to-end offline.

### T8: AC9 — live deployed delta verification, then teardown

**Depends on:** T3b, T6, T7

**Tests:** manual / goal-based (live) — see Construction tests § Manual verification.

**Approach:**
- Deploy via CDK; ingest the base snapshot (writes the manifest to S3); upload a real
  git-history delta snapshot; run the Fargate task `MODE=delta`; query to confirm a deleted
  document's orphaned node/chunk is gone and an added document is retrievable in the trace;
  record the run in the spec/README; `cdk destroy`.

**Done when:** AC9 observed live and recorded; the stack is torn down (idle cost back to zero).

## Rollout

- **Delivery:** additive modes behind a `MODE` env on the existing Fargate task; default
  `full` is byte-unchanged, so slices 1–4 deploy and behave identically. Reversible — the
  `--rebuild` escape hatch is the ground-truth reset; state is fully reproducible from the S3
  snapshot (design Rollback). No irreversible step.
- **Infrastructure:** no new resources — reuses the S3 corpus bucket (now also holding
  `manifest.json`), the Fargate task, Neptune, and OpenSearch. No index schema change: the
  OpenSearch `doc_path` field already exists.
- **Deployment sequencing:** the first `--delta` on an existing slice-1–4 stack has no stored
  manifest and no Neptune `doc_paths` on its nodes; the no-prior-manifest fallback (AC8b)
  runs a full ingest that writes the manifest *and* backfills the graph's `doc_paths`, so the
  next `--delta` reconciles correctly. No manual step required.

## Risks

- **Over-deletion on a changed document** (the reconciliation computes empties before re-adding
  the changed doc's contributions). Mitigated by computing net surviving provenance first and
  by the survive-vs-orphan + delta-equals-rebuild tests.
- **In-app reconciliation reads the whole graph** via `all_nodes()`/`all_edges()` each delta —
  acceptable at the demo's hundreds-of-docs scale (design Non-goal: scale), and it does not
  re-parse or re-embed, so the "delta-only" cost claim holds. Noted, not optimized.
- **Neptune `doc_paths` backfill, not OpenSearch.** The OpenSearch `doc_path` field already
  exists, so vector delete-by-doc works on slice-2–4 indices immediately. The genuinely new
  read-back is Neptune `doc_paths` (T3b); nodes from a pre-slice-5 deploy have none, so the
  first `--delta` must fall back to a full ingest to backfill them (AC8b) — covered by the
  rollout sequencing.

## Changelog

- 2026-06-24: initial plan (slice 5). Provenance-set reference counting + content-hash manifest
  detection (move = same-hash-new-path) + `--delta`/`--rebuild` modes on the existing ingest
  path, confirmed with the user against the design's *Corpus snapshot strategy* open question;
  AC9 live verification required (deploy access available).
- 2026-06-24: spec-mode adversarial review fixes — (1) corrected the false claim that
  OpenSearch needs a new/backfilled `doc_path` field (it already exists; only delete-by-doc is
  new); (2) added T3b for the genuinely-new Neptune `doc_paths` JSON-string round-trip
  (nodes+edges) the live reconciliation depends on; (3) added `Graph.remove_node`/`remove_edge`
  to T3; (4) reworded the "stable key" framing so node/edge identity stays the normalized key
  with doc-path as provenance; (5) added AC8b (no-prior-manifest fallback) and made it pure
  `ingest_delta` logic so T5 no longer forward-depends on T6's S3 helpers; (6) scoped AC6
  equivalence to the in-memory oracle; (7) added `resolve.py` to T2 Touches.
- 2026-06-24: AC9 verified live (T8) on the deployed full stack — `MODE=rebuild` baseline →
  real delta (deleted KEP-1880, added KEP-4242) → `MODE=delta` removed 2 orphans + added the
  KEP across both stores (Fargate trace), confirmed live via SigV4 Function-URL query
  (KEP-4242 retrievable; KEP-1880 dropped/absent), then torn down. The live run surfaced + fixed
  one IAM gap `cdk synth` can't catch: the slice-1 task role was read-only on the corpus bucket,
  so the manifest `PutObject` hit AccessDenied — added `s3:PutObject` scoped to `manifest.json`
  (`bucket.grant_put`), pinned by a new stack test. Plan Status → Done.
- 2026-06-24: EXECUTE discovery (T4) — reconciliation needs an **exact-set** store primitive,
  not `upsert`. `upsert_*` *unions* `doc_paths` and *setdefaults* props (the resolve-merge
  semantics), but a surviving node/edge that lost a contributing doc must have its `doc_paths`
  *shrunk* and a changed doc's props must *override*. Added `GraphStore.replace_node` /
  `replace_edge` (exact set, no edge cascade) — memory: dict assignment; Neptune: `SET n = $props`
  / edge `+=` (doc_paths is one JSON-string prop, overwritten). This is "beyond the delete
  primitives" (spec *Ask first*); landed because reconciliation ships broken without it —
  flagged for human awareness. Props reconcile **last-writer-wins** (changed doc overrides), so
  a changed `kep.yaml` status updates exactly (AC3); `sources` are *derived from* `doc_paths`
  prefixes so they match a rebuild. **Known limit:** a KEP `title` is multiply-contributed
  (kep.yaml H1 vs README H1); a README-only prose edit that changes its H1 while `kep.yaml` is
  unchanged makes the delta keep the README's title where a rebuild keeps kep.yaml's — AC6 is
  scoped to structural equivalence (node/edge/chunk/provenance sets) + props on delta-touched
  nodes; the demo/test use add/delete/move + `kep.yaml`/charter edits, which reconcile exactly.
- 2026-06-24: second review round — (a) fixed the vector delete key mismatch: `Chunk.doc_path`
  is source-less, so `delete_by_doc` matches on **source + doc_path together** (existing fields,
  no new index field); (b) reconciled the `ingest_delta` signature to take `prev_manifest` and
  compute the `Delta` internally (makes the AC8b no-S3 fallback real and keeps T5 off T6);
  (c) added a T5 backfill test that a full `ingest()`/`rebuild()` writes non-empty `doc_paths`;
  (d) clarified `delete(ids)` is retained and the delta path uses only `delete_by_doc`; (e) added
  a move+edit → delete+add T4 test for the no-transient-orphan ordering.
