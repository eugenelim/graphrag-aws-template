# RFC-0003: Medallion staging for ingestion

- **Status:** Accepted
- **Author:** eugenelim
- **Approver:** eugenelim
- **Date opened:** 2026-06-28
- **Date closed:** 2026-06-28
- **Decision weight:** standard <!-- standard, on three work-loop triggers: structural (new state.py/silver.py/graphdelta.py module boundaries + an ArtifactStore seam), multi-package (packages/graphrag + apps/ingestion + infra IAM), and a new persisted-artifact + IAM-grant surface. Not heavy: no frozen-ADR reversal and the change is reversible (--rebuild stays the reset). -->
- **Related:** RFC-0002 (ingestion pattern axis); ADR-0002 (ephemeral teardown-first topology); ADR-0005 (community detection in Fargate); ADR-0006 (schema-guided LLM extraction guard); `packages/graphrag/src/graphrag/{delta,ingest}.py` (slice 5 incremental delta)

## Reviewer brief

- **Decision:** should we re-shape the ingest pipeline into three persisted stages (Bronze/Silver/Gold) whose per-document derived artifacts are cached by content **and** config, and evolve the manifest into a per-stage `IngestState`?
- **Recommended outcome:** accept.
- **Change if accepted:**
  - A content-**and-config**-addressed Silver cache (chunks+embeddings, and per-doc *candidate* triples) so unchanged docs skip Bedrock on re-ingest, and an embedder/schema change correctly forces recompute.
  - The `Manifest` widens to a versioned `IngestState` (per-doc stage watermark + Silver keys + config fingerprints), backward-compatible with today's v1 envelope.
  - `_reconcile_graph` is lifted to an explicit `GraphDelta` (plan/apply split).
- **Affected surface:** `packages/graphrag` (`delta.py`, `ingest.py`, `embed.py`, `extract_llm.py`, new `state.py`/`silver.py`/`graphdelta.py`); `apps/ingestion` driver; one new S3 prefix + its task IAM grant. No query-Lambda change.
- **Stakes:** reversible — staged code + a cache prefix; `--rebuild` remains the ground-truth reset, and the v1→v2 state upgrade is read-only on old data.
- **Review focus:** (1) is the config-fingerprint correctness fix worth the new state surface; (2) the deliberate *no-Step-Functions* call for D4 under ADR-0002.
- **Not in scope:** parallel/orchestrated execution (Step Functions, multi-task Fargate); making community-detection (Gold-global) incremental; any retrieval-path or query-Lambda change.

## The ask

- **Recommendation (BLUF):** Adopt a Bronze/Silver/Gold staging model for `graphrag` ingestion whose **Silver** per-document artifacts are persisted in S3 and keyed by `content_hash ⊕ config_fingerprint`, widen the manifest into a backward-compatible `IngestState`, and materialise graph mutations as an explicit `GraphDelta`. Keep execution in the single on-demand Fargate task; design Silver to be parallelisable later without adding orchestration now.
- **Why now (SCQA):** *Situation* — slice 5 already ships incremental delta (content-hash manifest, add/change/delete/move classification, provenance-set reconciliation, orphan removal), and ADR-0006 added LLM extraction + Titan embedding to ingestion. *Complication* — the manifest hashes raw bytes only, blind to *how* derived state is produced, and the two expensive derivations land on opposite failure modes (both confirmed by code — see Evidence): (1) **embedding** runs under `--delta`, so a config-only embedder change (Titan v2→v3, unchanged bytes) yields an *empty* delta and silently serves **stale vectors**; (2) **schema-guided extraction** runs on `full`/`rebuild` only with **no per-doc caching** — every rebuild re-LLM-extracts the *entire* corpus, and a schema change is invisible to `--delta`. *Question* — do we make the derived, expensive work first-class staged state, addressed by content *and* configuration?
- **Decisions requested:**

  | ID | Question | Recommendation | Why | Decide by | Reviewer action |
  | --- | --- | --- | --- | --- | --- |
  | D1 | Decouple into Bronze/Silver/Gold with persisted intermediate artifacts? | Yes — persisted, **disposable** (removed on `destroy`) | Only persistence lets a later run *skip* recompute; in-memory cannot | this review | Confirm staged-persistence direction |
  | D2 | Silver cache key = `content_hash ⊕ config_fingerprint`? | Yes (embedder `model_id`+dims, extraction-schema hash) | Closes the confirmed stale-on-config bug; hermeticity precedent | this review | Confirm the key shape |
  | D3 | Widen `Manifest` → versioned `IngestState` (back-compat)? | Yes — extend the existing envelope to v2 | Reuses the repo's own versioned-envelope pattern; `diff_manifests` unchanged | this review | Confirm state evolution |
  | D4 | Single in-process driver, or orchestrated parallel? | Single driver now; Silver as a pure per-doc fn (parallel later) | ADR-0002 teardown-first; ADR-0005 runs ingest work in-task | this review | Rule on *no Step Functions now* |
  | D5 | Explicit `GraphDelta` value, or keep inline reconcile? | Explicit `plan`/`apply` split | Narratable (Charter P1), dry-runnable before Neptune | this review | Confirm the refactor |

