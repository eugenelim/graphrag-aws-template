# Spec: opencypher-templates

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [Charter — Pattern coverage table, *Cypher Templates* row](../../CHARTER.md#pattern-coverage-against-the-graphragcom-catalog) (the coverage contract this slice ships), [RFC-0001 feasibility note §2](../../rfc/0001-notes/aws-feasibility.md) (Neptune parameterized openCypher VERIFIED; read-replica is the *text2cypher* guardrail, not this slice's), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (reuses the synthesizer seam + the question entity-linking from the hybrid slice), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (rides the existing in-VPC query Lambda behind the IAM-auth Function URL; adds no billable resource), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python)
- **Brief:** [`docs/product/briefs/graphrag-pattern-catalog.md`](../../product/briefs/graphrag-pattern-catalog.md)
- **Contract:** none (new internal Python interfaces + an additive `mode` field on the existing in-VPC Function URL; no repo-root `contracts/` API surface, consistent with the hybrid slice)
- **Shape:** mixed

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> The **Cypher Templates** pattern from the [graphrag.com](https://graphrag.com)
> catalog, implemented on Neptune openCypher — the **safe half** of the
> governed-vs-risky teaching pair. The risky half (`text2opencypher-guarded`,
> LLM-authored query text executed read-only) is a separate slice; this one ships
> the path an enterprise reaches for first. `Depends on:` the hybrid slice
> ([`hybrid-orchestration`](../hybrid-orchestration/spec.md)) — it reuses the
> `GraphStore` seam, `NeptuneGraphStore._run`, the `Synthesizer` seam, question
> entity-linking (`link_question`), the in-VPC query Lambda + IAM-auth Function URL,
> and the showcase set.

## Objective

A solution architect evaluating GraphRAG for an enterprise needs to *see* the
**governed** graph-query path — the one a risk-averse team ships before it trusts
an LLM to write queries. This slice delivers it: a small library of
**expert-authored, parameterized openCypher templates** on Neptune, where the
LLM's only job is to **select the right template** from the fixed library and the
**parameters are extracted and validated deterministically** from the question.
The query that actually executes is always one of the vetted, reviewed templates,
with every value bound through the openCypher parameter map — never a string the
model wrote, never a value interpolated into the query text.

A question runs through: (1) **template selection** — a Bedrock Claude call
(Converse) picks exactly one template id from the supplied catalog, validated
against the fixed set (an id outside the set is a governed *no-match*, never a
fabricated query); (2) **parameter extraction** — the template's typed slots are
filled deterministically (entity slots via the slice-1 normalizers/`link_question`
so each value is a confirmed graph node id; enum slots checked against a declared
set; integer slots parsed and bounded); (3) **execution** — the selected
template's parameterized openCypher runs on Neptune; (4) **synthesis** — a Bedrock
Claude answer is composed over the returned rows; and (5) the result carries a
full **audit trace**: which template was selected and why, the bound parameters
and how each was extracted, the **literal parameterized openCypher plus its
parameter map shown separately**, the rows returned, and the answer. That trace is
the pedagogy — an auditor can read exactly which reviewed query ran with which
validated values, with no black-box hop.

The whole path runs **offline by default** (in-memory store + a deterministic rule
selector + the offline synthesizer) for credential-free CI and a laptop demo, and
**live** against the deployed VPC stores + Bedrock through the existing query
Lambda. The teaching contrast is explicit: because the executable surface is a
fixed, reviewed, read-only library and the parameters are validated, this path is
*injection-safe and auditable by construction* — it does **not** need Neptune's
read-replica enforcement that the LLM-authored `text2opencypher-guarded` path
relies on (RFC-0001 §2). A watcher leaves able to say when they would choose the
governed templates over the flexible text2cypher path.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule.

### Always do

- **Bind every parameter through the openCypher parameter map; never interpolate a
  value into the query text.** Entity ids, enum values, and integers ride the
  `$param` map exactly as `NeptuneGraphStore._run` already does — string
  interpolation of any user-/LLM-derived value is the injection vector and is
  forbidden (matches the `neptune.py` posture).
- **Keep the executable surface a fixed, reviewed, read-only library.** Each
  template is expert-authored parameterized openCypher containing only read
  clauses (`MATCH`/`OPTIONAL MATCH`/`WITH`/`WHERE`/`RETURN`/`ORDER BY`/`LIMIT`) —
  no `CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE` or mutating procedure call. The LLM
  selects an id from this library and never authors query text.
- **Re-validate every bound parameter before execution.** Entity slots resolve
  through the slice-1 `normalize`/`link_question` functions to a *confirmed* graph
  node id (an unconfirmed candidate is dropped and recorded); enum slots are
  checked against the template's declared set; integer slots are parsed and
  range-bounded. A param value is never free-form model text.
- **Pair each template's openCypher with an app-layer evaluator that returns the
  identical sorted result.** The parameterized openCypher is the governed artifact
  that runs on Neptune; a paired evaluator over the `GraphStore` seam runs the
  same template against the in-memory backend for offline/CI, with results sorted
  so the two backends are byte-identical — the same invariant `neighbors_batch`
  already lives under (`packages/graphrag/AGENTS.md`).
- **Treat the question and the returned rows as untrusted data at the Claude
  boundary.** Reuse the `BedrockClaudeSynthesizer` posture: question and rows ride
  Converse `messages` as data (never the `system` block), the answer is
  display-only, the client is the default botocore-chain client over TLS, and the
  selection call carries the same defensive untrusted-data directive (OWASP
  LLM01/LLM08).
- **Reuse the existing query Lambda + Function URL for the live path.** The
  governed path is dispatched by an **additive, backward-compatible** `mode` field
  on the existing IAM-auth Function URL (`"hybrid"` default | `"governed"`) — no
  new endpoint, no new ingress. This discharges the hybrid slice's *Ask first* rail
  ("changing the Function-URL request/response contract once a downstream consumes
  it"): the `mode` field is purely additive with a back-compat default, so an
  existing hybrid caller is unaffected — the same additive carve-out slice 4 used
  to add `persona` to the same request body.
- **Keep teardown a feature** (charter principle 4): the slice adds no billable
  resource and no standing cost.

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** Selection uses the
  existing `bedrock-runtime` Converse client (no new dependency); reach for any
  other LLM/HTTP client only with sign-off, recorded in
  `packages/graphrag/AGENTS.md`.
- **Changing the Function-URL request/response contract beyond the additive
  `mode` field, or changing the governed result/trace schema once a consumer
  depends on it.**
- **Pinning or changing the selection model id away from the synthesis-model
  default.** Selection reuses the already-granted synthesis Claude model, so the
  IAM grant is unchanged today; a *different* model would widen the grant (AC8).
- **Adding a template whose openCypher is not a bounded read** (or that needs a
  write/admin capability) — that is out of this slice's read-only contract.

### Never do

- **Never route LLM-authored openCypher text to Neptune.** That is the separate
  `text2opencypher-guarded` slice (RFC-0001 §2). This slice's executable surface
  is the fixed, reviewed template library only.
- **Never string-interpolate a user-/LLM-derived value into a query** — always the
  parameter map.
- **Never execute with an unvalidated/missing required parameter, or with a
  template id outside the fixed set.** Refuse with a governed *no-match* result
  that runs no query.
- **Never let the Lambda's governed import graph `import yaml` at module load.**
  The bundle is PyYAML-free; the existing `sys.modules` guard test is extended to
  the governed modules.
- **Never add a new top-level directory or module boundary** beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces
  (AGENTS.md: top-level directories need an RFC). New code lands as modules/docs
  inside those.
- **Never let the offline rule selector or the offline synthesizer back a quality
  claim** — both are labeled non-semantic in the output; semantic behaviour is the
  live path.
- **Never expose a public, unauthenticated endpoint or weaken the Function URL
  below IAM-auth.**

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1 — TDD + goal-based static check.** A test over the template registry
  asserts, for every template, that the openCypher is read-only, every value is a
  declared `$param` (no interpolation token in the query string), and the declared
  param slots exactly match the placeholders used.
- **AC2–AC6 — TDD (fast unit/integration over the fixture corpus).** Dual-form
  execution identity, parameter extraction/validation, the Bedrock selector
  (against a mock), the governed orchestration + audit trace, and the CLI verb are
  deterministic over the bundled fixtures with the **offline rule selector +
  offline synthesizer** (or mocks for the network adapters); each carries a
  red-stub-first construction test in `plan.md`. Because the offline selector is
  non-semantic, selection *correctness* is asserted structurally (the rule selector
  picks the template the showcase entry names) and the live semantic selection is
  AC9.
- **AC4 also pins a security posture:** the selection Converse call uses the
  **default-TLS** botocore client (no `verify=False`, no plaintext `endpoint_url`),
  places the catalog + question as **data** in `messages` (never `system`), pins a
  bounded `maxTokens`, and **rejects a returned template id outside the fixed
  set**. The `ruff` `S` ruleset stays enabled.
- **AC7 — TDD with mock (in-VPC query Lambda governed dispatch).** With the
  selector, the store, and the synthesizer mocked, `lambda_handler` with
  `mode="governed"` runs the governed path end-to-end and returns the audit
  envelope; an unknown `mode` is a client error; no network call in the unit test.
  A `sys.modules` assertion proves the governed import graph stays PyYAML-free.
- **AC8 — goal-based check (`cdk synth` + `aws_cdk.assertions.Template`),
  CDK-env-gated.** The synthesized stack adds **no** new resource and **no** new
  IAM statement for the governed path: the query Lambda's Bedrock grant still
  scopes the synthesis model (Converse) with no wildcard `Resource`, and the
  Budgets value is asserted **unchanged at the literal `150`**.
- **AC9 — live deploy + governed-query smoke (active end-to-end).** Against the
  deployed stack (corpus dual-written), a SigV4-signed `mode: governed` call to the
  Function URL selects a template, binds a question-extracted parameter, executes
  the parameterized openCypher live on Neptune, and returns the audit trace (the
  executed cypher + param map + real rows) and a Claude answer; then the stack is
  destroyed. If live AWS access is unavailable in the build environment, this
  criterion ships deferred against a backlog anchor (the slice-1 precedent), with
  the offline + mocked path proving the orchestration.
- **AC10 — goal-based check (governed showcase set + presenter/explanation doc).**
  A `governed_queries` section in the showcase set holds **≥4** queries, each
  labeled with the template it should select, the parameter it should bind, and the
  gold rows it should return; a loader/test asserts it parses and every named
  template id + gold entity resolves in the fixture corpus. A doc under
  `docs/guides/` walks the governed path and states the governed-vs-risky contrast.

Gates: `ruff` (lint+format, `S` security ruleset), `mypy` (typecheck), `pytest`
(tests). Already wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [x] **AC1 — A fixed library of expert-authored, parameterized, read-only
  openCypher templates.** A template registry holds **≥4** templates over the
  corpus's structural question classes (e.g. "the KEPs a SIG owns", "who
  tech-leads a SIG", "the SIGs a person participates in", "a KEP's owning SIG").
  Each template declares: a stable `id`, a `description` (the question class, used
  for selection), typed parameter slots (each `entity`/`enum`/`int` with the
  entity kind or allowed set/bounds), and a **parameterized openCypher** string.
  A static check asserts, for every template: the query is **read-only** (no
  `CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE`/mutating `CALL`), every value is a
  declared `$param` (no value interpolation token in the string), and the declared
  slots exactly match the `$param` placeholders. *(TDD + goal-based static check)*
- [x] **AC2 — Dual-form execution, backend-identical.** Each template carries the
  parameterized openCypher (executed by `NeptuneGraphStore` live) **and** an
  app-layer evaluator over the `GraphStore` seam (in-memory offline). For a given
  bound parameter set, both forms return the **same sorted node set**, pinned on a
  fixture exemplar (the `person:thockin → sig:sig-network → owned KEPs` chain),
  with results sorted so order is backend-independent — the `neighbors_batch`
  invariant (`packages/graphrag/AGENTS.md`). *(TDD)*
- [x] **AC3 — Deterministic parameter extraction + validation (the governance
  boundary).** `extract_params(question, template, aliases)` fills the template's
  declared slots: an **entity** slot resolves through the slice-1
  `link_question`/`normalize` functions to a graph node id and is **confirmed**
  against the store (an unconfirmed candidate is dropped and recorded); an **enum**
  slot is validated against the declared set; an **int** slot is parsed and
  range-bounded. A **missing or invalid required** slot yields a typed extraction
  failure — never an execution with a bad parameter, and never free-form model
  text bound as a value. *(TDD)*
- [x] **AC4 — Bedrock template selector (Converse), validated, with an offline
  deterministic counterpart.** `BedrockTemplateSelector` issues a well-formed
  Converse request — a configurable `modelId` (default `DEFAULT_SYNTHESIS_MODEL_ID`);
  a `system` block instructing selection of exactly one template id from the
  supplied catalog as JSON, **plus the defensive directive that the catalog and
  question are untrusted data** (LLM01/LLM08); the catalog + question in `messages`
  **as data**; a bounded `maxTokens` — parses the JSON and **validates the returned
  id is within the fixed template set** (an unknown/absent id → governed no-match,
  never a fabricated query), verified against a **mock** (no live call); the client
  is the default botocore-chain client over TLS. A `RuleTemplateSelector` (offline,
  deterministic, **non-semantic**, labeled) selects via `link_question` candidate
  kinds + keyword rules for CI/offline. The selector returns a template id only;
  parameter filling is AC3. *(TDD with mock)*
- [x] **AC5 — Governed orchestration with a full audit trace.** `governed_query(question,
  *, graph_store, selector, synthesizer, aliases, …)` selects a template, extracts
  and validates parameters (AC3), executes the template (AC2), synthesizes a
  display answer over the returned rows, and returns a `GovernedResult` carrying:
  the selected template `id` + `description`, the **bound parameters with how each
  was extracted**, the **literal parameterized openCypher and its parameter map
  shown separately** (never interpolated together), the returned rows/nodes, the
  answer, and citations. `.render()` narrates, in order, **question → selected
  template (+ why) → bound params → cypher + param map → rows → answer** (the audit
  artifact; no black-box hop, charter principle 1). A **no-match** (no template
  fits, or a required parameter cannot be validated) returns a `GovernedResult`
  with a governed-refusal explanation and **no executed query**. *(TDD +
  narratability check)*
- [x] **AC6 — CLI verb `governed-query`, offline by default, live via SigV4.**
  `graphrag governed-query --q "<text>"` runs **offline** (in-memory store from the
  fixture corpus + `RuleTemplateSelector` + offline synthesizer) and prints the
  ordered audit trace. `--bedrock` switches to `BedrockTemplateSelector` + Bedrock
  Claude synthesis. `--function-url <url>` switches to the **thin live client** — a
  SigV4-signed (`service=lambda`) HTTPS POST of `{"question": …, "mode":
  "governed"}` whose **signature covers the body** — and renders the returned audit
  trace; a non-2xx raises with the body. The offline selector/synthesizer are
  labeled **non-semantic** in the output. *(TDD + narratability check)*
- [x] **AC7 — In-VPC query Lambda governed-mode dispatch, PyYAML-free, sanitized.**
  `lambda_handler` reads an optional `mode` (`"hybrid"` default | `"governed"`);
  `governed` builds the live Neptune store + `BedrockTemplateSelector` (the same
  Converse model) + `BedrockClaudeSynthesizer` from the execution role, runs
  `governed_query`, and returns the audit envelope (template id, params, cypher +
  param map, rows, answer, citations, trace). An **unknown mode** is a client
  error; the **over-long-question** guard and the **generic sanitized error
  envelope** (correlation id, no internal endpoint/ARN/stack detail) apply exactly
  as for hybrid. The governed import graph stays **PyYAML-free** (the existing
  `sys.modules` guard is extended to the governed modules). Exercised with the
  selector, store, and synthesizer **mocked** (no network); reuses the **same**
  `governed_query` the CLI uses. *(TDD with mock; live in AC9)*
- [x] **AC8 — IaC unchanged: no new resource, no widened grant, cost held.** The
  governed path synthesizes **no** new CDK resource and **no** new IAM statement —
  selection reuses the already-granted synthesis-model `bedrock:Converse` action
  and the existing Neptune data-access. This "no widened grant" holds **because the
  selector's default model id equals the already-granted synthesis model**
  (`DEFAULT_SYNTHESIS_MODEL_ID`, AC4); pointing selection at a *different* model is
  an *Ask first* change (Boundaries), since it would re-scope the grant. A synth
  assertion confirms the query Lambda's Bedrock grant still scopes the synthesis
  model with **no wildcard `Resource`**, and the Budgets value is asserted
  **unchanged at the literal `150`**. Per ADR-0002. *(goal-based synth,
  CDK-env-gated)*
- [ ] **AC9 — Live deploy + governed-query smoke (in-VPC).** (deferred: opencypher-templates-live-smoke)
  Against the deployed stack with the corpus dual-written, a SigV4-signed `mode: governed`
  call to the Function URL selects a template, binds a question-extracted parameter, executes
  the parameterized openCypher **live on Neptune**, and returns the audit trace (the executed
  cypher + param map + real rows) and a Bedrock Claude answer; then the stack is destroyed
  (teardown-first). **Deferred:** live AWS access (creds, CDK bootstrap, Bedrock model access)
  is unavailable in this build environment; the offline + mocked path (AC2–AC8, AC10) proves
  the orchestration, and the live smoke is the only step left — the slice-1 precedent. *(live
  smoke)*
- [x] **AC10 — Governed showcase set + the governed-vs-risky teaching framing.** A
  `governed_queries` section in the showcase `queries.yaml` holds **≥4** queries,
  each labeled with the template it should select, the parameter it should bind, and
  the gold rows it should return (all resolving in the fixture corpus); a
  loader/test asserts it parses and every named template id + gold entity resolves.
  A doc under `docs/guides/` walks the governed path with the exact CLI commands and
  **states the governed-vs-risky contrast** — parameterized templates are bounded
  and auditable; `text2opencypher` is flexible but needs read-only enforcement — so
  a watcher can state when they would choose each. *(goal-based)*

## Assumptions

- Technical: runtime is Python 3.11+; runtime deps stay `pyyaml` + `boto3>=1.35`,
  infra extra is `aws-cdk-lib`/`constructs`, dev is `pytest`/`ruff` (with the `S`
  ruleset)/`mypy` (source: `pyproject.toml`; `packages/graphrag/AGENTS.md`).
- Technical: parameterized openCypher executes via `NeptuneGraphStore._run(query,
  params)`, which JSON-encodes the parameter map and never string-interpolates a
  value — so templates ride the existing, vetted execution path (source:
  `packages/graphrag/src/graphrag/store/neptune.py:157`).
- Technical: template selection and synthesis use the existing `boto3`
  `bedrock-runtime` **Converse** client at `DEFAULT_SYNTHESIS_MODEL_ID =
  us.anthropic.claude-sonnet-4-6`; no new dependency (source: `synthesize.py:49,198`;
  `claude-api` skill — Bedrock Claude is invoked via boto3 Converse with
  `us.anthropic.`-prefixed ids).
- Technical: parameter extraction reuses the slice-1 `link_question`/`normalize`
  functions on the controlled vocabulary (`@handles`, SIG slugs, KEP numbers), so a
  bound entity value is byte-identical to its resolved graph node id (source:
  `entity_link.py:71`).
- Technical: the live governed path **reuses the existing in-VPC query Lambda + the
  IAM-auth Function URL**, dispatched by an additive back-compat `mode` field; the
  Lambda's IAM already grants `bedrock:Converse` on the synthesis model and Neptune
  data-access, so the slice adds **no new infra resource or IAM statement** (source:
  `apps/infra/stacks/graphrag_stack.py:397`; `query_lambda.py:94`).
- Technical: the offline path executes templates against the in-memory store via a
  per-template app-layer evaluator returning the identical sorted result — the
  established `neighbors_batch` dual-form invariant (source:
  `packages/graphrag/AGENTS.md` invariants; `store/base.py`).
- Technical: the governed orchestration's import graph stays PyYAML-free so it can
  run in the `Code.from_asset` Lambda bundle; the template registry is pure Python
  (no yaml), and the existing `sys.modules` guard is extended to it (source:
  `query_lambda.py:20-34`; `AGENTS.md` PyYAML-free section).
- Process: full work-loop mode — security boundary (Bedrock + Neptune network I/O;
  an untrusted question routed to an LLM selector; an IAM-auth public Function URL)
  and structural (new modules + a Function-URL contract extension); constrained by
  the charter coverage table + RFC-0001 §2 + ADR-0001/0002/0003 (source:
  `docs/CONVENTIONS.md` risk triggers; brief Spec map row `opencypher-templates`).
- Product: the audience is a solution architect evaluating the *governed* graph-query
  path; the slice ends at the template library + selection + offline/live execution +
  the governed-vs-risky framing, with the LLM-authored `text2opencypher-guarded`
  path out of scope (source: charter coverage table; brief Scope/Non-goals).

## Changelog

- 2026-06-25 — Spec authored. Cypher Templates pattern: fixed read-only
  parameterized openCypher library, LLM selects + deterministic param extraction,
  full audit trace; rides the existing query Lambda via an additive `mode` field
  (no new infra); offline executes via the `neighbors_batch` dual-form invariant;
  governed-vs-risky contrast documented as the teaching payoff.
- 2026-06-25 — Implemented and shipped. AC1–AC8 + AC10 met (offline + mocked, full gates
  green: ruff/mypy/pytest). New modules `templates.py`/`params.py`/`select.py`/`governed.py`,
  `governed-query` CLI verb, additive `mode: governed` query-Lambda dispatch, governed
  showcase set + the governed-vs-risky explanation doc; no new dependency, no new infra.
  AC9 (live deploy smoke) deferred — live AWS unavailable in the build environment (backlog
  `opencypher-templates-live-smoke`).
