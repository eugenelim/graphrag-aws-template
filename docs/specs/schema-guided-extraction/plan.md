# Plan: schema-guided-extraction

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Schema-guided extraction is the **structural mirror of the deterministic extractor**,
with the entity/edge authority *widened* from labeled-field regex to an LLM — and the
guard moved from "the rules only ever emit what they matched" to **run-time validation
+ grounding**. The shape mirrors the retrieval-side governed-vs-risky pair
(`opencypher-templates` ↔ `text2opencypher-guarded`): this slice is the **governed,
auditable** end (a closed schema the model is held to), so its guard is ADR-0006's
closed-schema validator + entity-grounding, the ingestion analog of ADR-0004's
read-only validator + IAM backstop.

The pieces, each a familiar `packages/graphrag` stereotype:

- An **extractor** seam (`extract_llm.py`): a `TripleExtractor` protocol with a
  `BedrockTripleExtractor` (Converse, mirroring `BedrockClaudeSynthesizer`'s
  untrusted-data posture) for the live path and a deterministic non-semantic
  `RuleTripleExtractor` for CI/offline, plus the `EXTRACTION_SCHEMA` constant (the
  closed set of LLM-extractable edge kinds + the schema description shown to the model).
- A **closed-schema validator** (`validate_triple`) — the first guard, rejecting any
  triple whose predicate or endpoint kind is off-schema.
- An **entity-grounding** check (`ground_triple`) — the second guard, resolving each
  endpoint via the existing `normalize` functions and dropping any triple naming an
  entity the deterministic graph did not already resolve (no new resolver).
- An **orchestrator** (`extract_schema_guided`) wiring extract → validate → ground →
  stamp into an `ExtractionResult` whose `.render()` is the per-triple audit trace.
- An **ingest phase** (`_schema_extraction_writeback` in `apps/ingestion/entrypoint.py`)
  that runs the orchestrator after the deterministic graph write, on `MODE=full`/`rebuild`
  only — mirroring `_community_writeback`'s **injectable-store + MODE-scoping** shape
  (ADR-0005), **but adding a `SCHEMA_EXTRACTION` flag gate** that community detection has
  none of: community detection keys its live trigger off `NEPTUNE_ENDPOINT` and runs
  unconditionally, whereas this pass must be **no-op-by-default** even on a deployed task
  (the default-off contract, AC5).
- A **read-side provenance** change: the `expand`/seed-and-expand trace (and the graph
  templates) surfaces each traversed edge's `extraction_method`, so the
  write-side stamp is matched by a read-side marker (AC11) — without it, the existing
  all-edge-kinds traversal blends LLM edges into answers silently.
- A **CLI verb** (`extract-llm`) exposing the pass offline + live for the demo.
- The **honest-win gate**: a pinned gold set of prose edges absent from the
  deterministic graph + a test that fails the slice if the bar isn't cleared.

The riskiest parts are (1) the **entity-grounding** check — it must reuse `normalize`
exactly so an LLM-asserted endpoint resolves to the *same* id the deterministic pass
produced (else a real relationship between known entities is falsely dropped),
mitigated by reusing the resolver functions verbatim and pinning the exemplar; and (2)
the **honest-win bar** — it must be measured against gold prose edges genuinely absent
from the deterministic graph (RFC-0002 pre-mortem: a strawman contrast violates
Principle 2), mitigated by hand-authoring the gold set from the actual SIG-README /
KEP-Motivation prose and asserting each gold edge's absence from the deterministic
graph as part of the test.

## Constraints

- **ADR-0006** — the guard is the closed-schema validator + entity-grounding +
  distinguishable `extraction_method` provenance + the per-triple replayable trace; the
  pass runs ingest-time, default-off, honest-win-gated. The synth fitness test (T7) is
  the ADR's named Confirmation. ADR-0006 is **Accepted** (the guard decision stands);
  the charter-row `Planned → Have` flip remains gated on the live honest-win (T9/T10).
- **RFC-0002 / charter ingestion table** (*Schema-guided LLM* row) — this slice ships
  that one `Planned` *Extraction*-stage pattern; free-form extraction + fuzzy resolution
  stay `Backlog`.
- **Charter Principle 1 (clarified)** — narratable ⇒ traceable; the LLM hop is allowed
  *because* its prompt/schema/per-triple provenance are inspectable in the replayable
  trace. Principle 2 — the contrast must be a measured honest win (T8). Principle 4/5 —
  additive, default-off, teardown-first, no new resource/grant.
- **ADR-0005** — the ingest-time Bedrock hop shape (Fargate task, reuse the task's
  Converse grant, persist output, narrate later); this slice extends the "narrate stored
  output" affordance to "replay per-triple provenance".
- **ADR-0001** — reuse the `Synthesizer`/Converse seam + untrusted-data posture; no new
  model for extraction (reuse the synthesis Converse model, so no widened Bedrock grant).
