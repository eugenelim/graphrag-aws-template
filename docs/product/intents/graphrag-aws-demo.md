# Intent: A reproducible enterprise-knowledge-platform demo on managed AWS — show when graph-augmented retrieval beats vector search under real enterprise concerns

- **Slug:** `graphrag-aws-demo`
- **Level:** `feature`
- **Scale:** `app`
- **Maturity:** `greenfield`
- **Parent intent:** _(none — top of this app's tree)_

## Outcome

The demo is the product, so the outcome is **qualitative-but-falsifiable**: it
is met when a watching architect can *correctly articulate the trade-off
unprompted* and *reproduce the pipeline on their own corpus*. It spans the three
pillars the demo must cover end to end — **ingestion**, **retrieval**, and
**search**.

- **Input (steerable):** two authored knobs. (1) The **contrasting query
  scenarios** the demo runs side-by-side across the three retrieval modes —
  vector-only, graph-only, hybrid GraphRAG — and the **legibility of each mode's
  retrieval trace** (what was retrieved, from which store, and why). (2) The set
  of **enterprise-platform behaviors exercised live** — ingestion from **two
  heterogeneous sources**, **permission-filtered retrieval**, and **incremental
  delta re-ingest** — each a behavior we choose to surface and narrate.
- **Outcome (lagging):** an architect who watches the demo can, without
  prompting, (a) state a query where graph traversal returns an answer vector
  similarity cannot, and the reverse; (b) name the AWS service doing each job
  across ingest → retrieve → search; (c) `git clone` the template and reproduce
  ingestion, retrieval, and search on Markdown corpora of their own; and (d)
  explain how the platform handles the enterprise concerns they actually face —
  **multiple sources, who-can-see-what, and keeping the index fresh** — pointing
  to where each is handled in the architecture.
- **Guardrail:** scope stays on **Markdown files and natural-language org
  entities** (teams, roles, processes, guides) — never functional-code parsing;
  **every step stays narratable live** — no black-box hop the presenter cannot
  explain on stage; and **synthetic constructs stay labeled synthetic** — the
  visibility labels we add are presented as a stand-in for real ACLs, never
  passed off as production authorization. If a "make it work" shortcut makes the
  data flow unexplainable, the demo has failed even if it runs.

## Opportunity

A team deciding how to ground an LLM on their organizational knowledge faces a
real fork: **is plain vector RAG enough, or does a knowledge graph earn its
keep?** The honest answer is "it depends on your query shapes" — but the
practitioner literature is mostly hand-wavy blog claims and vendor decks, with no
runnable, service-by-service reference they can poke at. And the blog demos
quietly dodge the concerns that actually block enterprise RAG: **knowledge lives
in many sources, not one; people may only retrieve what they're cleared to see;
and the corpus changes constantly.** A demo that ignores those isn't answering
the question an enterprise architect is actually asking.

The job to be done, solution-independent:

> "Help me **judge whether graph-augmented retrieval is worth it for my corpus**
> — show me, concretely and on infrastructure I already trust, how ingestion,
> retrieval, and search differ between vector, graph, and hybrid approaches, so I
> can decide and then **reproduce it myself**."

Two jobs sit behind that, served by one artifact:

- **Understand (the room).** A live audience needs to *see* the difference land
  — the same question, three retrieval strategies, visibly different answers and
  traces. Comprehension is the lagging outcome; the contrasting scenarios are how
  we steer it.
- **Reproduce (the architect).** An evaluator needs a template they can clone and
  point at their own Markdown so the "it depends" becomes a thing they can
  measure on their data, not take on faith.

A relatable, non-code corpus — a public handbook-style knowledge garden — makes
the entities (teams, roles, guides) and their relationships intuitive without
demanding domain expertise from the room, keeping attention on the *retrieval
mechanics* rather than the subject matter. Using **two overlapping sources**
(rather than one) lets the demo land the enterprise punchline graph owns and
vector cannot reach: the *same* entity appearing in both sources, resolved to a
single node — knowledge unified across silos.

## Assumptions

What must be true for the bet to pay off. `de-risk-intent` picks the riskiest,
predeclares a kill condition in its own currency, and tests it.

- **Entities resolve across two heterogeneous sources into single graph nodes.**
  The same team / role / person referenced in both sources can be matched and
  merged into one node — so cross-source unification is real, not staged.
  **✅ DE-RISKED 2026-06-23 — SURVIVED.** See the De-risk verdict below.
- **The two sources are chosen to genuinely overlap.** Source selection
  guarantees shared entities; without overlap, heterogeneity demonstrates
  nothing and the cross-source story collapses. **✅ RESOLVED — corpus chosen:
  Kubernetes `community` + `enhancements` (see verdict).**
- **The Markdown corpus parses cleanly into both stores.** Front-matter,
  headings, and relative inter-doc links resolve into discrete graph nodes/edges
  *and* coherent vector chunks. **(Co-riskiest — the whole demo rests on this
  dual-write being clean enough to narrate.)**
- **The three modes diverge visibly on real queries — and each has an *honest*
  win.** There exist query scenarios where vector-only wins on prose meaning,
  where graph-only wins on multi-hop structure, and where hybrid beats both — the
  contrast is real, not staged, and vector is a fair baseline rather than a
  strawman. If all three return the same answer, or vector never wins, the demo
  has no punch. **✅ DE-RISKED 2026-06-23 — SURVIVED (tri-modal validation; see
  second verdict below).**
- **Synthetic visibility labels are credible enough to teach authorization.**
  Labels we attach to documents (e.g. public / internal / confidential, or
  role-scoped) stand in convincingly for real enterprise ACLs and let
  permission-filtered retrieval demonstrate the concept across all three modes —
  with permissions modelled naturally as graph edges.
- **Incremental re-ingest keeps both stores consistent.** Editing, deleting, or
  moving a source doc re-ingests only the delta and updates vector *and* graph
  stores in step — no orphaned chunks, no stale nodes. This is demoable live on
  real git history.
- **CLI + architecture narration is enough.** The audience grasps the value
  through commands, retrieval traces, and a clear architecture story, without a
  polished graphical UI.
- **Managed AWS services compose without bespoke plumbing.** OpenSearch (vector),
  Neptune (graph), and Bedrock/Titan v2 (embeddings) inter-operate so the only
  custom code we own is the Markdown parsing, entity resolution, and hybrid query
  orchestration — not infrastructure glue.
- **One hybrid orchestration pattern is enough to teach the concept.** A single
  defensible pattern (vector-entry → graph-hop, *or* parallel-retrieve →
  merge-at-synthesis) carries the lesson; we don't need to demo both. _(Which one
  is an open architecture decision — deferred to the architect skills, not
  resolved here.)_
