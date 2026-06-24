# RFC-0001 — Adopt the project charter (mission, scope, principles, architecture patterns)

- **Status:** Accepted (2026-06-23)
- **Author:** eugenelim
- **Created:** 2026-06-23
- **Last updated:** 2026-06-23
- **Discussion:** [PR #2](https://github.com/eugenelim/graphrag-aws-template/pull/2)
- **Follow-on artifacts:** the Detailed-proposal block was transcribed into
  [`docs/CHARTER.md`](../CHARTER.md) (link depths renormalized per the Follow-on
  note); [`CONVENTIONS.md` § 1](../CONVENTIONS.md) amended to permit the
  patterns spine for reference-template projects; the catalog brief's slices are
  now schedulable.

## Summary

`docs/CHARTER.md` ships as an unfilled template. This RFC proposes the
project's first real charter — **mission, scope, principles, and an ordered set
of architecture patterns & approaches** — synthesized from the artifacts the
project already produced (the framed intent, the received brief, the accepted
architecture design, and ADR-0001 / ADR-0002). The charter is `living` but
rarely changed, and `CONVENTIONS.md` § 1 requires substantive charter content to
go through an RFC; this is that RFC. On acceptance, the **Detailed proposal**
block below is dropped into `docs/CHARTER.md` verbatim.

This charter deliberately extends the minimal CNCF shape with an **ordered
architecture-patterns section**, because for this project *the documentation is a
key deliverable* — adopting teams clone the repo to work through their own
ground-an-LLM-on-our-knowledge considerations, and the ordered patterns are the
spine they reason along. The convention tension this creates (§ 1 says
architecture state lives in `architecture/`) is addressed in Drawbacks and
resolved by a follow-on CONVENTIONS amendment.

## Motivation

The repo has a clear, well-de-risked direction recorded across several
documents, but no single page that states the *why* in language a newcomer —
human or agent — can read whole in two minutes. That gap costs us in three
concrete ways:

- **Scope drift has nowhere to bounce off.** The brief's non-goals are real
  (no production authz, no GUI, no third source), but they live in a
  feature-level brief, not in the foundational document an agent reads first.
  When a request arrives to, say, "make the visibility labels enforce real
  IAM," there is no charter-level "does not" bullet to point at.
- **The principles that resolve ties are implicit.** "Narratable over magical"
  and "vector must be a fair baseline" are load-bearing decisions from the two
  SURVIVED de-risk verdicts and the design's guardrails — but they're currently
  reconstructable only by reading the intent and design end to end. A tie
  between "make the demo impressive" and "keep every hop explainable" should be
  resolved by a written principle, not re-litigated each time.
- **The charter is the citation root.** ADRs, specs, and architecture docs cite
  upward; the charter is the top of that chain. An empty top means the chain
  dangles.
- **The documentation is a deliverable, and the patterns have no ordered home.**
  This repo is a *reference template*: adopting teams clone it to work through
  their own "should a graph earn its keep for our corpus?" decision. The
  architecture *reasoning* — which patterns, in what order, and the consideration
  each forces — is therefore product, not byproduct. Today it's spread across the
  design doc, two ADRs, and the brief's spec map; no single artifact presents it
  as the *ordered set of considerations* an adopter walks. The charter is the
  natural home for that spine because it's the stable, read-first page and the
  citation root the per-pattern artifacts already hang off.

The mission and scope are stable enough to commit: the corpus is locked
(Kubernetes `community` + `enhancements`), two de-risk verdicts SURVIVED, and the
two architecture decisions are Accepted (ADR-0001, ADR-0002). This is the right
moment to pin the foundation — early enough to steer the build, late enough that
the content won't churn.

## Detailed proposal

The text below is the proposed body of `docs/CHARTER.md` (the template's framing
comments and the standing "What's NOT in this charter" / "When to revise"
sections are retained as-is and omitted here for brevity).

---

### Mission

A clone-and-deploy AWS **reference template** that lets an architect *see* — and
then *reproduce on their own Markdown corpus* — when graph-augmented retrieval
beats plain vector search for grounding an LLM on organizational knowledge, and
that documents the architecture patterns and trade-offs as a deliverable teams
reason through for their own context.

### Scope

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
  Markdown/YAML parsing, entity resolution, and hybrid query orchestration — not
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

### Principles

1. **Narratable over magical.** Every ingest → retrieve → search step must be
   explainable live; no black-box hop the presenter cannot narrate. The hybrid
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

### Architecture patterns & approaches (the considerations, in order)

These are the architectural decisions *any* team grounding an LLM on their own
knowledge has to work through. The demo resolves each one for the Kubernetes
corpus; the deliverable for an adopting team is this **ordered set of
considerations and where each is decided**, so they can re-decide for their own
context. Each pattern is an application of the principles above — narratable,
honest, reproducible, teardown-first — to one stage of the pipeline. The
*current* shape of the code lives in [`architecture/`](../architecture/) and the
binding choices in the cited ADRs; this list is the stable spine that orders
them.

1. **Corpus & cross-source entity resolution.** Pick sources that genuinely
   overlap and resolve their shared entities into single graph nodes via
   normalized match + a small alias table — no trained model, so it stays
   narratable. *Consider for your corpus:* do your sources share entities with
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
   compute runs inside the VPC. →
   **ADR-0002**.
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

### Pattern coverage against the graphrag.com catalog

[graphrag.com](https://graphrag.com) is the de-facto vocabulary for GraphRAG
retrieval patterns, but it documents them **only against Neo4j/Cypher**. This
template provides an openCypher/Neptune implementation of the enterprise-relevant
subset, wired on OpenSearch + Neptune + Bedrock. The table maps catalog patterns
to our slices (the mapping is not 1:1 with the ordered considerations above — one
slice can carry several patterns) and is the coverage contract: `Have` is on the
committed core-demo path, `Planned` is committed by this RFC, `Backlog` is named
but not scheduled, `Non-goal` is out of scope by the Scope section above.
(Nothing is built yet — the demo brief's slices are unscaffolded; these glyphs
track *commitment*, not implementation, which the brief status rolls up.) AWS
feasibility for every `Have`/`Planned` row was verified against current AWS docs
(see [`0001-notes/aws-feasibility.md`](0001-notes/aws-feasibility.md)); the
per-pattern *mechanism* (which library, which endpoint) is decided at slice time,
not here, so the cells stay at service-and-shape altitude.

| graphrag.com pattern | AWS implementation (our stack) | Status |
| --- | --- | --- |
| **Basic Retriever** (vector RAG) | OpenSearch k-NN + Titan v2 embeddings | ✅ Have — `vector-rag-baseline` |
| **Graph-Enhanced Vector Search** | OpenSearch hit → Neptune openCypher traversal (our *seed-and-expand* hybrid is a superset, seeding from both semantic hits and question entity-linking) | ✅ Have — `hybrid-orchestration` |
| **Pattern Matching** | Neptune openCypher traversal over the resolved entity graph | ✅ Have — `graph-ingestion-resolution` |
| **Metadata Filtering / Self-Query** | Bedrock extracts structured filters from the question → OpenSearch *filtered* k-NN (filter applied during ANN search) | ◔ Planned — `metadata-filtering` (planned to ride the permission slice; formalized as its own example) |
| **Cypher Templates** | Expert-authored **parameterized openCypher** templates on Neptune; Bedrock selects the template and extracts parameters — the governed, auditable, low-risk enterprise path | ◔ Planned — `opencypher-templates` |
| **Parent-Child Retriever** | OpenSearch nested child-chunk vectors for precise matching → return the parent document body for context-complete synthesis | ◔ Planned — `parent-child-retrieval` |
| **Text2Cypher** | Bedrock Claude → **Text2openCypher**, executed **read-only** against Neptune — the flexible-but-risky foil to Cypher Templates, with the guardrail made explicit (endpoint/validation mechanism decided at slice time) | ◔ Planned — `text2opencypher-guarded` |
| **Global Community Summary** (MS GraphRAG global) | Community detection over the entity graph + Bedrock-generated community summaries stored in Neptune; serves "summarize across the whole corpus" questions our seed-and-expand can't, **without a standing analytics service** (compute-location and algorithm decided at slice time — see notes) | ◔ Planned — `global-community-summary` |
| **Local Retriever** (MS GraphRAG local) | Entity-vector seeding in OpenSearch → Neptune graph traversal | ○ Backlog (overlaps seed-and-expand) |
| **Dynamic Cypher Generation** | openCypher snippet library + Bedrock composition | ○ Backlog |
| **Hypothetical Question Retriever** | Bedrock pre-generates per-chunk questions at ingest → embed in OpenSearch | ○ Backlog |
| **Memory Graphs** (episodic / procedural / semantic / temporal) | — | ✗ Non-goal (agent memory; future extension) |

Two honesty notes that travel with this table: (1) these are Neo4j-Cypher
patterns **translated** to Neptune openCypher — close, not identical, and named
as such; (2) the Global Community Summary slice will diverge from Microsoft's
reference pipeline on the clustering algorithm — the managed AWS options ship
**Louvain**, not **Leiden** (true Leiden would need an external `leidenalg`
step), so the slice states which it used. The divergence is flagged, not papered
over; the feasibility note carries the detail.

---

## Drawbacks

- **Committing principles early can ossify.** If the build surfaces a reason to,
  say, demo two hybrid patterns rather than one, principle 1's "narratable"
  framing shouldn't be read as forbidding it. *Mitigation:* the charter's own
  "When to revise" section plus the RFC route keep it contestable; principles
  resolve ties, they don't forbid revisiting.
- **Seven principles is the top of the 5–7 band.** Principle 5 ("synthetic stays
  synthetic") reads close to 1 and 7, but it is kept because it catches a failure
  the others don't: 1 governs *explainability* (can you narrate the hop) and 7
  governs *what we deliver* (the docs are the product) — neither forbids
  *misrepresenting* a synthetic construct as a real ACL, which is principle 5's
  job. The distinct failure mode earns the slot. If it ever stops resolving a tie
  in practice, fold it.
- **The ordered architecture-patterns section departs from the minimal charter
  shape and overlaps `architecture/`.** CONVENTIONS § 1 says "current architecture
  state → `architecture/`, decisions → `adr/`," and the charter template's "What's
  NOT in this charter" repeats it — so a reviewer will rightly ask why patterns
  live here. *The distinction that resolves it:* the charter captures the
  **pattern and the consideration to weigh** (durable, stable, teaching content);
  the **current-state snapshot** of how the code is wired stays in
  `architecture/`, and the **binding resolved choice** stays in the ADRs, which
  the list cites rather than restates. The section is a *map and an ordering*, not
  a second copy of the design. *Mitigation:* a follow-on CONVENTIONS § 1 amendment
  (below) makes this explicit so the two documents don't silently disagree — and
  if the list ever starts duplicating ADR/`architecture/` detail rather than
  ordering it, that's the signal it has drifted out of charter altitude.
- **A pattern list can rot as the build teaches us things.** The entries above
  reflect the design as accepted today. *Mitigation:* they're framed as durable
  *considerations* (the questions every adopter faces), not as implementation
  detail, so they're stable against code churn; each cites a living artifact that
  carries the detail, and material change routes back through this RFC process.

## Alternatives considered

- **Leave the charter as a stub (a few lines) per Profile A.** The scaling-profile
  guidance says a microservice can keep CHARTER.md to a few lines. *Rejected:*
  this repo is a *teaching template* whose non-goals are unusually load-bearing —
  the whole point is showing where to stop — so an explicit scope/principles page
  earns its keep here even at small contributor count.
- **Fold the charter content into the brief / intent and skip a standalone page.**
  *Rejected:* those are feature-altitude and product-altitude artifacts; the
  charter is the project-altitude citation root, and the document hierarchy in
  CONVENTIONS depends on it existing as a distinct layer.
- **Edit `CHARTER.md` directly without an RFC.** *Rejected:* CONVENTIONS § 1 and
  the charter template both require substantive charter content to route through
  an RFC. Editing it directly is named as "the single fastest way to lose the
  trust this document is meant to build."
- **Keep the architecture patterns out of the charter and only in
  `architecture/` + ADRs (the standard hierarchy).** This is the convention-pure
  option. *Rejected for this project specifically:* the documentation is a key
  deliverable — adopters clone the repo to reason through these very decisions —
  and `architecture/`/ADRs answer "what *we* built / decided," not "what *you* must
  consider, in what order." No artifact owned that ordered-considerations view, so
  it has to live somewhere read-first and stable. The charter is that place;
  generic single-app repos that aren't teaching templates should keep this section
  out and follow the standard hierarchy.
- **Put the ordered patterns in `architecture/overview.md` instead.** *Rejected:*
  `overview.md` is *descriptive* (the map of code as-built) and `reference.md` is
  the *normative golden path* — both are current-state, contributor-facing, and
  churn with the code. The considerations spine is adopter-facing, stable, and
  belongs at the citation root, not in a living current-state doc.

## Resolved during drafting

- **Mission wording — "reference template," not "reference demo."** Settled to
  foreground reproducibility and the documentation-as-deliverable framing over the
  live-presentation reading. The body uses it throughout.

## Unresolved questions

- **Does the project ever need a `GOVERNANCE.md`?** Not now (single maintainer /
  consensus). The charter's standing section already points there *if and when*
  contributor count forces it. No action this RFC.

## Follow-on artifacts

On acceptance:

- The **Detailed proposal** block replaces the placeholders in
  [`docs/CHARTER.md`](../CHARTER.md) (Mission, Scope, Principles) and adds the
  **Architecture patterns & approaches** section. The template's "What's NOT in
  this charter" and "When to revise" sections stay as shipped (the former gains a
  one-line note distinguishing the *ordered considerations* the charter now owns
  from the *current-state* docs in `architecture/`). The one relative link in the
  block is written `../architecture/` here so it resolves from `docs/rfc/`; on
  transcription it renormalizes to `architecture/`, its correct depth from
  `CHARTER.md` — the only edit to the otherwise-verbatim block.
- **CONVENTIONS § 1 amendment.** Add a sentence to "What goes here" allowing the
  ordered architecture-patterns spine *for reference-template projects*, with the
  pattern-vs-current-state distinction this RFC draws, so § 1 and the charter
  don't disagree. (A convention edit is the standard follow-on of an Accepted RFC,
  per CONVENTIONS § 3.)
- **A sibling brief carries the five `Planned` patterns.** They are a distinct
  received outcome at a different appetite, so they live in their own brief,
  [`product/briefs/graphrag-pattern-catalog.md`](../product/briefs/graphrag-pattern-catalog.md)
  (drafted alongside this RFC so the scope is reviewable here), and as a "Later"
  theme on [`product/roadmap.md`](../product/roadmap.md) — *not* bolted onto the
  demo brief. Acceptance schedules that brief's slices; rejection withdraws the
  brief. Each pattern is one `new-spec` away; none is on the demo's
  irreducible-core path.
- **Doc-wording correction (from the feasibility note § 6).** Soften the absolute
  "Neptune has no public endpoint" in
  [`architecture/.../design.md`](../architecture/graphrag-aws-architecture/design.md)
  to "VPC-only by default, IAM-enforceable." ADR-0002's body is frozen, so its
  Context wording gets a status-only correction note rather than an in-place edit
  (or a superseding ADR if the maintainer prefers) — flagged for the maintainer to
  choose.
- This RFC's row in [`docs/rfc/README.md`](README.md) is set to `Accepted` (after
  the `Open → Final Comment Period` steps in the documented lifecycle).
- The two governing architecture decisions
  ([ADR-0001](../adr/0001-hybrid-orchestration-seed-and-expand.md),
  [ADR-0002](../adr/0002-ephemeral-vpc-store-topology.md)) already exist and are
  cited by the patterns section as the decisions they resolve. The `Planned`
  pattern slices may each surface their own ADR at spec time (e.g. the
  Text2openCypher read-replica guardrail, or computing communities in Fargate vs.
  Neptune Analytics) — decided per slice, not by this RFC.

## References

- [graphrag.com](https://graphrag.com) — the GraphRAG pattern catalog (Neo4j; CC
  BY 4.0) this template implements on AWS.
- [`0001-notes/aws-feasibility.md`](0001-notes/aws-feasibility.md) — promoted
  AWS-capability verification (Neptune Analytics / openCypher / OpenSearch /
  Bedrock), with citations to current AWS docs, backing every `Have`/`Planned`
  row.
- [Intent](../product/intents/graphrag-aws-demo.md) ·
  [Brief](../product/briefs/graphrag-aws-demo.md) ·
  [Design doc](../architecture/graphrag-aws-architecture/design.md) — the framing
  and decisions this charter synthesizes.