- **ADR-0002** — ride the existing Fargate task + Neptune cluster + corpus bucket; add no
  billable resource; Budgets unchanged at `150`; the grant is reused, never widened.
- **ADR-0003** — IaC stays AWS CDK Python.
- **`packages/graphrag/AGENTS.md`** — runtime deps stay `pyyaml` + `boto3>=1.35` (no NLP/
  parser dependency); the extraction modules are **ingest-only** and must not enter the
  query Lambda's import graph; the offline extractor is labeled non-semantic.

## Construction tests

Most tests live under their task. Cross-cutting:

**Integration tests:**
- Offline end-to-end (`extract_schema_guided` over the fixture corpus with
  `RuleTripleExtractor` + in-memory store) on the pinned exemplar (a SIG-collaboration
  edge stated in a SIG README that the deterministic graph lacks) — asserts the
  candidate validates, grounds to two known SIG ids, is written stamped
  `schema-guided-llm`, and `.render()` emits doc/span → triple → verdict → edge in order
  (T4).
- A **flag-off** ingest run produces a graph **byte-identical** to the deterministic-only
  graph (no LLM edges, no trace); a **flag-on** run gains exactly the validated edges; a
  `MODE=delta` run never invokes the pass (T5).
- The honest-win gold set: every gold edge resolves in the fixture corpus **and is absent
  from the deterministic graph** (relationship-level absence against `resolve()`), and the
  offline pass **plumbs** every gold edge through validation/grounding/stamping (T8) — the
  recall/precision *measurement* is the **live** gate (T9), not this seeded offline check.

**Manual verification:** AC6 `extract-llm` CLI exercised end-to-end (offline trace
render); AC9 live deploy + schema-guided ingest smoke (run — live AWS is available),
including a live query traversing an LLM-only edge and a trace-artifact replay.

## Design (LLD)

Stack: Python 3.11+, `boto3` `bedrock-runtime` Converse, AWS CDK Python. Conforms to the
existing `packages/graphrag` module stereotypes (a pure logic module + an injectable
adapter + an orchestrator + a CLI verb), mirroring `validate`↔`templates`,
`generate`↔`select`, `text2cypher`↔`governed` on the retrieval side.

### Design decisions
- **The model authors *which entities relate and how*; the guard is closed-schema
  validation + entity grounding, not parameterization.** This is the deliberate contrast
  with deterministic extraction — there is no labeled field to read; the model proposes
  triples and the validator/grounding accept only schema-conforming triples between
  already-resolved entities. *Rejected:* trusting model entities (would invent nodes —
  ADR-0006). Traces to: AC1, AC2, AC4.
- **LLM-extractable edge kinds are a small closed addition to `EdgeKind`, *disjoint*
  from the deterministic set, distinguished by an `extraction_method` edge prop set
  authoritatively.** Extending the enum keeps LLM edges traversable by the existing
  retrieval paths (the teaching payoff) while the prop keeps them distinguishable. The
  disjointness is **load-bearing**: it guarantees an LLM edge never shares a `(src,
  kind, dst)` key with a deterministic edge, so the `setdefault` merge in `upsert_edge`
  (`model.py:111-121`) can never mislabel a deterministic edge or strip an LLM stamp;
  `extraction_method` is set, not `setdefault`-merged. *Rejected:* a separate edge
  namespace/store (duplicates the graph surface for no narratability gain — ADR-0006);
  overlapping kinds (would reintroduce the collision). Traces to: AC1, AC4, AC5.
- **Distinguishability is enforced at read as well as write.** The
  `expand`/seed-and-expand trace and the graph templates surface each traversed edge's
  `extraction_method` (AC11). LLM edges are in expand scope **by default** (the
  teaching payoff — an answer only reachable via an LLM edge), but any answer that
  leans on one shows it in the trace, so it is never blended silently. *Rejected:*
  excluding LLM edges from expand (kills the payoff); a write-only stamp (the existing
  all-kinds traversal would blend them unmarked — design review 2026-06-27). Traces to:
  AC11.
- **Ingest-time, default-off, MODE-scoped, reusing the existing grant.** Mirrors
  `_community_writeback` exactly. *Rejected:* query-time extraction (re-derives edges per
  query, couples read latency to an LLM pass — ADR-0006); on-by-default (loses the
  deterministic default — RFC-0002 non-goal). Traces to: AC5, AC7.
- **Per-triple source-span provenance persisted as a replayable artifact.** Extends the
  ADR-0005 "narrate stored output" affordance to "replay the prompt + per-triple
  provenance" — the answer to RFC-0002's "explainable *live*" pre-mortem. *Rejected:*
  recomputing extraction in the demo room (off the critical path; replay is the live
  bar). Traces to: AC4, AC9.
