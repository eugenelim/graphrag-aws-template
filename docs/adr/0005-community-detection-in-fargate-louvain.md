# ADR-0005: Community detection runs in the Fargate ingest task (Louvain via networkx), not a standing Neptune Analytics service

- **Status:** Superseded by ADR-0014 <!-- Proposed | Accepted | Rejected | Deprecated | Superseded by ADR-NNNN -->
- **Date:** 2026-06-26
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** [RFC-0001 feasibility note §1](../rfc/0001-notes/aws-feasibility.md) (VERIFIED Neptune Analytics ships **Louvain** not Leiden, and the managed service is **avoidable** — compute in Fargate and write back); [docs/CHARTER.md](../CHARTER.md) coverage table *Global Community Summary* row + the Louvain-vs-Leiden honesty note; [ADR-0002](0002-ephemeral-vpc-store-topology.md) (the teardown-first, no-standing-extra-service cost posture this decision must not break); [ADR-0001](0001-hybrid-orchestration-seed-and-expand.md) (the synthesizer + query-Lambda seam reused); the [`global-community-summary`](../specs/global-community-summary/spec.md) slice this decision ships under

## Context

The `global-community-summary` slice ships the **Global Community Summary**
pattern (Microsoft GraphRAG's *global* search): detect **communities** over the
resolved entity graph, generate a **summary** per community with an LLM, store the
summaries, and answer "summarize across the whole corpus" questions by
map-reducing over those summaries — the question class the seed-and-expand hybrid
cannot serve (it has no seed entity to expand from).

Two upstream facts, established before this slice, frame the decision:

- **[RFC-0001 §1](../rfc/0001-notes/aws-feasibility.md) verified the managed
  option and flagged it as avoidable.** Amazon **Neptune Analytics** offers
  community detection callable from openCypher (`CALL neptune.algo.louvain(...)`),
  but it is a **separate, in-memory, standing** service distinct from the Neptune
  Database the stack runs — it bulk-imports from a cluster/snapshot, computes, and
  the results must be written back for query-time lookup. The note's verdict:
  the dependency is **avoidable** — compute in the existing Fargate ingest task
  and write results back, introducing **no new managed service**.
- **Neptune Analytics ships Louvain, not Leiden.** Microsoft's reference GraphRAG
  pipeline uses the **Leiden** algorithm (Louvain's successor — Leiden guarantees
  well-connected communities, which Louvain can violate at intermediate levels).
  The managed AWS primitive offers **Louvain**. True Leiden on AWS would require an
  external `leidenalg` step (which itself pulls `python-igraph`, a C-extension).

The charter (principle 4, *Teardown is a feature*; principle 6, *Managed
services, minimal glue*) and ADR-0002 forbid a design that adds a standing
billable service or breaks one-command teardown. The charter's coverage-table
honesty note **commits the slice to stating which clustering algorithm it used**.

The decision has two coupled axes — **where** detection computes (managed service
vs. our compute) and **which algorithm** runs — plus a consequent **dependency**
choice. They are decided together here because the algorithm available depends on
where it runs.

## Decision

> Community detection runs **inside the on-demand Fargate ingest task**, using the
> **Louvain** algorithm via **`networkx`** (`networkx.algorithms.community.louvain_communities`,
> with a pinned random seed for reproducibility). The detected partition and the
> Bedrock-generated per-community summaries are written **back into the existing
> Neptune Database** as `Community`-labeled nodes (plus a `communityId` stamp on
> each member entity). **No Neptune Analytics service, and no standing service of
> any kind, is provisioned.**

Concretely:

1. **Compute location — the Fargate ingest task.** On a full ingest (`MODE=full`,
   and on `--rebuild`), after the entity graph is written, the same task reads the
   graph back (`all_nodes()` / `all_edges()`), runs Louvain in-process, generates
   summaries via Bedrock Converse, and writes the results back to Neptune. The task
   is **already on-demand and teardown-free** (it runs, ingests, exits — nothing
   standing), so community detection inherits that posture for free. Delta
   re-ingest does **not** recompute communities (a named scope boundary — see the
   slice spec); they are (re)built on full ingest / `--rebuild`.
2. **Algorithm — Louvain (not Leiden).** Louvain is chosen deliberately over
   Leiden so the algorithm **matches the managed alternative** (Neptune Analytics'
   Louvain): an adopting team evaluating "self-compute in Fargate vs. the managed
   Neptune Analytics service" compares **the same algorithm in two locations**, an
   apples-to-apples cost/operability trade-off rather than an algorithm change
   confounding the comparison. The divergence from Microsoft GraphRAG's Leiden is
   **stated** in the slice and the charter note, not papered over.
3. **Dependency — `networkx`, ingest-only.** Louvain runs via `networkx`
   (pure-Python, MIT, no C-extension build) rather than a hand-rolled implementation
   (community detection is well-understood but fiddly and randomized — a hand-roll
   is a liability, against AGENTS.md *prefer the boring obvious solution*).
   `networkx` is declared as an **optional `ingest` dependency**, installed in the
   Fargate image and the dev/test environment, and is kept **out of the query
   Lambda's import graph** (the same discipline that keeps the Lambda PyYAML-free):
   the detection module imports `networkx` lazily, and a `sys.modules` guard test
   asserts the query-side global-search modules pull in **no `networkx`**. The
   Lambda reads pre-computed summaries; it never detects.
4. **Write-back model — `Community` nodes in the existing Neptune Database.** Each
   community is a `Community`-labeled node carrying `id`, `title`, `summary`,
   `entity_ids` (membership), a composed `tier` (visibility), and `size`. Member
   entities additionally carry a `communityId` property (the literal "write
   communityId back" affordance + the narratable entity→community trace). These ride
   the **existing** Neptune cluster — **no new billable resource**; Budgets is held
   at the literal `150` (ADR-0002).

This adds **one** IAM grant: the **ingest task role** gains `bedrock:Converse`
(scoped to the synthesis model + its underlying foundation-model ARNs, via the
existing `_bedrock_synthesis_invoke()` helper) so the task can generate summaries.
The query Lambda's **read-only** Neptune grant (ADR-0004) already permits reading
`Community` nodes — **no query-side grant change**.

## Decision drivers

- **Teardown / cost posture (ADR-0002, charter principle 4).** The design must add
  no standing billable service — Neptune Analytics is a standing in-memory service,
  disqualifying it for a teardown-first demo.
- **Managed services, minimal glue (charter principle 6).** Reuse the existing
  on-demand Fargate task and the existing Neptune Database; add the smallest
  possible new surface (one Python library, one IAM grant).
- **Honest comparison (charter principle 2) + the coverage honesty note.** State
  the algorithm; make the managed-vs-self-compute trade-off legible by holding the
  algorithm constant (Louvain both ways).
- **Reproducibility (charter principle 3).** Louvain is randomized; a pinned seed
  makes the partition reproducible across runs on the locked corpus.
- **Narratability (charter principle 1).** A watcher can state, in one sentence,
  where detection runs and why ("the same ingest task that builds the graph runs
  Louvain and writes the summaries back — no extra service to stand up or tear
  down").

## Consequences

**Positive:**
- **No new standing service; teardown unbroken.** Detection is a transient phase of
  an already-transient task; nothing survives `destroy`. Budgets unchanged at `150`.
- **Backend-symmetric and offline-testable.** Because detection runs on
  `all_nodes()`/`all_edges()` from any `GraphStore`, the in-memory backend runs the
  **same** Louvain offline (networkx is in the dev env), so the slice is exercised
  credibly in CI with no AWS — the project's offline-first invariant.
- **The managed alternative stays documented, not adopted.** An adopting team that
  *wants* the managed path has a named, verified option (Neptune Analytics, same
  algorithm) and a clear trade (standing cost for less self-managed compute).
- **The Lambda stays lean.** `networkx` never enters the query bundle; the read path
  only loads pre-computed summaries.

**Negative:**
- **We diverge from Microsoft GraphRAG's Leiden.** Louvain can yield a less
  well-connected partition at intermediate levels. Mitigated by: stating the
  divergence (charter note + slice), using a single flat partition (not the
  intermediate-level hierarchy where Louvain's weakness bites most), and naming
  `leidenalg`+`igraph` as the documented route to true Leiden if an adopter needs it.
- **A new runtime dependency exists** (`networkx`), forever (AGENTS.md). Mitigated
  by scoping it to the `ingest` extra (out of the Lambda) and recording it in
  `packages/graphrag/AGENTS.md`.
- **Detection cost scales with graph size in the task.** At the demo's
  hundreds-of-docs scale this is trivially in-memory; for a very large corpus the
  managed Neptune Analytics path (built for scale) becomes the better trade — named
  as the scale-out alternative, not built here (charter: HA/scale is a non-goal).
- **Summaries are generated once at ingest** and can go stale relative to a delta
  re-ingest that does not recompute them. Mitigated by scoping recompute to full
  ingest / `--rebuild` and naming delta-community-sync as a future extension.

**Neutral / to revisit:**
- If the template ever adopts Neptune Analytics (e.g. a persistent, large-corpus
  variant), the same `Community`-node write-back contract holds — only the compute
  location changes. That would be a **new ADR**, not an edit to this one.
- Louvain's **hierarchy** (`louvain_partitions` — multiple levels) is available but
  unused; the slice writes a single flat partition. Adding levels is an additive
  future change.

## Confirmation

- **Synth fitness test (CDK `aws_cdk.assertions.Template`).** Asserts the **ingest
  task role** gains `bedrock:Converse` scoped to the synthesis model (no wildcard
  `Resource`); that **no new billable/compute resource** is added (no Neptune
  Analytics graph, no second cluster); that the query-Lambda Neptune grant is
  **unchanged** (still read-only per ADR-0004); and that Budgets is the literal `150`.
- **Import-graph guard (unit).** A `sys.modules` test blocks `networkx`, then
  imports the query-side global-search modules + the query Lambda and asserts they
  load — proving `networkx` stays out of the Lambda import graph (the PyYAML-free
  discipline, extended).
- **Reproducibility test (unit).** Louvain run twice with the pinned seed over the
  fixture graph yields the **identical** partition.
- **Offline detection test (unit).** Louvain runs over the in-memory backend's
  `all_nodes()`/`all_edges()` and produces communities with no AWS.
- **Live smoke (slice AC).** A deploy dual-writes the corpus, the Fargate task
  detects communities + writes `Community` nodes via Bedrock summaries, a live
  `mode: global` Function-URL call map-reduces over them, and the stack is destroyed
  — proving the whole path needs no standing service.

## Alternatives considered

- **Amazon Neptune Analytics (the managed `CALL neptune.algo.louvain`).**
  *Rejected against the teardown/cost driver:* it is a **standing, in-memory,
  separately-billed** service that must be provisioned, bulk-imported into, and torn
  down — exactly the standing footprint ADR-0002 and charter principle 4 forbid for
  a clone-and-forget demo. Documented as the **managed alternative** (same Louvain
  algorithm) for an adopter who wants scale over ephemerality. (RFC-0001 §1.)
- **True Leiden via `leidenalg` + `python-igraph`.** *Rejected against
  minimal-glue + the apples-to-apples comparison:* `leidenalg` pulls
  `python-igraph` (a C-extension — heavier install, build surface) and would change
  the algorithm relative to the managed Neptune Analytics option, confounding the
  self-compute-vs-managed comparison the slice teaches. Named as the route to true
  Leiden (Microsoft GraphRAG parity) if an adopter needs it.
- **Hand-rolled Louvain.** *Rejected against AGENTS.md "prefer the boring obvious
  solution":* community detection is well-understood but fiddly and randomized;
  a hand-roll is a correctness liability for no benefit over a standard, audited
  library.
- **Reduce-only "global" (no community detection — summarize all entities in one
  pass).** *Rejected against the pattern's identity:* the Global Community Summary
  pattern is *community*-structured map-reduce; collapsing it to a single
  whole-graph summary loses the structure the pattern exists to exploit and would
  not be the catalog pattern. (The map-reduce *bounded scale* is the slice's
  concern, not the elimination of communities.)

## References

- [RFC-0001 feasibility note §1 — Neptune Analytics community detection](../rfc/0001-notes/aws-feasibility.md)
- [Neptune Analytics clustering algorithms (Louvain, label propagation)](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/clustering-algorithms.html)
- [Neptune Analytics vs Neptune Database](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/neptune-analytics-vs-neptune-database.html)
- [networkx `louvain_communities`](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.louvain.louvain_communities.html)
- [Microsoft GraphRAG — global search / community reports (Leiden)](https://microsoft.github.io/graphrag/)
- [Traag, Waltman & van Eck (2019), "From Louvain to Leiden"](https://www.nature.com/articles/s41598-019-41695-z)
