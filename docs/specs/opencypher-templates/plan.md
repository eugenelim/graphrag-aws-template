# Plan: opencypher-templates

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

The governed path reuses every seam the hybrid slice built. The shape: a new
**pure-Python template registry** (`templates.py`) holds expert-authored
parameterized openCypher strings, each paired with an app-layer evaluator over the
`GraphStore` seam (the `neighbors_batch` dual-form invariant) so the offline
backend executes the identical query. A **selector** seam (`select.py`) decides
*which* template — a Bedrock Converse call (`BedrockTemplateSelector`) for the live
path, a deterministic rule selector (`RuleTemplateSelector`) for CI/offline — but
its output is validated against the fixed template set, so it can only name a
vetted query. A **parameter extractor** (`params.py`) fills the chosen template's
typed slots deterministically (entity slots via the slice-3 `link_question`/
`normalize` functions, confirmed against the store; enums and ints validated), so a
bound value is never free-form model text. An orchestrator (`governed.py`,
`governed_query`) wires selection → extraction → execution → synthesis into a
`GovernedResult` whose `.render()` is the audit trace. A CLI verb
(`governed-query`) and an additive `mode: "governed"` branch in the existing query
Lambda expose it offline and live.

The riskiest parts are (1) the dual-form execution identity — the openCypher and
the app-layer evaluator must return byte-identical sorted node sets, mitigated by
pinning the exemplar in a test exactly as `neighbors_batch` does; and (2) keeping
the governed import graph PyYAML-free so it bundles in the `Code.from_asset`
Lambda, mitigated by making `templates.py`/`select.py`/`params.py`/`governed.py`
import only yaml-free modules and extending the existing `sys.modules` guard test.
**No new infra or IAM** — selection reuses the already-granted synthesis-model
`bedrock:Converse` action and the existing Neptune data-access (AC8).

## Constraints

- **Charter coverage table** (*Cypher Templates* row) — this slice ships that row;
  it is the governed half of the governed-vs-risky pair.
- **RFC-0001 feasibility §2** — Neptune parameterized openCypher is VERIFIED; a run-time
  read-only guard is the *text2cypher* guardrail, **not** this slice's (templates are
  read-only by review + lint). The text2cypher guard is IAM read-only scoping (ADR-0004),
  not RFC-0001 §2's named read-replica.
- **ADR-0001** — reuse the `Synthesizer` seam + `link_question`; no new matching
  model.
- **ADR-0002** — ride the existing in-VPC query Lambda + IAM-auth Function URL; add
  no billable resource; Budgets unchanged at `150`.
- **ADR-0003** — IaC stays AWS CDK Python.
- **`packages/graphrag/AGENTS.md`** — runtime deps stay `pyyaml` + `boto3>=1.35`;
  the query import graph stays PyYAML-free; traversal logic in the app layer behind
  the `GraphStore` seam with a sorted, backend-identical trace.

## Construction tests

Most tests live under their task. Cross-cutting:

**Integration tests:**
- Offline end-to-end (`governed_query` over the fixture corpus with
  `RuleTemplateSelector` + in-memory store + offline synthesizer) on the
  `@thockin → sig-network → owned KEPs` exemplar — asserts the selected template,
  the bound param (`person:thockin`), and the returned KEP rows (T5).
- PyYAML-free import-graph guard: blocks `import yaml`, then imports
  `governed`/`templates`/`select`/`params` (extends `test_query_lambda.py`) (T7).

**Manual verification:** AC9 live deploy + governed-query smoke (run if live AWS is
available; otherwise deferred — see Rollout).

## Design (LLD)

Stack: Python 3.11+, `boto3` `bedrock-runtime` Converse, `botocore` SigV4 to
Neptune openCypher, AWS CDK Python. Conforms to the existing `packages/graphrag`
module stereotypes (a pure logic module + an injectable adapter + an orchestrator +
a CLI verb), mirroring `synthesize.py`/`entity_link.py`/`hybrid.py`.

### Design decisions
- **Templates are a Python registry, not a YAML/JSON data file.** The cypher
  strings are reviewed code → literal "governed/auditable" + Lambda-safe (no yaml
  in the import graph). *Rejected:* a YAML library (would pull yaml into the Lambda
  import graph) and a query DSL compiled to cypher (the brief wants *literal*
  expert-authored openCypher, not generated). Traces to: AC1.