- **Offline via a non-semantic `RuleTripleExtractor`; live Bedrock is the semantic
  oracle.** Mirrors `RuleText2CypherGenerator`/`TemplateSynthesizer`. The offline path
  proves the orchestration + provenance contract, never extraction quality (that is AC9,
  live). *Rejected:* a local NLP/IE model (heavy dep, breaks pure-Python posture).
  Traces to: AC3, AC5, AC8.
- **Ship-or-Backlog honest-win gate.** The slice commits the *intent*; it ships only if
  the measured win clears the bar (Principle 2). *Rejected:* shipping the documentation
  contrast without the runnable win (a strawman — RFC-0002 pre-mortem). Traces to: AC8.

### Data & schema
- `EXTRACTION_SCHEMA: ExtractionSchema` — the fixed schema shown to the model and echoed
  in the trace: the closed set of LLM-extractable edge kinds (`COLLABORATES_WITH` SIG↔SIG,
  `SUPERSEDES`/`DEPENDS_ON` KEP→KEP — pinned here; the *closedness* is ADR-0006, the
  members are this plan's call) with their permitted endpoint `EntityKind`s, plus a
  natural-language instruction to emit only triples over this schema returning
  `(subject_id, predicate, object_id)` with the source span.
- `CandidateTriple(subject: str, predicate: str, object: str, source_doc: str, span:
  str)` — what the extractor returns (subject/object are raw mentions; predicate is a
  string to be validated against the closed set).
- `TripleValidation(ok: bool, triple: CandidateTriple, violated_rule: str | None)` —
  the closed-schema verdict (AC1).
- `GroundedTriple(src_id: str, dst_id: str, kind: EdgeKind, source_doc: str, span: str)`
  — a validated + grounded triple ready to become an edge (AC2); `None` when ungrounded.
- `TraceEntry(candidate: CandidateTriple, verdict: str, edge: Edge | None, reason: str |
  None)` — one candidate's provenance (`verdict` ∈ accepted / off-schema-rejected /
  dropped-ungrounded).
- `ExtractionResult(schema: ExtractionSchema, prompt: str, entries: list[TraceEntry],
  edges: list[Edge])` — the audit artifact; `edges` are the accepted edges (each
  `props["extraction_method"] == "schema-guided-llm"`, plus `source_doc`/`span` props).
- New `EdgeKind` members (additive): `COLLABORATES_WITH`, `SUPERSEDES`, `DEPENDS_ON` —
  emitted only by the LLM pass; the deterministic extractor never produces them, so
  existing edge-kind-count tests are unaffected.

### Interfaces & contracts
- Internal Python seams only (no repo-root `contracts/`). The Fargate task gains a
  default-off `SCHEMA_EXTRACTION` env flag (additive). No Function-URL contract change —
  the LLM edges are read by the **existing** retrieval modes (graph/hybrid) with no new
  query mode. Traces to: AC5, AC7, AC9.

### Component / module decomposition
- New: `extract_llm.py` (`TripleExtractor` protocol + `BedrockTripleExtractor` /
  `RuleTripleExtractor`, `EXTRACTION_SCHEMA`, `CandidateTriple`), `validate_triple.py`
  (`validate_triple`, `TripleValidation`), `ground.py` (`ground_triple`,
  `GroundedTriple`), `schema_extract.py` (`extract_schema_guided` + `ExtractionResult` +
  `TraceEntry`). *(Module split kept small; co-locating validate/ground into
  `extract_llm.py` is an acceptable simplification to decide at EXECUTE if the seam stays
  thin.)*
- Reused: `model` (`Edge`/`EdgeKind`/`Node`/`Graph`), `normalize` (grounding), `resolve`
  (the deterministic graph), `synthesize` (Converse posture), `sources`/`parse` (prose
  bodies), `store/*`.
- Modified: `model.py` (additive `EdgeKind` members), `cli.py` (`extract-llm` verb),
  `apps/ingestion/entrypoint.py` (`_schema_extraction_writeback` after the graph write +
  trace-artifact write), `apps/infra/stacks/graphrag_stack.py` (the `SCHEMA_EXTRACTION`
  default-off env flag on the task definition — no grant change), `showcase/queries.yaml`
  (a graph query answerable only via an LLM edge, for AC9/AC10). Traces to: AC1–AC10.

### State & control flow
`_schema_extraction_writeback` (MODE=full/rebuild only, flag on): read the just-written
graph (`store.all_nodes()`/`all_edges()`) + the parsed prose docs → `extract_schema_guided`
→ for each accepted edge `store.upsert_edge(edge)` (merge-on-upsert; the
`extraction_method` prop rides through) → write the `ExtractionResult` trace artifact to
the corpus bucket → print the narratable count (`+N schema-guided edges; M
off-schema-rejected; K dropped-ungrounded`). `extract_schema_guided`: for each prose doc,
`extractor.extract(doc, schema)` → for each candidate, `validate_triple` →
`ground_triple` → accepted ⇒ build `Edge(... props={"extraction_method":
"schema-guided-llm", "source_doc": ..., "span": ...})`, record `TraceEntry`. Traces to:
AC4, AC5.

