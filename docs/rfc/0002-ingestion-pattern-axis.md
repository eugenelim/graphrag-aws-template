# RFC-0002: Establish ingestion as a first-class pattern axis

- **Status:** Accepted (2026-06-27)
- **Author:** eugenelim
- **Approver:** eugenelim
- **Date opened:** 2026-06-27
- **Date closed:** 2026-06-27
- **Related:** [RFC-0001](0001-adopt-project-charter.md) (the charter this amends); [ADR-0004](../adr/0004-text2cypher-read-only-guard.md) (precedent: a narratable LLM hop); [ADR-0005](../adr/0005-community-detection-in-fargate-louvain.md) (ingestion-side community detection)

## The ask

- **Recommendation (BLUF):** Amend the charter (via this RFC, per CONVENTIONS § 1) to make **ingestion a first-class pattern axis** alongside retrieval: add an *Ingestion pattern coverage* table that names what the repo already does, add one extraction-strategy consideration to the ordered patterns spine, and clarify Principle 1 so "narratable" means *traceable*, not *no-LLM*. Commit exactly one new ingestion pattern — **schema-guided LLM extraction** — as `Planned`; name three more as `Backlog`.
- **Why now (SCQA):** *Situation* — the template implements the graphrag.com **retrieval** catalog richly (every committed pattern shipped) and documents it as a coverage contract. *Complication* — graphrag.com is a retrieval catalog, so ingestion is left as a single implicit point: deterministic, no-LLM extraction tuned to the controlled-vocabulary K8s corpus. The charter itself asks the adopter (pattern #1) *"do your sources share entities with stable IDs… or do you face harder prose↔handle resolution?"* — then only ever demonstrates the easy side. *Question* — should ingestion be modelled as a pattern spectrum the adopter reasons across, the way retrieval already is?
- **Decisions requested:**
  1. **Add an *Ingestion pattern coverage* table** (our taxonomy, not graphrag.com) + one new spine consideration. · recommended · decide-by: 2026-07-04 (default if no objection: adopt).
  2. **Adopt the stage × extraction-strategy taxonomy** grounded in the LlamaIndex `PropertyGraphIndex` extractors and the Microsoft GraphRAG indexing stages. · recommended · decide-by: 2026-07-04.
  3. **Commit one `Planned` pattern — schema-guided LLM extraction**; three others (`free-form LLM extraction`, `lexical/document-structure-graph chunking`, `fuzzy/embedding resolution`) stay `Backlog`. · recommended · decide-by: 2026-07-04.
  4. **Clarify Principle 1 and Scope**: narratable ⇒ inspectable inputs/outputs/decision with a trace, LLM hops allowed when traced. · recommended · decide-by: 2026-07-04.

## Problem & goals

**Diagnosis.** The repo treats *retrieval* as a spectrum of named, status-tracked patterns an adopter chooses among, but treats *ingestion* as a fixed substrate with one implementation. That asymmetry is a teaching gap, and it bites harder on the ingestion side than the retrieval side: retrieval patterns are swappable at read-time, but ingestion decisions are baked into the graph every retrieval pattern then reads. An adopter whose corpus lacks the K8s corpus's controlled-vocabulary entities — the common case — finds the template demonstrates the one extraction strategy that *doesn't* apply to them, and is silent on the ones that do.

The gap is not an oversight to paper over; it falls directly out of the charter's framing (the coverage table is the *graphrag.com retrieval* catalog) and Principle 1 as currently worded (read literally, "no black-box hop" plus pattern #1's "no trained model" reads as *no-LLM ingestion*). So closing it is a charter amendment, which is why it routes through an RFC rather than a spec.

**Goals.**
- Name the ingestion patterns the repo *already* implements, so they read as deliberate points on a spectrum rather than the only option.
- Add a small, prior-art-grounded ingestion taxonomy the adopter can reason across for their own corpus.
- Commit one high-teaching-value addition (schema-guided LLM extraction) that demonstrates the *contrast* deterministic extraction is one end of.
- Keep the change honest against the existing principles — narratable, fair-comparison, teardown-first — and against the locked-corpus / two-source non-goals.

