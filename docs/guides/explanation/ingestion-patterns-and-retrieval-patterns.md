# About ingestion patterns and retrieval patterns — two spectrums, not one

> Most GraphRAG writing treats *retrieval* as a menu of patterns you choose
> among, and *ingestion* as a fixed pipeline you run once. This page argues
> the asymmetry is a mistake: ingestion is a spectrum of choices too, and the
> sharpest of them — **how entities and edges leave the text** — decides what
> every retrieval pattern can later return. This is the conceptual frame; for
> the routing decision that comes first, read
> [choosing what to ingest](choosing-what-to-ingest.md), and for the commands,
> the [three-mode demo](../tutorials/three-mode-demo.md).

## The question this page answers

The [graphrag.com](https://graphrag.com) catalog gave the field a shared
vocabulary for **retrieval** patterns — Basic Retriever, Graph-Enhanced Vector
Search, Cypher Templates, Text2Cypher, Global Community Summary, and the rest.
A team evaluating GraphRAG learns to ask *"which retrieval pattern fits my
question classes?"* and to weigh the governed end (templates) against the
flexible end (text2cypher). That is exactly the right instinct — applied to only
half the system.

Because the catalog is a *retrieval* catalog, it leaves ingestion as a single
implicit point. But ingestion makes choices just as consequential, and they bind
*harder*: a retrieval pattern is swappable at read time, while an ingestion
decision is **baked into the graph every retrieval pattern then reads**. If you
never extracted an edge, no retrieval mode — however clever — can return it. So
the question this page adds is the mirror of the retrieval one: *which ingestion
patterns fit my corpus, and which one is the default I'm silently accepting?*

## Two coverage tables, one discipline

This template tracks both axes as named, status-marked patterns. The
[charter](../../CHARTER.md) carries two coverage tables side by side:

- **Retrieval pattern coverage** — the graphrag.com catalog, translated to
  Neptune openCypher + OpenSearch + Bedrock. Vector RAG, seed-and-expand hybrid,
  pattern matching, metadata self-query, parent-child, the governed-vs-risky
  Cypher pair, global community summary.
- **Ingestion pattern coverage** — *our* taxonomy (graphrag.com does not
  enumerate ingestion), adapted from the
  [LlamaIndex `PropertyGraphIndex`](https://developers.llamaindex.ai/python/examples/property_graph/property_graph_basic/)
  extractor families and the
  [Microsoft GraphRAG indexing stages](https://microsoft.github.io/graphrag/index/default_dataflow/),
  organized by ingestion *stage*: Extraction, Resolution, Chunking, Graph build.

Both tables use the same honest glyph contract — `Have` (on the committed demo
path), `Planned` (committed, not yet built), `Backlog` (named, not scheduled) —
so "what this template does" and "what it merely names" never blur. Establishing
the ingestion table as a first-class peer of the retrieval table is
[RFC-0002](../../rfc/0002-ingestion-pattern-axis.md)'s whole purpose.

## The sharpest ingestion axis: extraction strategy

Of the ingestion stages, the one that most decides whether the graph earns its
keep is **extraction** — *how do entities and edges leave the text?* It runs as a
spectrum, exactly the way the retrieval governed-vs-risky pair does:

| | **Deterministic** | **Schema-guided LLM** | **Free-form LLM** |
| --- | --- | --- | --- |
| How edges leave the text | Rules: front-matter, YAML, labeled-field regex | An LLM, held to a **fixed entity/edge schema** | An LLM, **unconstrained** |
| Prior art | LlamaIndex `ImplicitPathExtractor` | `SchemaLLMPathExtractor` | `SimpleLLMPathExtractor` |
| Wins when | Your entities have stable IDs and edges live in structure | Edges live in **prose**, but you know the shape you want | You don't know the shape, and want coverage over consistency |
| Cost | Free, perfectly narratable, zero hallucination | One bounded LLM hop you must keep narratable | A larger LLM surface; the least checkable output |
| In this template | ✅ **Have** — `graph-ingestion-resolution` | ◔ **Planned** — `schema-guided-extraction` | ○ Backlog |

The instinct to recognize is that this is the **same shape** as the retrieval
catalog's governed-vs-risky pair. Deterministic extraction is to schema-guided
extraction what `opencypher-templates` is to `text2opencypher-guarded`: the
governed, auditable end against the flexible, model-authored end. Schema-guided
sits deliberately at the *safer* end of the LLM half — a closed schema gives the
output a ground truth to check against, the way a template grammar does — while
free-form is the riskier end you reach for only when you can't name the shape in
advance.

### Where the default bites

This template's deterministic extractor is excellent on the part of the corpus
it was built for: Kubernetes SIG slugs and GitHub `@handles` are a controlled
vocabulary, so it normalizes mentions to canonical IDs and merges on collision
with **no model and no hallucination** — every edge is explainable as "these two
rows produced the same ID." That is the right default, and it is why the charter
keeps it as the baseline.

But it reads prose *only* through labeled fields — a line like `**Authors:**
…` — and routes the rest of a prose body to the **vector** index, not the graph.
So the relationships stated in the *narrative* of a SIG README or a KEP's
`Motivation` section — *this SIG collaborates with that one*, *this KEP
supersedes that one*, *this proposal depends on that one* — are **structurally
unreachable** to the deterministic graph. Not missed by a weak rule; unreachable
by design, because no labeled field carries them.

That is precisely the gap schema-guided LLM extraction fills, and why it is the
one ingestion pattern this template commits to building beyond the deterministic
baseline: an LLM pass over those same prose bodies, **constrained to a fixed
schema**, surfaces the inter-entity edges the rules cannot reach — on the *same*
corpus, so the contrast is a fair comparison, not a bigger dataset.

## Keeping an ingest-time LLM hop honest

The charter's first principle is *narratable over magical* — but narratable does
**not** mean *no LLM*. An LLM hop is narratable when its inputs, outputs, and
decision are inspectable in a trace, exactly as the query-time Bedrock synthesis
and text2openCypher hops already are. The bar is the trace, not the absence of a
model.

For an extraction hop that bar is higher than for a query-time one, because a bad
extracted edge is baked into the graph rather than wrong just once. So the
schema-guided pattern is held to four checks
([ADR-0006](../../adr/0006-schema-guided-llm-extraction-guard.md)):

1. **A closed schema.** The model may emit only triples whose relationship and
   endpoint kinds are in a fixed, small set — the ground truth a presenter can
   show, and the analog of a validated template grammar. Anything off-schema is
   rejected, never written.
2. **Entity grounding.** Both ends of an extracted relationship must resolve to
   an entity the deterministic pass *already* found. The model may relate known
   entities; it may **not invent them**. This keeps the "every node is a
   resolved, controlled-vocabulary entity" property intact.
3. **Distinguishable provenance.** Every model-asserted edge is stamped as such,
   so it is never confused with a deterministic fact — a consumer can always tell
   which edges a model proposed.
4. **A per-triple, replayable trace.** For every candidate the trace records the
   source span it came from, the prompt and schema shown to the model, and the
   verdict (accepted / rejected / dropped). Because extraction runs at ingest, off
   the demo's critical path, the live bar is met by **replaying** that trace —
   showing exactly which sentence produced which edge — not by re-running the
   model in the room.

And one honesty gate sits above all four: the pattern ships **only if it is a
measured win** — if the LLM pass recovers real prose edges the deterministic
graph genuinely lacks, checked against a hand-authored gold set. If it can't, the
contrast would be a strawman, and the pattern stays `Backlog`. The template
commits the *intent*, not an unearned result.

## The running contrast

> *This section becomes runnable when the `schema-guided-extraction` slice
> ships; it is `Planned` today. The deterministic baseline below is `Have` and
> runnable now.*

The payoff is a side-by-side you can run on one corpus. Ingest with deterministic
extraction only, and ask a graph question whose answer lives in structure —
*"which KEPs does sig-network own?"* — and the graph answers it cleanly, because
`OWNS` edges come straight from the KEP front-matter. Ask a question whose answer
lives in narrative — *"which SIGs collaborate with sig-network?"* — and the
deterministic graph is silent, because no labeled field ever carried a
collaboration edge.

Now re-ingest with schema-guided extraction turned on. The collaboration edges,
extracted from the README prose and validated against the schema, are now in the
graph — stamped as model-asserted, each traceable to the sentence it came from —
and the same graph query answers the narrative question. Two extraction
strategies, one corpus, a question that only the second can serve: that is the
ingestion spectrum made concrete, the mirror image of the
[three-mode demo](../tutorials/three-mode-demo.md)'s retrieval contrast.

## How to use this when evaluating GraphRAG for your corpus

- **Name your default.** If you run a deterministic extractor, you have chosen
  the deterministic point on the spectrum — make it a choice, not an accident.
  Ask whether the edges your questions need live in structure (it fits) or in
  prose (it will be silent).
- **Reach for schema-guided when you know the shape.** If your relationships hide
  in narrative *and* you can name the entity/edge kinds you want, the
  schema-guided pattern earns its one LLM hop — and the four checks above are what
  keep it honest.
- **Reach for free-form only when you can't.** Unconstrained extraction trades
  checkability for coverage; it is the riskier end, the natural next step after
  schema-guided, not the place to start.
- **Decide routing first.** Whether a source belongs to the graph, the vector
  store, or both is the upstream decision — [choosing what to ingest](choosing-what-to-ingest.md)
  is the page for that. Extraction strategy is *how* the graph half gets built
  once you've routed to it.

## See also

- [Choosing what to ingest](choosing-what-to-ingest.md) — the upstream routing
  decision (vector vs. graph vs. both) this page assumes.
- [Governed vs. risky graph queries](governed-vs-risky-graph-queries.md) — the
  retrieval-side governed-vs-flexible pair this extraction spectrum mirrors.
- [RFC-0002 — establish ingestion as a first-class pattern axis](../../rfc/0002-ingestion-pattern-axis.md)
  — why the ingestion coverage table exists.
- [ADR-0006 — the schema-guided extraction guard](../../adr/0006-schema-guided-llm-extraction-guard.md)
  — how the ingest-time LLM hop is kept safe and narratable.
- [Project charter — Architecture patterns + the two coverage tables](../../CHARTER.md)
  — the ordered considerations and the `Have`/`Planned`/`Backlog` contract.