### Behavior & rules
- Closed-schema validation (`validate_triple`): predicate ∈ the closed LLM-extractable
  `EdgeKind` set; endpoint kinds ∈ `EntityKind`; non-empty subject/object; conservative
  — ambiguous ⇒ reject with a rule name. Traces to: AC1.
- Entity grounding (`ground_triple`): map subject/object raw mentions to ids via the same
  `normalize` functions the deterministic pass uses, keyed by the expected endpoint kind
  (e.g. a SIG endpoint → `sig_id`, a KEP endpoint → `kep_id`); accept iff both ids are in
  the graph's node set; else drop with reason. Reuses `aliases.yaml` for person/prose-name
  cases. Traces to: AC2.
- Provenance: every accepted edge carries `extraction_method: "schema-guided-llm"` +
  `source_doc` + `span`; deterministic edges are untouched (`extraction_method` absent or
  `"deterministic"`). Traces to: AC4.

### Failure, edge cases & resilience
- Extractor returns empty/unparseable ⇒ no candidates (logged; not a failure).
- An off-schema or ungrounded candidate ⇒ recorded in the trace, never written.
- A candidate can **never** duplicate a deterministic edge's `(src, kind, dst)` key —
  the LLM-extractable kinds are disjoint from the deterministic kinds (T1), so the
  `setdefault` merge cannot mislabel a deterministic edge. Two LLM passes over the same
  triple union sources; `extraction_method` is set authoritatively and stable. (The
  honest-win measure counts only edges absent from the deterministic graph.)
- Bedrock error (live) ⇒ the ingest phase logs and continues with the deterministic graph
  intact (the pass is additive; a failed LLM pass must not corrupt the deterministic
  graph) — a named resilience rule, asserted in T5.
- `MODE=delta` ⇒ pass not invoked (asserted). Traces to: AC2, AC4, AC5.

### Quality attributes (NFRs / security)
- **Graph-honesty guarantee (defense in depth):** closed-schema validator (layer 1) +
  entity-grounding (layer 2 — no invented entities) + distinguishable `extraction_method`
  provenance at **write and read** (AC4 + AC11, so a wrong-but-in-schema edge is labeled
  model-asserted, source-span-traceable, and marked in any answer that uses it). The
  **live** honest-win gate (T9) measures **both recall** (≥ gold bar of edges the
  deterministic graph lacks) **and precision** (≤ a false-positive ceiling of off-gold
  edges) — precision is a gated bar, not only a provenance mitigation. T8 (offline) pins
  only the absence invariant + contract shape and makes no quality claim.
- **Untrusted-input at the Claude boundary:** prose bodies + schema as Converse `messages`
  data, defensive `system` directive (emit-only-schema-conforming-triples; embedded
  instructions are content, not commands), bounded `maxTokens`, default-TLS client,
  extraction output is data-only (never a tool call). `ruff` `S` stays enabled. Traces to:
  AC3, AC4.
- **No new infra/grant; teardown-first:** reuse the ingest task's existing scoped
  `bedrock:Converse` grant + the corpus bucket; default-off env flag; Budgets `150`.
  Traces to: AC7.
- **Ingest-only / PyYAML-free Lambda discipline:** the extraction modules are ingest-only;
  if any shared module is touched, the existing `sys.modules` guard is extended so the
  query Lambda import graph stays PyYAML-free. Traces to: AC5 (Never-do).

### Dependencies & integration
No new runtime dependency (Converse via existing `bedrock-runtime`; validator, grounding,
and rule extractor are pure Python). No new billable/compute resource; the ingest task's
Bedrock grant is **reused unchanged** and the trace artifact rides the existing corpus
bucket. Traces to: AC7.

## Tasks

### T1: Closed-schema triple validator + the schema constant (AC1)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/validate_triple.py, packages/graphrag/src/graphrag/model.py, packages/graphrag/tests/test_validate_triple.py
**Tests:**
- `# STUB: AC1` reject table: unknown predicate, a deterministic-only predicate (e.g.
  `AUTHORS` — not in the LLM-extractable set), an unknown endpoint kind, an empty
  subject/object, a malformed candidate — each rejected with the rule named.
- Accept table: each closed LLM-extractable edge kind (`COLLABORATES_WITH`,
  `SUPERSEDES`, `DEPENDS_ON`) with valid endpoint kinds passes; the closed set is pinned
  as a constant assertion.
- **Disjointness assertion (load-bearing):** assert directly that the LLM-extractable
  edge-kind set ∩ the deterministic edge-kind set = ∅ (not inferred from a count) — the
  invariant the read-side marker (AC11) and the no-collision stamp (AC4) rest on.
