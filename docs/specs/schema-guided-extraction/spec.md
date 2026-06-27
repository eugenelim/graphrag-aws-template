# Spec: schema-guided-extraction

- **Status:** Approved <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0006](../../adr/0006-schema-guided-llm-extraction-guard.md) (the extraction guard this slice ships — closed schema + entity-grounding validator, ingest-time, distinguishable provenance, honest-win-gated), [RFC-0002](../../rfc/0002-ingestion-pattern-axis.md) (establishes ingestion as a first-class pattern axis and commits **schema-guided LLM extraction** as the one `Planned` pattern), [Charter — *Ingestion pattern coverage* table, *Schema-guided LLM* row + spine item #9 + the clarified Principle 1](../../CHARTER.md#ingestion-pattern-coverage-our-taxonomy) (the coverage contract this slice ships; narratable ⇒ traceable, LLM allowed when traced), [ADR-0004](../../adr/0004-text2cypher-read-only-guard.md) (the retrieval-side precedent: an LLM hop made safe + narratable by a validator the presenter can show), [ADR-0005](../../adr/0005-community-detection-in-fargate-louvain.md) (the ingest-time Bedrock hop in the Fargate task whose persisted output is narrated later — the affordance this slice extends to per-triple replay), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (reuses the `Synthesizer`/Bedrock-Converse seam + untrusted-data posture), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (rides the existing on-demand Fargate task + Neptune cluster + corpus bucket; adds **no** billable resource; the grant is reused, never widened), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python)
- **Brief:** [`docs/product/briefs/graphrag-pattern-catalog.md`](../../product/briefs/graphrag-pattern-catalog.md)
- **Contract:** none (new internal Python interfaces — an extraction module + validator + an `ExtractionResult`/trace — plus an additive, default-off `SCHEMA_EXTRACTION` flag on the existing Fargate ingest task; no repo-root `contracts/` API surface, consistent with the sibling pattern slices)
- **Shape:** mixed

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> The **Schema-guided LLM extraction** pattern — the one `Planned` *Extraction*-stage
> pattern committed by [RFC-0002](../../rfc/0002-ingestion-pattern-axis.md) — implemented
> on AWS as the **LLM-assisted end of the extraction-strategy spectrum** whose
> deterministic end is the shipped `graph-ingestion-resolution` baseline. A Bedrock
> Claude pass reads the corpus's **prose bodies** (SIG READMEs, KEP `Motivation` /
> `Alternatives` sections) and extracts **triples constrained to a fixed entity/edge
> schema** — the free-narrative inter-entity relationships (cross-SIG collaboration,
> KEP supersession/dependency) the deterministic pass reads prose only via
> labeled-field regex and therefore **structurally cannot reach**. The hop is made
> safe + narratable by the **ADR-0006 guard**: a closed-schema validator + an
> **entity-grounding** check (the model may relate entities the corpus already
> resolved, never invent them) + per-triple source-span provenance + a
> `extraction_method` stamp that keeps model-asserted edges **distinguishable** from
> deterministic facts. It runs as an **additive, default-off phase of the existing
> Fargate ingest task** (the ADR-0005 ingest-time-Bedrock precedent), reusing the
> task's existing `bedrock:Converse` grant — **no new infra, no widened grant**.
> `Depends on:` the graph slice ([`graph-ingestion-resolution`](../graph-ingestion-resolution/spec.md))
> for `extract`/`resolve`/`normalize`, the `Graph`/`Node`/`Edge` model, and the
> `GraphStore` seam; and the synthesis seam ([`hybrid-orchestration`](../hybrid-orchestration/spec.md))
> for `BedrockClaudeSynthesizer`'s Converse + untrusted-data posture, which the
> extractor mirrors.

## Objective

A solution architect evaluating GraphRAG for an enterprise needs to *see* that
**ingestion is a spectrum of choices**, not a fixed substrate — and specifically to
see the **deterministic↔LLM extraction contrast** the charter's pattern #1 / spine
#9 asks them to reason about. The shipped deterministic extractor demonstrates one
end (controlled-vocabulary IDs, no model, free); this slice delivers the other end
**on the same corpus**: a **schema-guided LLM extraction** pass that surfaces the
inter-entity relationships hiding in prose that the deterministic rules cannot
reach, so the architect can weigh the two strategies side by side and state when
they would choose each for *their* corpus.

