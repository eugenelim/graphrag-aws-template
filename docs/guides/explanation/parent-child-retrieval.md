# About parent-child retrieval — match small, answer large

> Why this template matches a **small child chunk** for precision but synthesizes the
> answer from the **larger parent document body**, how that's built on OpenSearch as a
> *nested* document rather than a cross-document join, and when the pattern earns its
> place. This page is for understanding the pattern and the trade-off; the exact
> commands are in *Try it* below.

## The question this page answers

Chunking a corpus for vector search forces one awkward choice: **how big is a chunk?**
Make it small and the embedding is sharp — a focused passage matches a focused
question cleanly — but the chunk you hand the LLM is a fragment, missing the
surrounding context the answer often depends on. Make it large and the LLM gets the
whole story, but the embedding is a blurry average of several topics and matches less
precisely. A single flat chunk has to be *both* the thing you match on and the thing
you read from, so it's sized for a compromise and does neither job well.

The graphrag.com **Parent-Child Retriever** pattern refuses the compromise: it splits
the two jobs across two granularities. The **child** is small, sized purely for match
precision. The **parent** is the whole document, returned in full for synthesis. The
interesting questions are: *what do you match on*, *what do you return*, and *how do
you wire that on OpenSearch without a cross-document join*.

## How it works on this stack

1. **Index parents with nested children.** At ingest, each document becomes one
   OpenSearch **parent document** that holds its child chunks as a `nested` array —
   each child carrying its own `knn_vector` — plus the document's full prose in an
   app-stored `body` field. The child vectors are the *same* vectors the flat index
   already computed (one embed pass, written to both indexes — no extra Bedrock cost).
2. **Match on the child vector during the ANN scan.** A nested k-NN query scores each
   parent by its **best-matching child** (`score_mode: max`) on the **Lucene HNSW**
   engine, the same engine the flat index uses
   ([RFC-0001 §3/§4](../../rfc/0001-notes/aws-feasibility.md)). `inner_hits` surfaces
   *which* child matched, so the trace shows the precise hit.
3. **Return the parent body for synthesis.** The query returns the parent document,
   and synthesis reads its `body` — the whole document, not the matched fragment. So
   the match is precise *and* the context is complete.

## Why it's a nested document, not a `has_child` join

Elasticsearch has a `has_child` query that joins a parent document to separately-indexed
child documents at query time. OpenSearch's k-NN path doesn't lean on that; the verified
mechanism here is a **single nested document** — the children live *inside* the parent,
and the parent body is a top-level field on that same document
([RFC-0001 §3](../../rfc/0001-notes/aws-feasibility.md)). The app stores the parent body
at ingest and reads it back from the hit; there is no cross-document join. Because the
parent *is* the returned unit, a parent whose several children all match still comes back
**once** (scored by its best child), so there's no duplicate-parent dedup to do either.

## Parent-child vs. the flat vector baseline — the contrast to watch

The demo runs the flat [`vector-rag-baseline`](../../specs/vector-rag-baseline/spec.md)
on the same corpus, so you can put the two side by side on the **same question**:

| | Flat vector mode | Parent-child mode |
| --- | --- | --- |
| **What matches** | one chunk's vector | one *child* chunk's vector |
| **What's returned** | that same chunk | the child's whole **parent document body** |
| **Match precision** | tied to chunk size (a compromise) | high — children are sized small |
| **Synthesis context** | the matched fragment only | the complete parent document |

Run a question whose answer sits in a specific passage — *"what does the in-place pod
resize KEP say about its risks?"* — through both. Flat mode returns the matched "Risks
and Mitigations" chunk; parent-child matches that same precise chunk but hands synthesis
the **whole KEP-1287 README**, so the answer also reflects the Summary the risks depend
on. That's the decoupling, made visible.

## How it composes with the permission filter

Parent-child still respects the [`permission-filtered-retrieval`](../../specs/permission-filtered-retrieval/spec.md)
posture: when a persona clearance is supplied, a `visibility` `terms` clause rides the
same nested query as a parent-level filter, composed `AND` with the child match. So a
document above a persona's clearance is never returned — parent-child can only *narrow*.
(A parent's visibility is its document's single composed tier; a document's chunks share
one tier.) The self-query metadata filter is a separate slice and out of scope here —
parent-child composes with the permission filter only.

## When to reach for it

Parent-child retrieval earns its place when your documents are **long enough that a
single chunk can't be both a precise match and enough context** — KEPs, RFCs, design
docs, runbooks — and you'd rather the LLM synthesize over the whole document than over
a fragment. The cost is storage (the parent bodies and the nested children live
alongside the flat index) and a second index to keep populated. The honest limit:
parent = document here, so a very long parent can blow past a model's context window —
at which point you'd reach for an intermediate "section" parent or a summarize-then-cite
step, neither of which this teaching slice ships.

## Try it

Offline (no AWS — the deterministic, **non-semantic** `HashEmbedder` + the in-memory
nested store over the bundled fixture corpus):

```bash
# match a precise child, return the whole parent KEP body for synthesis
graphrag parentchild-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --q "what does the in-place pod resize KEP say about its risks?"

# the flat-vector contrast on the SAME question: one matched chunk, no parent body
graphrag vector-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --q "what does the in-place pod resize KEP say about its risks?"
```

Live (real Titan embedding + Bedrock Claude synthesis over the deployed in-VPC stores,
via the IAM-auth Function URL):

```bash
graphrag parentchild-query \
  --function-url "$QUERY_FUNCTION_URL" \
  --q "what does the in-place pod resize KEP say about its risks?"
```

The trace narrates, in order, **question → matched child(ren) (the precise match) →
returned parent(s) (the full body) → answer** — so you can see exactly which small chunk
the search matched and which whole document synthesis read. No black-box hop.