**Approach:**
- Add `COLLABORATES_WITH`/`SUPERSEDES`/`DEPENDS_ON` to `EdgeKind` (additive; deterministic
  path never emits them). Define `EXTRACTION_SCHEMA` as the closed edge-kind set with, per
  kind, **exactly one `(src EntityKind, dst EntityKind)` pair** (consumed by grounding,
  T2). `TripleValidation` dataclass; `validate_triple(triple, *, schema)` conservative —
  ambiguous ⇒ reject with a rule name. Pin `LLM_EXTRACTABLE_EDGE_KINDS` and
  `DETERMINISTIC_EDGE_KINDS` as named frozensets so the disjointness is a one-line assert
  and a future deterministic-rule addition that violates it fails the test.
**Done when:** `test_validate_triple.py` green; `ruff`/`mypy` clean.

### T2: Entity-grounding check (AC2)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/ground.py, packages/graphrag/tests/test_ground.py
**Tests:**
- `# STUB: AC2`: a triple whose endpoints normalize to ids present in a fixture graph is
  grounded (`GroundedTriple` with the canonical ids + kind); a triple naming an ungrounded
  entity (a SIG/KEP not in the graph) returns `None` with the reason recorded; a
  prose-name endpoint resolves via the alias table to the same id the deterministic pass
  produces. An **ambiguous-endpoint candidate is dropped, never guessed**. A unit
  assertion pins that grounding calls the existing `normalize` functions (no new resolver).
**Approach:**
- `ground_triple(triple, graph) -> GroundedTriple | None`: look up the validated
  predicate's **single** `(src EntityKind, dst EntityKind)` pair in `EXTRACTION_SCHEMA`
  (T1) to pick each endpoint's `normalize` function deterministically — no inference; if
  the schema entry is ambiguous (it never is for the pinned three, but the code fails
  closed), drop with reason. Check membership in `graph.nodes`, build `GroundedTriple` or
  drop with reason.
**Done when:** `test_ground.py` green; gates clean.

### T3: Extractor seam — Bedrock + offline rule extractor (AC3)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/extract_llm.py, packages/graphrag/tests/test_extract_llm.py
**Tests:**
- `# STUB: AC3`: `BedrockTripleExtractor` against a **mock** Converse client returns parsed
  `CandidateTriple`s (fence/JSON-tolerant); the request's defensive `system` directive is a
  **pinned module constant** (the `synthesize._SYSTEM_PROMPT` precedent) and the test
  asserts it is present **and that prose+schema ride `messages`, with the prose absent from
  `system`** (not a loose keyword match); bounded `maxTokens`; a **per-document candidate
  cap** truncates an over-long extraction; default-TLS client (no `verify=False`); an
  empty/garbled response ⇒ no candidates. A unit assertion pins
  `BedrockTripleExtractor().model_id == DEFAULT_SYNTHESIS_MODEL_ID` (the no-widened-grant
  equality AC7 leans on). `RuleTripleExtractor` emits within-schema candidates for the
  exemplar doc, labeled non-semantic.
**Approach:**
- `TripleExtractor` protocol `extract(doc, schema) -> list[CandidateTriple]`;
  `BedrockTripleExtractor` (configurable `modelId=DEFAULT_SYNTHESIS_MODEL_ID`, injectable
  client, a `_SYSTEM_PROMPT`-style pinned directive constant, a `MAX_CANDIDATES_PER_DOC`
  cap) mirroring `BedrockClaudeSynthesizer`'s Converse + untrusted-data posture;
  `RuleTripleExtractor` (keyword rules over the exemplar prose → schema candidates;
  labeled non-semantic in `model_id`).
**Done when:** `test_extract_llm.py` green; gates clean.

### T4: Orchestration + ExtractionResult + per-triple trace (AC4)
**Depends on:** T1, T2, T3
**Touches:** packages/graphrag/src/graphrag/schema_extract.py, packages/graphrag/tests/test_schema_extract.py
**Tests:**
- `# STUB: AC4` happy path: offline (`RuleTripleExtractor` + fixture graph) on the exemplar
  (a SIG-collaboration edge stated in prose, absent from the deterministic graph) — the
  candidate validates, grounds to two known SIG ids, is written stamped
  `schema-guided-llm` with `source_doc`/`span`, and `.render()` emits doc/span → triple →
  verdict → edge in order; an off-schema candidate is recorded `off-schema-rejected` with
  no edge; an ungrounded candidate is recorded `dropped-ungrounded` with no edge.
  Deterministic edges in the same graph keep `extraction_method == "deterministic"` (or
  absent) and are untouched.
**Approach:**
- `extract_schema_guided(docs, graph, *, extractor, schema)`: per prose doc, extract →
  validate (T1) → ground (T2) → accepted ⇒ `Edge` with the provenance props; accumulate
  `TraceEntry`s + `edges`; `ExtractionResult.render()` audit ordering.
