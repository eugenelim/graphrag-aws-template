# Charter

> The foundational document for this project. Modeled on the
> [CNCF project charter pattern](https://contribute.cncf.io/maintainers/governance/charter/):
> mission, scope, and principles in a single place — plus, because this is a
> *reference template* whose documentation is a key deliverable, an ordered
> **architecture-patterns** spine teams reason through for their own context
> (see [RFC-0001](rfc/0001-adopt-project-charter.md)). Kept stable; rarely changed.

Changes to this file go through an RFC. The rest of the docs in this repo
are scaffolding around it; this file is the why. *Established by
[RFC-0001](rfc/0001-adopt-project-charter.md) (2026-06-23).*

---

## Mission

A clone-and-deploy AWS **reference template** that lets an architect *see* — and
then *reproduce on their own Markdown corpus* — when graph-augmented retrieval
beats plain vector search for grounding an LLM on organizational knowledge, and
that documents the architecture patterns and trade-offs as a deliverable teams
reason through for their own context.

## Scope

What this project does:

- **Ingests** Markdown + structured YAML from **two heterogeneous public
  sources** (`kubernetes/community` + `kubernetes/enhancements`) and **resolves
  shared entities** (SIGs, people, KEPs, ownership) into single graph nodes.
- **Contrasts all three retrieval modes side by side** — vector (OpenSearch +
  Titan v2), graph (Neptune traversal), and a hybrid *seed-and-expand*
  orchestration — over a curated per-mode query set, each with a **legible
  retrieval trace** (what was retrieved, from which store, and why).
- **Exercises the enterprise concerns that block real RAG** — permission-filtered
  retrieval via synthetic visibility labels, and incremental delta re-ingest that
  keeps both stores consistent.
- **Ships as reproducible, teardown-first infrastructure** — one-command deploy
  *and* one-command destroy on a clean AWS account, with idle cost bounded and
  documented.
- **Leans on managed AWS services** so the only custom code we own is the
  Markdown/YAML parsing, entity resolution, the extraction-strategy variants
  (deterministic and LLM-assisted), and hybrid query orchestration — not
  infrastructure glue.
- **Documents the architecture patterns and trade-offs as a first-class
  deliverable** — the ordered considerations below, each tied to where it's
  resolved — so an adopting team can re-decide every one for their own corpus,
  not just run ours.
- **Implements the [graphrag.com](https://graphrag.com) pattern catalog on AWS.**
  That catalog (maintained by Neo4j) documents GraphRAG patterns only against
  Neo4j/Cypher; this template provides an **openCypher/Neptune implementation** of
  the enterprise-relevant subset — each pattern wired on OpenSearch + Neptune
  (openCypher) + Bedrock. See the coverage table below.

What this project does **not** do:

- **Production authorization.** Visibility labels are synthetic stand-ins for
  real ACLs — never passed off as production IAM, multi-tenancy, or data authz.
- **Functional source-code parsing.** Scope is natural-language org entities
  (teams, roles, processes, guides) — never parsing application code.
- **A polished graphical UI.** The medium is a CLI over the query API plus a
  clear architecture narration.
- **Horizontal scale, cost/latency tuning, or high availability** beyond
  "demoable" on a corpus of hundreds of docs (single-AZ, single-node stores are
  deliberate).
- **Sources beyond the two Kubernetes repos.** The ingestion seam is pluggable; a
  third source is a future extension, not built here.
- **Agent memory graphs** — the episodic / procedural / semantic / temporal
  memory shapes that make up roughly a third of the graphrag.com catalog. Those
  serve conversational-agent memory, a different product; a pluggable future
  extension, not built here. (Named explicitly so an adopter isn't confused why we
  implement the *retrieval* patterns but skip the *memory* ones.)
- **A general-purpose GraphRAG framework or library.** The demo is the product;
  it is a reference to learn from and clone, not a packaged dependency.

The "does not" list is at least as important as the "does" list. It's how we —
and AI agents working in the repo — know when a request is out of bounds. If you
find the project being asked to do things that aren't on either list, that's a
signal to refine this section, not to drift.

## Principles

The values that resolve ties when reasonable people disagree.

1. **Narratable over magical.** Every ingest → retrieve → search step must be
   explainable live; no black-box hop the presenter cannot narrate. *Narratable
   does not mean no-LLM* — an LLM hop is narratable when its inputs, outputs, and
   decision are inspectable in the trace (as Bedrock synthesis and text2openCypher
   already are); the bar is the trace, not the absence of a model. The hybrid
   query returns a trace naming each seed entity, graph hop, and citation — if a
   "make it work" shortcut makes the data flow unexplainable, the demo has failed
   even if it runs.
2. **Honest comparison.** Vector is a *fair baseline*, never a strawman: each
   mode must have genuine wins (de-risk verdict #2 required ≥3 honest wins per
   mode from real content). Fairness lives in query selection, so the curated
   per-mode query set is a first-class deliverable, not an afterthought.
3. **Reproducible by construction.** A fresh `git clone` + one-command deploy
   reproduces ingestion, retrieval, and search on the corpus. If a watching
   architect cannot reproduce it on their own Markdown, the demo has not met its
   outcome — comprehension in the room and reproduction by the evaluator are the
   two jobs, served by one artifact.
4. **Teardown is a feature.** The stack is ephemeral and teardown-first; one
   `destroy` removes every billable resource, idle cost is bounded and documented,
   and a Budgets alarm guards the cloned-and-forgotten footgun. A demo that
   accrues silent standing cost is a broken demo.
5. **Synthetic stays labeled synthetic.** Constructs we add to teach a concept
   (visibility labels standing in for ACLs) are always presented as stand-ins,
   never dressed up as the real thing.
6. **Managed services, minimal glue.** We compose AWS-managed primitives
   (OpenSearch, Neptune, Bedrock) rather than building infrastructure, so
   attention stays on retrieval mechanics — the lesson — not on plumbing.
7. **The documentation is a deliverable.** The product is the demo *and* the
   written reasoning — the ordered patterns, trade-offs, and considerations below
   are half of what an adopting team clones the repo for. Decisions optimize for
   an architect's comprehension and reproduction, not production-grade operation;
   when the two pull apart (HA, scale, real authz), the teaching posture wins and
   the production concern is named as a non-goal. A pattern we apply but never
   explain has shipped half-done.

## Architecture patterns & approaches (the considerations, in order)

These are the architectural decisions *any* team grounding an LLM on their own
knowledge has to work through. The demo resolves each one for the Kubernetes
corpus; the deliverable for an adopting team is this **ordered set of
considerations and where each is decided**, so they can re-decide for their own
context. Each pattern is an application of the principles above — narratable,
honest, reproducible, teardown-first — to one stage of the pipeline. The
*current* shape of the code lives in [`architecture/`](architecture/) and the
binding choices in the cited ADRs; this list is the stable spine that orders
them.

1. **Corpus & cross-source entity resolution.** Pick sources that genuinely
   overlap and resolve their shared entities into single graph nodes via
   normalized match + a small alias table — deterministic for this
   controlled-vocabulary corpus; an LLM-assisted extraction contrast is a flagged,
   trace-narratable alternative (see the ingestion coverage table below).
   *Consider for your corpus:* do your sources share entities with
   stable IDs (a controlled vocabulary), or do you face harder prose↔handle
   resolution? → de-risk verdicts in the intent; slice `graph-ingestion-resolution`.
2. **Single-parse dual-write ingestion.** Parse Markdown + YAML once and write the
   vector and graph stores from the same pass, so they never diverge. *Consider:*
   which of your doc types are prose-rich enough to embed vs. structure-rich
   enough to graph — favour the prose-rich subset for the vector showcase. →
   design doc § Proposal; slices 1–2.
3. **Store topology — ephemeral, VPC-resident, teardown-first.** Managed stores in
   private subnets, scale-to-zero compute (Lambda query + on-demand Fargate
   ingest), VPC endpoints over NAT, one-command deploy *and* destroy. *Consider:*
   neither Neptune nor OpenSearch scales to zero, so standing cost is real; Neptune
   is VPC-only by default (and IAM-enforceable as such), which is why query/ingest
   compute runs inside the VPC. → **ADR-0002**.
4. **Vector retrieval baseline.** Chunk → embed (Titan v2) → k-NN, returning a
   visible retrieval trace + source provenance. *Consider:* keep this a *fair*
   baseline — query selection, not corpus structure, decides whether vector looks
   strong or weak. → slice `vector-rag-baseline`.
5. **Graph retrieval.** Multi-hop (1–2) Neptune traversal over the resolved
   entities, for the structural questions vector cannot scope. *Consider:* bound
   hops and seed counts so expansion enriches rather than buries the answer. →
   **ADR-0001**.
6. **Hybrid orchestration — seed-and-expand.** Seed graph entities from *both*
   semantic hits and question entity-linking, expand in Neptune, merge with the
   vector chunks, synthesize, and return the seed/hop trace. *Consider:* which
   hybrid direction fits your query classes — vector-entry→hop, parallel→merge, or
   seed-and-expand (we chose the last because controlled-vocabulary entity-linking
   is nearly free here). → **ADR-0001**.
7. **Permission-filtered retrieval.** Synthetic visibility labels carried as
   Neptune edge/node properties *and* OpenSearch metadata filters, applied **during
   traversal on edges** — not only to the final nodes, or a forbidden node leaks
   via a reachability path. *Consider:* this is a teaching stand-in, not real
   authz; where does authorization ride *your* retrieval path? → slice
   `permission-filtered-retrieval`.
8. **Incremental delta re-ingest.** Diff against a corpus snapshot, upsert/delete
   by a stable key (doc path + content hash) with an explicit orphan-removal pass
   and a `--rebuild` escape hatch, keeping both stores consistent. *Consider:*
   freshness under change is the concern most demos dodge — consistency across two
   stores is the hard part. → slice `incremental-delta-reingest`.
9. **Extraction strategy — deterministic vs. LLM-assisted** *(an ingestion-stage
   consideration, grouped logically with #1–#2; placed last to preserve the spine's
   existing numbering, which specs and code reference by number).* Decide how
   entities and edges leave the text: deterministic rules where the corpus has
   controlled-vocabulary IDs (narratable, free, what we default to), or
   schema-guided / free-form LLM extraction where relationships live in prose.
   *Consider:* deterministic wins on clean structured corpora; the more your edges
   hide in narrative, the more an LLM pass earns its keep — at the cost of a hop you
   must keep narratable (trace the prompt, the schema, and per-triple provenance). →
   ingestion coverage table below; slice `schema-guided-extraction` (Planned).

### Pattern coverage against the graphrag.com catalog

[graphrag.com](https://graphrag.com) is the de-facto vocabulary for GraphRAG
retrieval patterns, but it documents them **only against Neo4j/Cypher**. This
template provides an openCypher/Neptune implementation of the enterprise-relevant
subset, wired on OpenSearch + Neptune + Bedrock. The table maps catalog patterns
to our slices (the mapping is not 1:1 with the ordered considerations above — one
slice can carry several patterns) and is the coverage contract: `Have` is on the
committed core-demo path, `Planned` is committed by RFC-0001, `Backlog` is named
but not scheduled, `Non-goal` is out of scope by the Scope section above.
(Nothing is built yet — the demo brief's slices are unscaffolded; these glyphs
track *commitment*, not implementation, which the brief status rolls up.) AWS
feasibility for every `Have`/`Planned` row was verified against current AWS docs
(see [RFC-0001 feasibility notes](rfc/0001-notes/aws-feasibility.md)); the
per-pattern *mechanism* (which library, which endpoint) is decided at slice time,
not here, so the cells stay at service-and-shape altitude.

| graphrag.com pattern | AWS implementation (our stack) | Status |
| --- | --- | --- |
| **Basic Retriever** (vector RAG) | OpenSearch k-NN + Titan v2 embeddings | ✅ Have — `vector-rag-baseline` |
| **Graph-Enhanced Vector Search** | OpenSearch hit → Neptune openCypher traversal (our *seed-and-expand* hybrid is a superset, seeding from both semantic hits and question entity-linking) | ✅ Have — `hybrid-orchestration` |
| **Pattern Matching** | Neptune openCypher traversal over the resolved entity graph | ✅ Have — `graph-ingestion-resolution` |
| **Metadata Filtering / Self-Query** | Bedrock extracts structured filters from the question → OpenSearch *filtered* k-NN (filter applied during ANN search on the **Lucene** engine) | ✅ Have — `metadata-filtering` (formalizes the permission slice's seam into a real during-ANN filter, question-derived) |
| **Cypher Templates** | Expert-authored **parameterized openCypher** templates on Neptune; Bedrock selects the template and extracts parameters — the governed, auditable, low-risk enterprise path | ◔ Planned — `opencypher-templates` |
| **Parent-Child Retriever** | OpenSearch nested child-chunk vectors for precise matching → return the parent document body for context-complete synthesis | ✅ Have — `parent-child-retrieval` |
| **Text2Cypher** | Bedrock Claude → **Text2openCypher**, executed **read-only** against Neptune — the flexible-but-risky foil to Cypher Templates, with the guardrail made explicit (endpoint/validation mechanism decided at slice time) | ◔ Planned — `text2opencypher-guarded` |
| **Global Community Summary** (MS GraphRAG global) | Community detection over the entity graph + Bedrock-generated community summaries stored in Neptune; serves "summarize across the whole corpus" questions our seed-and-expand can't, **without a standing analytics service** — **Louvain** computed **in the Fargate ingest task** (not Neptune Analytics), ADR-0005 | ✅ Have — `global-community-summary` |
| **Local Retriever** (MS GraphRAG local) | Entity-vector seeding in OpenSearch → Neptune graph traversal | ○ Backlog (overlaps seed-and-expand) |
| **Dynamic Cypher Generation** | openCypher snippet library + Bedrock composition | ○ Backlog |
| **Hypothetical Question Retriever** | Bedrock pre-generates per-chunk questions at ingest → embed in OpenSearch | ○ Backlog |
| **Memory Graphs** (episodic / procedural / semantic / temporal) | — | ✗ Non-goal (agent memory; future extension) |

Two honesty notes that travel with this table: (1) these are Neo4j-Cypher
patterns **translated** to Neptune openCypher — close, not identical, and named
as such; (2) the Global Community Summary slice diverges from Microsoft's
reference pipeline on the clustering algorithm — it uses **Louvain**, not **Leiden**
(true Leiden would need an external `leidenalg` step; Louvain matches the managed AWS
option so the self-compute-vs-managed trade is apples-to-apples), and detection runs
**in the Fargate ingest task, not a standing Neptune Analytics service**. The divergence
is flagged, not papered over; [ADR-0005](adr/0005-community-detection-in-fargate-louvain.md)
records the decision and the [feasibility note](rfc/0001-notes/aws-feasibility.md) the detail.

### Ingestion pattern coverage (our taxonomy)

graphrag.com documents *retrieval* patterns; it does not enumerate ingestion as a
pattern space. This table is therefore **our taxonomy** — adapted from the
[LlamaIndex `PropertyGraphIndex`](https://developers.llamaindex.ai/python/examples/property_graph/property_graph_basic/)
extractor families and the
[Microsoft GraphRAG indexing stages](https://microsoft.github.io/graphrag/index/default_dataflow/),
**not** the graphrag.com catalog above. It names the ingestion patterns the repo
already implements so they read as deliberate points on a spectrum, organized by
ingestion stage, with the same glyph contract as the retrieval table (`Have` /
`Planned` / `Backlog`). Established by [RFC-0002](rfc/0002-ingestion-pattern-axis.md).

| Ingestion stage | Pattern | Our implementation | Status |
| --- | --- | --- | --- |
| **Extraction** | Structural / deterministic (no-LLM) | Front-matter + YAML + bounded labeled-field regex over prose; the `ImplicitPathExtractor` analog | ✅ Have — `graph-ingestion-resolution` |
| **Extraction** | **Schema-guided LLM** | Bedrock extracts triples constrained to a fixed entity/edge schema over the free-narrative relationships the deterministic pass leaves unextracted; trace emits prompt + schema + per-triple provenance | ◔ Planned — `schema-guided-extraction` |
| **Extraction** | Free-form LLM | Bedrock extracts unconstrained triples (the diverse, less-consistent end) | ○ Backlog |
| **Resolution** | Normalized-match + alias table (no model) | `normalize` + `aliases.yaml`; merge falls out of upsert | ✅ Have — `graph-ingestion-resolution` |
| **Resolution** | Fuzzy / embedding-based | Similarity-clustered resolution for the no-stable-ID case | ○ Backlog |
| **Chunking** | Sliding-window | 1000/150 over the prose-rich subset | ✅ Have — `vector-rag-baseline` |
| **Chunking** | Lexical / document-structure graph | Chunk nodes + structural edges (heading hierarchy, NEXT/PARENT) | ○ Backlog |
| **Graph build** | Community detection + summarization | Louvain in Fargate + Bedrock summaries (Louvain-not-Leiden divergence flagged above) | ✅ Have — `global-community-summary` |

Two honesty notes travel with this table, mirroring the retrieval table's: (1) the
taxonomy is **ours**, adapted from LlamaIndex / Microsoft prior art, not the
graphrag.com catalog; (2) the extraction-strategy spectrum (structural →
schema-guided → free-form) is the LlamaIndex `Implicit` / `Schema` / `Simple`
path-extractor axis named in our vocabulary. Patterns are classified by their
*dominant* stage — the stages are pipeline phases, not disjoint buckets (a pattern
can span two), so the table places each where its decision lives.

## What's NOT in this charter

To keep this file from becoming everything-and-the-kitchen-sink:

- **Decision history** lives in [`adr/`](adr/). The charter is what we
  believe; ADRs are the choices we made because of those beliefs.
- **Current product state** lives in [`product/`](product/). The charter
  is direction; product/ is where we are.
- **Current architecture state** lives in [`architecture/`](architecture/).
  Note the altitude: this charter's *Architecture patterns & approaches* section
  owns the **ordered considerations** (the stable, durable patterns a team
  weighs); `architecture/` owns the **current-state snapshot** of how the code is
  wired today, and the **ADRs** own the binding resolved choices. The charter
  orders and explains; it does not duplicate the snapshot or the decision record.
- **Conventions for how we work** live in [`CONVENTIONS.md`](CONVENTIONS.md).
- **Governance** (roles, decision-making processes, voting) lives in
  [`GOVERNANCE.md`](GOVERNANCE.md) if and when the project is large
  enough to need it. Most small/medium projects don't — a single
  maintainer or small group operating by consensus is fine, and forcing
  governance ceremony on a project that doesn't need it produces theater,
  not clarity.

## When to revise

Revise this charter when:

- The mission has actually changed (rare — usually means a fork).
- The scope has shifted enough that PRs are routinely landing for things
  the current scope doesn't cover.
- A principle has stopped resolving ties — it's being ignored, or it
  contradicts another principle in ways we haven't acknowledged.
- The ordered patterns have drifted from how the build actually resolved them,
  or a pattern's coverage status changed (e.g. a `Planned` pattern shipped).

Revise via RFC. Editing the charter directly without discussion is the
single fastest way to lose the trust this document is meant to build.
