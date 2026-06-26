# Spec: parent-child-retrieval

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [Charter — Pattern coverage table, *Parent-Child Retriever* row](../../CHARTER.md#pattern-coverage-against-the-graphragcom-catalog) (the coverage contract this slice ships), [RFC-0001 feasibility note §3](../../rfc/0001-notes/aws-feasibility.md) (nested `knn_vector` child vectors match while the parent doc is scored/returned, VERIFIED on this stack — and the load-bearing caveat: **it is nested-doc, not an Elasticsearch `has_child` cross-doc join — the app stores and fetches the parent body**), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (reuses the vector embedder + `Synthesizer` seam + retrieval-trace posture), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (rides the existing in-VPC query Lambda behind the IAM-auth Function URL; the nested index lands on the same OpenSearch domain, teardown-first; adds no billable resource), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python)
- **Brief:** [`docs/product/briefs/graphrag-pattern-catalog.md`](../../product/briefs/graphrag-pattern-catalog.md)
- **Contract:** none (new internal Python interfaces + an additive `mode` value on the existing in-VPC Function URL; no repo-root `contracts/` API surface, consistent with the sibling pattern slices)
- **Shape:** mixed

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> The **Parent-Child Retriever** pattern from the [graphrag.com](https://graphrag.com)
> catalog, implemented on OpenSearch — **small child chunks carry the vectors for precise
> matching; the larger parent document body is returned for context-complete synthesis.**
> A flat chunk index forces one tradeoff: a small chunk matches precisely but truncates the
> context handed to the LLM, while a large chunk gives context but dilutes the match vector.
> Parent-child **decouples** the two — the match happens on a small child chunk's vector,
> the context comes from that child's whole parent document. On OpenSearch this is a
> **nested `knn_vector`**: each parent document holds its child chunks as a nested array,
> the k-NN scan matches a child vector **during** ANN, and the parent document — including
> its app-stored `body` — is the unit scored and returned (RFC-0001 §3). It is **not** an
> Elasticsearch `has_child` cross-document join; the app stores the parent body on the
> parent document at ingest and reads it back from the hit — the verified mechanism, named
> as such. `Depends on:` the vector slice ([`vector-rag-baseline`](../vector-rag-baseline/spec.md))
> and the permission slice ([`permission-filtered-retrieval`](../permission-filtered-retrieval/spec.md))
> — it reuses the chunk pipeline (`chunk_corpus`, the `{source}/{doc_path}` parent key, the
> `entity_ids`/`visibility` metadata), the Titan embedder, the `BedrockClaudeSynthesizer`
> Converse posture, the in-VPC query Lambda + IAM-auth Function URL, and the slice-4
> visibility `terms` filter (a parent-level `bool.filter` composed AND with the nested child
> match). It ships as an
> **additive new retrieval mode on a new nested index** — the flat `graphrag-chunks` index
> and the vector / hybrid / self-query / permission modes are untouched, so the demo can run
> the **same question** through flat `vector` mode and `parentchild` mode side by side and
> *see* the context difference.

## Objective

A solution architect evaluating GraphRAG needs to *see* the **parent-child** pattern: a
user asks a question whose answer lives in a specific passage — *"what does the KEP-1880
README say about its rollout plan?"* — and the system matches the **precise child chunk**
(the small, focused passage that embeds cleanly) but synthesizes the answer from the
**whole parent document body** (so the LLM has the surrounding context the answer depends
on, not just the matched fragment). This slice delivers it on the same stores and query
path as the other retrieval modes.

The load-bearing engineering point is *what the vector matches versus what synthesis
reads*. A flat chunk index makes a single chunk serve both jobs, so it is sized for a
compromise: small enough to match precisely, large enough to carry context — and it does
neither well. Parent-child splits the jobs across two granularities. The **child** is sized
purely for match precision (a section/window of prose, one embedding vector). The **parent**
is the whole document, returned in full for synthesis. On OpenSearch the two live in **one
nested document**: the parent document carries its children as a `nested` field, each child
with its own `knn_vector`, and the parent's full prose in an app-stored `body` field. A
nested k-NN query scores the parent by its **best-matching child** (`score_mode: max`) and
returns the parent document — `inner_hits` names *which* child matched, so the trace shows
both the precise match and the complete context. The match runs **during** the ANN scan on
the **Lucene HNSW** engine the index already uses (RFC-0001 §3/§4). Because the parent
document *is* the returned unit, there is no cross-document join and no result duplication to
dedup — the `has_child` caveat (RFC-0001 §3) does not bite.

The pattern **composes with the permission filter** (the slice-4 visibility `terms`
clause rides the same nested query, so parent-child never returns a document above a
persona's clearance — it can only narrow). It threads through a dedicated **parent-child
retrieval mode**, and the result carries a **trace** naming the matched child, the returned
parent, and the body the answer was synthesized from — so the mechanism is narratable, never
a black-box hop. The whole path runs **offline by default** (a deterministic `HashEmbedder`
+ an in-memory nested store + an offline synthesizer) for credential-free CI and a laptop
demo, and **live** against the deployed OpenSearch domain + Bedrock through the existing
query Lambda.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule, even under time pressure.

### Always do

- **Match on the child vector DURING the nested ANN scan; synthesize over the parent body.**
  The nested k-NN query scores a parent by its best-matching child vector (`score_mode:
  max`) on the **Lucene HNSW** engine (the index method the deployed domain already uses,
  RFC-0001 §3/§4) and returns the parent document; synthesis reads the parent's `body`, not
  the matched child's text. This decoupling is the slice's load-bearing property.
- **Store the parent body on the parent document at ingest; read it back from the hit.**
  The parent body is an **app-stored** top-level field on the same nested document that
  carries the children — never an Elasticsearch `has_child` cross-document join (RFC-0001 §3
  caveat). One write puts the body in; one nested search reads the body and the matched child
  back out.
- **Group children into parents by the existing `{source}/{doc_path}` key.** A parent is a
  document; its children are that document's `chunk_corpus` chunks (each a section/window),
  grouped by the source-qualified doc id that the chunk id already embeds
  (`{source}/{path}#{ordinal}`). The parent inherits the document's `entity_ids` and its
  composed `visibility` tier (a document's chunks share one tier).
- **Reuse the chunk embeddings — embed each child exactly once.** The parent-child index is
  populated from the **same** `chunk_corpus` + Titan-embed pass that writes the flat index;
  child vectors are the chunk vectors, computed once and written to both indexes (no second
  Bedrock embedding pass, no extra cost).
- **Compose the parent-child query with the permission (visibility) filter.** When a
  clearance is applied, the visibility `terms` clause rides the same nested query as a
  **parent-level `bool.filter`** (a sibling of the `nested` `knn` clause, on the parent's
  `visibility` field — distinct from the child-vector HNSW scan), so parent-child can only
  *narrow* — a document above a persona's clearance is never returned. The fail-closed
  `None`-vs-empty clearance semantics (slice 4) are preserved.
- **Pair the OpenSearch nested store with an in-memory equivalent.** The in-memory
  `ParentChildStore` scores each parent by its best child's cosine and applies the identical
  visibility predicate, so the offline backend returns the same parent hit set — the slice-4
  backend-identical invariant (`packages/graphrag/AGENTS.md`).
- **Treat the question as untrusted data at the Claude boundary.** Reuse the
  `BedrockClaudeSynthesizer` posture: the question + parent bodies ride Converse `messages`
  **as data** (never the `system` block), the `system` block carries the defensive
  untrusted-data directive (OWASP LLM01/LLM08), `maxTokens` is bounded, the client is the
  default botocore-chain client over TLS, and the answer is display-only.
- **Reuse the existing query Lambda + Function URL for the live path.** Parent-child is
  dispatched by an **additive, backward-compatible** `mode` value on the existing IAM-auth
  Function URL (`"hybrid"` default | … | `"parentchild"`) — no new endpoint, no new ingress —
  and the parent-child import graph stays **PyYAML-free** so it bundles in the
  `Code.from_asset` Lambda.
- **Keep teardown a feature** (charter principle 4): the nested index lands on the **same**
  OpenSearch domain at `create_index` on a fresh deploy (teardown-first); the slice adds
  **no** billable resource and **no** standing cost.

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** The nested store is request-body
  DSL over the same SigV4/HTTPS plumbing the flat OpenSearch adapter uses — no new
  dependency; reach for any other client only with sign-off, recorded in
  `packages/graphrag/AGENTS.md`.
- **Changing the parent granularity** (parent = document; child = section/window chunk) —
  e.g. a section-level parent, or a sliding-window parent — is a teaching-surface decision,
  not an implementation detail.
- **Pinning or changing the synthesis model id away from the default**, or changing the
  parent-child k-NN engine away from the index's Lucene HNSW.
- **Changing the Function-URL request/response contract beyond the additive `mode` value, or
  the parent-child result/trace schema once a consumer depends on it.**

### Never do

- **Never run the parent-child match as a cross-document `has_child` join.** The verified
  mechanism is a single nested document whose parent body is app-stored and read back; a
  cross-doc join is the explicitly-named non-mechanism (RFC-0001 §3).
- **Never synthesize from the matched child's text instead of the parent body.** The whole
  point is precise child match → complete parent-body context; synthesizing over the child
  fragment is the flat-baseline behavior this slice is contrasting against.
- **Never string-interpolate the query vector, `k`, or a filter value into a path or query
  string** — always the request-body nested `knn` / `terms` clauses (the `neptune.py` /
  `opensearch.py` parameterization posture; `ruff` `S` ruleset stays enabled).
- **Never let the parent-child query widen visibility.** It composes `AND` with the persona's
  clearance and can only narrow — a parent-child hit never re-admits a document above
  clearance.
- **Never re-embed the children for the parent-child index** — the chunk vectors are reused
  from the single embed pass (cost + drift discipline; charter pattern 2).
- **Never add a new top-level directory or module boundary** beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces
  (AGENTS.md: top-level directories need an RFC). New code lands as modules inside those (the
  new nested store is a module under the existing `packages/graphrag/src/graphrag/store/`).
- **Never let the parent-child import graph `import yaml` at Lambda module load** — the
  existing `sys.modules` guard test is extended to the parent-child modules.
- **Never expose a public, unauthenticated endpoint or weaken the Function URL below
  IAM-auth.**

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1 — TDD.** Pure functions over the parent-child data model: grouping chunks into
  parents by `{source}/{doc_path}`, assembling the parent body, inheriting `entity_ids` /
  `visibility`, and the `ParentDoc` / `ParentHit` value types are deterministic and trivially
  unit-tested; no store, no network; `import graphrag.parentchild` pulls in no `yaml`.
- **AC2 — TDD + goal-based mapping check.** A static check asserts the nested mapping declares
  `children` as `type: nested` with a `knn_vector` child whose method is `engine: "lucene"`
  (HNSW) and a Lucene-supported `space_type`; a `search` test asserts the adapter issues a
  **nested** `knn` query over `children.vector` with `score_mode: "max"` and `inner_hits`
  (parameterized — asserted via the adapter's mock HTTP client), composes the visibility
  `terms` filter as a top-level `bool.filter`, excludes the child vectors from `_source`, and
  returns parent hits carrying the parent body + the matched child; the in-memory store
  returns the same sorted parent hit set (backend-identical).
- **AC3 — TDD + narratability check.** Over the fixture, `parentchild_query` matches a child
  and returns its parent body; `synthesize` is called with the **parent body** as the context
  chunk (not the child text); the trace renders, in order, **question → matched child(ren)
  (precise) → returned parent(s) (full body) → answer**. With a clearance, a document above
  clearance is excluded (composition AND; fail-closed `None`-vs-empty preserved).
- **AC4 — TDD.** The full-ingest dual-write groups the embedded chunks into parents and writes
  the nested index from the **same** parse+embed pass (no second embed call — asserted by a
  spy/mock embedder invoked once); gated on `OPENSEARCH_ENDPOINT` / an injected store; a parent
  carries the document's full body, ordered children, `entity_ids`, and `visibility`.
- **AC5 — TDD.** `graphrag parentchild-query` runs offline (in-memory nested store from the
  fixture corpus + `HashEmbedder` + offline synthesizer) and prints the ordered trace,
  labeling the embedder/synthesizer non-semantic; `--bedrock` switches to the Titan embedder +
  Bedrock Claude synthesis; `--function-url` builds a SigV4 POST whose **body** carries
  `mode: "parentchild"`.
- **AC6 — TDD with mock.** With the store, embedder, and synthesizer mocked, `lambda_handler`
  with `mode="parentchild"` runs the path end-to-end and returns the trace envelope; an unknown
  `mode` is a client error; the over-long-question guard and the generic sanitized error
  envelope apply as for hybrid; a `sys.modules` assertion proves the parent-child import graph
  stays PyYAML-free; **no Neptune store is built on this branch** (parent-child is vector-only).
- **AC7 — goal-based (`cdk synth` + `aws_cdk.assertions.Template`), CDK-env-gated.** The nested
  index is an **app-side** addition (a new index on the existing domain at `create_index`), not
  CDK: the synthesized stack adds **no** new resource and **no** new IAM statement, the query
  Lambda's Bedrock grant still scopes the synthesis model (Converse) with no wildcard
  `Resource`, the path adds **no** Neptune statement, and the Budgets value is asserted
  **unchanged at the literal `150`**.
- **AC8 — goal-based (parent-child showcase set + explanation doc).** A `parentchild_queries`
  section holds **≥3** queries, each labeled with the expected matched child, the returned
  parent, and the contrast against flat `vector` mode (same question, fuller context); a
  loader/test asserts it parses and every named parent doc / entity resolves in the fixture
  corpus. A doc under `docs/guides/` walks the parent-child path and states the contrast
  (precise child match → complete parent-body context; one nested document, not a `has_child`
  join).
- **AC9 — live deploy + parent-child smoke (active end-to-end).** Against the deployed stack
  (corpus dual-written so the nested index is populated), a SigV4-signed `mode: parentchild`
  call matches a child **live on OpenSearch with the nested ANN on Lucene**, returns the trace
  (matched child + returned parent body) and a Claude answer synthesized from the parent body;
  a contrasting flat `mode: vector` call on the **same question** shows the smaller
  matched-chunk context; a third call with a **non-default persona/clearance** asserts the live
  parent hits exclude above-clearance documents (the visibility `bool.filter` composes AND with the
  child match — narrow-only). Then the stack is destroyed (teardown-first).

Gates: `ruff` (lint+format, `S` security ruleset), `mypy` (typecheck), `pytest`
(tests). Already wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [x] **AC1 — Parent-child data model + grouping (pure, PyYAML-free).** A
  `graphrag.parentchild` module (query-side) and a `graphrag.store.parentchild_base` module
  declare the value types: `ParentDoc(parent_id, source, doc_path, heading, entity_ids,
  visibility, body, children)` where each child carries `child_id`, `heading`, `text`, and its
  embedding vector; `ParentHit(parent, score, matched_child)`. `group_into_parents(embedded_chunks,
  bodies)` groups chunks by the source-qualified `{source}/{doc_path}` parent key (the prefix the
  chunk id already embeds), orders children by ordinal, sets the parent body from `bodies` (the
  document's full prose; a parent key absent from `bodies` is a **loud `ValueError`**, never a
  silent empty body), inherits the document's `entity_ids` and `visibility` (a document's chunks
  share one composed tier), and sets the parent `heading` to its **first child's heading** (the
  ordinal-0 section heading — a stable parent label; the parent represents the whole document and is
  cited by `doc_path`, not by a single section). The modules import **no `yaml`** (importable by the
  query Lambda). *(TDD)*
- [x] **AC2 — Nested `knn_vector` index + parent-child store seam (child match DURING ANN).**
  `_parentchild_mapping(dimensions)` declares `children` as `type: nested` whose `vector` is a
  `knn_vector` with method `engine: "lucene"` (HNSW) and a Lucene-supported `space_type`
  (`cosinesimil`, OpenSearch 2.11), plus the parent-level `parent_id`/`source`/`doc_path`/
  `entity_ids`/`visibility` (keyword) and `body` (text). `ParentChildStore.search(vector, k, *,
  allowed_labels)` (ABC + `OpenSearchParentChildStore` + `MemoryParentChildStore`) issues a nested
  `knn` query over `children.vector` with `score_mode: "max"` and `inner_hits` (the matched child),
  composes the slice-4 visibility `terms` clause as a top-level `bool.filter` when a clearance is
  applied (`allowed_labels=None` ⇒ no clause/unrestricted; an **empty** set ⇒ a `terms` clause
  matching nothing — the fail-closed permission semantics), excludes `children.vector` from
  `_source`, and returns `ParentHit`s carrying the parent body + the matched child. Query vector,
  `k`, and filter values ride the request body, never interpolated. The in-memory store scores each
  parent by its **best child's** cosine and applies the identical visibility predicate. Offline,
  the suite pins this in two halves — the in-memory store's real best-child ranking + visibility
  predicate, and the OpenSearch adapter's request body + hit parse against a mock HTTP client
  (a mock cannot exercise real HNSW ANN); the **full set-equality parity** (same parent-id set +
  same matched child per parent on the fixture-sized corpus, where HNSW ANN ≈ exact cosine) is the
  **live AC9 check**, matching the metadata-filtering precedent. The parent document *is* the
  returned unit, so there is no cross-document join and no duplicate-parent dedup needed (RFC-0001
  §3). *(TDD + goal-based mapping check)*
- [x] **AC3 — Parent-child orchestration with a trace; synthesizes over the parent body.**
  `parentchild_query(question, *, store, embedder, synthesizer, k, clearance=None)` embeds the
  question, runs `store.search` (threading `clearance.allowed`), and synthesizes over the returned
  **parent bodies** (each parent body is the `Synthesizer` context, **not** the matched child's
  text), returning a `ParentChildResult` whose `.render()` narrates, in order, **question →
  matched child(ren) (the precise match, with score) → returned parent(s) (the full body returned
  for context) → answer**. When a `clearance` is supplied the visibility filter composes AND (a
  document above clearance is excluded) and the fail-closed clearance semantics of AC2 hold through
  the orchestrator — parent-child never re-admits an above-clearance document. *(TDD +
  narratability check)*
- [x] **AC4 — Ingest dual-writes the parent-child index from one embed pass.** The full-ingest
  Fargate path (`apps/ingestion/entrypoint.py`), when `OPENSEARCH_ENDPOINT` (or an injected store)
  is present, groups the **already-embedded** chunks into parents (`group_into_parents`) and writes
  them to the nested index — reusing the **same** Titan embeddings the flat dual-write uses (the
  child embedding runs **exactly once**, asserted via a counting/mock embedder), so the two indexes
  cannot diverge and no extra Bedrock cost is incurred (charter pattern 2). The parent carries the
  document's full body, its ordered children (with vectors), `entity_ids`, and the composed
  `visibility`. *(TDD)*
- [x] **AC5 — CLI verb `parentchild-query`, offline by default, live via SigV4.**
  `graphrag parentchild-query --q "<text>"` runs **offline** (in-memory nested store from the
  fixture corpus + `HashEmbedder` + offline synthesizer) and prints the ordered trace, labeling the
  embedder/synthesizer **non-semantic**. `--bedrock` switches to `BedrockTitanEmbedder` + Bedrock
  Claude synthesis. `--function-url <url>` switches to the **thin live client** — a SigV4-signed
  (`service=lambda`) HTTPS POST of `{"question": …, "mode": "parentchild"}` (the persona rides the
  body when set) whose **signature covers the body** — and renders the returned trace; a non-2xx
  raises with the body. *(TDD)*
- [x] **AC6 — In-VPC query Lambda parent-child dispatch, PyYAML-free, sanitized.**
  `lambda_handler` reads the optional `mode` and on `"parentchild"` builds the live
  `OpenSearchParentChildStore` + `BedrockTitanEmbedder` + `BedrockClaudeSynthesizer` from the
  execution role (the optional persona resolves a clearance fail-closed), runs `parentchild_query`,
  and returns the trace envelope (matched child, returned parent(s), answer, citations, trace). An
  **unknown mode** is a client error; the **over-long-question** guard and the **generic sanitized
  error envelope** (correlation id, no internal endpoint/ARN/stack detail) apply exactly as for
  hybrid. The path builds **no Neptune store** (parent-child is vector-only — the same posture and
  grants as the self-query branch, `query_lambda.py`). The parent-child import graph stays **PyYAML-free** (the existing
  `sys.modules` guard is extended to the parent-child modules). Exercised with the store, embedder,
  and synthesizer **mocked** (no network); reuses the **same** `parentchild_query` the CLI uses.
  *(TDD with mock; live in AC9)*
- [x] **AC7 — IaC unchanged: new index is app-side, no new resource, no widened grant, cost
  held.** The nested index is created at `create_index` on the existing OpenSearch domain (in
  `store/parentchild_opensearch.py`), not CDK. The parent-child Lambda path uses the **same grants
  as the hybrid/self-query path** — the already-granted synthesis-model `bedrock:Converse`, the
  Titan embed grant, and the existing OpenSearch data-access — and **adds no Neptune statement**
  (parent-child never touches the graph store). A synth assertion confirms `cdk synth` adds **no**
  new resource and **no** new IAM statement for the parent-child path: the query Lambda's Bedrock
  grant still scopes the synthesis model with **no wildcard `Resource`**, and the Budgets value is
  asserted **unchanged at the literal `150`**. Per ADR-0002. *(goal-based synth, CDK-env-gated)*
- [x] **AC8 — Parent-child showcase set + the parent-child teaching framing.** A
  `parentchild_queries` section in the showcase `queries.yaml` holds **≥3** queries, each labeled
  with the expected **matched child**, the **returned parent**, and the **contrast** against flat
  `vector` mode (the same question returns a fuller context under parent-child); a loader/test
  asserts it parses and every named parent doc / entity resolves in the fixture corpus. A doc under
  `docs/guides/` walks the parent-child path with the exact CLI commands and **states the
  contrast** — the child is sized for *match precision*, the parent for *synthesis context*; the
  match runs during the nested ANN on the Lucene engine; the parent body is one nested document's
  app-stored field, **not** a `has_child` join — so a watcher can state when parent-child retrieval
  helps. *(goal-based)*
- [x] **AC9 — Live deploy + parent-child smoke (in-VPC).** Against the deployed stack with the
  corpus dual-written (the nested index populated), a SigV4-signed `mode: parentchild` call matches
  a child **live on OpenSearch with the nested ANN on the Lucene engine**, returns the trace
  (matched child + the returned parent body) and a Bedrock Claude answer **synthesized from the
  parent body**. A second + third call pair `mode: parentchild` with a **non-default
  persona/clearance** and assert the live parent hits **exclude above-clearance documents** —
  proving the visibility `terms` clause (a parent-level `bool.filter`, sibling of the child-vector
  match) composes AND with the nested child match, narrow-only. The flat-vs-parent-child *context*
  contrast is visible within the parentchild trace itself (small matched child + large returned
  parent body) and exercised directly offline (`vector-query` vs `parentchild-query` + the showcase);
  the Function URL has no standalone `vector` mode, so the dedicated flat contrast is the offline
  check, not a separate live mode. Then the stack is destroyed (teardown-first). **Verified live
  (2026-06-26):** deployed `GraphragSlice1` to `us-east-1` (`CREATE_COMPLETE`), Fargate `MODE=full`
  dual-write — graph 22 nodes / 28 edges / 6 cross-source merges; vector 13 chunks; **parent-child 6
  parents** on the new `graphrag-parents` nested index, all from **one** Titan embed pass. A
  `mode: parentchild` call matched **kep-1287 README#1 "Risks and Mitigations"** (real cosine 0.7838
  on the **live Lucene nested ANN**, `score_mode:max` + `inner_hits`) and returned the **whole
  KEP-1287 parent body** (444 chars, 2 child chunks); Claude synthesized over the parent body,
  surfacing the feature-gate/rollout context *beyond* the matched risks fragment, cited by parent
  `doc_path`. The same question under `public-reader` left the restricted kep-1287 parent **absent**
  (4 public parents) and under `maintainer` returned it **rank 1** — the visibility filter composes
  AND with the child match, narrow-only. Then `scripts/destroy.sh` (teardown-first; no billable
  resource remains). Full trace in
  [`deployment-and-verification.md`](../../architecture/deployment-and-verification.md). *(live
  smoke)*

## Assumptions

- Technical: runtime is Python 3.11+; runtime deps stay `pyyaml` + `boto3>=1.35`, infra extra
  is `aws-cdk-lib`, dev is `pytest`/`ruff` (with the `S` ruleset)/`mypy`; this slice adds **no**
  runtime dependency (source: `pyproject.toml`; `packages/graphrag/AGENTS.md`).
- Technical: the deployed OpenSearch domain runs the k-NN index on the **Lucene HNSW** engine
  (`cosinesimil`, dim 256), switched there by the metadata-filtering slice; nested `knn_vector`
  child vectors that match while the parent doc is scored/returned is VERIFIED on this stack, and
  the parent document is the returned unit so no cross-document `has_child` join and no
  duplicate-parent dedup is needed (source: `store/opensearch.py:84-110` `_knn_mapping`; RFC-0001
  §3; user confirmation 2026-06-25).
- Technical: the parent body is **app-stored** — written as a top-level `body` field on the same
  nested parent document at ingest and read back from the hit `_source` — not an Elasticsearch
  `has_child` cross-doc join (source: RFC-0001 §3 caveat; user confirmation 2026-06-25).
- Technical: each chunk already carries the parent key `{source}/{doc_path}` (the chunk id is
  `{source}/{path}#{ordinal}`) plus owning `entity_ids` and a composed `visibility` tier, so a
  parent groups its chunks by that key and inherits the document's tier (source: `chunk.py:50,123`;
  `chunk.py` `Chunk` fields).
- Technical: the parent-child index is populated from the same `chunk_corpus` + Titan-embed pass
  that writes the flat index; the child vectors are the chunk vectors, computed once and written to
  both indexes — no second embed call, no extra Bedrock cost (source:
  `apps/ingestion/entrypoint.py:_vector_dual_write`; charter pattern 2; user confirmation
  2026-06-25).
- Technical: the in-memory `ParentChildStore` mirrors the OpenSearch nested adapter (scores a
  parent by its best child's cosine, applies the identical visibility predicate) so the offline
  backend returns the same parent hit set — the slice-4 backend-identical invariant (source:
  `store/vector_memory.py`; `packages/graphrag/AGENTS.md`).
- Technical: synthesis reuses `BedrockClaudeSynthesizer.synthesize(question, context_chunks,
  graph_facts)` — the parent bodies are passed as the context (wrapped as the `VectorHit` shape the
  synthesizer reads), so Claude sees the full parent context, not the matched child fragment
  (source: `synthesize.py:81`; `query_lambda.py`).
- Technical: the live parent-child path **reuses the existing in-VPC query Lambda + the IAM-auth
  Function URL**, dispatched by the additive back-compat `mode` field; the Lambda's IAM already
  grants `bedrock:Converse` (synthesis), the Titan embed grant, and OpenSearch data-access, so the
  slice adds **no new infra resource or IAM statement** and builds **no Neptune store** on this
  branch (source: `query_lambda.py:99-107,198-224`; `apps/infra/stacks/graphrag_stack.py`).
- Technical: the nested index lands on the existing domain at `create_index` on a fresh deploy
  (teardown-first); incremental delta re-ingest of the parent-child index is out of scope (the flat
  index handles delta — slice 5; the parent-child index is (re)built on full ingest / `--rebuild`),
  named as a future extension (source: `apps/ingestion/entrypoint.py:run` MODE=full; user
  confirmation 2026-06-25).
- Technical: the offline CLI/test path uses the deterministic non-semantic `HashEmbedder` + an
  offline synthesizer (labeled non-semantic), exactly as the vector/self-query offline paths do
  (source: `embed.py:44` `HashEmbedder`; `cli.py:_cmd_selfquery_query` offline branch).
- Product: the audience is a solution architect evaluating the *parent-child* pattern; the slice
  ends at child-match + parent-body return + synthesis over the parent body + the trace + the
  contrast framing against the flat baseline; **parent = the document, child = the section/window
  chunk** (source: charter coverage table; brief Scope; user confirmation 2026-06-25).
- Product: parent-child ships as an **additive new retrieval mode on a new nested index** alongside
  the untouched flat `graphrag-chunks` index, so the demo runs the same question through flat
  `vector` and `parentchild` modes side by side; the self-query metadata filter is **out of scope**
  for this slice (parent-child composes with the permission filter only) (source: user confirmation
  2026-06-25).
- Product: a parent's `visibility` is its document's single composed tier (a document's chunks
  share one tier; no mixed-tier document is expected in the K8s corpus) (source: user confirmation
  2026-06-25).
- Process: no new ADR — the nested `knn_vector` mechanism is verified by RFC-0001 §3, the topology
  is pinned by ADR-0002, and IaC by ADR-0003; the nested index shape + module design are
  slice-level LLD in `plan.md` (source: `docs/rfc/0001-notes/aws-feasibility.md` §3; user
  confirmation 2026-06-25).
- Process: full work-loop mode — security boundary (an untrusted question routed to an LLM
  synthesizer; OpenSearch network I/O; an IAM-auth public Function URL) and structural (a new nested
  index + new modules/store + a Function-URL `mode` extension); constrained by the charter coverage
  table + RFC-0001 §3 + ADR-0001/0002/0003 (source: `docs/CONVENTIONS.md` risk triggers; brief Spec
  map row `parent-child-retrieval`).
- Process: the live AC (AC9) is run when AWS access is available (live deploy is available in this
  environment), else deferred with a backlog anchor created atomically (source: user auto-memory
  `live-deploy-available`; the metadata-filtering AC9 precedent).

## Changelog

- 2026-06-25 — Spec authored. Parent-Child Retriever pattern: small child chunks carry the
  vectors for precise matching (nested `knn_vector`, matched during the ANN scan on the Lucene
  engine), the larger parent document body is app-stored on the same nested document and returned
  for context-complete synthesis (not an Elasticsearch `has_child` cross-doc join). Ships as an
  additive new retrieval mode on a new nested index alongside the untouched flat baseline; reuses
  the chunk pipeline + the single Titan embed pass (children embedded once, written to both
  indexes); composes with the permission filter; rides the existing query Lambda via an additive
  `mode: parentchild` (no new infra); offline runs via `HashEmbedder` + the in-memory
  backend-identical nested store.
- 2026-06-26 — Implemented and shipped. AC1–AC8 met offline (full gates green: ruff/mypy/pytest,
  435+ tests). New `parentchild.py` (`group_into_parents` + `parentchild_query` +
  `ParentChildResult`) + `store/parentchild_{base,opensearch,memory}.py` (nested `knn_vector` index,
  `score_mode:max` + `inner_hits`, parent-level visibility filter, in-memory best-child cosine), the
  embed-once ingest dual-write of the nested index, the `parentchild-query` CLI verb, the additive
  `mode: parentchild` query-Lambda dispatch (vector-only, no Neptune), the showcase set + explanation
  doc, and the infra synth test (no new resource/grant, Budgets 150). No new dependency, no new infra
  resource. Adversarial + security review clean (review fixes: best-child `-inf` seed; trace label;
  `k`-clamp hardening deferred to backlog as cross-cutting). AC2 wording scoped offline parity to the
  two halves (memory ranking + adapter body/parse) with full set-equality verified live.
- 2026-06-26 — **AC9 verified live** (the deferral never opened). Deployed `GraphragSlice1`, dual-wrote
  the corpus (graph 22 nodes / 28 edges / 6 merges; vector 13 chunks; **parent-child 6 parents** on
  the new nested index, one embed pass), ran live `mode: parentchild` Function-URL calls (precise
  child match on the live Lucene nested ANN → whole parent body → Claude answer; public-reader vs
  maintainer compose proving the visibility filter ANDs with the child match, narrow-only), then
  destroyed the stack. All 9 ACs met.
