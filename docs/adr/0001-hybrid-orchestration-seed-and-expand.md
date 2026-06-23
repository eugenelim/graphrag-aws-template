# ADR-0001: Hybrid retrieval is one *seed-and-expand* orchestration — not single-direction or parallel-merge

- **Status:** Accepted
- **Date:** 2026-06-23
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** [`docs/architecture/graphrag-aws-architecture/design.md`](../architecture/graphrag-aws-architecture/design.md) (D1 — the deliberation this records); [`docs/product/briefs/graphrag-aws-demo.md`](../product/briefs/graphrag-aws-demo.md) (slice 3 `hybrid-orchestration`); [`docs/product/intents/graphrag-aws-demo.md`](../product/intents/graphrag-aws-demo.md) (two SURVIVED de-risk verdicts); ADR-0002 (the topology these stores live in)

## Context

The demo contrasts three retrieval modes (vector / graph / hybrid) over the
Kubernetes `community` + `enhancements` corpus. **Explainability/demoability is the
top-ranked quality attribute** — every retrieval step must be narratable live. The
curated demo-query set contains two structurally different query classes:

- **Semantic-led** ("what are the risks of in-place pod resize") — the answer lives
  in prose meaning; vector search carries it.
- **Entity-led** ("summarize the motivations of KEPs the SIG @thockin tech-leads
  owns") — the question names an entity and needs the graph to *scope* a set before
  prose is summarized.

A single hybrid pattern must serve both. The corpus makes one option cheap that is
normally expensive: entities are a **controlled vocabulary** (SIG slugs, GitHub
`@handles`), so question-to-entity linking reuses the *same normalized-match +
alias table the slice-1 resolver already builds* (de-risked, SURVIVED). Graph also
cannot query itself from free text without *some* seed.

## Decision

> We will implement the hybrid retrieval mode as a single **seed-and-expand**
> orchestration: seed graph entities from **both** the entities owning the top-k
> vector hits **and** entities linked from the question; expand 1–2 hops in
> Neptune; merge the graph facts with the vector chunks; and synthesize with a
> Bedrock Claude model, returning the answer with citations and a visible
> seed/hop trace.

Boundary: this governs the **hybrid mode only**. The slice-3 comparison runner
still executes vector-only and graph-only **independently** for side-by-side
contrast — that is the demo's pedagogy, distinct from the hybrid mode's internal
orchestration.

## Decision drivers

- **Narratability** — the trace must show which seeds came from semantics vs. the
  question, and which hops enriched the answer.
- **Query coverage** — must serve both semantic-led and entity-led classes.
- **Complexity budget** — minimize custom orchestration code.
- **Reuse** — the slice-1 entity resolver/alias table should do double duty.

## Consequences

**Positive:**
- Covers both query classes with one pattern; entity-led scoping works because the
  graph is seeded directly from question entities.
- Most narratable option — the dual-seed trace directly demonstrates graph
  *augmenting* vector.
- Question-entity-linking is near-free here (controlled vocabulary → reuses the
  resolver), so the second seed source costs almost no extra code.

**Negative:**
- Two seed sources feeding one expansion can **over-expand** and bury the answer —
  bounded by a hop limit (1–2) and a seed cap, with the seed set surfaced in the
  trace so over-expansion is visible, not silent.
- Entity-linking can **misseed** (a question term matches the wrong entity) —
  mitigated by reusing the slice-1 alias table and showing every seed in the trace.

**Neutral / to revisit:**
- The synthesis Claude model is not pinned here (cost/latency vs. quality — an open
  question in the design doc).

## Confirmation

- The curated entity-led query returns the correctly-scoped KEP set in its trace
  (eval check, not vibes).
- Over-expansion stays bounded under the hop/seed caps across the curated set.

## Alternatives considered

- **Vector-entry → graph-hop only (single direction).** Simplest, canonical
  GraphRAG. *Rejected:* drops the entity-led query class for essentially no
  complexity saving, because entity-linking is nearly free on this controlled-
  vocabulary corpus.
- **Parallel-retrieve → merge-at-synthesis (fully independent modes).** Robust to
  either mode missing. *Rejected:* the graph cannot query itself from free text
  without a seed, so "independent" graph retrieval is illusory — seed-and-expand
  keeps the independence that matters (two seed sources) without the pretence.

## References

- Design doc D1: [`graphrag-aws-architecture/design.md`](../architecture/graphrag-aws-architecture/design.md)
- De-risk verdicts (cross-source resolution; tri-modal fitness): [`intents/graphrag-aws-demo.md`](../product/intents/graphrag-aws-demo.md)