**Done when:** orchestration tests green; gates clean.

### T5: Flagged ingest phase — default-off, MODE-scoped, additive (AC5)
**Depends on:** T4
**Touches:** apps/ingestion/entrypoint.py, apps/ingestion/tests/test_entrypoint.py
**Tests:**
- `# STUB: AC5`: with `SCHEMA_EXTRACTION` unset, a full-ingest run's **persisted store
  output (node/edge set + labels)** is byte-identical to the deterministic-only graph (no
  LLM edges, no trace artifact) — asserted at the store level, with a grep that the new
  `EdgeKind` members don't leak into any label/index/schema enumeration on a flag-off run;
  with it set (injected `RuleTripleExtractor` + in-memory store) the graph gains exactly the
  validated edges and a trace artifact is written under a **server-side key** (asserted via
  an injected S3 client — key = `CORPUS_PREFIX` + constant filename, never from doc/span);
  `MODE=delta` never invokes the pass; a raising extractor leaves the deterministic graph
  intact (the additive-resilience rule).
**Approach:**
- Add `_schema_extraction_writeback(env, store, docs, extractor=None, s3_client=...)`
  called in the `MODE=full`/`rebuild` branch **after** the deterministic graph write
  (mirroring `_community_writeback`'s injectable-store + MODE-scoping, **but adding a
  `SCHEMA_EXTRACTION` flag gate** community detection lacks); inject extractor + S3 in
  tests; write the trace artifact to the corpus bucket under a server-side-derived key (the
  `write_manifest` confinement pattern). No-op when the flag is unset.
**Done when:** `test_entrypoint.py` schema-extraction cases green; gates clean.

### T6: CLI verb `extract-llm` (AC6)
**Depends on:** T4
**Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_cli.py
**Tests:**
- `# STUB: AC6`: offline run prints the ordered per-triple trace + the non-semantic label;
  `--bedrock` selects `BedrockTripleExtractor`. (Manual QA: the real verb exercised
  end-to-end, output recorded — AC6.)
**Approach:**
- Add `_cmd_extract_llm` + parser (`--q`/corpus args, `--bedrock`, `--region`); offline
  default (in-memory store from the fixture corpus + `RuleTripleExtractor`); `--bedrock` ⇒
  `BedrockTripleExtractor`. Reuse the corpus-loading + render plumbing the sibling verbs use.
**Done when:** `test_cli.py` green; the verb exercised by hand (trace renders); gates clean.

### T7: IaC — default-off flag, no grant change, no new resource, cost held (AC7)
**Depends on:** none
**Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py
**Tests:**
- `# STUB: AC7`: synth assertion — the **ingest task role's** `bedrock:Converse` grant is
  unchanged (still scopes the synthesis model, no wildcard `Resource`); the
  `SCHEMA_EXTRACTION` env var on the task definition **defaults off**; **no new resource**
  is added; the query-Lambda Neptune grant is unchanged (read-only); the Budgets value is
  the literal `150`. (Assert the grant is byte-identical to the pre-slice statement — the
  ADR-0006 Confirmation fitness test, failing if a later edit widens it.)
**Approach:**
- Add the `SCHEMA_EXTRACTION` (default off) env var to the ingest task definition;
  **change no IAM statement** (the Converse grant already exists from ADR-0005). The synth
  test pins no-widening + cost-held.
**Done when:** CDK-env-gated synth test green; gates clean.

### T8: Offline absence invariant + contract shape + gold set (AC8)
**Depends on:** T2, T4
**Touches:** packages/graphrag/src/graphrag/showcase/queries.yaml, packages/graphrag/tests/test_honest_win.py
**Tests:**
- `# STUB: AC8`: a pinned gold set of prose inter-entity edges (each `(src_id, kind,
  dst_id)` resolving in the fixture corpus). The test builds the **actual** deterministic
  graph (`resolve()` over the real fixture corpus) and asserts, for each gold edge, that
  **no deterministic edge of *any* kind connects its two endpoints in the asserted
  direction** — absence at the **relationship** level, not the trivial "no edge of a
  never-emitted kind" — so the contrast is real, not a strawman. It then asserts the offline
  pass plumbs the gold edges through validation/grounding/stamping. The seeded offline
  extractor makes **no quality claim**: this is the contract + absence invariant, *not* the
  ship gate (that is T9, live).
