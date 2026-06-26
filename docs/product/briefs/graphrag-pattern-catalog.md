# Brief: An AWS/Neptune implementation of the graphrag.com pattern catalog — the enterprise-relevant subset

- **Slug:** `graphrag-pattern-catalog`
- **Received:** 2026-06-23
- **Owner:** _(unassigned — set on `receive-brief`)_
- **Governed by:** [RFC-0001 — Adopt the project charter](../../rfc/0001-adopt-project-charter.md)
  <!-- this brief realizes the charter's "Pattern coverage against the graphrag.com catalog" table -->
- **Sibling brief:** [`graphrag-aws-demo.md`](graphrag-aws-demo.md) (the three-mode
  contrast demo this expansion sits behind)

## Outcome

The [graphrag.com](https://graphrag.com) catalog is the de-facto vocabulary for
GraphRAG retrieval patterns, but it documents them only against Neo4j/Cypher.
This brief delivers an **openCypher/Neptune implementation of the
enterprise-relevant subset** on the same stack the demo already stands up
(OpenSearch + Neptune + Bedrock, ephemeral teardown-first topology). The outcome
is met when an architect can point at a named graphrag.com pattern, see it
running on managed AWS with a legible retrieval trace, and read the trade-off
that tells them whether to adopt it for their own corpus. This is **breadth of
honest, narratable pattern coverage** — distinct from the demo brief's outcome,
which is comprehension of the three-mode *contrast*.

## Success metrics

- **Coverage:** each of the five `Planned` patterns in the charter's coverage
  table ships as a working slice — runnable on the deployed stack, with a visible
  retrieval trace, on the locked K8s corpus.
- **The governed-vs-risky pair lands as a teaching contrast:** Cypher Templates
  (parameterized, auditable) and Text2openCypher (flexible, read-only-guarded) run
  the same question side-by-side, and a watcher can state when they'd choose each.
- **No regression to the demo's posture:** every added pattern stays narratable
  (no black-box hop), reuses the existing stores and query path where it can, and
  does not introduce a standing managed service that breaks the teardown/cost goal.
- **Honest divergences are documented, not hidden:** where an AWS implementation
  differs from the Neo4j reference (openCypher translation; Louvain vs. Leiden),
  the slice says so.

## Scope / Non-goals

**In scope** — the five patterns the charter marks `Planned`:

- **Metadata Filtering / Self-Query** — Bedrock extracts structured filters from
  the question → OpenSearch filtered k-NN (filter applied during ANN search).
- **Cypher Templates** — expert-authored parameterized openCypher templates;
  Bedrock selects the template and extracts parameters.
- **Parent-Child Retriever** — OpenSearch nested `knn_vector` child chunks for
  precise matching → return the parent document body for context-complete answers.
- **Text2openCypher (guarded)** — Bedrock Claude → openCypher, executed read-only
  against Neptune, with validation; the flexible-but-risky foil to Cypher Templates.
- **Global Community Summary** — community detection over the entity graph +
  Bedrock-generated community summaries stored in Neptune, for "summarize across
  the whole corpus" questions; computed without a standing analytics service.

**Non-goals:**

- The three `Backlog` patterns (Local Retriever, Dynamic Cypher Generation,
  Hypothetical Question Retriever) — named in the charter, not scheduled here.
- **Agent memory graphs** (episodic / procedural / semantic / temporal) — a
  different product; a charter Non-goal.
- Anything the demo brief or the charter already rules out (production authz,
  functional code parsing, a GUI, HA/scale tuning, sources beyond the two repos).
- Re-deciding the store topology or hybrid pattern — fixed by ADR-0001 / ADR-0002;
  these slices ride the same stores and query path.

## Appetite

**Appetite-gated, behind the demo's three-mode core (slices 1–3 of the sibling
brief).** Each pattern slice is sized to roughly one delivery pass. The catalog
expansion is deferrable in whole or in part without breaking the demo; the
governed-vs-risky pair (Cypher Templates + Text2openCypher) is the highest-value
sub-bet if appetite is limited. None of this is committed until RFC-0001 is
accepted.

## Spec map

<!-- One row per pattern slice. Status is AUTO-DERIVED by the coverage lint from
each spec's own Status: field once specs are scaffolded — do not hand-edit.
Ordered by value; all depend on the demo's stores (sibling slices 1–2). -->

| Spec | graphrag.com pattern | Status |
| --- | --- | --- |
| `opencypher-templates` | Cypher Templates | Shipped (all 10 ACs incl. AC9 verified live) |
| `text2opencypher-guarded` | Text2Cypher | Shipped (all 12 ACs incl. AC10 verified live) |
| `metadata-filtering` | Metadata Filtering / Self-Query | Shipped (all 10 ACs incl. AC9 verified live) |
| `parent-child-retrieval` | Parent-Child Retriever | Shipped (all 9 ACs incl. AC9 verified live) |
| `global-community-summary` | Global Community Summary | _not yet scaffolded_ |
