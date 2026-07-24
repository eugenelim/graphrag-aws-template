# ADR-0013: Multi-strategy server-side routing: rules-first cascade over named-graph partitions

- **Status:** Accepted
- **Date:** 2026-07-23
- **Decision-makers:** eugenelim
- **Supersedes:** [ADR-0001](0001-hybrid-orchestration-seed-and-expand.md), [ADR-0008](0008-automatic-engine-routing-local-vs-global.md) — RFC-0004 reversed both; this ADR records the replacement routing decision for the SPARQL/RDF platform
- **Related:** [RFC-0004 §D3](../rfc/0004-biz-ops-kg-pivot.md); [ADR-0008](0008-automatic-engine-routing-local-vs-global.md) (superseded routing decision — reversed by RFC-0004); [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine); [ADR-0012](0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition the strategies operate over); `spec-multi-strategy-routing`; `spec-normative-partition`

## Decision summary

- **Decision:** We will implement caller-opaque, server-side multi-strategy routing inside the MCP tool server, using a rules-first → Bedrock LLM fallback cascade that selects among six retrieval strategies from detected query signals, with every call returning a transparent strategy trace.
- **Because:** Callers (AI agents, IDE LLMs) cannot reliably detect whether a question requires exhaustive normative recall or best-match descriptive retrieval — routing that stays server-side and caller-opaque keeps that logic in one place and lets the tool surface remain stable as strategies evolve.
- **Applies to:** The `ask` tool's internal routing path; the `get_policies` tool is always normative exhaustive and has no routing step.
- **Tradeoff accepted:** Routing bugs are server-side, invisible to callers without the strategy trace; a misroute on the `ask` path cannot be corrected by the caller mid-call.
- **Revisit if:** A caller type emerges that consistently needs to override the server's routing decision — re-open as an optional `strategy` hint parameter.

## Context

RFC-0004 introduced **asymmetric retrieval semantics**: normative knowledge (policies, standards) demands exhaustive recall (a missed policy is a compliance gap); descriptive knowledge (SOPs, transcripts) demands best-match precision (a miss is "I don't know"). Six retrieval strategies implement those semantics:

| Strategy | Stores | Right for |
|---|---|---|
| `hybrid_graph` | OpenSearch k-NN + Neptune SPARQL expand | Narrow factual question anchored to a named entity |
| `structured` | Neptune SPARQL only | Aggregation or relationship question with an entity URI |
| `graph_expand` | Neptune SPARQL property paths | Entity neighbourhood traversal |
| `vector_only` | OpenSearch k-NN | No entity anchor, specific factual |
| `global` | Neptune taxonomy + Bedrock synthesise | Broad thematic question |
| `normative_exhaustive` | Neptune `urn:graph:normative` + OpenSearch threshold | All applicable policies — exhaustive recall |

ADR-0012 placed each document in a named graph whose retrieval semantics are fixed at partition membership. What is **missing** is the call-by-call decision of which strategy fits a given `ask` question — and where that decision lives.

Two options exist: the caller picks (explicit `strategy` parameter or separate per-strategy tools), or the server infers from the question. The former forces routing logic onto every caller and must be replicated across all AI IDE integrations. The latter makes the tool surface stable and keeps the routing contract in one place.

ADR-0008 established a rules-first → Bedrock fallback cascade shape for local-vs-global routing; RFC-0004 reversed ADR-0008 (the local/global engines are replaced), but the cascade *shape* — `RuleQueryRouter` fires first, `BedrockQueryRouter` fires only for ambiguous cases — carries forward.

## Decision

> We will implement multi-strategy routing entirely server-side inside the MCP tool server. The `ask` tool resolves routing internally and returns the chosen strategy in every response. Callers do not supply a `strategy` parameter.

Concretely:

1. **Rules-first cascade.** `RuleQueryRouter` fires first — deterministic signal detection over the question text: entity URI present, aggregation verb, specificity level. Each row in the routing matrix below maps a detected signal to a strategy without an LLM call.
2. **Bedrock fallback.** `BedrockQueryRouter` fires only when `RuleQueryRouter` returns `ambiguous`. The LLM call is bounded by the same untrusted-data guardrails as ADR-0008 / ADR-0004 (question is untrusted data, routing output is strict-validated to the fixed strategy vocabulary).
3. **Routing matrix** (`ask` tool only; `get_policies` is always `normative_exhaustive` — no routing step):

   | Detected signal | Strategy | Stores |
   |---|---|---|
   | Aggregation verb + entity or class | `structured` | Neptune only |
   | Named entity URI + relationship verb | `graph_expand` | Neptune only |
   | Named entity URI + factual verb | `hybrid_graph` | OpenSearch + Neptune + Bedrock embed |
   | No entity + specific factual | `vector_only` | OpenSearch + Bedrock embed |
   | No entity + thematic / broad | `global` | Neptune taxonomy + Bedrock |
   | Ambiguous / mixed (Bedrock decides) | `hybrid_graph` (default) | OpenSearch + Neptune + Bedrock embed |

4. **`get_policies` is always `normative_exhaustive`.** It executes an exhaustive SPARQL `SELECT` over `urn:graph:normative` (no routing step) UNION a vector-threshold leg that can only add semantically adjacent policies — it can never gate or drop one. Hard-fails if the normative partition is unavailable; a partial result is worse than none.
5. **Normative-first principle (AI workflow convention).** AI workflows call `get_policies` before any descriptive retrieval. The policy constraints govern what an agent may do with descriptive knowledge; this ordering is enforced by convention, not by the platform — the tools are kept distinct to make it easy.
6. **Transparent strategy trace.** Every `ask` / `get_policies` response includes `strategy`, `decided_by` (`rule` | `bedrock`), and a span tree tracing the stores touched and latency per leg. This satisfies charter principle 1 (no black-box hop) and is required for the intra-partition attribute-mismatch residual documented in ADR-0012 to be diagnosable.