- **Dual-form per template (openCypher + app-layer evaluator), sorted-identical.**
  The exact `neighbors_batch` invariant — the openCypher runs live on Neptune, the
  evaluator runs offline over `neighbors`/`get_node`, both sorted by node id.
  *Rejected:* openCypher-only with execution proven only live (breaks the repo's
  offline-executes-everything posture and the offline demo). Traces to: AC2.
- **The LLM selects; extraction is deterministic.** The selector returns only a
  template id (validated ∈ the fixed set); every parameter value is re-derived/
  validated mechanically. This minimizes and bounds the LLM's authority — the
  teaching contrast with text2cypher. *Rejected:* letting the LLM emit param values
  as free text (would make a bound value model-authored). Traces to: AC3, AC4.
- **Selection via JSON-instructed Converse + strict parse/validate**, mirroring
  `BedrockClaudeSynthesizer`'s text-parse. *Rejected:* Converse `toolConfig`
  tool-use for structured output — viable but unused in this repo and heavier;
  noted as an alternative. Traces to: AC4.
- **Live path rides the existing Function URL via an additive `mode` field.**
  Back-compat (absent ⇒ `hybrid`); no new endpoint/IAM. Traces to: AC6, AC7, AC8.

### Data & schema
- `ParamSpec(name: str, kind: Literal["entity","enum","int"], entity_kind:
  EntityKind | None, choices: tuple[str,...] | None, min: int | None, max: int |
  None, required: bool)`.
- `Template(id: str, description: str, params: tuple[ParamSpec,...], cypher: str,
  evaluate: Callable[[GraphStore, Mapping[str,object]], list[Node]])` — frozen.
- `TEMPLATES: tuple[Template,...]` + `TEMPLATE_BY_ID: dict[str,Template]`.
- `BoundParam(name, value, via)` (provenance of the binding); `GovernedResult(question,
  template_id, template_description, bound_params: list[BoundParam], cypher: str,
  param_map: dict[str,object], rows: list[Node], answer: str, citations: list[str],
  no_match_reason: str | None)`.
- The ≥4 templates over the corpus's structural classes: `sig_owned_keps`
  (`$sig` → owned KEPs), `sig_tech_leads` (`$sig` → tech-lead persons),
  `person_sigs` (`$person` → SIGs participated/led), `kep_owning_sig` (`$kep` →
  owning SIG). Each `RETURN`s a node under alias `n`. Traces to: AC1.

### Interfaces & contracts
- Internal Python seams only (no repo-root `contracts/`). The Function URL request
  gains an optional `mode` field (additive); the governed response envelope adds
  `template_id`, `params`, `cypher`, `rows` to the existing `{answer, citations,
  trace}` shape. Traces to: AC6, AC7.

### Component / module decomposition
- New: `templates.py` (registry + evaluators), `select.py` (selector seam +
  Bedrock/rule impls), `params.py` (extraction/validation), `governed.py`
  (orchestrator + `GovernedResult`). Reused: `entity_link.link_question`,
  `normalize.*`, `synthesize.Synthesizer`, `store/*`, `query.traverse`/`neighbors`.
  Modified: `cli.py` (verb), `query_lambda.py` (mode dispatch), `store/neptune.py`
  (a `run_template_query(cypher, params) -> list[Node]` executor). Traces to:
  AC1–AC7.

### State & control flow
`governed_query`: `select` → if `None` ⇒ no-match result (no query) → else
`extract_params` → if failure ⇒ no-match result (no query) → `execute` (Neptune
cypher live / evaluator offline, sorted) → `synthesize` over rows → `GovernedResult`.
`render()` order: question → template (+why) → bound params → cypher + param map →
rows → answer. Traces to: AC5.

### Behavior & rules
- Read-only lint: a template's cypher contains no `CREATE|MERGE|SET|DELETE|REMOVE`
  (word-boundary, case-insensitive) and no mutating `CALL`.
- No-interpolation lint: the cypher has no `{` / f-string/`%`/`.format` marker; every
  value reference is `$name`; the set of `$name` placeholders equals the declared
  `ParamSpec` names.
- Entity param confirmed: `link_question` candidate normalized id must resolve via
  `store.get_node`; else dropped + recorded; a required entity with no confirmed
  binding ⇒ extraction failure. Traces to: AC1, AC3.

### Failure, edge cases & resilience
- No template fits ⇒ governed no-match (`no_match_reason` set, no query run).
- Required param missing/invalid ⇒ no-match (no query run).
- Selector returns an id ∉ set, malformed JSON, or empty ⇒ treated as no-match.
- Lambda: over-long question rejected pre-orchestration; any failure ⇒ generic
  sanitized envelope + correlation id (reuse existing handler scaffolding);
  unknown `mode` ⇒ client error. Traces to: AC4, AC5, AC7.