**Approach:**
- Hand-author the gold set from the actual SIG-README / KEP-Motivation prose (the RFC
  de-risk's named edge classes: cross-SIG collaboration, KEP supersession/dependency);
  assert relationship-level absence against `resolve()`'s graph; run the offline pass and
  assert plumbing. Pin the gold set so T9 can measure live recall/precision against it. Add
  the **LLM-only-edge demo query** (e.g. "which SIGs collaborate with sig-network?") to a
  new top-level `extraction_queries` group in `showcase/queries.yaml` (mode `graph`/`hybrid`)
  so AC9/AC10's "exact CLI + graph query" is buildable; a loader/test asserts it parses.
**Done when:** relationship-level absence + contract-plumbing test green; gates clean.

### T11: Read-side edge provenance in the retrieval trace (AC11)
**Depends on:** T1, T4
**Touches:** packages/graphrag/src/graphrag/query.py, packages/graphrag/src/graphrag/templates.py, packages/graphrag/tests/test_query.py
**Tests:**
- `# STUB: AC11`: a fixture graph holding both a `deterministic` edge and a
  `schema-guided-llm` edge; `expand`/`neighbors_batch` (and a graph template) traverse both;
  the retrieval trace attributes each hop's `extraction_method`, so an answer leaning on a
  model-asserted edge is visibly marked. A traversal over deterministic-only edges shows no
  `schema-guided-llm` hops (no false marking).
**Approach:**
- Thread `extraction_method` (read from the edge props) into the expand/seed-and-expand
  hop trace (`query.py:169-212`) and the graph template selection (`templates.py:131-147`)
  — a minimal additive trace field, no traversal-scope change (LLM edges stay traversable
  by default; they are just marked). Keep the change PyYAML-free if it touches a
  Lambda-imported module (extend the `sys.modules` guard if needed).
**Done when:** `test_query.py` provenance-trace cases green; gates clean.

### T9: Live deploy + schema-guided ingest smoke — the honest-win ship gate (AC9)
**Depends on:** T5, T7, T8, T11
**Tests:**
- Manual/live: deploy with `SCHEMA_EXTRACTION=on`, put the corpus in S3, run the Fargate
  task → live Bedrock extracts triples over the prose → validated + grounded → distinguishable
  `schema-guided-llm` edges in live Neptune; a SigV4 Function-URL graph/hybrid query
  traverses an **LLM-only edge** to answer a question the deterministic graph cannot, the
  answer's trace marking that hop **model-asserted** (AC11); replay the per-triple trace
  artifact from the corpus bucket. **The ship gate:** confirm the live pass **recovers ≥ the
  gold recall bar AND writes ≤ the false-positive ceiling** (recall + precision) against the
  T8 gold set. Then `scripts/destroy.sh`.
**Approach:**
- Live AWS is available — run the smoke end-to-end and record it in
  `deployment-and-verification.md` (mirror the global-community-summary AC10 record),
  including the LLM-only-edge query, the model-asserted trace marking, and the trace replay;
  then tear down. **If the live recall/precision bars are not both met, surface and drop the
  row to `Backlog`** (run-or-defer; atomic backlog heading) — this is the real ship-or-not
  decision.
**Done when:** live smoke recorded incl. the LLM-only-edge query + model-asserted trace + the
recall+precision confirmation; teardown leaves no billable resource.

### T10: Teaching contrast doc + drift-closure metadata (AC10 + CONVENTIONS § 4)
**Depends on:** T1, T2, T4, T5, T6, T8, T11
*(AC10 is the teaching contrast in the ingestion pattern-axis guide; the rest realizes the
drift-closure metadata invariants — Status flip, AC ticks, architecture/AGENTS docs,
charter row + ADR-0006 status. Finalization, not scope creep.)*
**Touches:** docs/guides/explanation/ingestion-patterns-and-retrieval-patterns.md, docs/architecture/overview.md, docs/architecture/security.md, packages/graphrag/AGENTS.md, docs/specs/README.md, docs/CHARTER.md, docs/adr/0006-schema-guided-llm-extraction-guard.md, docs/adr/README.md
**Tests:**
- Goal-based: the guide shows the contrast **running** (exact `extract-llm` CLI + a graph
  query answerable only via an LLM edge); the architecture + AGENTS docs record the new
  modules/invariants; repo spec-status lint clean; AC checkboxes reflect reality; charter
  row `Planned → Have` (ADR-0006 is already Accepted).
**Approach:**
- Complete the ingestion pattern-axis guide's "running contrast" section (the guide's
  conceptual frame is authored up front — see Changelog; T10 makes its contrast runnable
  against shipped code).