The pass reads the prose bodies, asks a Bedrock Claude (Converse) call to extract
triples **constrained to a fixed schema** (a closed set of LLM-extractable edge
kinds between known entity kinds), and writes the **validated, grounded** edges into
the graph the deterministic pass already built — marked `extraction_method:
"schema-guided-llm"` so they are never confused with deterministic facts. Because
the model authors *which entities relate and how*, the guard (ADR-0006) is
load-bearing: a **closed-schema validator** rejects any triple whose predicate or
endpoint kind is off-schema, and an **entity-grounding** check drops any triple
naming an entity that does not normalize to one the deterministic graph already
resolved — the model may assert *relationships between known entities*, never
*invent entities*. Every candidate triple — accepted, off-schema-rejected, or
dropped-ungrounded — is recorded with its **source span** in a trace persisted as a
**replayable artifact**, so a presenter can show the prompt, the schema, and which
text produced which edge, with no black-box hop.

The path runs **offline by default** (in-memory store + a deterministic
non-semantic `RuleTripleExtractor` over the fixture corpus) for credential-free CI
and a laptop demo, and **live** in the deployed Fargate ingest task (Bedrock + the
just-written Neptune graph) behind a default-off flag. The contrast is explicit and
runnable: the same corpus ingested with the flag off (deterministic only) and on
(deterministic + schema-guided) yields a graph whose new LLM-only edges answer
questions the deterministic graph cannot — and the slice ships **only if** that win
is measured honest against a gold set (else the charter row drops to `Backlog`).

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule.

### Always do

- **Validate every model-authored triple against the closed schema before it is
  written.** A triple is accepted only if its predicate is in the closed
  LLM-extractable edge-kind set **and** both endpoint kinds are in the existing
  `EntityKind` set. An off-schema triple is **never written** to the graph — it is
  rejected and recorded in the trace with the rule it violated (ADR-0006).
- **Ground every endpoint to an entity the deterministic graph already resolved.**
  Both endpoints must `normalize` to an entity id present in the graph the
  deterministic pass built; a triple naming an ungrounded entity is **dropped**
  (recorded in the trace with the reason). The model relates known entities; it
  does **not** invent them.
- **Stamp every LLM-extracted edge distinguishable, and surface it at read time
  too.** Each written edge carries `extraction_method: "schema-guided-llm"`;
  deterministic edges carry `"deterministic"`. Distinguishability is enforced at
  **both** ends: at write (the stamp) **and at read** — the retrieval trace
  (`expand`/seed-and-expand and the graph templates) surfaces `extraction_method`
  per traversed edge, so any answer that leans on a model-asserted edge shows it.
  Model-asserted edges and deterministic facts are never blended *silently* into an
  answer (AC11). The LLM-extractable edge-kind set is **disjoint** from the
  deterministic edge-kind set (a load-bearing invariant — AC1), so the `(src, kind,
  dst)` key of an LLM edge can never collide with a deterministic edge under
  merge-on-upsert; `extraction_method` is set authoritatively, not `setdefault`-merged.
- **Emit per-triple source-span provenance into a replayable trace.** For each
  candidate triple the trace records the source doc + the text span it came from,
  the prompt + schema shown to the model, and the verdict (accepted /
  off-schema-rejected / dropped-ungrounded); accepted triples additionally record
  the resulting edge. The trace is persisted as an artifact a presenter can replay
  (the ADR-0005 affordance, extended to per-triple provenance). The trace
  explains *why an edge exists* (ingest-time provenance); per-answer attribution —
  *why this edge is in this answer* — is the read-side `extraction_method` surfacing
  above (AC11). Both are required; neither substitutes for the other.
- **Confine the trace-artifact S3 key.** The trace artifact key is derived
  **server-side** from `CORPUS_PREFIX` + a constant filename — the `write_manifest`
  pattern (`entrypoint.py:77-79`) — **never** from a doc path, span, triple, or any
  model-supplied text, so a poisoned doc/span cannot write outside the corpus prefix
  (CWE-23). (AC5/AC7.)
- **Bound the per-document extraction volume.** A single prose body yields at most a
  bounded number of candidate triples (a per-doc cap), so a large or adversarial
  document cannot amplify into an unbounded number of Bedrock calls (denial-of-wallet
  at ingest — OWASP `LLM10:2025 Unbounded Consumption`) or unbounded graph writes. The corpus is **operator-supplied,
  trusted-origin** (the two locked K8s repos), so amplification is additionally
  bounded by ingest scope — but the per-doc cap is the explicit guard. (AC3/AC5.)