## Problem & goals

The fused pipeline (`ingest.py:ingest` / `ingest_delta`) parses the whole corpus on every run and holds all derived state — chunks, vectors, extracted triples, the resolved graph — in memory, writing to the stores and discarding it. There is **no persisted artifact between raw bytes and the live stores**. Three consequences:

1. **Correctness (embedding/delta path):** the manifest hashes raw bytes only (`delta.py:33-51`); `ingest_delta` re-embeds only content-changed docs (`ingest.py:278-312`). A change to the embedder model with unchanged sources produces an empty delta — so **stale vectors** are served until a manual `--rebuild`. The system cannot tell "the inputs changed" from "the way we derive changed."
2. **Cost & freshness (extraction path):** schema-guided extraction runs on `full`/`rebuild` only and re-LLM-extracts the **entire** corpus every time — there is no per-doc cache, so an unchanged document is re-extracted on every rebuild (`apps/ingestion/entrypoint.py:288` — "`MODE=delta` never reaches here"). A schema change is therefore invisible to `--delta` and only takes effect on a full rebuild. Likewise a *moved* file (same bytes, new path) is re-embedded today even though its derived artifact is identical (`delta.py` classifies it a move, but `ingest_delta` re-embeds the move target).
3. **Coupling:** stages cannot run, resume, or scale independently; a crash mid-run restarts from raw parse.

**Goals.**
- A change to inputs **or** derivation configuration invalidates *at least* the affected derived artifacts, preferring over- to under-invalidation (a coarse fingerprint may recompute a little extra; it must never serve stale).
- Unchanged (and moved) documents incur **zero** Bedrock cost on re-ingest — including on a full rebuild.
- The mutation applied to Neptune is inspectable before it is applied (Charter Principle 1).
- Backward-compatible: an existing v1 manifest upgrades without a migration script; `--rebuild` stays the ground-truth reset.