- **Knowledge surface:** in-repo doc set (greenfield template — knowledge base,
  backlog, and roadmap are empty scaffolding). No internal domain surface was
  available, so domain grounding leans on the **public Kubernetes `community` +
  `enhancements` corpus** (chosen at de-risk); confidence on org-specific framing
  is lowered accordingly.

## De-risk verdict (2026-06-23) — SURVIVED

- **Reversibility:** two-way door (nothing published; corpus/method cheap to
  swap early). → `prototype-led`, with the predeclared bar below.
- **Riskiest assumption tested:** cross-source entity resolution works — *and*
  overlapping public Markdown sources exist — well enough to demo live.
- **Kill condition (predeclared, before the probe):** proceed only if (1) a
  license-clear, mostly-Markdown public pair with confirmed shared entities can
  be named; (2) ≥ ~15 shared entities; (3) a narratable resolver — normalized
  match + small alias table, no trained model — is plausibly ≥ ~80%
  precision/recall; (4) ≥ 3 cross-source multi-hop scenarios where hybrid beats
  vector-only. *Easy-but-real resolution does not kill — for a live demo,
  narratable-and-works is the goal.*
- **Probe:** sourcing survey of candidate public corpus pairs (existing data, not
  rolled our own).
- **Result vs. line:** all four clauses cleared, several emphatically. Chosen
  corpus: **`github.com/kubernetes/community` + `github.com/kubernetes/enhancements`**
  (both Apache-2.0). Shared entities number in the hundreds — 27 SIG slugs appear
  in both repos; dozens of GitHub handles appear as both SIG chairs/leads
  (`sigs.yaml`) and KEP authors/approvers (`kep.yaml`). Resolution is guaranteed
  by construction: `kep.yaml`'s `owning-sig` uses the *same controlled-vocabulary
  slug* as the `community` SIG directory; handles are stable IDs; the only alias
  case is prose name ↔ `@handle`. Four concrete cross-source multi-hop scenarios
  identified.
- **What it revealed (fed back into Assumptions above):** (a) corpus is K8s
  SIG/KEP, not the GitLab handbook originally assumed — stronger heterogeneity
  (two independent repos) and a clean enterprise-org-knowledge mapping (SIG=team,
  chair/lead=role, KEP=decision, ownership edges); (b) old KEPs predate
  `kep.yaml` and carry metadata only in prose — keep as a realistic "messy data"
  wrinkle that justifies prose-based graph extraction; (c) GitLab handbook +
  `team_members` YAML is the **backup corpus** if we later want to demo *harder*
  resolution (prose name vs. handle), but it carries post-layoff staleness risk.
- **Open confirmation (cheap, do first in build):** clone both repos and run a
  ~20-line resolver over a sample of SIG slugs + handles to confirm the ≥80% bar
  empirically rather than purely by construction.

## De-risk verdict #2 (2026-06-23) — SURVIVED (tri-modal fitness)

- **Assumption tested:** the corpus is a *fair, strong* substrate for all three
  retrieval modes — not graph-rich but semantically thin (which would make
  vector-only a strawman and the comparison rigged).
- **Kill condition (predeclared, before fetching content):** pass only if, from
  *real* fetched content, each mode has ≥3 concrete honest wins — vector on
  prose-meaning queries (credible baseline, not strawman), graph on multi-hop
  structure, hybrid where neither alone suffices and the examples are genuine.