- **Treat the prose bodies as untrusted external content at the Claude boundary.**
  Reuse the `BedrockClaudeSynthesizer` posture: the doc text rides Converse
  `messages` as **data** (never the `system` block); the `system` block carries the
  defensive directive that any instruction embedded in the prose must not be
  followed and that it must emit only schema-conforming triples (LLM01/LLM08); a
  bounded `maxTokens`; the default-TLS botocore client; the extraction output is
  **never** treated as an instruction or a tool call — only as candidate triples to
  validate.
- **Keep the pass additive and default-off.** Deterministic extraction stays the
  default and the `Have` baseline; schema-guided extraction runs only when the
  `SCHEMA_EXTRACTION` flag is set (CLI flag / Fargate env), on `MODE=full` /
  `--rebuild` only (not `delta`). With the flag off, the graph is byte-identical to
  today's.
- **Gate the ship on a *measured live* honest win.** The ship-or-`Backlog`
  decision is the **live** run (AC9): the slice ships only if the **live Bedrock**
  pass recovers ≥ the gold bar of prose inter-entity edges the deterministic graph
  does not contain **and** writes no more than the false-positive ceiling of
  off-gold edges. The offline check (AC8) pins the absence invariant + the
  orchestration/provenance contract shape and makes **no** semantic-quality claim
  (the offline `RuleTripleExtractor` is seeded, so a green offline test is not a
  cleared gate). If the live bar is not cleared, the slice does not ship and the
  charter row stays `Backlog` — the RFC commits the intent, not an unearned win.
- **Keep teardown a feature** (charter principle 4): the slice adds **no** billable
  resource and **no** new IAM grant — it reuses the ingest task's existing
  `bedrock:Converse` grant and the existing corpus S3 bucket; the only deploy change
  is a default-off env flag.

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** Extraction uses the
  existing `bedrock-runtime` Converse client and pure Python (the schema validator,
  the grounding check, and the offline rule extractor are pure Python) — reach for
  any other LLM/parser/NLP dependency only with sign-off, recorded in
  `packages/graphrag/AGENTS.md`.
- **Pinning or changing the extraction model id away from the synthesis-model
  default.** Extraction reuses the already-granted synthesis Claude model, so the
  IAM grant is unchanged today (AC7's no-widened-grant property holds *by
  construction* via the `model_id == DEFAULT_SYNTHESIS_MODEL_ID` equality of AC3). A
  *different* model breaks that property: it requires a **coordinated IAM-grant
  change + an ADR-0006 amendment**, not a one-line default swap — which is why it is
  gated here and reconciled with the Never-do "never widen the grant" below (the
  three sites are one rule: the grant stays unchanged *because* the model stays the
  default).
- **Adding, removing, or changing the closed set of LLM-extractable edge kinds, or
  relaxing the entity-grounding rule to admit new entities.** Both change the
  honesty bound recorded in ADR-0006 — a schema change is a design decision, not a
  mechanical edit.
- **Running schema-guided extraction on `MODE=delta`, or making it the default.**
  Both change the scope/risk envelope (delta-community-style staleness; loss of the
  deterministic default).

### Never do

- **Never write a triple that failed schema validation or entity grounding.** An
  off-schema or ungrounded triple is rejected/dropped and recorded in the trace —
  it never reaches the graph.
- **Never let an LLM-extracted edge be indistinguishable from a deterministic one —
  at write or at read.** The `extraction_method` stamp is mandatory at write, and an
  answer that traverses a model-asserted edge must surface that in its trace (AC11);
  a model-asserted edge that reads as a deterministic fact, or rides into an answer
  silently, is a narratability and honesty failure.
- **Never add an LLM-extractable edge kind that overlaps the deterministic
  edge-kind set, or emit an LLM-extractable kind from a deterministic rule.** The
  two sets are disjoint by invariant (AC1); overlap would let `extraction_method`
  collide on a shared `(src, kind, dst)` key under merge-on-upsert and break
  distinguishability.
- **Never treat the extraction output as an instruction.** The model's triples are
  data to validate; no caller evaluates, shells out on, or feeds them back as a
  command.
- **Never widen the ingest task's Bedrock grant or add a new billable resource.**
  Extraction reuses the existing scoped `bedrock:Converse` grant at the synthesis
  model; Budgets stays at the literal `150`.
- **Never let the extraction modules drag PyYAML into the query Lambda import
  graph.** The pass is ingest-only; keep its modules out of the Lambda bundle's
  import graph (extend the existing `sys.modules` guard if a shared module is
  touched).
