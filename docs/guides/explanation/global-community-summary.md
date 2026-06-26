# About global community summary — answering questions about the whole corpus

> Why this template detects **communities** over the entity graph and summarizes each
> one so it can answer **corpus-wide** questions the seed-and-expand hybrid structurally
> can't, where the clustering runs (the on-demand Fargate ingest task, **not** a standing
> Neptune Analytics service), and the two honest divergences this slice carries. This
> page is for understanding the pattern and its trade-offs; the exact commands are in
> *Try it* below.

## The question this page answers

The seed-and-expand hybrid ([ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md))
answers **local** questions: it finds seed entities — from a semantic hit or by linking
a name in the question — and expands their neighborhood. That works when the question
*has* a seed. But a question like *"what are the major areas of work across all the
SIGs, and how do they relate?"* has **no seed**: the answer isn't a neighborhood, it's
the **shape of the whole corpus**. Ask the hybrid and it has nothing to expand from.

The graphrag.com **Global Community Summary** pattern (Microsoft GraphRAG's *global*
search) answers exactly this class. The idea: partition the entity graph into
**communities** (clusters of densely-connected entities), write one LLM **summary** per
community, and answer a corpus-wide question by **map-reducing** over those summaries —
asking each community what it contributes, then combining the contributions. The
interesting questions are: *how do you find the communities*, *where does that compute
run*, and *how do you keep corpus-wide summaries from leaking across a permission
boundary*.

## How it works on this stack

1. **Detect communities at ingest, in the Fargate task.** After the entity graph is
   written, the **same on-demand Fargate ingest task** reads the graph back, builds an
   undirected graph of the entities, and partitions it with **Louvain** (via `networkx`,
   with a pinned seed so the partition reproduces). No standing service is stood up —
   detection is a transient phase of an already-transient task
   ([ADR-0005](../../adr/0005-community-detection-in-fargate-louvain.md)).
2. **Summarize each community via Bedrock.** For each community, the task feeds its
   member entities + the relationships among them to Bedrock Claude (the same
   `Synthesizer` seam the hybrid uses) and stores the result as a `Community` node in the
   **existing** Neptune cluster, tagged with its member ids, its size, and its composed
   visibility tier. Each member entity is also stamped with its `communityId`.
3. **Answer corpus-wide questions by map-reduce.** At query time (`mode: global` on the
   same in-VPC query Lambda), `global_query` reads the community summaries, runs a **map**
   step — asking each community what it contributes to the question, dropping the ones
   that contribute nothing — then a **reduce** step that combines the survivors into the
   final grounded answer, with a trace naming every community considered, each map
   verdict, and the citations (community ids + the member documents).

## The two honest divergences

This slice carries two divergences it **states** rather than hides (charter principle 2):

- **Louvain, not Leiden.** Microsoft GraphRAG uses the **Leiden** algorithm; this
  template uses **Louvain**. The reason is deliberate: the managed AWS alternative
  (Amazon Neptune Analytics) ships **Louvain**, so using Louvain here makes the
  *self-compute-in-Fargate* path and the *managed-service* path an apples-to-apples
  comparison — same algorithm, different compute location. True Leiden would need an
  external `leidenalg`/`igraph` step (a C-extension dependency); it's documented as the
  route to Microsoft-GraphRAG parity if an adopter needs it, not built here
  ([RFC-0001 §1](../../rfc/0001-notes/aws-feasibility.md),
  [ADR-0005](../../adr/0005-community-detection-in-fargate-louvain.md)).
- **In the Fargate task, not Neptune Analytics.** Neptune Analytics offers community
  detection as a managed service, but it's a **separate, standing, in-memory** service —
  exactly the standing footprint the teardown-first cost posture forbids
  ([ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md), charter principle 4). So
  detection runs in the ingest task and writes results back to the existing cluster;
  Neptune Analytics is documented as the managed alternative (same algorithm) for an
  adopter who wants scale over ephemerality.

## The permission boundary — why a corpus-wide summary is gated *whole*

A community summary is generated over **all** its member entities, so it can **blend
visibility tiers** — a single summary might describe a public KEP and an internal one
together. That makes the summary a corpus-wide aggregation **leak vector**: handing it to
a low-clearance persona would leak the restricted content through the summary, even
though the persona can't see the underlying entity.

So each community is tagged with its **composed (most-restrictive) member tier**
(`compose` of its members' visibilities — an unlabeled member composes as `public`, the
deliberate teaching default), and at query time a community summary is served **only if
the persona's clearance dominates that tier** — fail-closed, applied **before** the map
step so an above-clearance community never reaches the synthesizer, the trace, the
title, or the citations. A summary is served **whole or not at all**, never partially
redacted. (As everywhere in this demo, the visibility labels are a *synthetic teaching
stand-in for an ACL, never real authz* — charter principle 5.) One consequence worth
knowing: communities are recomputed only on a **full ingest / `--rebuild`**, so a
visibility-label change applied by an incremental delta needs a full re-ingest to
refresh the community tiers — a named residual
(`global-community-summary-delta-tier-refresh`), not a silent gap.

## Try it

Offline (no AWS — in-memory graph → Louvain → in-memory community store + the
deterministic, non-semantic offline synthesizer):

```bash
# Print the detected community partition + per-community summaries.
graphrag detect-communities \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements

# Answer a CORPUS-WIDE question by map-reduce over the community summaries.
graphrag global-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --q "What are the major areas of work across all the SIGs, and how do they relate?"

# Same question under a low-clearance persona — above-clearance communities drop out.
graphrag global-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --persona public-reader \
  --q "Summarize the breadth of KEPs and the SIGs that own them."
```

The offline synthesizer is **non-semantic** (structural demo only); the real semantic
answer is the live path. Live, the query rides the deployed query Lambda's Function URL:

```bash
graphrag global-query --function-url "$FUNCTION_URL" \
  --community ... --enhancements ... \
  --q "What are the major areas of work across all the SIGs?"
```

## When the pattern earns its place

Reach for global community summary when the questions you need to answer are about the
**corpus as a whole** — its themes, its breadth, how its areas relate — rather than
about a specific entity or passage. Those are the questions where vector retrieval
returns a scattered handful of chunks and the seed-and-expand hybrid has no seed. It is
*not* a replacement for local retrieval: a question with a clear entity or a specific
passage is still better served by the hybrid or parent-child. The honest trade is that
the community summaries are computed **once at ingest** (so they cost a Bedrock call per
community up front and can go stale relative to a delta), in exchange for answering a
question class the other modes structurally cannot.

## See also

- [ADR-0005 — community detection in the Fargate task, Louvain not Neptune Analytics](../../adr/0005-community-detection-in-fargate-louvain.md)
- [The seed-and-expand hybrid (ADR-0001)](../../adr/0001-hybrid-orchestration-seed-and-expand.md) — the *local* counterpart this complements
- [Charter — pattern coverage table + the Louvain-vs-Leiden honesty note](../../CHARTER.md#pattern-coverage-against-the-graphragcom-catalog)
- [The spec](../../specs/global-community-summary/spec.md) and [the plan](../../specs/global-community-summary/plan.md)