- Update `architecture/overview.md` (the schema-guided ingest phase) + `security.md` (the
  guard: closed-schema validator + entity grounding + distinguishable provenance — the
  contrast with deterministic extraction's no-model property).
- Update `graphrag` AGENTS.md module map (extract_llm/validate_triple/ground/schema_extract)
  + invariants (closed-schema validation; entity grounding; distinguishable
  `extraction_method`; ingest-only modules).
- Tick met ACs; flip spec Status `Approved → Shipped`; flip the charter row
  `Planned → Have` (ADR-0006 is already Accepted, `docs/adr/README.md` already current).
**Done when:** docs consistent; lints clean; charter row `Have`.

## Rollout

- **Delivery:** additive. The CLI gains a verb; the Fargate task gains a **default-off**
  `SCHEMA_EXTRACTION` flag; the graph model gains additive `EdgeKind` members the
  deterministic path never emits. Reversible — turning the flag off restores the prior
  graph exactly; no data migration, no published event. Rollback is reverting the PR (and,
  for a deployed graph, a flag-off `--rebuild`).
- **Infrastructure:** no new resource. The extraction modules run in the existing Fargate
  ingest task; the trace artifact rides the existing corpus S3 bucket. The **one infra
  change is a default-off env flag** on the task definition — **no IAM change** (the
  ingest task's `bedrock:Converse` grant already exists from ADR-0005). Budgets unchanged
  at `150` (AC7).
- **External-system integration:** Bedrock Claude (extraction) — already wired and granted
  for community summarization on the same task role at the same model (no widened grant);
  Neptune (the LLM edges ride the existing read-write ingest grant + are read by the
  existing read-only query grant). No new endpoint, no new query mode.
- **Deployment sequencing:** single PR. The default-off flag deploys with the code; the
  live smoke (AC9/T9) runs against a deploy of this branch with the flag turned on (AWS
  available), then tears down.

## Risks

- **The contrast is a strawman (Principle 2).** If the deterministic graph already captures
  the useful prose edges, the LLM pass has no honest win. Mitigation: the honest-win gate
  (T8) measures recovery of gold edges **proven absent** from the deterministic graph; the
  RFC de-risk spike already shows the deterministic pass reads prose only via labeled-field
  regex and emits no free-narrative inter-entity edges. If the bar isn't met, the slice
  doesn't ship and the row drops to `Backlog`.
- **A model-authored edge is wrong within the schema.** A plausible-but-unsupported
  `DEPENDS_ON`. Mitigation: per-triple source-span provenance (checkable against the cited
  span), the distinguishable `extraction_method` stamp (downstream knows it is
  model-asserted), and the gold-set precision the honest-win gate measures. Named residual,
  not eliminated (ADR-0006).
- **Entity grounding falsely drops a real edge.** If the LLM's mention doesn't normalize to
  the deterministic id. Mitigation: grounding reuses the **exact** `normalize` functions +
  alias table the deterministic pass uses; the gold set exercises prose-name endpoints.
- **An LLM pass corrupts the deterministic graph on failure.** Mitigation: the phase is
  additive and resilient — a Bedrock/extractor error logs and leaves the deterministic
  graph intact (T5); the flag is default-off.
- **The read path silently blends LLM edges into answers.** The existing
  `expand`/seed-and-expand traversal follows all edge kinds, so an LLM edge rides into an
  answer with no marker unless the trace surfaces it. Mitigation: AC11/T11 thread
  `extraction_method` into the retrieval trace; the disjoint-edge-kind invariant + the
  authoritative stamp keep write-side provenance intact (design review 2026-06-27).
- **Extraction modules drag PyYAML/weight into the query Lambda.** Mitigation: the modules
  are ingest-only; if a shared module is touched (T11 touches `query.py`/`templates.py`),
  the existing `sys.modules` guard is extended (the PyYAML-free discipline).

## Changelog

- 2026-06-27: initial plan. Extractor seam (Bedrock + offline rule), closed-schema
  validator, entity-grounding check (reusing `normalize`, no new resolver), orchestrator +
  per-triple replayable trace, additive default-off `SCHEMA_EXTRACTION` ingest phase
  (`_community_writeback` shape; no IAM change — reuses the ADR-0005 Converse grant),
  `extract-llm` CLI verb, the ADR-0006 synth fitness test (no-widening + cost-held), the
  honest-win gate (gold edges proven absent from the deterministic graph), the runnable
  deterministic↔schema-guided contrast in the ingestion pattern-axis guide, and the live
  smoke. AC9 to run live (AWS available).
- 2026-06-27: pre-EXECUTE review pass (adversarial + security + design). Split the
  honest-win gate into an offline absence/contract check (T8, no quality claim) and the
  **live** recall+precision ship gate (T9); made absence a **relationship-level** assertion
  against `resolve()` (not the trivial never-emitted-kind check); added **AC11/T11
  read-side `extraction_method` provenance** (the existing all-edge-kinds expand would
  otherwise blend LLM edges silently); pinned the **disjoint-edge-kind invariant** +
  authoritative (non-`setdefault`) stamping to close the merge-collision hole; pinned the
  **1:1 predicate→endpoint-kind mapping** for grounding (drop-not-guess); added the
  **per-doc candidate cap** + **server-side trace-key confinement**; corrected the
  "measures precision" misdescription; reconciled the model-id Ask-first item with the
  no-widened-grant Never-do.