- **Never add a new top-level directory or module boundary** beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces
  (AGENTS.md: top-level directories need an RFC). New code lands as modules/docs
  inside those.

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1 — TDD.** The closed-schema validator is pure logic with a compressible
  invariant: a table of off-schema triples (unknown predicate, unknown endpoint
  kind, a *deterministic-only* predicate like `AUTHORS`, malformed) is rejected with
  the rule named; a table of in-schema triples is accepted. The closed LLM-extractable
  edge-kind set is pinned, **and its disjointness from the deterministic edge-kind set
  is asserted directly** (not inferred from a count) — the load-bearing invariant the
  read-side distinguishability and the no-collision stamp rest on.
- **AC2 — TDD.** The entity-grounding check is pure logic over a fixture graph: a
  triple whose endpoints normalize to known ids is accepted; one naming an
  ungrounded entity is dropped with the reason recorded; an **ambiguous-endpoint-kind**
  candidate is dropped, never guessed. The `normalize`-reuse is asserted (no new
  resolution path), and each closed edge kind maps to **exactly one `(src EntityKind,
  dst EntityKind)` pair** that selects the normalizer.
- **AC3 — TDD with mock.** The Bedrock extractor issues a well-formed Converse
  request (defensive system directive incl. emit-only-schema-conforming-triples;
  prose + schema in `messages` as data; bounded `maxTokens`; default-TLS client;
  `modelId` defaults to `DEFAULT_SYNTHESIS_MODEL_ID` — a tested equality so AC7's
  no-widened-grant property holds by construction), parses the returned triples
  (fence-tolerant), verified against a **mock** (no live call). A
  `RuleTripleExtractor` (offline, deterministic, **non-semantic**, labeled) emits
  within-schema triples for the pinned exemplar.
- **AC4 — TDD.** The orchestration over the fixture corpus + `RuleTripleExtractor` +
  in-memory store: candidate triples are validated (AC1), grounded (AC2), accepted
  ones written as edges stamped `schema-guided-llm`; the `ExtractionResult`/trace
  records each candidate's source span + verdict and `.render()` narrates, in order,
  doc/span → triple → verdict → resulting edge. Deterministic edges in the same
  graph are untouched and stay `deterministic`.
- **AC5 — TDD (integration over the fixture corpus).** The flagged ingest phase: with
  the flag **off**, the resolved graph is byte-identical to the deterministic-only
  graph (no LLM edges, no trace); with the flag **on** (offline `RuleTripleExtractor`),
  the graph gains the validated LLM-only edges and the trace artifact is produced;
  `MODE=delta` never runs the pass (asserted). Because the offline extractor is
  non-semantic, this pins the **orchestration + provenance contract**, not extraction
  quality (that is AC9, live).
- **AC6 — Visual / manual QA + goal-based.** The CLI verb `extract-llm` is the
  user-invoked artifact: run offline over the fixture corpus, observe the rendered
  per-triple trace (prompt, schema, source span, verdict, edge) and the
  non-semantic label; `--bedrock` switches to the live extractor. Exercised
  end-to-end through the documented happy path and the observed output recorded.
- **AC7 — goal-based check (`cdk synth` + `aws_cdk.assertions.Template`),
  CDK-env-gated.** The ingest task role's `bedrock:Converse` grant is **unchanged**
  (still scopes the synthesis model, no wildcard `Resource`); **no new resource** is
  added; the `SCHEMA_EXTRACTION` env flag defaults off on the task definition; the
  query-Lambda Neptune grant is unchanged (read-only, ADR-0004); the Budgets value
  is asserted **unchanged at the literal `150`**.
- **AC8 — goal-based check (offline: absence invariant + contract shape; *not* the
  honesty gate).** A hand-authored gold set of prose inter-entity edges is pinned;
  the test builds the **actual deterministic graph** (`resolve()` over the real
  fixture corpus) and asserts, for each gold edge, that **no deterministic edge — of
  any kind — connects its two endpoints in the asserted direction** (absence at the
  *relationship* level, not merely "no edge of a never-emitted kind"), and that the
  offline pass plumbs the gold edges through validation + grounding + stamping. The
  offline `RuleTripleExtractor` is seeded, so this makes **no semantic-quality
  claim** — it pins the absence invariant and the orchestration/provenance contract;
  the honesty gate is AC9 (live).
- **AC11 — TDD + narratability check.** The read path surfaces provenance: when
  `expand`/seed-and-expand (and the graph templates) traverse an edge, the retrieval
  trace records that edge's `extraction_method`, so an answer leaning on a
  model-asserted edge shows it. A test traverses a graph containing both
  deterministic and `schema-guided-llm` edges and asserts the trace attributes each
  hop's method (the read-side half of distinguishability).