- **Probe:** fetched and read real KEP READMEs (1287 ~18k words, 3299, 2086), SIG
  Network README + charter, `governance.md`, `community-membership.md`,
  `sigs.yaml`, `kep.yaml`.
- **Result vs. line:** all three modes PASS. Vector — 4 prose-grounded wins from
  real excerpts (~2M words of prose across 326+ KEPs); credible baseline. Graph —
  4 multi-hop joins confirmed against real structured fields. Hybrid — 4 genuine
  both-needed queries (graph scopes the KEP set, semantic summarizes prose
  motivations/risks). Overall: a fair, balanced substrate.
- **What it revealed (decomposition inputs):**
  1. **Fairness lives in query selection, not corpus structure.** Vector looks
     weak if asked structural questions and strong if asked prose questions —
     so a **curated per-mode demo-query set (~5-6 each)** is a first-class
     deliverable, not an afterthought.
  2. **KEP prose is uneven** — flagship KEPs are prose-rich, small fix-KEPs
     terse. **Ingestion scope should favour prose-rich doc types** (KEP READMEs,
     SIG charters, `governance.md`, `community-membership.md`; optionally
     `contributors/design-proposals/` and SIG meeting notes for extra semantic
     depth), and vector showcase queries should target the rich subset.

## Decomposition

**Decomposed 2026-06-23.** App scale → the leaf feature intent projects to **one
`core` brief** at [`docs/product/briefs/graphrag-aws-demo.md`](../briefs/graphrag-aws-demo.md);
the five shippable slices below become that brief's **Spec map** rows (each →
one spec via `new-spec`). Cut **by shippability** (each slice ingests → retrieves
→ answers end-to-end and demos on its own), *not* by pillar or layer — an
"ingestion-only" slice would not ship a demoable increment.

| # | Slice (spec slug) | What ships (vertical, demoable alone) | Depends on |
| --- | --- | --- | --- |
| 1 | `graph-ingestion-resolution` | **Lead.** Parse Markdown + YAML from *both* K8s sources → extract entities/edges (SIG, person, KEP, subproject, ownership) → resolve cross-source into single Neptune nodes (normalized match + alias table) → CLI multi-hop graph query. Includes the de-risk "open confirmation" (resolver ≥80% on a labeled sample). | none |
| 2 | `vector-rag-baseline` | Chunk the prose-rich subset → Titan v2 embeddings → OpenSearch → CLI semantic query with retrieval trace + provenance. The fair vector baseline. | none |
| 3 | `hybrid-orchestration` | Orchestrate vector + graph into a hybrid answer + a **side-by-side three-mode runner** over the curated per-mode query set with visible traces. The demo's payoff (the contrast). | 1, 2 |
| 4 | `permission-filtered-retrieval` | Synthetic visibility labels on docs → propagate to both stores (graph edges + vector metadata filter) → persona/clearance flag → permission-filtered retrieval across all three modes. The authorization concern. | 1, 2 |
| 5 | `incremental-delta-reingest` | Git-delta detection (add/change/delete/move) → re-ingest only the delta → update both stores consistently (no orphan chunks, no stale nodes) → CLI before/after. The freshness concern. | 1, 2 |

**Cross-cutting deliverable (not a standalone slice):** the curated per-mode
showcase query set + presenter script (from De-risk verdict #2) accretes across
slices 1–3 and is consolidated in slice 3.

### Decomposition decisions

- **Cut by shippability, projected as one brief + a 5-row Spec map** — not as
  five child *feature intents*. The intent is large for a `feature`, but at app
  scale spinning up five children each through frame → de-risk → decompose is
  ceremony disproportionate to a single-repo demo; the slices are specs, which is
  exactly the feature-leaf output. The brief's Spec map carries the five.
- **Rejected the pillar cut (ingest / retrieve / search).** Tempting because the
  outcome names those three pillars, but an ingestion-only or retrieval-only slice
  can't ship a demoable increment — that's a layer cut, the named anti-pattern.
  The pillars survive as the *internal structure* of every slice, not as slice
  boundaries.
- **Lead with slice 1 (graph + cross-source resolution), per the de-risk verdict
  and explicit steer** — even though a pure `vector-rag-baseline` is the thinner
  walking skeleton and would de-risk the AWS ingestion plumbing earlier. The
  centerpiece risk is already retired (two SURVIVED verdicts), so ordering follows
  product value, not residual risk; slice 1 still stands up the ingestion pipeline
  and one datastore. _Flip to vector-first only if the AWS plumbing proves riskier
  than expected once the architect skills land._
- **Delivery order ≠ demo-narrative order.** Built 1→2→3→4→5; the final demo
  script narrates vector → graph → hybrid to build the contrast. Don't conflate.
- **Two enterprise concerns (slices 4, 5) sequenced after the three-mode core.**
  They depend on stores 1 & 2 existing and add enterprise credibility, but the
  core comparison (slices 1–3) is the irreducible demo; 4 and 5 are gated on
  appetite and could be deferred to the backlog without breaking the payoff.
