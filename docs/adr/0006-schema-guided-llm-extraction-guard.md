# ADR-0006: Schema-guided LLM extraction is guarded by a closed schema + entity-grounding, runs at ingest, and is distinguishable in the graph

- **Status:** Accepted <!-- Proposed | Accepted | Rejected | Deprecated | Superseded by ADR-NNNN -->
- **Date:** 2026-06-27
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:**
  - [RFC-0002](../rfc/0002-ingestion-pattern-axis.md) — establishes ingestion as a first-class pattern axis and commits **schema-guided LLM extraction** as the one `Planned` pattern; this ADR is the guard decision that slice ships under.
  - [docs/CHARTER.md](../CHARTER.md) — *Ingestion pattern coverage* table (the *Schema-guided LLM* row) + spine item #9 + the clarified Principle 1 (*narratable ⇒ traceable, LLM allowed when traced*).
  - [ADR-0004](0004-text2cypher-read-only-guard.md) — the retrieval-side precedent: an LLM hop made safe + narratable by a validator the presenter can show; this is its ingestion-side analog.
  - [ADR-0005](0005-community-detection-in-fargate-louvain.md) — the precedent that an ingest-time Bedrock hop runs in the Fargate task, reuses the task's `bedrock:Converse` grant, and persists output narrated later.
  - [ADR-0001](0001-hybrid-orchestration-seed-and-expand.md) — the `Synthesizer`/Bedrock-Converse seam + untrusted-data posture reused.
  - [ADR-0002](0002-ephemeral-vpc-store-topology.md) — the teardown-first, no-standing-extra-service, no-widened-grant cost posture this decision must not break.
  - [`schema-guided-extraction`](../specs/schema-guided-extraction/spec.md) — the slice this decision ships under.

## Context

The deterministic extractor (`graphrag.extract`, the `graph-ingestion-resolution`
slice) reads the corpus into entities and edges with **no model**: front-matter,
YAML, and *labeled-field* regex over prose (e.g. `_PROSE_AUTHORS` matching
`**Authors:** …`). It is narratable and free, and it is the charter's default and
the `Have` baseline of the *Extraction* stage. But it reads prose **only** through
labeled-field patterns; everything else in a prose body is routed to the *vector*
index (`chunk.py`), not the *graph*. So the **free-narrative inter-entity
relationships** stated in the bodies of SIG READMEs and KEP `Motivation` /
`Alternatives` sections — cross-SIG collaboration, KEP supersession and
dependency, informal ownership — are **structurally unreachable** to the
deterministic graph (RFC-0002 de-risk spike).

RFC-0002 commits **schema-guided LLM extraction** (`Planned`) as the one pattern
that demonstrates the deterministic↔LLM contrast at its most teachable, safest
end: a Bedrock Claude pass extracts triples from those same prose bodies,
**constrained to a fixed entity/edge schema**, surfacing the edges the
deterministic rules cannot reach. This is an **LLM hop crossing into the graph
every retrieval pattern then reads** — so the guard is load-bearing in a way the
query-time text2cypher hop is not (a bad text2cypher query returns wrong rows
*this once*; a bad extracted edge is *baked into the graph* and silently colors
every future answer).

The clarified Principle 1 (RFC-0002 P3) permits the hop — *narratable means
traceable, not no-LLM* — but only if its inputs, outputs, and decision are
inspectable in a trace. Principle 2 (*fair comparison*) requires the contrast be
an **honest win**, not a strawman. ADR-0002 and charter principle 4 forbid a new
standing service or a widened grant. The decision below is how the slice satisfies
all three at once. It has three coupled axes — **what the model is allowed to
emit**, **where the pass runs**, and **how its output stays distinguishable and
honest** — decided together because they constrain one another.

## Decision

> Schema-guided LLM extraction runs as an **additive, default-off phase of the
> Fargate ingest task**, after the deterministic graph write. A Bedrock Claude
> Converse call extracts triples from prose bodies, constrained to a **fixed,
> closed entity/edge schema**; a **two-part validator** accepts a triple only if
> (1) its predicate and endpoint kinds are in the closed schema and (2) both
> endpoints **ground to entities the deterministic graph already resolved**.
> Accepted edges are written to the **existing** Neptune cluster carrying an
> `extraction_method: "schema-guided-llm"` provenance stamp and per-triple
> source-span provenance, persisted as a **replayable trace artifact**. The slice
> ships only if the pass clears a **measured honest-win bar** at build time;
> otherwise the charter row drops to `Backlog`.

Concretely:

1. **Closed schema — what the model may emit.** The model is shown a fixed schema
   and may emit triples only over it: a small, closed set of **LLM-extractable
   edge kinds** (the free-narrative relationships the deterministic pass misses —
   `COLLABORATES_WITH` (SIG↔SIG), `SUPERSEDES` / `DEPENDS_ON` (KEP→KEP)), with
   subject/object **entity kinds** drawn from the existing `EntityKind` set. A
   triple whose predicate or endpoint kind is outside the schema is **rejected**,
   never written — the ingestion-side analog of text2cypher's read-only validator
   (ADR-0004): the closed set is the ground truth the presenter shows and the
   model's output is checkable against it. The exact edge-kind members are the
   slice's design call (pinned in its plan); the *closedness* is this decision.
2. **Entity grounding — the honesty bound.** Both endpoints of an accepted triple
   must **normalize to an entity id that already exists in the deterministic
   graph**. The model may assert *relationships between entities the corpus already
   names*; it may **not invent entities**. A triple naming an entity that grounds
   to no known id is **dropped** (recorded in the trace as dropped, with the
   reason). This is what keeps the graph honest under an LLM pass and reuses the
   existing controlled-vocabulary resolution (`normalize` + `aliases.yaml`) rather
   than adding fuzzy resolution (that is a separate `Backlog` pattern).
3. **Compute location — the Fargate ingest task, reusing the existing grant.** The
   pass runs after the deterministic graph write on `MODE=full` / `--rebuild`
   (not on `delta` — same scope boundary as community detection), behind a
   **default-off flag** (`SCHEMA_EXTRACTION`), so deterministic extraction stays
   the default and the LLM strategy is an **additive, flagged contrast, not a
   migration** (RFC-0002 non-goal). It reuses the **`bedrock:Converse` grant the
   ingest task role already holds** for community summarization (ADR-0005) at the
   **same** `DEFAULT_SYNTHESIS_MODEL_ID` — so there is **no widened Bedrock grant,
   no new IAM resource, no new billable resource**; Budgets stays at the literal
   `150` (ADR-0002). The trace artifact is written to the **existing** corpus S3
   bucket the task already writes the manifest to.