- **AC9 — live deploy + ingest smoke (active end-to-end; *the* honest-win ship
  gate).** A deploy with the flag **on** has the Fargate task extract triples via
  **live Bedrock** over the corpus prose, validate + ground them, write
  distinguishable `schema-guided-llm` edges to **live Neptune**, and a live
  Function-URL query traverse an **LLM-only edge** (a question the deterministic
  graph cannot answer) with the answer's trace marking the hop model-asserted
  (AC11). The live pass must **recover ≥ the gold bar** of prose edges the
  deterministic graph lacks **and write ≤ the false-positive ceiling** of off-gold
  edges (recall floor + precision ceiling — the measured honest win). The per-triple
  trace artifact is replayed from the corpus bucket; then the stack is destroyed. If
  the live bar is not cleared, the slice does not ship and the row drops to
  `Backlog` (run-or-defer, atomic `docs/backlog.md` heading). Live AWS deploy is
  available in this environment (run it; do not auto-defer).
- **AC10 — goal-based check (teaching contrast doc + spec map).** The ingestion
  pattern-axis explanation guide shows the deterministic↔schema-guided contrast
  **running** (exact `extract-llm` CLI + a graph query answerable only via an LLM
  edge), so a watcher can state when they would choose each; the develop-offline /
  architecture docs and `packages/graphrag/AGENTS.md` record the new modules and
  invariants; **on ship the charter row flips `Planned → Have`** (ADR-0006 — the guard
  decision — is already Accepted; the ship gate decides only whether the *slice* lands).

## Acceptance Criteria

- [ ] **AC1 — Closed-schema triple validator (the governance boundary).**
  `validate_triple(triple, *, schema) -> TripleValidation` accepts a candidate
  triple iff its predicate is in the closed LLM-extractable edge-kind set **and**
  both endpoint kinds are in the `EntityKind` set, and the triple is well-formed
  (non-empty subject/object, a single predicate). An off-schema triple
  (unknown/mutating predicate, a *deterministic-only* predicate, unknown endpoint
  kind, malformed) yields a typed `TripleValidation` naming the violated rule and is
  **never written**. The closed edge-kind set is a pinned constant **whose
  disjointness from the deterministic edge-kind set is asserted directly** (the
  load-bearing invariant for read-side distinguishability and no-collision
  stamping); the validator is **conservative** — ambiguous ⇒ reject. *(TDD)*
- [ ] **AC2 — Entity-grounding check (the honesty bound).** `ground_triple(triple,
  graph) -> GroundedTriple | None` resolves each endpoint via the existing
  `normalize` functions (no new resolution path) and accepts the triple **iff both
  endpoints resolve to an entity id already present in the deterministic graph**; a
  triple naming an entity that grounds to no known id returns `None` (dropped) with
  the reason recorded. Each closed edge kind maps to **exactly one `(src EntityKind,
  dst EntityKind)` pair** that selects the normalizer per endpoint; an
  **ambiguous-endpoint candidate is dropped, never guessed**. The model relates known
  entities; it never invents them. Grounding reuses `normalize` + the alias table — a
  unit assertion pins that no new resolver is introduced. *(TDD)*
- [ ] **AC3 — Bedrock schema-guided extractor (Converse), with an offline
  deterministic counterpart.** `BedrockTripleExtractor` issues a well-formed Converse
  request — a configurable `modelId` (default `DEFAULT_SYNTHESIS_MODEL_ID`); a
  `system` block instructing it to extract only triples conforming to the fixed
  schema and that the prose is untrusted data whose embedded instructions must not be
  followed (LLM01/LLM05/LLM08); the prose + schema in `messages` **as data**; a
  bounded `maxTokens`, and a **per-document candidate cap** (the volume bound — a
  large/adversarial doc cannot amplify into unbounded calls/writes) — and parses the
  returned triples (fence/JSON-tolerant), verified against a **mock** (no live call);
  the client is the default botocore-chain client over TLS. The defensive `system`
  directive is a **pinned module constant** (the `synthesize._SYSTEM_PROMPT`
  precedent); the test asserts it is present **and that the prose rides `messages`,
  with the prose absent from `system`** (not a loose keyword match). A
  `RuleTripleExtractor` (offline, deterministic, **non-semantic**, labeled) emits
  within-schema triples for the pinned exemplar for CI/offline. A unit assertion pins
  `BedrockTripleExtractor().model_id == DEFAULT_SYNTHESIS_MODEL_ID` (the
  no-widened-grant equality AC7 leans on). *(TDD with mock)*
