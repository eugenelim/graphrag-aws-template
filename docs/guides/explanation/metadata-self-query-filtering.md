# About metadata self-query filtering — letting the question pick the filter

> Why this template lets the LLM read a **structured filter** out of a
> natural-language question and apply it to vector retrieval, why that filter has to
> run *during* the ANN scan rather than after it, and how it differs from the fixed
> permission filter the demo already ships. This page is for understanding the
> pattern and the trade-off; the exact commands are in *Try it* below.

## The question this page answers

Plain vector search ranks the *whole* corpus by similarity and hands back the top
`k`. But a lot of real questions carry an implicit *scope* the embedding alone
can't honour — "in **the enhancements repo**, which KEPs does **SIG Node** own?"
names two structured constraints (`source = enhancements`, an entity) on top of the
semantic intent. The graphrag.com **Metadata Filtering / Self-Query** pattern is the
move that turns those words into a filter the retriever can enforce. The interesting
questions are: *who writes the filter* (the LLM, from the question), *how do you keep
that safe*, and *where in the search does the filter run*.

## How it works on this stack

1. **Extract.** A Bedrock Claude call (the Converse API) reads the question and emits
   a small JSON object over a **fixed, declared field schema** — `source` (the
   cross-source repo, `community` | `enhancements`) and `entity_ids` (a SIG / KEP /
   person). The model produces *only* a filter; it never writes a query.
2. **Validate (the governance boundary).** The model's output is re-validated
   deterministically before it touches OpenSearch: a `source` value is kept only if
   it is in the closed enum; an `entity_ids` value is resolved through the same
   controlled-vocabulary normalizers the rest of the pipeline uses, to a graph-node
   id. An **undeclared field, or a value that doesn't resolve, is dropped and
   recorded** — never bound as free-form model text. This is the same
   "the-LLM-proposes, the-code-disposes" split the governed templates path uses for
   parameters: the model's authority is bounded by construction.
3. **Filter during the ANN scan.** The validated filter rides the OpenSearch request
   body as a parameterized `terms` clause and is applied **while** the k-NN search
   runs — so the search returns `k` results *from the qualifying subset*, not `k`
   results that then get pruned.

## Why "during the scan" is the load-bearing detail

A **post-filter** ranks the whole corpus, takes the top `k`, and *then* drops the
rows that don't match the filter — so a constrained question can come back with
**fewer than `k`** relevant chunks, or none, and recall quietly degrades. An
**efficient filter** applied during the approximate-nearest-neighbour scan restricts
the candidate set first and still fills `k` from what qualifies. AWS verifies this
efficient-during-ANN behaviour on the **Lucene / Faiss HNSW** engines, not on the
`nmslib` engine the index was first built on
([RFC-0001 §4](../../rfc/0001-notes/aws-feasibility.md)). So this slice switches the
k-NN index method to **Lucene HNSW** — which, as a bonus, closes the same
post-filter recall caveat the permission filter carried on the old engine.

## Self-query vs. the permission filter — two filters, different origins

The demo already filters vector retrieval once, in the
[`permission-filtered-retrieval`](../../specs/permission-filtered-retrieval/spec.md)
slice. The two are worth holding side by side because they share a seam but differ
in *where the filter comes from*:

| | Permission filter | Self-query filter |
| --- | --- | --- |
| **Origin** | **Fixed** — the persona's clearance, supplied with the request | **Question-derived** — the LLM reads it out of the question |
| **Field** | `visibility` (a synthetic ACL stand-in) | `source`, `entity_ids` (corpus metadata) |
| **Purpose** | *who may see what* (an authz stand-in) | *what the question is scoped to* (relevance) |
| **Where it runs** | A `terms` clause during the ANN scan | A `terms` clause during the ANN scan |

They **compose**: both apply on the same k-NN call as independent clauses, so a
self-query filter can only ever *narrow* a persona's results — it never re-admits a
chunk the clearance excluded. (And the fail-closed clearance semantics hold through
the merge: a `None` clearance is unrestricted, but an *empty* clearance still
matches nothing, regardless of any self-query filter.)

## When to reach for it

Self-query metadata filtering earns its place when your corpus carries **structured
metadata users actually scope by** in natural language — a source, a product area, a
date range, an owner — and you'd otherwise be relying on the embedding to "just know"
the scope. It is *not* an authorization mechanism (that's the permission filter's
job, and a real ACL would be enforced server-side, not extracted from the prompt).
The honest limit: the filter is only as good as the declared schema and the
extractor; the schema is fixed and reviewed precisely so the model can't invent a
field or smuggle a raw query through.

## Try it

Offline (no AWS — the deterministic, **non-semantic** rule extractor + the in-memory
store over the bundled fixture corpus):

```bash
# vector mode: source + entity self-query, narrowed to SIG Network's enhancement KEPs
graphrag selfquery-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --q "in the enhancements repo, which KEPs are owned by SIG Network?"

# hybrid mode: the self-query filter is threaded into the seed-and-expand vector LEG
graphrag selfquery-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --mode hybrid \
  --q "in the enhancements repo, what does SIG Node own?"
```

Live (real Bedrock extraction + the deployed in-VPC stores, via the IAM-auth Function
URL — the rule extractor is offline-only, so the semantic extraction is the live
path):

```bash
graphrag selfquery-query \
  --function-url "$QUERY_FUNCTION_URL" \
  --q "in the enhancements repo, which KEPs are owned by SIG Network?"
```

The trace narrates, in order, **question → extracted filter (with what was dropped)
→ filtered hits → answer** — so you can see exactly which structured filter the model
produced, that it was validated against the schema, and that the search ran over only
the qualifying chunks. No black-box hop.