4. **Distinguishable provenance, enforced at write *and* read.** Every
   LLM-extracted edge carries `extraction_method: "schema-guided-llm"`
   (deterministic edges are `"deterministic"`). Distinguishability holds at **both**
   ends, or it does not hold: (a) **at write** — the LLM-extractable edge-kind set is
   **disjoint** from the deterministic edge-kind set (a load-bearing invariant; a
   deterministic rule emitting an LLM-set kind is a guard violation), so an LLM edge
   never shares a `(src, kind, dst)` key with a deterministic edge under
   merge-on-upsert, and `extraction_method` is **set authoritatively** (not
   `setdefault`-merged — `Graph.upsert_edge` keeps existing keys, which would
   otherwise let a colliding write mislabel an edge); (b) **at read** — the retrieval
   trace surfaces `extraction_method` per traversed edge, because the existing
   seed-and-expand traversal follows *all* edge kinds, so a write-only stamp would let
   LLM edges ride into answers unmarked (the exact "silently colors every future
   answer" failure this guard exists to prevent). The per-triple trace records, for
   each candidate triple: the source doc + the text span it came from, the prompt +
   schema shown to the model, the validation verdict (accepted / rejected-off-schema /
   dropped-ungrounded), and — for accepted triples — the resulting edge. It is
   persisted as an artifact the presenter can **replay** (the ADR-0005 affordance,
   extended from "narrate the summary" to "replay the prompt + per-triple
   provenance"). Replay explains *why an edge exists*; the read-side marker explains
   *why an edge is in this answer* — complementary, and together they realize the
   "explainable *live*" bar (extraction runs off the demo's critical path).
5. **Honest-win gate — a *live*, recall-and-precision, ship-or-Backlog gate.** The
   slice's ship decision is the **live** run: the LLM pass must **recover ≥ N gold
   inter-entity edges** from prose that the deterministic graph does not contain
   (recall) **and write ≤ a false-positive ceiling** of off-gold edges (precision —
   the named "wrong-but-in-schema edge" risk is *gated*, not only mitigated by
   provenance), measured against a hand-authored gold set over the locked corpus. The
   *offline* check pins only the **relationship-level absence invariant** (no
   deterministic edge connects the gold endpoints) + the orchestration contract, and
   makes no quality claim (the offline rule extractor is seeded). If the live bars are
   not both met, the slice does **not** ship and the charter row stays `Backlog` — the
   RFC commits the *intent*, not an unearned win (Principle 2).

This adds **no IAM grant** (the ingest task's Converse grant already exists) and
**no new resource**. The genuinely new custom surface is the schema definition,
the prompt, the validator, and the trace-emit — application logic of the same
kind the charter already says we own (parsing, resolution, orchestration), so
Principle 6 (*managed services, minimal glue*) is satisfied, not waived.

## Decision drivers

- **Narratability under an ingest-time LLM hop (Principle 1, clarified).** A
  watcher must be able to state what the model was allowed to emit, see the
  output checked against a closed set, and trace each accepted edge to a source
  span — by replay, since the hop runs at ingest.
- **The graph stays honest (Principle 2).** Model-asserted edges are
  distinguishable from deterministic facts and cannot invent entities; the
  contrast ships only if it is a measured honest win.
- **Teardown / cost posture (ADR-0002, principle 4).** No standing service, no new
  resource, no widened grant — reuse the on-demand task and its existing grant.
- **Additive, reversible (principle 5).** Default-off flag; deterministic
  extraction is untouched and remains the default; turning the flag off restores
  the prior graph exactly.
- **Minimal glue (principle 6).** Reuse the `Synthesizer`/Converse seam, the
  controlled-vocabulary resolvers, the Fargate task, and the corpus bucket; add
  only the schema + prompt + validator + trace.

## Consequences

**Positive:**
- **The deterministic↔LLM contrast becomes runnable and honest.** A question only
  answerable via an LLM-extracted edge (e.g. "which SIGs collaborate with
  sig-network?") demonstrates the win; the trace replay shows *why* it is safe.
- **Backend-symmetric and offline-testable.** The pass runs behind the
  `GraphStore` seam over `all_nodes()` with an offline non-semantic
  `RuleTripleExtractor`, so the orchestration, validator, grounding, and trace are
  exercised in CI with no AWS; live Bedrock is the semantic oracle.
- **No new infra, grant, or standing cost.** Reuses the ingest task's existing
  Converse grant and the corpus bucket; Budgets unchanged at `150`.
- **The free-form end stays a documented, un-adopted option.** An adopter who
  wants the diverse, schema-free end has a named `Backlog` pattern and a clear
  trade (more coverage, no closed set to narrate against, the riskier end).

**Negative:**
- **A model-authored edge can still be wrong within the schema** (a plausible but
  unsupported `DEPENDS_ON`). Mitigated by the per-triple source-span provenance
  (a reviewer/presenter can check the claim against the cited span), the
  distinguishable `extraction_method` stamp at write and read (downstream consumers
  and live answers know it is model-asserted), and the **live honest-win gate's
  false-positive ceiling** (precision is a *gated bar*, not only a provenance
  mitigation) — but a within-schema, within-ceiling false edge can still ship, the
  residual cost of admitting an LLM into ingest, named not hidden.
- **More custom surface to maintain** than the deterministic extractor it sits
  beside (schema + prompt + validator + trace). Accepted as the cost of closing a
  gap the charter itself surfaces (RFC-0002).
- **The closed schema is narrower than free narrative.** It will miss relationships
  outside its edge-kind set by design (the consistency/coverage trade LlamaIndex's
  `SchemaLLMPathExtractor` names). Free-form extraction is the named
  `Backlog→Planned` promotion if more coverage is funded.

**Neutral / to revisit:**
- If the schema grows beyond a handful of edge kinds, or if grounding is relaxed to
  admit new entities, that is a **new ADR** (it changes the honesty bound), not an
  edit to this one.
- Entity *grounding* deliberately reuses controlled-vocabulary resolution; the
  no-stable-ID (fuzzy/embedding resolution) case is a separate `Backlog` pattern,
  not in scope here.
- **Delta staleness.** LLM edges carry `doc_paths` (from their `source_doc`), so the
  slice-5 delta orphan pass removes them when their source doc is removed, like any
  edge; but since extraction runs **full/rebuild only**, an LLM edge can go stale
  relative to a delta that does not recompute it — the ADR-0005 community-staleness
  analog, named here, refreshed by a full re-ingest.

## Confirmation

- **Schema-validator unit test.** A table of off-schema triples (unknown predicate,
  unknown endpoint kind, malformed) is rejected with the rule named; in-schema
  triples pass — the closed-set guarantee.
- **Entity-grounding unit test.** A triple whose endpoint normalizes to a known
  graph id is accepted; one naming an ungrounded entity is dropped and recorded in
  the trace with the reason — the no-invented-entities guarantee.
- **Provenance / distinguishability test (write + read).** Every written
  LLM-extracted edge carries `extraction_method: "schema-guided-llm"` set
  authoritatively; deterministic edges are unaffected; the ingest trace ties each
  accepted edge to a source span; and the **retrieval trace surfaces
  `extraction_method` per traversed edge** (an `expand` over a mixed graph marks the
  LLM hop) — the read-side half.
- **Disjointness assertion (unit).** The LLM-extractable edge-kind set ∩ the
  deterministic edge-kind set = ∅, asserted directly (not via a count) — the
  invariant that makes the authoritative stamp collision-free.
- **Synth fitness test (CDK `aws_cdk.assertions.Template`).** The ingest task
  role's `bedrock:Converse` grant is **unchanged** (still scoped to the synthesis
  model, no wildcard `Resource`); **no new resource** is added; the query-Lambda
  Neptune grant is unchanged (read-only, ADR-0004); Budgets is the literal `150`.
- **Absence invariant (offline).** Each gold prose edge connects two endpoints that
  **no deterministic edge of any kind** links in the asserted direction — absence at
  the relationship level, the anti-strawman check (makes no quality claim).
- **Honest-win test (live, the ship gate).** The **live** pass recovers ≥ N gold
  prose edges absent from the deterministic graph (recall) **and** writes ≤ the
  false-positive ceiling of off-gold edges (precision); failing either bar fails the
  slice (drops the row to `Backlog`). The offline fixture is seeded and is **not**
  this gate.
- **Live smoke (slice AC).** A deploy with the flag on has the Fargate task extract
  triples via Bedrock, validate + ground them, write distinguishable edges, and a
  live query traverse an LLM-only edge; the trace artifact is replayed; then the
  stack is destroyed.

## Alternatives considered

- **Free-form (schema-free) LLM extraction.** *Rejected for the committed slice,
  named as the `Backlog→Planned` next step:* without a closed schema there is no
  ground truth the presenter can show the output against (Principle 1), and
  unconstrained triples are the diverse-but-inconsistent, riskier end — the
  ingestion analog of text2cypher to this slice's templates. It demonstrates the
  same contrast but at the less-teachable, less-governed end; commit the safer end
  first (RFC-0002 Decision 3).
- **Trust the model's entities (no grounding).** *Rejected against Principle 2:* an
  LLM that can invent entities pollutes the graph with unverifiable nodes and
  breaks the "every node is a resolved, controlled-vocabulary entity" property the
  whole resolution story rests on. Grounding to already-resolved ids keeps the
  graph honest.
- **Extract at query time instead of ingest.** *Rejected against the pattern's
  nature and the precedent:* extraction is an **ingest** decision baked into the
  graph every retrieval pattern reads (RFC-0002 diagnosis); doing it per-query
  would re-derive the same edges repeatedly and couple read latency to an LLM pass.
  ADR-0005 already establishes ingest-time Bedrock hops persisted and narrated
  later.
- **A separate edge namespace / separate store for LLM edges.** *Rejected against
  minimal glue:* extending `EdgeKind` with a small closed set plus an
  `extraction_method` provenance prop keeps LLM edges traversable by the existing
  retrieval paths (the teaching payoff) while staying distinguishable; a parallel
  store would duplicate the graph surface for no narratability gain.
- **Do nothing (deterministic only).** *The honest baseline (RFC-0002
  Decision-1(d)):* the demo still works, but the charter keeps asking adopters a
  question (pattern #1 / spine #9) it never shows two answers to.

## References

- [RFC-0002 — establish ingestion as a first-class pattern axis](../rfc/0002-ingestion-pattern-axis.md)
- [ADR-0004 — text2cypher read-only guard (the retrieval-side analog)](0004-text2cypher-read-only-guard.md)
- [ADR-0005 — community detection in the Fargate ingest task (ingest-time Bedrock precedent)](0005-community-detection-in-fargate-louvain.md)
- [LlamaIndex — `SchemaLLMPathExtractor` ("more consistent but potentially less comprehensive")](https://developers.llamaindex.ai/python/examples/property_graph/dynamic_kg_extraction/)
- [LlamaIndex — Property Graph Index extractors (`Implicit` / `Simple` / `Schema`)](https://developers.llamaindex.ai/python/examples/property_graph/property_graph_basic/)
- [Microsoft GraphRAG — indexing dataflow (Graph Extraction stage)](https://microsoft.github.io/graphrag/index/default_dataflow/)
