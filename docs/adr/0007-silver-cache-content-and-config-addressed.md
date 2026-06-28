# ADR-0007: Silver cache addressing — content-and-config over content-only

- **Status:** Accepted
- **Date:** 2026-06-28
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** RFC-0003 (medallion staging — the accepted proposal this records); ADR-0002 (ephemeral teardown-first topology); ADR-0006 (schema-guided LLM extraction guard)

## Context

RFC-0003 (Accepted) introduces a Silver stage that persists per-document derived
artifacts — chunks+embeddings and ungrounded candidate triples — so unchanged
documents skip the expensive Bedrock work on re-ingest. The artifact's S3 key is
the cache key, and the question this ADR settles is **what that key covers**.

The constraints in force at the time of this decision:

- The current ingest manifest hashes **raw document bytes only** (`delta.py:33-51`);
  the delta path is blind to *how* derived state is produced. A change to the
  embedder model (`embed.py`) or the extraction schema (`extract_llm.py`,
  `EXTRACTION_SCHEMA`) with unchanged source bytes produces an empty delta and
  serves **stale vectors** under `--delta` (confirmed by code inspection in RFC-0003).
- The embedder already exposes `model_id` + `dimensions` (`embed.py:25-36`); the
  extraction schema is a module constant with no version field today.
- ADR-0002 mandates an **ephemeral, teardown-first** topology: `destroy` must
  remove every billable resource. Any persisted artifact must be disposable, not
  retained infrastructure state.
- Grounding of schema-guided candidate triples is a **global** operation
  (`extract_schema_guided(docs, graph, …)` grounds against the resolved graph),
  so it cannot be cached per-document — only candidate extraction can.

## Decision

**We will key each Silver per-document artifact by its content hash combined with
a configuration fingerprint** — `silver/{config_fingerprint}/{content_hash}/…` —
where the fingerprint is derived from the embedder (`model_id` + `dimensions`) for
the chunks/vectors artifact and from a hash of `EXTRACTION_SCHEMA` for the
candidate-triples artifact.

Specifically:

- A Silver cache **hit** requires the artifact to exist at the key built from the
  *current* content hash **and** the *current* fingerprint. A change to either the
  bytes or the configuration lands on a new key, misses, and recomputes.
- Invalidation is enforced by **key construction**, not a separate comparison; the
  fingerprint is additionally **stamped on the artifact** for audit (Charter
  Principle 1) and recorded in `IngestState.fingerprints`.
- Silver holds only per-document work (chunk, embed, *candidate*-extract).
  Validation and grounding of candidates against the global graph remain a **Gold**
  responsibility — Silver is never grounded.
- Silver artifacts are written under the existing **corpus bucket** prefix that
  `destroy` already removes, so they inherit ADR-0002's disposability. They are a
  cache, never a system of record; `--rebuild` remains the ground-truth reset.

## Decision drivers

- **Correctness** — a configuration change must never silently serve stale derived
  data. This is the discriminating driver.
- **Cost** — unchanged (and moved) documents must incur zero Bedrock cost on
  re-ingest, including on a full rebuild.
- **Disposability** — the artifact store must respect ADR-0002 teardown-first.
- **Reuse** — prefer extending existing seams (`Embedder`, the versioned manifest
  envelope) over net-new machinery.

## Consequences

**Positive:**
- A change to inputs **or** derivation configuration invalidates exactly the
  affected artifacts automatically — closes the stale-vector bug without operator
  discipline.
- Unchanged and *moved* documents (same content hash) reuse Silver verbatim; only
  `added ∪ changed ∪ fingerprint-stale` documents recompute.
- A superseded fingerprint generation is left in place, giving a cheap rollback
  path within the stack's lifetime.

**Negative:**
- A new S3 prefix and its **own key-scoped IAM `grant_put`** on the ingest task
  (a recurring gotcha — a new ingest artifact needs its own grant).
- Fingerprint **granularity must be tuned**: too coarse and a cosmetic schema edit
  recomputes everything. We accept over- to under-invalidation as the safe
  direction.
- `IngestState` is larger than the v1 manifest (per-doc Silver keys + fingerprints).

**Neutral / to revisit:**
- Grounding stays global in Gold; this ADR deliberately does **not** make
  community detection or grounding incremental (mirrors microsoft/graphrag #741).
- Silver retention/GC is left to the stack lifetime (teardown removes all); a TTL
  is a later concern only if S3 cost shows up.

## Confirmation

The RFC-0003 follow-on spec (`docs/specs/medallion-staging/`) carries the
acceptance criteria that confirm conformance:

- a re-ingest of an unchanged corpus performs **zero** Bedrock embed/extract calls
  (cache-hit skip), and
- a fingerprint bump (embedder model or `EXTRACTION_SCHEMA` change) forces recompute
  of the affected artifacts.

These are live acceptance criteria, run against the deployed task.

## Alternatives considered

- **Content-only key (status quo manifest).** Rejected on the *correctness* driver:
  a config change with unchanged bytes yields an empty delta and serves stale
  vectors — the bug this decision exists to close.
- **Content-only key + manual `--rebuild` on config change.** Rejected on
  *correctness*: it relies on a human remembering to rebuild after every model or
  schema change, and fails silently when they don't. `--rebuild` is retained as an
  escape hatch, not the invalidation mechanism.
- **Doc-id-keyed cache (instead of content-hash).** Rejected on *cost*: a moved
  file (same bytes, new path) would miss and recompute even though its derived
  artifact is identical; content-addressing makes a move a cache hit.

## References

- RFC-0003: Medallion staging for ingestion (`docs/rfc/0003-medallion-staging.md`).
- External prior art surveyed in RFC-0003: Bazel hermeticity (config in the cache
  key), LlamaIndex IngestionPipeline (node + transformation hashing), dbt
  incremental models.