- [ ] **AC4 — Extraction orchestration with a full per-triple trace.**
  `extract_schema_guided(docs, graph, *, extractor, schema, ...) -> ExtractionResult`
  reads the prose bodies, calls the extractor, validates each candidate (AC1),
  grounds it (AC2), and returns an `ExtractionResult` carrying: the prompt + schema
  shown to the model, **every** candidate triple with its source span and verdict
  (accepted / off-schema-rejected / dropped-ungrounded), and the accepted edges
  (each stamped `extraction_method: "schema-guided-llm"`, carrying source-span
  provenance). `.render()` narrates, in order, **doc/span → candidate triple →
  verdict → resulting edge** (the audit artifact; no black-box hop, charter principle
  1). Deterministic edges in the same graph are untouched and remain `deterministic`.
  Because the LLM-extractable kinds are disjoint from the deterministic kinds (AC1),
  an accepted edge never shares a `(src, kind, dst)` key with a deterministic edge;
  `extraction_method` is **set authoritatively** (not `setdefault`-merged), so no
  merge-on-upsert collision can mislabel a deterministic edge or strip an LLM stamp.
  *(TDD + narratability check)*
- [ ] **AC5 — Flagged, additive ingest phase (default-off), MODE-scoped.** The
  Fargate full-ingest path (`MODE=full` / `--rebuild`) gains a
  `_schema_extraction_writeback` phase **after** the deterministic graph write, run
  **only** when `SCHEMA_EXTRACTION` is set. With the flag **off** the **persisted
  store output** (node/edge set + labels) is byte-identical to the deterministic-only
  graph — asserted at the store level, not just "no LLM edges", since the new
  `EdgeKind` members must not leak into any label/index/schema enumeration on a
  flag-off run. With the flag **on** (offline `RuleTripleExtractor` in tests; live
  Bedrock deployed) the graph gains the validated, grounded, stamped LLM-only edges
  and the trace artifact is written to the corpus bucket under a **server-side key**
  (`CORPUS_PREFIX` + constant filename; never a doc/span/model-supplied path — the
  `write_manifest` confinement pattern). `MODE=delta` **never** runs the pass
  (asserted — same scope boundary as community detection); a **raising extractor
  leaves the deterministic graph intact** (additive resilience). *(TDD integration)*
- [ ] **AC6 — CLI verb `extract-llm`, offline by default, live via `--bedrock`.**
  `graphrag extract-llm` runs the pass **offline** (in-memory store from the fixture
  corpus + `RuleTripleExtractor`) and prints the ordered per-triple trace
  (prompt/schema → doc/span → triple → verdict → edge), labeling the extractor
  **non-semantic**; `--bedrock` switches to `BedrockTripleExtractor`. The real built
  verb is exercised end-to-end through its documented happy path and the observed
  output recorded. *(manual QA + goal-based)*
- [ ] **AC7 — IaC: ingest-task Bedrock grant unchanged; no new resource; cost held.**
  The **ingest task role's** `bedrock:Converse` grant is **unchanged** (still scopes
  the synthesis model with **no wildcard `Resource`**) — this holds *because*
  `BedrockTripleExtractor`'s default `modelId` equals `DEFAULT_SYNTHESIS_MODEL_ID`
  (AC3); the slice adds **no new billable/compute resource and no new IAM grant**; the
  `SCHEMA_EXTRACTION` env flag **defaults off** on the task definition; the query
  Lambda's Neptune grant is unchanged (read-only, ADR-0004); the Budgets value is
  asserted **unchanged at the literal `150`**. Per ADR-0006 / ADR-0002. *(goal-based
  synth, CDK-env-gated)*
- [ ] **AC8 — Offline absence invariant + contract shape (NOT the honesty gate).** A
  hand-authored gold set of prose inter-entity edges is pinned. The test builds the
  **actual deterministic graph** (`resolve()` over the real fixture corpus) and
  asserts, for each gold edge, that **no deterministic edge of *any* kind connects its
  two endpoints in the asserted direction** — absence at the *relationship* level, not
  the trivial "no edge of a never-emitted kind" — so the contrast is real, not a
  strawman; and that the offline pass plumbs the gold edges through
  validation/grounding/stamping. The seeded offline `RuleTripleExtractor` makes **no
  semantic-quality claim**: a green AC8 is *not* a cleared ship gate (that is AC9,
  live). *(goal-based)*
