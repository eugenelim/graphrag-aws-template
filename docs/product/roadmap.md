# Roadmap

> Direction for the next 2-4 quarters. **Not** commitments. The whole point
> of writing this down is that it can change.

**Last updated:** 2026-06-23
**Reviewed:** quarterly. Next review: 2026-09-23.

If the current date is more than 90 days past "Last updated", treat this
file as stale and ask before relying on it.

## Now (current quarter)

Delivering the GraphRAG demo, sliced from
[`product/briefs/graphrag-aws-demo.md`](briefs/graphrag-aws-demo.md). Each slice is
a vertical that ships and demos on its own; architecture is settled in
[ADR-0001](../adr/0001-hybrid-orchestration-seed-and-expand.md) and
[ADR-0002](../adr/0002-ephemeral-vpc-store-topology.md).

- **Graph ingestion + cross-source entity resolution.** Parse both K8s sources →
  resolve entities into single Neptune nodes → multi-hop graph query. The lead
  slice. [spec: `graph-ingestion-resolution` — not yet scaffolded]
- **Vector RAG baseline.** Prose-rich subset → Titan v2 → OpenSearch → semantic
  query with trace. [spec: `vector-rag-baseline` — not yet scaffolded]
- **Hybrid orchestration + three-mode comparison.** Seed-and-expand hybrid + the
  side-by-side runner over the curated query set. The demo's payoff.
  [spec: `hybrid-orchestration` — not yet scaffolded]

## Next (following 1-2 quarters)

The two enterprise-concern slices — appetite-gated, deferrable without breaking the
three-mode core.

- **Permission-filtered retrieval.** Synthetic visibility labels propagated into
  both stores; persona/clearance filtering across all three modes.
  [spec: `permission-filtered-retrieval` — intent only]
- **Incremental delta re-ingest.** Git-delta detection → consistent update of both
  stores. [spec: `incremental-delta-reingest` — intent only]

## Later

- A third ingestion source (the ingestion seam is pluggable; proving heterogeneity
  beyond the two K8s repos).
- GitLab handbook + `team_members` corpus as a *harder* entity-resolution showcase
  (the recorded backup corpus).

## Not in scope

- **Production authorization / real ACLs / multi-tenancy.** Visibility labels are
  synthetic stand-ins — a Non-goal in the brief, not a roadmap gap.
- **Functional source-code parsing.** The demo is Markdown + natural-language org
  entities only.
- **A polished graphical UI.** The interface is a CLI over the query API.
- **High availability / scale / latency tuning.** Single-AZ, demo-scale by design
  (see ADR-0002).

## How this file is maintained

- **Owners:** the maintainers.
- **Updates:** roadmap items move between sections via small PRs. Substantive
  additions or deletions go through an RFC.
- **Review cadence:** quarterly. The review updates the "Last updated" date
  even if no items change.
- **Drift signal:** if items in "Now" haven't moved in two consecutive
  reviews, either they're not actually being worked on (move them out)
  or the roadmap doesn't reflect what the team is doing (rewrite it to
  match).
