# Brief: A reproducible enterprise-knowledge-platform demo on managed AWS — show when graph-augmented retrieval beats vector search

- **Slug:** `graphrag-aws-demo`
- **Received:** 2026-06-23
- **Owner:** _(unassigned — set on `receive-brief`)_
- **Parent intent:** [`docs/product/intents/graphrag-aws-demo.md`](../intents/graphrag-aws-demo.md) <!-- projected from the feature intent; two SURVIVED de-risk verdicts -->

## Outcome

A team deciding how to ground an LLM on organizational knowledge needs to judge —
concretely, on infrastructure they trust — whether a knowledge graph earns its
keep over plain vector RAG, and the honest answer depends on their query shapes.
Today that answer lives in hand-wavy blog claims with no runnable, service-by-service
reference, and the demos that exist quietly dodge the concerns that actually block
enterprise RAG (many sources, who-can-see-what, a corpus that changes). This demo
is a runnable AWS reference that an architect can *watch* (to see the three
retrieval modes diverge on the same question) and *clone* (to reproduce ingestion,
retrieval, and search on Markdown corpora of their own). Corpus is locked to the
public Kubernetes `community` + `enhancements` repos (Apache-2.0), where SIGs =
teams, chairs/leads = roles, KEPs = decisions, and ownership = who-owns-what.

## Success metrics

<!-- Demo product → mix of qualitative-but-falsifiable and hard signals. -->

- **Comprehension (lagging):** after the demo, a watching architect can, unprompted,
  (a) name a query where graph wins and one where vector wins, and (b) name the AWS
  service doing each job across ingest → retrieve → search.
- **Reproducibility (lagging):** a fresh `git clone` + documented deploy reproduces
  ingestion, retrieval, and search on the corpus following the README, on a clean
  AWS account.
- **Contrast (steerable input):** ≥5–6 curated showcase queries *per mode* run
  side-by-side with visible per-mode retrieval traces (what was retrieved, from
  which store, why).
- **Resolution quality:** cross-source entity resolver ≥ ~80% precision/recall on a
  hand-labeled sample of shared entities (SIG slugs + GitHub handles).
- **Guardrail:** scope stays Markdown + natural-language org entities (no functional-code
  parsing); every pipeline step is narratable live (trace visible, no black-box hop);
  synthetic visibility labels are presented as a stand-in, never as real ACLs.

## Scope / Non-goals

**In scope:**

- Ingestion from **two** sources (`kubernetes/community` + `kubernetes/enhancements`),
  Markdown + structured YAML, favouring the prose-rich doc subset.
- **Cross-source entity resolution** into single graph nodes (normalized match + alias table).
- All **three retrieval modes** — vector (OpenSearch + Titan v2), graph (Neptune
  traversal), hybrid orchestration — with a side-by-side comparison runner.
- A **search CLI** with visible retrieval traces and source provenance.
- **Permission-filtered retrieval** via synthetic visibility labels (permissions as graph edges).
- **Incremental delta re-ingest** keeping both stores consistent.
- A curated per-mode demo-query set + presenter script.
- A reproducible template (clone → deploy on AWS → ingest → query).

**Non-goals:**

- Functional source-code parsing (org-entity Markdown only).
- A polished graphical UI (CLI + architecture narration is the medium).
- Production-grade authorization / real ACLs / multi-tenancy (labels are synthetic).
- Horizontal scale, cost optimization, or latency tuning beyond "demoable."
- Sources beyond the two K8s repos (the ingestion seam is pluggable; a third source
  is a future extension, not built here).
- **Choosing the hybrid orchestration pattern** (vector-entry→graph-hop vs.
  parallel→merge) and the **store/deployment topology** — deferred to the architect
  skills, pinned at the spec stage.

## Appetite

A focused build, not a quarter — on the order of a few weeks of agent-assisted
delivery. Each Spec-map slice is sized to roughly one delivery pass. The
three-mode core (slices 1–3) is the irreducible demo; the two enterprise-concern
slices (4–5) are gated on remaining appetite and may defer to the backlog without
breaking the payoff.

## Spec map

<!-- One row per shippable slice from the intent's Decomposition. Status is
AUTO-DERIVED by the coverage lint from each spec's own Status: field once specs
are scaffolded — do not hand-edit. Ordered by delivery dependency. -->

| Spec | Status |
| --- | --- |
| `graph-ingestion-resolution` | _not yet scaffolded_ |
| `vector-rag-baseline` | _not yet scaffolded_ |
| `hybrid-orchestration` | _not yet scaffolded_ |
| `permission-filtered-retrieval` | _not yet scaffolded_ |
| `incremental-delta-reingest` | _not yet scaffolded_ |