- [ ] **AC9 — Live deploy + schema-guided ingest smoke (the contrast proven).**
  Against a deploy with `SCHEMA_EXTRACTION` on and the corpus in S3, the Fargate task
  extracts triples via **live Bedrock** over the prose bodies, validates + grounds
  them, and writes distinguishable `schema-guided-llm` edges to **live Neptune**; a
  live SigV4 Function-URL query traverses an **LLM-only edge** to answer a question the
  deterministic graph cannot (e.g. a SIG-collaboration or KEP-dependency question), the
  answer's trace marking that hop **model-asserted** (AC11). **This is the honest-win
  ship gate:** the live pass must **recover ≥ the gold bar** of prose edges the
  deterministic graph lacks **and write ≤ the false-positive ceiling** of off-gold
  edges (recall floor + precision ceiling). The per-triple trace artifact is replayed
  from the corpus bucket. Then the stack is destroyed (teardown-first). If the live bar
  is not cleared, the slice **does not ship** and the charter row stays `Backlog`
  (recorded in `docs/backlog.md`, run-or-defer). Live AWS deploy is available in this
  environment (run it; do not auto-defer). *(live smoke)*
- [ ] **AC10 — Teaching contrast doc + drift-closure metadata.** The ingestion
  pattern-axis explanation guide
  ([`ingestion-patterns-and-retrieval-patterns.md`](../../guides/explanation/ingestion-patterns-and-retrieval-patterns.md))
  shows the deterministic↔schema-guided contrast **running** (exact `extract-llm` CLI
  + a graph query answerable only via an LLM edge) so a watcher can state when they
  would choose each; the architecture/develop-offline docs and
  `packages/graphrag/AGENTS.md` record the new modules + invariants (closed-schema
  validation; entity grounding; distinguishable provenance; ingest-only/PyYAML-free);
  the spec is added to `docs/specs/README.md`; **on ship the charter row flips `Planned →
  Have`** (ADR-0006 — the guard decision — is already Accepted; the ship gate decides only
  whether the *slice* lands). *(goal-based)*
- [ ] **AC11 — Read-side provenance (distinguishability at read).** The retrieval
  trace surfaces `extraction_method` per traversed edge: when `expand`/seed-and-expand
  (and the graph templates) follow an edge, the trace records whether the hop used a
  `deterministic` or `schema-guided-llm` edge, so an answer leaning on a model-asserted
  edge shows it and is never blended *silently*. A test traverses a graph holding both
  edge classes and asserts the trace attributes each hop's method. (The write-side
  stamp is AC4; this is the read-side half the "distinguishable" guarantee needs.)
  *(TDD + narratability check)*

## Assumptions

- Technical: the deterministic extractor reads prose **only** via labeled-field regex
  (`_PROSE_AUTHORS`, `extract.py:19`/`:159`) and routes the rest of the prose to the
  *vector* index (`chunk.py`), so it emits **no free-narrative inter-entity edges** —
  the structural gap this slice fills (source: `packages/graphrag/src/graphrag/extract.py`,
  `chunk.py`; RFC-0002 de-risk spike).
- Technical: the read path (`expand`/seed-and-expand via `query.py` `neighbors_batch`,
  and the graph `templates.py`) traverses over **all** `EdgeKind` in both directions and
  selects edges by `kind` alone — so LLM edges are traversable **by default** (the
  teaching payoff: a question answerable only via an LLM edge), but the read path does
  **not** today read `extraction_method`, which is why AC11 threads it into the retrieval
  trace (source: `query.py:169-212`; `templates.py:131-147`).
- Technical: `upsert_edge` merges props with `setdefault` (existing keys win,
  `model.py:111-121`); the disjoint-edge-kind invariant (AC1) guarantees an LLM edge
  never shares a `(src, kind, dst)` key with a deterministic edge, so `extraction_method`
  is set authoritatively with no collision — the invariant is load-bearing, not
  incidental (source: `model.py`; design review 2026-06-27).
- Technical: `Node`/`Edge` carry `doc_paths` driving slice-5 delta orphan removal
  (`model.py:48-71`); LLM edges populate `doc_paths` from their `source_doc` so a delta
  that removes the source doc removes its LLM edges like any other — but since the pass
  runs **full/rebuild only**, an LLM edge can go stale relative to a delta that doesn't
  recompute it (the ADR-0005 community-staleness analog; named, not silently handled)
  (source: `model.py`; ADR-0005 staleness note).