### Quality attributes (NFRs / security)
- Parameterization: every value bound via `$param` (no interpolation) — pinned by
  the AC1 lint and the `neptune.py` posture.
- Untrusted-input at the Claude boundary (selection + synthesis): catalog/question/
  rows as Converse `messages` data, defensive system directive, bounded `maxTokens`,
  default-TLS client, display-only answer (reuse `synthesize.py` posture). `ruff` `S`
  stays enabled. Traces to: AC4, AC5.
- PyYAML-free governed import graph (Lambda bundle). Traces to: AC7.

### Dependencies & integration
No new runtime dependency (Converse via existing `bedrock-runtime`; SigV4 via
`botocore`). No new infra resource; reuse the query Lambda's existing grants.
Traces to: AC8.

## Tasks

### T1: Template registry + static governance lint (AC1)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/templates.py, packages/graphrag/tests/test_templates.py
**Tests:**
- `# STUB: AC1` for every template: read-only (no mutating clause/CALL),
  no-interpolation (`$param` only; no `{`/`%`/`.format`), declared param names ==
  `$placeholders`; registry has ≥4 templates; `TEMPLATE_BY_ID` round-trips.
**Approach:**
- Define `ParamSpec`, `Template` (frozen dataclass with `evaluate` callable),
  `TEMPLATES`, `TEMPLATE_BY_ID`, `get_template(id)`.
- Author the four templates' parameterized openCypher (`RETURN n`) + a docstring
  each naming the question class; evaluators are stubbed here (filled in T2).
- Write the lint helpers (`_is_read_only`, `_declared_matches_placeholders`).
**Done when:** `test_templates.py` green; `ruff`/`mypy` clean.

### T2: Dual-form execution — Neptune executor + app-layer evaluators (AC2)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/templates.py, packages/graphrag/src/graphrag/store/neptune.py, packages/graphrag/src/graphrag/governed.py, packages/graphrag/tests/test_templates.py, packages/graphrag/tests/test_neptune.py
**Tests:**
- `# STUB: AC2` exemplar: in-memory `evaluate` for `sig_owned_keps` with
  `{"sig": "sig:sig-network"}` returns the sorted owned-KEP nodes from the fixture
  graph; the Neptune path (mocked `_run` rows) returns the **identical sorted
  list** for the same cypher+params.
**Approach:**
- Implement each template's `evaluate(store, params)` over `get_node`/`neighbors`
  (sorted by node id).
- Add `NeptuneGraphStore.run_template_query(cypher, params) -> list[Node]` (decode
  `row["n"]` via `_node_from_result`, sorted).
- Add `governed.execute_template(store, template, params) -> list[Node]` dispatch
  (Neptune cypher vs app-layer evaluator), sorted-identical.
**Done when:** exemplar identity test green across both backends; gates clean.

### T3: Parameter extraction + validation (AC3)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/params.py, packages/graphrag/tests/test_params.py
**Tests:**
- `# STUB: AC3`: entity slot confirmed (`@thockin`→`person:thockin` present in
  store) binds; unconfirmed candidate dropped+recorded; enum slot valid/invalid;
  int slot parsed + out-of-bounds rejected; missing required ⇒ typed failure.
**Approach:**
- `extract_params(question, template, aliases, store) -> ParamBinding |
  ExtractionFailure`: entity via `link_question` + `store.get_node` confirm; enum
  against `choices`; int parsed + `min`/`max`; record `via` per bound param.
**Done when:** `test_params.py` green; gates clean.

### T4: Selector seam — Bedrock + offline rule selector (AC4)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/select.py, packages/graphrag/tests/test_select.py
**Tests:**
- `# STUB: AC4`: `BedrockTemplateSelector` against a **mock** Converse client
  returns a valid id; an id ∉ set / malformed JSON / empty ⇒ `None`; the Converse
  request has the defensive system directive, catalog+question in `messages` (not
  `system`), bounded `maxTokens`, default-TLS client (no `verify=False`).
  `RuleTemplateSelector` picks the expected template for the exemplar question
  structurally.
**Approach:**
- `TemplateSelector` protocol `select(question, templates) -> str | None`.
- `BedrockTemplateSelector` (configurable `modelId=DEFAULT_SYNTHESIS_MODEL_ID`,
  injectable client): build catalog string, JSON-instructed Converse, parse +
  validate id ∈ set.