**Non-goals** (could have been goals; deliberately dropped):
- **Building all four named ingestion patterns.** Only schema-guided LLM extraction is committed; the rest are named `Backlog` so the coverage contract is honest about commitment vs. naming (same discipline RFC-0001 used).
- **Adding a third corpus source to showcase messy extraction.** The contrast is demonstrable on the existing corpus (see De-risk); the "no sources beyond the two K8s repos" non-goal stands.
- **Replacing deterministic extraction.** It stays the default and the `Have` baseline; the LLM strategy is an additive, flagged contrast — not a migration.
- **A general ingestion framework.** Same as the charter's existing "not a general-purpose GraphRAG library" non-goal; these are reference examples, not a packaged extractor API.

## Proposal

Four concrete edits to `docs/CHARTER.md`, transcribed on acceptance (the verbatim-block discipline of RFC-0001).

### P1 — New "Ingestion pattern coverage" table

A second coverage table, parallel to the existing graphrag.com retrieval table, under its own heading that states plainly it is **our taxonomy** — grounded in the LlamaIndex `PropertyGraphIndex` extractor families and the Microsoft GraphRAG indexing stages — **not** the graphrag.com catalog (which documents retrieval only). Same glyph contract as the retrieval table (`Have` / `Planned` / `Backlog`), organized by ingestion stage:

| Ingestion stage | Pattern | Our implementation | Status |
| --- | --- | --- | --- |
| **Extraction** | Structural / deterministic (no-LLM) | Front-matter + YAML + bounded regex over prose; the `ImplicitPathExtractor` analog | ✅ Have — `graph-ingestion-resolution` |
| **Extraction** | **Schema-guided LLM** | Bedrock extracts triples constrained to a fixed entity/edge schema over the free-narrative relationships the deterministic pass leaves unextracted; trace emits prompt + schema + per-triple provenance | ◔ Planned — `schema-guided-extraction` |
| **Extraction** | Free-form LLM | Bedrock extracts unconstrained triples (the diverse, less-consistent end) | ○ Backlog |
| **Resolution** | Normalized-match + alias table (no model) | `normalize` + `aliases.yaml`; merge falls out of upsert | ✅ Have — `graph-ingestion-resolution` |
| **Resolution** | Fuzzy / embedding-based | Similarity-clustered resolution for the no-stable-ID case | ○ Backlog |
| **Chunking** | Sliding-window | 1000/150 over the prose-rich subset | ✅ Have — `vector-rag-baseline` |
| **Chunking** | Lexical / document-structure graph | Chunk nodes + structural edges (heading hierarchy, NEXT/PARENT) | ○ Backlog |
| **Graph build** | Community detection + summarization | Louvain in Fargate + Bedrock summaries (Louvain-not-Leiden divergence flagged) | ✅ Have — `global-community-summary` |

Two honesty notes travel with this table, mirroring the retrieval table's: (1) the taxonomy is **ours**, adapted from LlamaIndex / Microsoft prior art, not the graphrag.com catalog; (2) the extraction-strategy spectrum (structural → schema-guided → free-form) is the LlamaIndex `Implicit` / `Schema` / `Simple` path-extractor axis named in our vocabulary.

### P2 — One new ordered-spine consideration

**Appended as a new spine item #9** — *not* inserted after the current #1/#2, even though it logically groups with the ingestion considerations there. The spine items are referenced by number ("charter pattern 2", "charter pattern 7", …) in 30+ places across shipped specs and code; renumbering to insert mid-list would break every reference to patterns 3–8. Appending at #9 preserves them. The item's prose notes it belongs logically with #1–#2:

> **Extraction strategy — deterministic vs. LLM-assisted** *(an ingestion-stage consideration, grouped logically with #1–#2; placed last to preserve the spine's existing numbering).* Decide how entities and edges leave the text: deterministic rules where the corpus has controlled-vocabulary IDs (narratable, free, what we default to), or schema-guided / free-form LLM extraction where relationships live in prose. *Consider:* deterministic wins on clean structured corpora; the more your edges hide in narrative, the more an LLM pass earns its keep — at the cost of a hop you must keep narratable (trace the prompt, the schema, and per-triple provenance). → spine; slice *schema-guided extraction* (Planned).

### P3 — Principle 1 clarification

Amend Principle 1 to make explicit what is already de-facto true (synthesis and text2openCypher are accepted LLM hops):

> **Narratable over magical.** Every ingest → retrieve → search step must be explainable live; no black-box hop the presenter cannot narrate. *Narratable does not mean no-LLM* — an LLM hop is narratable when its inputs, outputs, and decision are inspectable in the trace (as Bedrock synthesis and text2openCypher already are). The bar is the trace, not the absence of a model.

### P4 — Scope edits

This RFC must strike *every* charter location that currently equates model-absence with narratability, or a surviving line will contradict the new Principle 1. The complete set, each with its disposition:

- **`CHARTER.md:84` (Principle 1 body, "ingest → … no black-box hop").** Amended by **P3** (the equivalence is removed; the bar becomes the trace). No further edit.
- **`CHARTER.md:42` (Scope, "the only custom code we own is … parsing, entity resolution, and hybrid query orchestration").** Generalized to add *"and the extraction-strategy variants (deterministic and LLM-assisted)."*
- **`CHARTER.md:130` (spine pattern #1, "no trained model, so it stays narratable").** Softened to *"deterministic for this controlled-vocabulary corpus; an LLM-assisted extraction contrast is a flagged, trace-narratable alternative — see the ingestion coverage table."*
- **The new ingestion table's "no model" cells** (deterministic extraction, normalized-match resolution) are **retained deliberately** — they describe a *technique* (this pattern uses no model), not the *equivalence* (no model ⇒ narratable) that P3 strikes. A "no-model" pattern stays a legitimate, recommended point on the spectrum; it is simply no longer the *definition* of narratable.
- The "Sources beyond the two Kubernetes repos" non-goal (`CHARTER.md:65`) is **unchanged** (the contrast rides the existing corpus).

## Options considered

**Decision-1 axis — which charter artifact hosts the ingestion taxonomy.** These exhaust the artifacts that *could* host it (the two existing coverage surfaces, the spine, or nothing):

| Option | Prior art | Trade-off | |
| --- | --- | --- | --- |
| **(a) New parallel coverage table + spine item** | RFC-0001's retrieval table shape | Honest provenance (ours ≠ graphrag.com); at-a-glance contract; small duplication with spine, managed by the same charter/`architecture/` altitude split | ★ |
| (b) Extend the graphrag.com table with ingestion rows | — | Misrepresents provenance: graphrag.com is retrieval-only, so ingestion rows would falsely read as catalog patterns | |
| (c) Spine considerations only, no table | RFC-0001's spine | Loses the at-a-glance coverage/commitment contract the glyph table gives | |
| (d) Do-nothing | — | Ingestion stays a single implicit point; the teaching gap and the charter's own unanswered pattern-#1 question persist | |

**Decision-2 axis — how to slice ingestion into named patterns** (patterns are classified by their *dominant* stage; within extraction the strategies are MECE along *LLM-involvement × schema-constraint*). The stages are pipeline phases, not disjoint buckets — a pattern can span two (schema-guided extraction that also emits chunk nodes touches Extraction *and* Chunking; single-parse dual-write deliberately fuses extraction and chunking in one pass). The table classifies by where each pattern's *decision* lives, and notes the overlap rather than pretending it away:

| Option | Prior art | Trade-off | |
| --- | --- | --- | --- |
| **(a) Stage × extraction-strategy spectrum** | LlamaIndex `PropertyGraphIndex` extractors; MS GraphRAG stages | Maps 1:1 onto established taxonomies; covers all stages without inventing categories | ★ |
| (b) Extraction-strategy spectrum only | LlamaIndex extractors | Omits resolution / chunking / graph-build stages where the repo also makes choices | |
| (c) Flat list mixing stages | — | No organizing axis; not MECE; invents the grouping | |

**Decision-3 axis — how much to commit** (do-nothing through commit-all):

| Option | Trade-off | |
| --- | --- | --- |
| Commit none (name all Backlog) | Closes the *documentation* gap but ships no contrast; the spectrum stays one-ended in practice | |
| **Commit one (`Planned` = schema-guided LLM extraction)** | Demonstrates the deterministic↔LLM contrast at its most teachable point; bounded appetite (one delivery pass) | ★ |
| Commit all four | Honest spectrum but multiplies appetite well beyond one delivery pass; against the catalog brief's appetite discipline | |

*Why schema-guided and not free-form, given both demonstrate the contrast?* Two reasons specific to this project. (1) **Narratability** (Principle 1): a fixed entity/edge schema makes the LLM's output checkable against a closed set the presenter can show, the way `text2opencypher-guarded` validates against a known grammar — free-form output has no such ground truth to narrate against. (2) **The enterprise lesson**: schema-guided is the *governed, auditable* extraction strategy, the ingestion-side analog of the `opencypher-templates` end of the retrieval governed-vs-flexible pair; free-form is the riskier `text2cypher`-style end and is the natural *next* `Backlog→Planned` promotion if a second extraction slice is funded. So this is a deliberate one-pattern commit at the safer, more teachable end — not a one-pattern stand-in for a two-pattern flagship.

**Decision-4 axis — what "narratable" forbids:** (a) no-LLM (status quo, forbids the contrast); **(b) traceable, LLM-allowed-if-traced** ★ (matches the two already-accepted LLM hops — synthesis, text2openCypher); (c) an explicit ingestion-only carve-out (narrower than needed and creates a second narratability definition). (b) is a clarification of existing practice, not a reversal.

Do-nothing (Decision-1 (d)) is the honest baseline for the whole RFC: the demo still works, but the charter keeps asking adopters a question (pattern #1) it never shows two answers to.

## Risks & what would make this wrong

- **Pre-mortem — the contrast is a strawman.** If deterministic extraction already captures everything useful in the corpus, schema-guided LLM extraction has no honest win and Principle 2 is violated. *Mitigation:* the De-risk spike shows real inter-entity edges live in the prose narrative the deterministic pass leaves unextracted (it reads prose only via labeled-field regex); and the **slice itself gates on a measured honest-win bar** at build time (as every mode's query set already does) — if it can't clear it, the slice doesn't ship and the row drops to `Backlog`. The RFC commits the *intent*, not an unearned win.
- **Pre-mortem — narratability erosion.** An LLM extraction hop could become the "black-box hop" Principle 1 forbids if its trace is thin. *Mitigation:* the spine item and table both require the trace to emit prompt + schema + per-triple provenance; this is an explicit AC for the slice, enforced the way `text2opencypher-guarded` enforces its trace.
- **Pre-mortem — "explainable *live*" gap.** Principle 1's bar is explainability *live* in the demo room, and the accepted LLM hops it cites (synthesis, text2openCypher) run at *query* time, where the trace is read live. An extraction hop runs at *ingest* time, off the demo's critical path. *Mitigation:* the slice persists the extraction trace as an artifact the presenter can **replay** (show the prompt, the schema, and the triples a given source span produced). The precedent is `global-community-summary` / ADR-0005: an ingest-time Bedrock step (community summarization) whose persisted output is narrated at query time rather than recomputed in the room. This RFC extends that affordance from "narrate the stored output" to "replay the prompt + per-triple provenance" — a heavier trace than ADR-0005 ships, made an explicit slice AC. The live bar is met by replay, not by running extraction in the room.
- **Pre-mortem — coverage-table rot.** Two tables drift out of sync with the build. *Mitigation:* the ingestion table uses the same glyph/commitment contract and the same `architecture/`-vs-charter altitude split RFC-0001 already maintains; statuses roll up from slice `Status:` fields, not hand-edited.
- **Key assumptions (falsifiable):**
  - *The K8s prose bodies contain extractable edges deterministic rules miss.* Falsified if a sample of SIG READMEs / KEP Motivation sections yields no inter-entity relationships beyond what front-matter already encodes.
  - *An LLM extraction hop can be made as narratable as text2openCypher.* Falsified if per-triple provenance can't be tied back to source spans in a trace a presenter can read aloud.
  - *Adopters experience ingestion strategy as a real decision.* Falsified if every realistic corpus is either trivially structured or needs bespoke work the spectrum doesn't inform.
- **Principle 6 ("managed services, minimal glue") — reconciled, not waived.** Principle 6 is about not building *infrastructure*; the extraction hop is Bedrock-composed (managed), so it does not add infra glue. The genuinely new custom surface is the **schema definition, the prompt, and the trace-emit** — application logic of the same kind the charter already says we own (parsing, resolution, orchestration). The approver should rule explicitly, but the RFC's position is that Principle 6 is **satisfied**: no new standing service, no new infra, and the added code is in the "lesson" layer the charter deliberately keeps custom.
- **Drawbacks (not "none"):** a second coverage table is more surface to maintain and a slightly heavier charter; committing a `Planned` slice spends appetite that could go to a retrieval `Backlog` item (e.g. Local Retriever); and even staying within Principle 6, the schema+prompt+trace code is more custom surface to maintain than the deterministic extractor it sits beside. These are accepted as the cost of closing a gap the charter itself surfaces.

## Evidence & prior art

- **Spike / de-risk result.** Riskiest assumption: schema-guided LLM extraction can earn an honest win on the *locked* K8s corpus without a third source. **Survives** (analytical spike against `packages/graphrag/src/graphrag/extract.py` + corpus): the deterministic pass reads prose bodies, but only via **labeled-field regex** — it matches lines like `Authors:` (`_PROSE_AUTHORS`, `extract.py:19`, applied to `md.body` at `extract.py:159`) and otherwise routes prose to the *vector* index (`chunk.py`), not the *graph*. It therefore extracts **no free-narrative inter-entity edges** — cross-SIG collaboration, KEP supersession/dependency, and informal ownership relationships stated in the narrative of SIG READMEs and KEP `Motivation`/`Alternatives` sections. An LLM pass over those same documents surfaces those edges the deterministic rules structurally cannot reach — an honest win on the existing corpus, so the two-source non-goal holds. Residual risk (the narrative may be sparser than expected) is what the slice's honest-win bar gates at build time, not this RFC; **this deferral is legitimate only because the analytical premise above is now stated correctly** (labeled-field-only graph extraction, not "ignores prose").
- **Repo precedent.**
  - `docs/CHARTER.md:48,171` / RFC-0001 — the existing coverage table is explicitly the *graphrag.com retrieval* catalog; ingestion needs its own table, not a row in that one.
  - `docs/adr/0004-text2cypher-read-only-guard.md:124` + `docs/specs/text2opencypher-guarded/` — precedent that an LLM hop is narratable by trace, and the governed-vs-flexible pairing this RFC mirrors.
  - `docs/product/briefs/graphrag-pattern-catalog.md:71` — precedent for "appetite-gated; one flagship contrast is the highest-value sub-bet."
  - Charter `Backlog` row "Hypothetical Question Retriever … at ingest" — precedent that an ingestion-side pattern can sit in the coverage contract.
  - `docs/specs/graph-ingestion-resolution/spec.md:81` ("anything learned is out of bounds") — the slice-level bound this RFC relaxes at charter level.
- **External prior art.**
  - [LlamaIndex — Property Graph Index](https://developers.llamaindex.ai/python/examples/property_graph/property_graph_basic/): `ImplicitPathExtractor` works **without an LLM**; `SimpleLLMPathExtractor` uses one; both are the defaults.
  - [LlamaIndex — Comparing LLM Path Extractors](https://developers.llamaindex.ai/python/examples/property_graph/dynamic_kg_extraction/): `SchemaLLMPathExtractor` limits output to a predefined schema ("more consistent but potentially less comprehensive"); `SimpleLLMPathExtractor` is schema-free and "diverse"; `DynamicLLMPathExtractor` is the middle ground. This is the extraction-strategy axis.
  - [Microsoft GraphRAG — Indexing Dataflow](https://microsoft.github.io/graphrag/index/default_dataflow/): six stages — Compose TextUnits → Document Processing → **Graph Extraction (LLM)** → **Graph Augmentation (Leiden)** → **Community Summarization (LLM)** → Text Embedding. Grounds ingestion as a multi-stage space and confirms the Louvain-vs-Leiden divergence the charter already flags.

## Open questions

None. Every decision is resolved with a recommended default above; the per-slice mechanism (which Bedrock model, schema shape, trace format) is decided at slice time, not here, consistent with RFC-0001's "mechanism decided at slice time" rule.

## Follow-on artifacts

Accepted 2026-06-27 by the sole maintainer (lifecycle compressed `Draft → Accepted`; no separate FCP, consistent with the single-maintainer/consensus model RFC-0001 records). Applied in the accepting PR:
- **`docs/CHARTER.md`** gained the *Ingestion pattern coverage* table (P1), the new spine consideration as **item #9** (P2 — appended, not inserted, to preserve the spine's existing numbering), the Principle 1 clarification (P3), and the Scope edits (P4) — transcribed from this RFC's Proposal.
- This RFC's row in [`docs/rfc/README.md`](README.md) set to `Accepted`.

Deferred follow-on (not in the accepting PR):
- **A new spec** `docs/specs/schema-guided-extraction/` (via `new-spec`) carries the one `Planned` pattern; it may surface its own ADR at spec time (e.g. the extraction schema, or the prose-body chunk boundary for extraction) — decided per slice, not here.
- **`docs/product/briefs/graphrag-pattern-catalog.md`** spec map is auto-derived by the coverage lint from each spec's `Status:` once the slice is scaffolded — not hand-edited here.