- Technical: edges already carry a `props: dict` and merge-on-upsert (`model.py:60-75`,
  `Graph.upsert_edge`), so `extraction_method` rides as an edge prop with no model
  surgery; the closed LLM-extractable edge kinds are added to `EdgeKind` (additive enum
  members the deterministic path never emits, so existing edge-kind-count tests are
  unaffected) (source: `packages/graphrag/src/graphrag/model.py`).
- Technical: entity grounding reuses the existing `normalize` functions (`sig_id`,
  `kep_id`, `person_id`, `subproject_id`) + `aliases.yaml`; no new resolver is added —
  the no-stable-ID (fuzzy/embedding) case is a separate `Backlog` pattern (source:
  `normalize.py`; `resolve.py`; RFC-0002 ingestion table).
- Technical: the pass runs at ingest time in the Fargate task as an additive phase
  after the graph write — mirroring the ADR-0005 `_community_writeback` phase's
  **injectable-store + `MODE=full`/`rebuild`-only** shape, **but gated on the
  `SCHEMA_EXTRACTION` flag** (no-op by default even on a deployed task), *unlike*
  community detection which keys off `NEPTUNE_ENDPOINT` and runs unconditionally
  (source: `apps/ingestion/entrypoint.py:207-326`).
- Technical: the ingest task role **already** holds a scoped `bedrock:Converse` grant
  at `DEFAULT_SYNTHESIS_MODEL_ID` (added for community summarization, ADR-0005/g-c-s
  AC8), so schema-guided extraction at the same model adds **no IAM grant and no new
  resource** (source: `global-community-summary` plan T8; `apps/infra/stacks/graphrag_stack.py`).
- Technical: extraction uses the existing `boto3` `bedrock-runtime` **Converse** client
  + `BedrockClaudeSynthesizer`'s untrusted-data posture (prose as `messages` data,
  defensive `system` directive, bounded `maxTokens`, default-TLS client); no new
  dependency (source: `synthesize.py`).
- Technical: the per-triple trace artifact is written to the **existing** corpus S3
  bucket the ingest task already writes the manifest to (`entrypoint.py:77-79`,
  `write_manifest`); the task role already has the needed S3 write, so no new grant
  (source: `apps/ingestion/entrypoint.py`; `incremental-delta-reingest` IAM note).
- Process: full work-loop mode — security boundary (untrusted prose routed to an LLM;
  model-authored triples crossing into the graph every retrieval pattern reads) **and**
  structural (new modules + new `EdgeKind` members + an ingest-phase change) **and**
  infra-flavored (Fargate ingest-task change, IaC assertion); constrained by ADR-0006 +
  RFC-0002 + the charter ingestion table + ADR-0001/0002/0003/0004/0005 (source:
  `docs/CONVENTIONS.md` risk triggers; RFC-0002 follow-on artifacts).
- Process: live AWS deploy is available in this environment, so AC9 runs live rather
  than deferring (source: user auto-memory `live-deploy-available`).
- Product: the audience is a solution architect weighing deterministic vs LLM-assisted
  extraction for *their* corpus; the slice ends at the schema-guided pass + validator +
  grounding + per-triple replayable trace + the runnable contrast + the measured honest
  win — free-form extraction and fuzzy resolution stay `Backlog` (source: RFC-0002 Scope
  / Non-goals; charter ingestion table).
- Product: this is the **safer, more teachable** end committed first — schema-guided is
  the governed, auditable extraction strategy (the ingestion analog of
  `opencypher-templates`); free-form is the riskier `text2cypher`-style end and the
  natural next `Backlog → Planned` promotion (source: RFC-0002 Decision-3 rationale).

## Changelog

- 2026-06-27 — Spec authored. Schema-guided LLM extraction (RFC-0002's one `Planned`
  ingestion pattern): a Bedrock pass extracts prose triples constrained to a fixed
  schema, guarded by a closed-schema validator + entity-grounding (no invented
  entities) + distinguishable `extraction_method` provenance + a per-triple replayable
  trace (ADR-0006). Runs as an additive, default-off phase of the Fargate ingest task
  reusing the existing scoped `bedrock:Converse` grant (no new infra/grant; the
  ADR-0005 precedent). Offline via a non-semantic `RuleTripleExtractor`; live Bedrock is
  the semantic oracle. Ships only if a measured honest-win bar is cleared (else the
  charter row drops to `Backlog`). On shipping, the charter row flips `Planned → Have`.
- 2026-06-27 — Spec **Approved** + ADR-0006 **Accepted** (sole-maintainer sign-off,
  pre-EXECUTE reviews clean). The guard decision stands; the charter-row `Planned → Have`
  flip remains gated on the live honest-win (AC9).