**Non-goals** (could-have-been-goals, deliberately dropped):
- **Parallel/orchestrated execution.** We design Silver to *be* parallelisable but add no Step Functions / multi-task Fargate now (D4) — that is a separate infra decision under ADR-0002.
- **Incremental community detection.** Community summaries (ADR-0005) are a Gold-stage *global* recompute the per-doc Silver cache cannot skip; making *them* incremental is out of scope (mirrors microsoft/graphrag #741).
- **Any retrieval-path change.** The query Lambda and hybrid retrieval are untouched; `delta.py` stays ingest-path-only (PyYAML-free query Lambda unaffected).

## Proposal

Three stages, split along the one fault line that matters: **Silver's per-document work — chunk, embed, *candidate*-extract — is content-addressed and embarrassingly parallel; Gold's global work — resolve, validate-and-ground, mutate — is transactional.** The split is dictated by the code: `extract_schema_guided(docs, graph, …)` (`schema_extract.py:101`) *grounds* candidate triples against the **global** resolved graph, so grounding is inherently a Gold step; only the candidate extraction is per-document.

**Bronze — raw landing.** Parse + hash the corpus (today's `load_corpus` + `manifest_from_docs`), land raw bytes in S3, write the manifest. Unchanged from today except it is named as the stage output. No-NAT, S3-consistent detection (ADR-0002) is preserved.

**Silver — per-document derived artifacts (the new cache).** For each doc, keyed by `content_hash ⊕ config_fingerprint`, persist under `s3://<corpus-bucket>/silver/<key>/`:
- `chunks.json` — `Chunk` list + Titan vectors (`chunk.py` + `embed.py`), keyed by the *embedder* fingerprint;
- `candidates.json` — deterministic edges (`extract.py`, already final per-doc) **plus** ungrounded schema-guided *candidate* triples (`extractor.extract` over the prose body), keyed by the *extraction* fingerprint. Validation and grounding are **not** done here — they need the global graph.

A cache **hit** loads JSON and makes **no Bedrock call**; a miss computes and writes the artifact. Keying on the **content hash** (not doc_id) means a *moved* file reuses Silver verbatim. Folding the **config fingerprint** into the key is the correctness fix: change Titan v2→v3 or edit `EXTRACTION_SCHEMA` and the key changes, forcing recompute; the old artifact remains for rollback.

**Gold — global resolution, grounding + transactional mutation.** Loads Silver `candidates.json` for the full current doc set (cached + freshly computed), runs cross-source `resolve()`, then `validate_triple` + `ground` (`ground.py`) the schema-guided candidates against the global resolved graph, computes a `GraphDelta`, and applies it (Neptune) alongside the vector delta (OpenSearch). The only stage that reads global state — and the only stage that re-runs when a *grounding-affecting* change (e.g. a renamed entity) lands without any doc content change.

Sketch (evolves `delta.py`/`ingest.py`, reusing real types):

```python
# state.py — widened manifest (supersedes the v1 dict)
@dataclass
class DocState:
    content_hash: str                    # Bronze == today's manifest value
    silver_chunks: str | None = None     # Silver vector-artifact key  (hash ⊕ embedder fp)
    silver_candidates: str | None = None # Silver candidate-artifact key (hash ⊕ extraction fp)
    stage: Stage = Stage.BRONZE          # per-doc watermark: bronze | silver | gold

@dataclass
class IngestState:
    docs: dict[str, DocState] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)  # also stamped on each artifact (audit)
    ingested_commit: str | None = None
    version: int = 2
    def as_manifest(self) -> dict[str, str]:        # project back → diff_manifests() reused as-is
        return {k: d.content_hash for k, d in self.docs.items()}
```

```python
# silver.py — content+config-addressed per-document cache (per-doc; NO grounding here)
def materialize_silver(doc, artifacts, embedder, extractor, schema, embedder_fp, extraction_fp) -> SilverArtifact:
    chunk_key, cand_key = f"{embedder_fp}/{doc.content_hash}", f"{extraction_fp}/{doc.content_hash}"
    if artifacts.has(chunk_key) and artifacts.has(cand_key):
        return artifacts.load_silver(doc.doc_id, chunk_key, cand_key)   # hit → no Bedrock
    chunks  = chunk_corpus([doc]); vectors = embedder.embed([c.text for c in chunks])
    # deterministic edges are final per-doc; schema-guided triples are UNGROUNDED candidates here
    candidates = extract([doc]) + list(extractor.extract(doc, schema))
    art = SilverArtifact(doc.doc_id, list(zip(chunks, vectors)), candidates)
    artifacts.write_silver(art, chunk_key, cand_key); return art
```

```python
# graphdelta.py — the mutation as an assertable value (D5)
@dataclass
class GraphDelta:
    upsert_nodes: list[Node]; upsert_edges: list[Edge]
    delete_nodes: list[str];  delete_edges: list[tuple[str, EdgeKind, str]]

def plan_graph_delta(store, scratch, removed_ids) -> GraphDelta: ...  # today's _reconcile_graph, pure
def apply_graph_delta(store, delta) -> int: ...                      # the only mutating step
```

The `config_fingerprint` is computed from `embedder.model_id`+`dimensions` (already exposed, `embed.py:25-36`) and a hash of `EXTRACTION_SCHEMA` (a new `schema_fingerprint()` — the schema is a constant today, `extract_llm.py:114-139`). It is recorded in `IngestState.fingerprints` (which drives invalidation) and also stamped on each Silver artifact for audit (Charter P1). When a fingerprint changes, the affected doc set widens automatically.

**Move classification stays content-hash-based** (`diff_manifests` is reused unchanged), but the Silver key *encodes* the fingerprint (`{fingerprint}/{content_hash}`), so a moved-**and**-config-changed document lands on a **new** key, misses the cache, and is recomputed — even though the diff calls it a pure move. Invalidation is enforced by key construction, not a separate stamp comparison; the stamped fingerprint is audit-only. `as_manifest()` deliberately exposes only `content_hash`, keeping move detection a content concern and fingerprint invalidation a property of the key.

**Migration.** v1 envelope (`{"version":1,"docs":{id:hash}}`) reads as `DocState(content_hash=hash, stage=BRONZE)` with Silver cold → first v2 run recomputes Silver once (an expected, one-time warm-up), then steady-state is incremental. No migration script; `--rebuild` unchanged.

## Options considered

Per decision, MECE along the stated axis; do-nothing always included. Recommended option starred.

**D1 — where derived state lives between runs:**

| Option | Trade-off vs goals | Prior art |
| --- | --- | --- |
| (a) Do-nothing (in-memory fused) | Recompute cost unbounded across runs; correctness bug persists | status quo `ingest.py` |
| (b) In-process stage split, no persistence | Cleaner code, still cannot skip recompute across runs | — |
| ★ (c) Persisted medallion (disposable) | Enables cross-run skip + restart; adds one S3 prefix (removed on `destroy`) | Databricks/MS medallion |

**D2 — what the Silver key covers:**

| Option | Trade-off | Prior art |
| --- | --- | --- |
| (a) Content bytes only | **Confirmed stale-on-config bug** | status quo manifest |
| (b) Content + manual `--rebuild` on config change | Correct only if a human remembers; silent if not | dbt full-refresh — the manual-rebuild analog (c) improves on |
| ★ (c) Content ⊕ config fingerprint | Automatic, exact invalidation; needs a schema fingerprint | Bazel hermeticity; LlamaIndex node+transform hash |

**D3 — state schema evolution:**

| Option | Trade-off | Prior art |
| --- | --- | --- |
| (a) Keep v1 manifest | No per-stage/Silver tracking; D1/D2 impossible | status quo |
| (b) Separate new state file | Two sources of truth to keep consistent | — |
| ★ (c) Extend versioned envelope → v2 | Reuses pattern; `diff_manifests` unchanged; v1 upgrades in | `delta.py:122-137` |

**D4 — stage-execution coordination:**

| Option | Trade-off | Prior art |
| --- | --- | --- |
| (a) Single in-process driver | Simple; no parallelism | status quo Fargate task |
| (b) Orchestrated parallel (Step Functions Map) | Throughput; **adds standing infra** vs ADR-0002 | AWS SFN Map |
| ★ (c) Single driver now, Silver pure per-doc fn | Parallelisable later, zero infra now | ADR-0002 / ADR-0005 |

**D5 — how the mutation set is represented** (axis: from least to most reified — these three exhaust "implicit in control flow → in-memory value → externalised durable record"):

| Option | Trade-off | Prior art |
| --- | --- | --- |
| (a) Implicit / inline `_reconcile_graph` (do-nothing) | Works; not dry-runnable, not narratable as a unit | status quo `ingest.py:159-203` |
| ★ (b) Materialised in-memory `GraphDelta` (plan/apply) | Inspectable before mutation; tiny refactor; no new infra | dbt/Delta MERGE change-set |
| (c) Externalised durable change-log (event stream / table) | Auditable + replayable, but adds storage + a consumer; over-built for one task | event sourcing / CDC log |

## Risks & what would make this wrong

**Pre-mortem (shipped and failed):**
- *Silver cache poisoning* — a partial write leaves a corrupt artifact served as a hit. **Mitigation:** content-addressed keys are immutable; write-then-verify (or write to a temp key, then atomic put); a fingerprint bump sidesteps a poisoned generation entirely.
- *Fingerprint too coarse* — a trivial, output-irrelevant schema edit invalidates all Silver. **Mitigation:** fingerprint the semantically-load-bearing fields (edge kinds/endpoints, model id+dims), not formatting; accept occasional over-invalidation as the safe direction.
- *Cost of warm-up surprises the operator* — first v2 run recomputes all Silver. **Mitigation:** it is exactly one full ingest's cost, narrated in the report; documented as expected.
- *S3 artifact left billing after teardown* — violates ADR-0002. **Mitigation:** Silver prefix lives in the corpus bucket already removed on `destroy`; teardown check covers it.

**Key assumptions (falsifiable):**
- The vector stale-on-config bug is real — *confirmed by code* (Evidence). If it were false, the embedding half of D2 degrades to a perf optimisation.
- Per-document **candidate** extraction and embedding are independent and cacheable — true today: chunking/embedding are per-doc, deterministic extraction is per-doc, and schema-guided *candidate* extraction is per-doc; only *grounding* is global, which is why this RFC places it in Gold rather than caching it in Silver.
- The corpus is large/expensive enough for caching to matter. **This is the weakest assumption** — the demo K8s corpus is small, so the *cost* win is modest today; the dominant present-day benefits are the *correctness* fix (vectors), eliminating the full-rebuild re-LLM-extraction of unchanged docs, and restartability. The cost win grows with LLM-extraction adoption; the RFC does not claim it is large today.

**Drawbacks:** more moving parts (a state schema, an artifact store seam, fingerprints); a new S3 prefix + IAM grant (a known repeat gotcha — a new ingest artifact needs its own key-scoped `grant_put`); the v2 state is larger than the v1 manifest. The Silver cache does **not** make Gold free — community detection still recomputes globally on any membership change.

## Evidence & prior art

- **Spike / de-risk (riskiest assumption) — CONFIRMED by code inspection.** `delta.py` has zero references to `model_id`/schema; `content_hash`/`manifest_from_docs` hash raw bytes only (`delta.py:33-51`); `ingest_delta` derives the recompute set from the content-hash delta alone (`ingest.py:278-312`). So an **embedder** change with unchanged sources ⇒ empty delta ⇒ no re-embed ⇒ **stale vectors** under `--delta`. The **extraction** failure mode is different and equally real: schema-guided extraction is wired to `full`/`rebuild` only with no per-doc cache (`apps/ingestion/entrypoint.py:288`, test `test_entrypoint.py:448` asserts `--delta` invokes the extractor zero times), so it re-LLM-extracts the whole corpus each rebuild and a schema change cannot reach a `--delta` run at all. A live deploy spike was *not* run: both facts are provable from the code, and deploying to confirm would burn Bedrock + teardown time for no added certainty.
- **Repo precedent.** RFC-0002 established the stage taxonomy (Extraction/Resolution/Chunking/Graph build) this RFC layers onto (`rfc/0002:43-55`); Pattern 8 "Incremental delta re-ingest" + `--rebuild` already exist (`CHARTER:171-176`); the versioned-envelope + graceful-fallback pattern is `delta.py:122-137`; ADR-0002 (teardown-first, S3 corpus source, no standing orchestration) and ADR-0005 (ingest work runs in-task) constrain D1/D4; Charter Principle 1 "narratable" (`CHARTER:85-92`) motivates D5.
- **External prior art:**
  - Medallion canon — [Databricks glossary](https://www.databricks.com/glossary/medallion-architecture), [Microsoft Learn](https://learn.microsoft.com/en-us/azure/databricks/lakehouse/medallion): Bronze=raw, Silver=conformed, Gold=consumption-ready; Silver forces upfront canonical-entity decisions (a noted caution).
  - Config-in-the-key — [Bazel remote caching](https://bazel.build/remote/caching) + [hermeticity](https://bazel.build/basics/hermeticity): a cache is safe only when the key covers inputs *and* configuration/toolchain; Bazel's own untracked-external-tool gap is exactly the failure D2 prevents.
  - Incremental ETL + the delete gap — [dbt incremental models](https://docs.getdbt.com/docs/build/incremental-models-overview): recompute only changed rows keyed on `unique_key`; dbt does **not** auto-propagate source deletes — validating an explicit orphan-removal pass (which the repo already has).
  - Closest analogue — [LlamaIndex IngestionPipeline](https://developers.llamaindex.ai/python/examples/ingestion/document_management_pipeline/): hashes **node + transformation**, skips unchanged docs, reprocesses on hash change — a production precedent for D2.
  - GraphRAG incremental is thin — [microsoft/graphrag #741](https://github.com/microsoft/graphrag/issues/741): `append` was *planned, not shipped*, and even with per-doc extraction caching, community summaries need graph-level recompute (motivates the Non-goal). [iText2KG (arXiv 2409.03284)](https://arxiv.org/html/2409.03284v1): academic incremental KG construction validates per-doc extract-then-merge.

## Open questions

1. **Fingerprint granularity for `EXTRACTION_SCHEMA`** — hash the whole schema object, or only edge-kind + endpoint definitions? *Recommended default:* hash the load-bearing fields (kinds/endpoints) plus extractor model id; revisit if over-invalidation bites. · owner: eugenelim · decide-by: spec stage.
2. **Silver retention / GC** — do superseded Silver generations (old fingerprints) get swept, or kept for rollback? *Recommended default:* keep within the ephemeral stack's lifetime (teardown removes all); add a TTL only if S3 cost shows up. · owner: eugenelim · decide-by: spec stage.

(Where the fingerprint is recorded — `IngestState.fingerprints` *and* stamped on each artifact — is settled in the Proposal, not an open question.)

## Follow-on artifacts

On acceptance:
- **ADR** — "Silver artifacts are content-and-config addressed" (records the key shape + the disposability constraint under ADR-0002).
- **Spec** — `docs/specs/medallion-staging/` slicing: (a) `IngestState` v2 + v1→v2 upgrade; (b) `ArtifactStore` seam + `materialize_silver` cache; (c) `GraphDelta` lift-out of `_reconcile_graph`; (d) staged driver + CLI/Fargate wiring + IAM grant; (e) live AC on a Bedrock cache-hit skip and on a fingerprint-bump forcing recompute.
- No `docs/CONVENTIONS.md` change required (this adds no top-level convention).