## Decision drivers

- **Caller-opaque routing keeps the tool surface stable.** Strategy names and thresholds can change server-side without breaking caller integrations. An explicit `strategy` parameter makes every change a caller-visible API break.
- **Rules-first is fast and free for deterministic signals.** An entity URI in the question always warrants graph expansion; an LLM call for that case is unnecessary latency and cost.
- **Bedrock fallback handles genuine ambiguity.** Mixed-signal questions (entity present, but the question is thematic) cannot be resolved by keyword rules alone. The Bedrock router is the same investment already made for `ask` synthesis — the routing call is incremental.
- **`get_policies` must be strategy-exempt.** Exhaustive normative recall has no ambiguous case — routing it through the cascade would be a latent misroute risk. A dedicated tool with a fixed strategy removes that risk structurally.
- **Trace is required for the honesty constraint.** ADR-0012's intra-partition residual (a correctly-partitioned policy with a wrong domain tag falls to the vector threshold leg) is only diagnosable if the trace exposes which filter narrowed the result and whether the vector leg fired.

## Consequences

**Positive:**
- Callers don't implement routing logic — one server-side change propagates to all consumers.
- Rules cover the majority of deterministic cases at zero LLM cost.
- `get_policies`'s exhaustive semantic is structurally separate from the `ask` routing path — a routing bug cannot accidentally apply best-match semantics to normative queries.
- The strategy trace makes misroutes diagnosable without server-side access.

**Negative:**
- A routing misroute on `ask` is invisible to the caller until they inspect the trace. A wrong strategy (e.g. `vector_only` for a question that should be `structured`) returns a plausible-looking result, not an error.
- `normative_exhaustive` hard-fail semantics differ from all other strategies' graceful degrade — a caller that does not distinguish `get_policies` from `ask` by error handling will see unexpected hard failures on normative calls.
- Bedrock fallback adds one LLM round-trip for ambiguous questions on the `ask` path before the synthesis call.

**Revisit if:** A caller type (agent orchestrator, batch workflow) consistently needs to override the server's routing decision — re-open as an optional `strategy` hint parameter with caller-supplied strategy strict-validated to the vocabulary.

## Confirmation

- **Mode:** lint/CI + reviewer-checked
- **Signal (`RuleQueryRouter` unit tests):** each row of the routing matrix has a unit test asserting the expected strategy for a fixture question; a question with an entity URI routes to `structured` or `hybrid_graph`, never to `vector_only` or `global`. Part of the offline CI suite — no AWS credentials.
- **Signal (strategy trace in responses):** every `ask` / `get_policies` integration test asserts the response carries `strategy` and `decided_by` fields; a test that drops the trace field fails.
- **Signal (`get_policies` isolation):** an integration fixture confirms `get_policies` never invokes `RuleQueryRouter` or `BedrockQueryRouter` — strategy is set to `normative_exhaustive` unconditionally before retrieval begins.
- **Owner:** eugenelim; spec owner: `spec-multi-strategy-routing`

## Alternatives considered

- **Client-side tool differentiation (separate tools per strategy).** Expose `structured_query`, `hybrid_query`, `graph_expand`, etc. as distinct MCP tools — the caller picks by tool name. *Rejected:* forces routing intelligence onto every caller; an IDE LLM must now implement the routing matrix to select the right tool; the tool surface grows with each new strategy; a one-line `RuleQueryRouter` change requires updating all caller integrations.
- **Bedrock-only routing (always LLM).** Route every `ask` question through Bedrock, no rules. *Rejected:* deterministic signals (entity URI present, aggregation verb) don't benefit from an LLM call; adds ~200–500 ms and a Bedrock API call to every `ask` invocation for zero routing quality gain; Bedrock throttle on the routing call delays synthesis.
- **Rules-only routing (no LLM fallback).** `RuleQueryRouter` returns `hybrid_graph` as the default for anything ambiguous. *Rejected:* mixed-signal questions (entity present, thematic intent) fall to `hybrid_graph` without a principled reason; Bedrock routing is an incremental cost already within the synthesis path's LLM budget; a principled fallback is better than a silent default.
- **Single strategy (always `hybrid_graph`).** One retrieval path for all `ask` questions. *Rejected:* pure-SPARQL structured queries (aggregation over entities) pay the OpenSearch embedding cost unnecessarily; `global` questions get entity-expansion noise; `normative_exhaustive` has a categorically different failure semantic — it cannot be collapsed into a single path without silently degrading the correctness guarantee.

## References

- [RFC-0004 §D3 — Named-graph partition + asymmetric failure semantics](../rfc/0004-biz-ops-kg-pivot.md)
- [ADR-0008](0008-automatic-engine-routing-local-vs-global.md) — superseded local-vs-global `mode="auto"` selector (same cascade shape, different strategies)
- [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) — SPARQL/RDF engine
- [ADR-0012](0012-owl-schema-only-and-named-graph-partition.md) — named-graph partition model + honesty constraint
- [biz-ops architecture design.md §Strategy routing matrix](../architecture/biz-ops-knowledge-graph/design.md)
- `spec-multi-strategy-routing`; `spec-normative-partition`