- `RuleTemplateSelector`: keyword + `link_question` candidate-kind rules; labeled
  non-semantic.
**Done when:** `test_select.py` green; gates clean.

### T5: Governed orchestration + GovernedResult (AC5)
**Depends on:** T2, T3, T4
**Touches:** packages/graphrag/src/graphrag/governed.py, packages/graphrag/tests/test_governed.py
**Tests:**
- `# STUB: AC5` happy path: offline (`RuleTemplateSelector` + in-memory store +
  `TemplateSynthesizer`) on the exemplar selects `sig_owned_keps` (or
  `person_sigs`), binds the question entity, returns the KEP rows, and
  `.render()` emits question → template → params → cypher+param-map → rows → answer
  in order; no-match (no template / invalid param) returns a result with
  `no_match_reason` and **no** cypher executed.
**Approach:**
- `governed_query(question, *, graph_store, selector, synthesizer, aliases, …)`:
  select → extract → execute (T2) → synthesize over rows → `GovernedResult`.
- `GovernedResult.render()` with the audit ordering; keep the module PyYAML-free.
**Done when:** `test_governed.py` green; gates clean.

### T6: CLI verb `governed-query` (AC6)
**Depends on:** T5
**Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_cli.py
**Tests:**
- `# STUB: AC6`: offline run prints the audit trace + non-semantic label;
  `--function-url` builds a SigV4 POST whose body carries `mode: "governed"`
  (assert via the extended `_function_url_query`); live render path.
**Approach:**
- Add `_cmd_governed_query` + parser (`--q`, corpus args, `--neptune-endpoint`,
  `--bedrock`, `--function-url`, `--region`); offline default (in-memory store +
  `RuleTemplateSelector` + `TemplateSynthesizer`), `--bedrock` ⇒
  `BedrockTemplateSelector` + `BedrockClaudeSynthesizer`.
- Extend `_function_url_query` to accept/send an optional `mode`.
**Done when:** `test_cli.py` green; gates clean.

### T7: Query Lambda governed-mode dispatch (AC7)
**Depends on:** T5
**Touches:** packages/graphrag/src/graphrag/query_lambda.py, packages/graphrag/tests/test_query_lambda.py
**Tests:**
- `# STUB: AC7`: `mode="governed"` with mocked selector/store/synthesizer returns
  the audit envelope; unknown `mode` ⇒ client-error envelope; over-long question
  still rejected; the PyYAML-free `sys.modules` guard now also imports
  `governed`/`templates`/`select`/`params`.
**Approach:**
- Read optional `mode` (default `hybrid`); on `governed` build live Neptune store +
  `BedrockTemplateSelector` (same model) + `BedrockClaudeSynthesizer`, run
  `governed_query`, `_serialize_governed(result)`; reject unknown mode.
**Done when:** `test_query_lambda.py` green; gates clean.

### T8: IaC unchanged — no new resource/grant, cost held (AC8)
**Depends on:** T7
**Touches:** apps/infra/tests/test_graphrag_stack.py
**Tests:**
- `# STUB: AC8`: synth assertion — the query Lambda's Bedrock grant still scopes
  the synthesis model (`bedrock:Converse`) with no wildcard `Resource`; the Budgets
  value is the literal `150`; no new Lambda/Function-URL resource is added for the
  governed path.
**Approach:**
- Extend the existing infra test (no stack code change expected; the governed
  modules ride the existing `Code.from_asset` bundle).
**Done when:** CDK-env-gated synth test green; gates clean.

### T9: Governed showcase set + presenter/explanation doc (AC10)
**Depends on:** T1, T5
**Touches:** packages/graphrag/src/graphrag/showcase/queries.yaml, packages/graphrag/src/graphrag/showcase/__init__.py, packages/graphrag/tests/test_showcase.py, docs/guides/explanation/governed-vs-risky-graph-queries.md
**Tests:**
- `# STUB: AC10`: `load_governed_showcase()` parses ≥4 entries; each names a real
  `template_id` and gold entities that resolve in the fixture corpus.
**Approach:**
- Add `governed_queries` to `queries.yaml` (id, query, template, param, gold,
  highlight) + `GovernedShowcaseQuery` + `load_governed_showcase()`.
- Write the explanation doc: governed templates (bounded, auditable) vs.
  text2cypher (flexible, read-only-enforced) with the exact `governed-query` CLI
  commands.
**Done when:** `test_showcase.py` green; doc renders; gates clean.

### T10: Spec metadata + drift closure (CONVENTIONS § 4) + architecture docs
**Depends on:** T1, T2, T3, T4, T5, T6, T7, T8, T9
*(Not an AC — this task realizes the drift-closure metadata invariants: Status
flip, AC checkbox ticks, the deferral register entry, and the architecture-docs
update. It is finalization, not scope creep.)*
**Touches:** packages/graphrag/AGENTS.md, docs/architecture/overview.md, docs/architecture/security.md, docs/specs/README.md, docs/specs/opencypher-templates/spec.md, docs/backlog.md
**Tests:**
- Goal-based: `tools/lint-spec-status.py` (or repo lint) clean; coverage lint maps
  the brief row; AC checkboxes reflect reality.
**Approach:**
- Update the `graphrag` AGENTS.md module map (templates/select/params/governed) +
  invariants (template dual-form; governed PyYAML-free).
- Update `architecture/overview.md` (governed path) + `security.md` (governed query
  posture: read-only library, parameterized binding, validated params, no run-time
  guard needed — the text2cypher contrast, which guards with IAM scoping per ADR-0004).
- Add the spec to `docs/specs/README.md`; tick met ACs; flip Status. **Verify** any
  deferral token (AC9, created atomically by T11) resolves to a real `docs/backlog.md`
  heading (CONVENTIONS § 4) — T11 owns the anchor+token creation; T10 only checks it.
**Done when:** docs consistent; lints clean; every deferral token resolves to a real
backlog heading.

### T11: Live deploy + governed-query smoke (AC9) — run-or-defer
**Depends on:** T6, T7, T8
**Tests:**
- Manual/live: deploy, dual-write the corpus, SigV4 `mode: governed` call selects a
  template, binds a question param, executes the parameterized openCypher live on
  Neptune, returns the audit trace + a Claude answer; then `cdk destroy`.
**Approach:**
- If live AWS access (creds, CDK bootstrap, Bedrock model access) is available in
  the build environment, run the smoke end-to-end and record it in
  `deployment-and-verification.md`; then tear down.
- **Otherwise defer, atomically:** in the *same* edit, create the `docs/backlog.md`
  heading `### opencypher-templates-live-smoke` (the slice-1 precedent) **and** set
  the spec's AC9 checkbox to `- [ ] AC9 … (deferred: opencypher-templates-live-smoke)`
  — the token and its target heading land together so the token never resolves to a
  missing heading (CONVENTIONS § 4). The offline + mocked path proves the
  orchestration. (T10 then only *verifies* the token resolves; it does not own the
  anchor creation.)
**Done when:** live smoke recorded **or** AC9 deferred with the backlog heading and
the token created in the same edit.

## Rollout

- **Delivery:** additive. The CLI gains a verb; the Function URL gains an optional
  back-compat `mode` field (absent ⇒ `hybrid`, unchanged). Reversible — no data
  migration, no published event. Rollback is reverting the PR.
- **Infrastructure:** none new. The governed modules ride the existing query
  Lambda's `Code.from_asset` bundle; selection reuses the granted synthesis-model
  Converse action and Neptune data-access. Budgets unchanged at `150` (AC8).
- **External-system integration:** Bedrock Claude (selection + synthesis) and
  Neptune openCypher — both already wired and granted for the hybrid path.
- **Deployment sequencing:** none — a single PR. The live smoke (AC9/T11) runs
  against a deploy of this branch if AWS is available, else defers.

## Risks

- **Dual-form drift (openCypher vs evaluator).** A template whose live cypher and
  offline evaluator diverge would mislead the demo. Mitigation: the AC2 exemplar
  identity test + keeping evaluators thin compositions of `neighbors`/`get_node`;
  live correctness is re-proven by AC9.
- **Selector over-trusts the LLM.** Mitigation: the id is validated ∈ the fixed set
  and every param is re-validated deterministically; a bad selection is a visible
  wrong-template in the trace, never an injected query.
- **PyYAML creeps into the governed import graph** (breaks the Lambda bundle).
  Mitigation: the extended `sys.modules` guard test (T7).
- **AC9 live access** may be unavailable in the build environment. Mitigation: the
  run-or-defer rule (T11) with a backlog anchor, consistent with slice-1.

## Changelog

- 2026-06-25: initial plan. Template registry as reviewed Python (Lambda-safe);
  dual-form execution (openCypher + app-layer evaluator, sorted-identical); LLM
  selects, deterministic param extraction; additive Function-URL `mode`; no new
  infra/IAM; AC9 run-or-defer.
