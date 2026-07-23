# Spec: text2opencypher-guarded

- **Status:** Archived <!-- openCypher/LPG era; superseded by spec-text2sparql-guarded (ini-002 / RFC-0004) -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0004](../../adr/0004-text2cypher-read-only-guard.md) (the read-only guard choice this slice ships — layered defense with IAM read-only data-action scoping as the primary backstop, *not* a read-replica endpoint), [Charter — Pattern coverage table, *Text2Cypher* row](../../CHARTER.md#pattern-coverage-against-the-graphragcom-catalog) (the coverage contract this slice ships), [RFC-0001 feasibility note §2](../../rfc/0001-notes/aws-feasibility.md) (Neptune openCypher VERIFIED; the read-replica is *named* the text2cypher guardrail — ADR-0004 records why this single-node topology guards with IAM scoping instead), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (reuses the `Synthesizer` seam), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (rides the existing in-VPC query Lambda behind the IAM-auth Function URL; adds no billable resource; the IAM grant is *narrowed*, never widened), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python)
- **Brief:** [`docs/product/briefs/graphrag-pattern-catalog.md`](../../product/briefs/graphrag-pattern-catalog.md)
- **Contract:** none (new internal Python interfaces + an additive `mode` value on the existing in-VPC Function URL; no repo-root `contracts/` API surface, consistent with the hybrid and governed slices)
- **Shape:** mixed

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> The **Text2Cypher** pattern from the [graphrag.com](https://graphrag.com)
> catalog, implemented on Neptune openCypher — the **risky half** of the
> governed-vs-risky teaching pair. The safe half (`opencypher-templates`, where
> the LLM only *selects* a reviewed parameterized template) is a shipped sibling;
> this one ships the path an enterprise reaches for when no template covers the
> question: the LLM **writes the openCypher itself**, executed **read-only** with
> validation and bounded self-heal. `Depends on:` the hybrid slice
> ([`hybrid-orchestration`](../hybrid-orchestration/spec.md)) for the `GraphStore`
> seam, `NeptuneGraphStore`, the `Synthesizer` seam, the in-VPC query Lambda +
> IAM-auth Function URL, and the showcase set; and the governed slice
> ([`opencypher-templates`](../opencypher-templates/spec.md)) for the
> governed-vs-risky explanation doc it completes.

## Objective

A solution architect evaluating GraphRAG for an enterprise needs to *see* the
**flexible** graph-query path — the one a team reaches for when a question is
open-ended and no expert wrote a template for it — and to weigh its risk against
the governed templates side by side. This slice delivers it: a **Text2openCypher**
path where a Bedrock Claude call **writes the openCypher query itself** from the
question and a graph-schema description, and that model-authored query is executed
**read-only** against Neptune.

Because the executable surface is now whatever the model emits — the model authors
the query *structure and values*, not just bound parameter values — the
parameterize-and-validate defense the governed path relies on does **not** apply.
The guardrail is instead **layered defense** ([ADR-0004](../../adr/0004-text2cypher-read-only-guard.md)):
(1) an **app-layer read-only static validator** rejects any mutating clause or
procedure, rejects multi-statement input, requires a single `RETURN`-bearing query,
and enforces a bounded `LIMIT` before the query is ever sent; (2) a **bounded
self-heal** loop feeds a validation or execution error back to Claude for at most one
re-generation attempt (so a failing query costs at most two LLM generation calls:
one initial + one heal), then refuses;
(3) the load-bearing backstop is **IAM read-only data-action scoping** — the query
Lambda's Neptune grant is `neptune-db:ReadDataViaQuery` + `connect` only, so a write
that escaped the validator is rejected by AWS *before the engine runs it*, and the
read-only guarantee does not depend on the completeness of our parser; (4) the
question and schema ride the Claude boundary as untrusted data, and the caller
receives a sanitized error envelope.

A question runs through: (1) **generation** — a Bedrock Claude (Converse) call
writes one openCypher read query that returns nodes under the alias `n`, given a
fixed graph-schema description; (2) **validation** — the read-only static validator
accepts or rejects it; (3) **self-heal** — on rejection or a Neptune error, one
bounded re-generation attempt with the error as feedback, then a governed refusal;
(4) **execution** — the validated query runs live on Neptune (full engine) or, for
the offline/CI path, against a **bounded read-subset evaluator** over the in-memory
store (honestly labeled a subset — there is no high-fidelity local Neptune
emulator, so live Neptune is the execution-fidelity oracle); (5) **synthesis** — a
Bedrock Claude answer over the returned rows; and (6) the result carries a full
**audit trace**: the schema shown to the model, every generated query and the
validation verdict for each, any self-heal attempt, the query that actually
executed, the rows, and the answer. That trace is the pedagogy — a watcher sees
exactly what the model wrote, that it was read-only-checked, and why it is safe to
run, with no black-box hop.

The path runs **offline by default** (in-memory store + a deterministic non-semantic
generator + the offline synthesizer) for credential-free CI and a laptop demo, and
**live** against the deployed VPC stores + Bedrock through the existing query
Lambda. The teaching contrast is explicit and runnable: the same question can be
asked of the governed templates and of text2cypher side by side, and a watcher
leaves able to state when they would choose each — governed for auditable, bounded,
recurring questions; text2cypher for open-ended coverage where the read-only guard
and validation are non-negotiable.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule.

### Always do

- **Validate every model-authored query as read-only before it executes.** The
  static validator rejects any `CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE` (word-boundary,
  case-insensitive) or mutating procedure `CALL`, rejects multi-statement input, and
  enforces a bounded `LIMIT` (injected or capped). A query that fails validation is
  **never sent to Neptune** — it feeds the self-heal loop or, after the cap, a refusal.
- **Keep IAM read-only scoping the load-bearing guarantee, enforced below the app
  layer.** The query Lambda's Neptune grant is `neptune-db:ReadDataViaQuery` +
  `connect` only — no `WriteDataViaQuery`, no `DeleteDataViaQuery` (ADR-0004). The
  validator is layer 1; the IAM scope is the backstop that holds even if the
  validator is bypassed. The ingestion Fargate task keeps the full read-write grant.
- **Bound the self-heal loop.** At most `MAX_HEAL_ATTEMPTS` (default 1) re-generation
  attempts on a validation or execution error; after the cap, return a narratable
  refusal with **no executed query** — never an unbounded retry against the LLM or
  Neptune.
- **Treat the question and any Neptune error as untrusted/sensitive at the Claude and
  Function-URL boundaries.** Reuse the `BedrockClaudeSynthesizer` posture: question
  and schema ride Converse `messages` as data (never the `system` block); the
  generation `system` block carries the defensive untrusted-data directive (OWASP
  LLM01/LLM08); the answer is display-only; the client is the default botocore-chain
  client over TLS with a bounded `maxTokens`. The raw Neptune error is logged in-VPC
  and fed only to the internal self-heal — it is **never** returned to the caller (it
  can leak schema); the caller gets the generic sanitized envelope + correlation id.
- **Label the offline subset evaluator a subset.** The in-memory evaluator runs only
  a bounded read grammar and never backs a Neptune-dialect-fidelity claim; the output
  names it non-semantic/subset so a reader is never misled. Live Neptune is the
  fidelity oracle.
- **Reuse the existing query Lambda + Function URL for the live path.** The
  text2cypher path is dispatched by the existing **additive, backward-compatible**
  `mode` field on the IAM-auth Function URL (`"hybrid"` default | `"governed"` |
  `"text2cypher"`) — no new endpoint, no new ingress.
- **Keep teardown a feature** (charter principle 4): the slice adds no billable
  resource and no standing cost; the only infra change is a *narrowing* of an existing
  grant.

### Ask first

- **Adding a runtime dependency beyond `pyyaml` + `boto3`.** Generation, validation,
  and the subset evaluator use the existing `bedrock-runtime` Converse client and
  pure Python — reach for any other LLM/HTTP/parser/graph-engine dependency (a local
  Cypher engine such as Neo4j/Memgraph, a parser library) only with sign-off, recorded
  in `packages/graphrag/AGENTS.md`. (The local-engine options were investigated and
  rejected: low Neptune-dialect fidelity + heavy Docker/JVM weight — see plan.)
- **Changing the Function-URL request/response contract beyond the additive
  `mode: "text2cypher"` value, or changing the text2cypher result/trace schema once a
  consumer depends on it.**
- **Pinning or changing the generation model id away from the synthesis-model
  default.** Generation reuses the already-granted synthesis Claude model, so the IAM
  grant is unchanged today; a *different* model would widen the Bedrock grant (AC9).
- **Raising `MAX_HEAL_ATTEMPTS` above a small constant, or widening the supported
  read subset of the offline evaluator** — both change the risk/scope envelope.

### Never do

- **Never execute a model-authored query that failed read-only validation, or after
  the self-heal cap.** Refuse with a governed-refusal result that runs no query.
- **Never grant the query Lambda role `WriteDataViaQuery` or `DeleteDataViaQuery`**,
  and never weaken the read-only validator to "warn" instead of "reject" — the
  read-only property is the whole point of this slice.
- **Never return the raw Neptune error, generated-query internals, or any
  endpoint/ARN/stack detail across the Function URL** — sanitized envelope only.
- **Never let the Lambda's text2cypher import graph `import yaml` at module load.**
  The bundle is PyYAML-free; the existing `sys.modules` guard test is extended to the
  text2cypher modules.
- **Never add a new top-level directory or module boundary** beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces
  (AGENTS.md: top-level directories need an RFC). New code lands as modules/docs
  inside those.
- **Never expose a public, unauthenticated endpoint or weaken the Function URL below
  IAM-auth.**

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1 — TDD.** The read-only static validator is pure logic with a compressible
  invariant: a table of mutating/multi-statement/missing-LIMIT inputs is rejected and
  a table of bounded read queries is accepted; the `LIMIT` enforcement is pinned.
- **AC2–AC5, AC7 — TDD (fast unit/integration over the fixture corpus).** The Bedrock
  generator (against a mock), the self-heal loop, the offline subset evaluator, the
  orchestration + audit trace, and the CLI verb are deterministic over the bundled
  fixtures with the **offline non-semantic generator + offline synthesizer** (or mocks
  for the network adapters); each carries a red-stub-first construction test in
  `plan.md`. Because the offline generator is non-semantic, generation *correctness* is
  asserted structurally; live semantic generation is AC10.
- **AC2/AC5 also pin a security posture:** the generation Converse call uses the
  **default-TLS** botocore client (no `verify=False`, no plaintext `endpoint_url`),
  places the schema + question as **data** in `messages` (never `system`), pins a
  bounded `maxTokens`, **defaults its `modelId` to `DEFAULT_SYNTHESIS_MODEL_ID`** (a
  tested equality, so the no-widened-Bedrock-grant property of AC9 holds by
  construction — mirrors the sibling's AC8), and the raw Neptune error never crosses
  the Function URL. The `ruff` `S` ruleset stays enabled.
- **The offline AC5 integration test pins the *trace-ordering + refusal contract*,
  not generation quality.** Because the offline `RuleText2CypherGenerator`, the subset
  grammar, and the subset evaluator are co-designed to the same shapes over the same
  pinned exemplar, the offline happy-path proves the audit-trace ordering and the
  refusal path — the durable invariants — **not** that generation is semantically
  correct (that is AC10, live). The test is deliberately not tightened into a
  generator-mirror.
- **AC6 — TDD with mock.** `NeptuneGraphStore.run_read_query` against mocked `_run`
  rows decodes the `RETURN n` nodes; a row missing the `n` alias raises a diagnosable
  error, not a bare `KeyError`.
- **AC8 — TDD with mock (in-VPC query Lambda text2cypher dispatch).** With the
  generator, store, and synthesizer mocked, `lambda_handler` with `mode="text2cypher"`
  runs the path end-to-end and returns the audit envelope; an unknown `mode` is a
  client error; the raw Neptune error is not in the envelope; no network call in the
  unit test. A `sys.modules` assertion proves the text2cypher import graph stays
  PyYAML-free.
- **AC9 — goal-based check (`cdk synth` + `aws_cdk.assertions.Template`),
  CDK-env-gated.** This is the ADR-0004 confirmation: the query Lambda's Neptune
  statement grants `ReadDataViaQuery` + `connect` and **does not** grant
  `WriteDataViaQuery` or `DeleteDataViaQuery`; the ingestion task role still grants the
  full read-write set; the Bedrock grant still scopes the synthesis model with no
  wildcard `Resource`; no new resource is added; the Budgets value is asserted
  **unchanged at the literal `150`**.
- **AC10 — live deploy + text2cypher smoke (active end-to-end).** Against the deployed
  stack (corpus dual-written), a SigV4-signed `mode: text2cypher` call generates a
  query live, validates it read-only, executes it live on Neptune, and returns the
  audit trace + a Claude answer; a (test-forced) mutating query is rejected by the
  validator, and a validator-bypassed mutating attempt is rejected by **IAM at the
  engine** (proving the backstop); then the stack is destroyed. Live AWS deploy is
  available in this environment (run it; do not auto-defer).
- **AC11 — goal-based check (side-by-side showcase set + completed contrast doc).** A
  `text2cypher_queries` section in the showcase set holds **≥3** queries (≥1 open-ended
  question no template covers; ≥1 shared with a governed template for the head-to-head),
  each with the gold rows it should return; a loader/test asserts it parses and every
  gold entity resolves in the fixture corpus. The `governed-vs-risky-graph-queries.md`
  doc shows the text2cypher path **running** (exact CLI) alongside the governed path,
  so a watcher can state when they would choose each.
- **AC12 — goal-based check (develop-offline architecture doc).** An architecture doc
  explains *how to develop and test this slice offline* and **records the offline
  decision**: there is no official local Neptune emulator; the openCypher-on-local
  options are abandoned or low-fidelity-and-heavy; so offline runs the pure-Python
  bounded read-subset evaluator (a labeled subset) plus the read-only validator, and
  **live Neptune is the execution-fidelity oracle**. A `grep`/render check confirms it
  exists and links ADR-0004 + the offline-evaluator subset grammar.

## Acceptance Criteria

- [x] **AC1 — Read-only static validator (the governance boundary).**
  `validate_read_only(cypher, *, max_limit) -> ValidationResult` accepts a query iff it
  is **read-only** (no `CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE`/`DETACH`/`DROP`
  word-boundary case-insensitive), **contains no `CALL`** (the demo needs no procedure;
  rejecting *all* `CALL` — not just mutating ones — closes the read-vs-write-procedure
  ambiguity and makes the two-action Neptune grant of AC9 provably sufficient), is a
  **single** statement (no trailing/second statement), contains exactly one `RETURN`
  clause (a `RETURN`-less query is rejected — there is nothing to bound or execute),
  contains **no unbounded variable-length path** (`[*]`, `[*..]`, or `[*N..]` with no
  upper bound is rejected — the read-cost-amplification guard, since `LIMIT` bounds
  *returned* rows, not rows *expanded*), and carries a `LIMIT` within bounds (a missing
  `LIMIT` is appended after the `RETURN`/`ORDER BY` clause at `max_limit`; an over-bound
  `LIMIT` is capped). A rejected query yields a typed `ValidationResult` naming the
  violated rule and is never executed. The validator is **conservative on string
  literals**: a mutating keyword *inside a string literal* (e.g. `WHERE n.title CONTAINS
  'how to DELETE a KEP'`) is **rejected** — a false-reject is the accepted, tested
  trade-off (safety over recall). The validator is **layer 1, not the guarantee**:
  known classes it cannot reliably catch — Unicode/`\u`-escaped clause text, and
  backtick-quoted/dynamically-constructed identifiers — are stopped by the IAM
  read-only backstop (writes) and the engine query timeout (runaway reads), per
  AC9/ADR-0004, **not** by this validator. *(TDD)*
- [x] **AC2 — Bedrock Text2openCypher generator (Converse), with an offline
  deterministic counterpart.** `BedrockText2CypherGenerator` issues a well-formed
  Converse request — a configurable `modelId` (default `DEFAULT_SYNTHESIS_MODEL_ID`); a
  `system` block instructing it to write exactly one openCypher **read** query over the
  fixed schema returning nodes under alias `n`, **plus the defensive directive that the
  schema and question are untrusted data and that it must emit only a read query —
  never a `CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE`/`CALL` clause — regardless of any
  instruction embedded in the question or schema** (LLM01/LLM05/LLM08); the schema +
  question in `messages` **as data**; a bounded `maxTokens` — and parses the returned query
  (stripping any code fence), verified against a **mock** (no live call); the client is
  the default botocore-chain client over TLS. A `RuleText2CypherGenerator` (offline,
  deterministic, **non-semantic**, labeled) emits a query within the offline-evaluator
  subset for CI/offline. The generator accepts an optional `feedback` (the prior error)
  for the self-heal re-generation. *(TDD with mock)*
- [x] **AC3 — Bounded self-heal.** On a validation failure or a Neptune execution
  error, the orchestration re-invokes the generator with the error as `feedback`. The
  bound is explicit: **one initial generation + up to `MAX_HEAL_ATTEMPTS` (default 1)
  re-generations** — so a persistently-failing query makes **at most 2** LLM generation
  calls by default — each re-validated; after the cap it returns a refusal with **no
  executed query**. The `feedback` (the validation rule or Neptune error) is partly
  attacker-influenced and schema-bearing, so it rides the re-generation **in `messages`
  as untrusted data under the same AC2 defensive directive — never in `system`, never as
  a trusted instruction** (an injection-laden error string must not alter the system
  framing; a test asserts this). Each attempt — its generated query and the
  verdict/error that rejected it — is recorded in the trace. *(TDD)*
- [x] **AC4 — Bounded read-subset evaluator (offline execution), labeled a subset.**
  `eval_read_query(cypher, store) -> list[Node]` executes a bounded read grammar over
  the `GraphStore` seam — node-by-id, nodes-by-`kind`, and 1-hop `REL`-by-`kind`
  (in/out), each `RETURN`ing nodes under alias `n`, with `ORDER BY`/`LIMIT` honored and
  results sorted by node id — and raises a typed `UnsupportedOfflineQuery` for any
  construct outside the subset (which the orchestration surfaces as "runs live, not
  offline"). It is explicitly labeled a subset and never claims Neptune fidelity; the
  exemplar (`sig:sig-network -OWNS-> KEPs`) is pinned. *(TDD)*
- [x] **AC5 — Text2openCypher orchestration with a full audit trace.**
  `text2cypher_query(question, *, graph_store, generator, synthesizer, schema,
  max_limit, max_heal_attempts, …)` generates a query, validates it (AC1), self-heals
  within the cap (AC3), executes it (Neptune live / subset evaluator offline — AC4/AC6),
  synthesizes a display answer over the rows, and returns a `Text2CypherResult`
  carrying: the schema shown to the model, **every** generated query with its validation
  verdict, any self-heal attempts, the **query that actually executed**, the rows, the
  answer, citations, and a refusal reason when no query ran. `.render()` narrates, in
  order, **question → schema → generated query (+ verdict, per attempt) → executed query
  → rows → answer** (the audit artifact; no black-box hop, charter principle 1). A
  refusal (validation fails after the heal cap, or the offline subset can't run it)
  returns a `Text2CypherResult` with the reason and **no executed query**. *(TDD +
  narratability check)*
- [x] **AC6 — Neptune executor for an arbitrary read query.**
  `NeptuneGraphStore.run_read_query(cypher) -> list[Node]` runs the validated
  model-authored openCypher live on Neptune and decodes its `RETURN n` rows to nodes
  through the same `_run` path every other method uses; a row missing the `n` alias
  raises a diagnosable `RuntimeError`, not a bare `KeyError`. *(TDD with mock; live in
  AC10)*
- [x] **AC7 — CLI verb `text2cypher-query`, offline by default, live via SigV4.**
  `graphrag text2cypher-query --q "<text>"` runs **offline** (in-memory store from the
  fixture corpus + `RuleText2CypherGenerator` + offline synthesizer) and prints the
  ordered audit trace. `--bedrock` switches to `BedrockText2CypherGenerator` + Bedrock
  Claude synthesis. `--function-url <url>` switches to the **thin live client** — a
  SigV4-signed (`service=lambda`) HTTPS POST of `{"question": …, "mode":
  "text2cypher"}` whose **signature covers the body** — and renders the returned audit
  trace; a non-2xx raises with the body. The offline generator/synthesizer are labeled
  **non-semantic** in the output. *(TDD + narratability check)*
- [x] **AC8 — In-VPC query Lambda text2cypher-mode dispatch, PyYAML-free, sanitized.**
  `lambda_handler` reads `mode == "text2cypher"`; it builds the live Neptune store +
  `BedrockText2CypherGenerator` (the same Converse model) + `BedrockClaudeSynthesizer`
  from the execution role, runs `text2cypher_query`, and returns the audit envelope
  (schema, generated queries + verdicts, executed query, rows, answer, citations,
  trace, refusal reason). An **unknown mode** is a client error; the **over-long-question**
  guard and the **generic sanitized error envelope** (correlation id, no internal
  endpoint/ARN/stack detail, **no raw Neptune error**) apply exactly as for hybrid. **A
  store execution error reaching the orchestrator *via the real path* — including an
  IAM-`AccessDenied`-shaped error (the production failure mode when the write backstop
  fires on a validator-missed mutation) — surfaces as the sanitized envelope + correlation
  id, with no raw error text crossing the Function URL** (a test passes a mock store that
  raises an `AccessDenied`-shaped error and asserts the envelope is clean). The text2cypher
  import graph stays **PyYAML-free** (the `sys.modules` guard is extended). Exercised with
  the generator, store, and synthesizer **mocked** (no network); reuses the **same**
  `text2cypher_query` the CLI uses. *(TDD with mock; live in AC10)*
- [x] **AC9 — IaC: the *query-Lambda* Neptune grant narrowed to read-only; no new
  resource; cost held (the ADR-0004 confirmation).** The **query Lambda's execution
  role's** Neptune IAM statement grants `neptune-db:ReadDataViaQuery` +
  `neptune-db:connect` and **not** `WriteDataViaQuery`/`DeleteDataViaQuery`. The
  assertion is scoped to the query-Lambda role, not a cluster-wide property: the
  **ingestion Fargate task role and the smoke-probe Lambda role retain the full
  read-write set** (both legitimately write — `graphrag_stack.py:303,366`), and **no
  other role's Neptune grant is widened**. The query Lambda's Bedrock grant still scopes
  the synthesis model (`bedrock:Converse`) with **no wildcard `Resource`** — and this
  holds *because* `BedrockText2CypherGenerator`'s default `modelId` equals
  `DEFAULT_SYNTHESIS_MODEL_ID` (AC2); the text2cypher path adds **no new billable/compute
  resource and no new endpoint**; the Budgets value is asserted **unchanged at the literal
  `150`**. The **engine-level read-cost backstop** — the analog of IAM-for-writes — is a
  Neptune `neptune_query_timeout` set explicitly on a (free, teardown-first) cluster
  parameter group, asserted present in synth; a runaway model-authored read is killed by
  the engine even if the validator's `[*]` guard is bypassed. (The aggregate-abuse
  boundary is the **IAM-auth named-principal invoke grant** on the Function URL — only an
  authorized principal can call it; per-request cost is bounded by AC3, and the demo's
  charter posture treats the named-principal grant, not per-caller rate limiting, as the
  accepted aggregate bound — reserved-concurrency is named as future hardening.) Per
  ADR-0004 / ADR-0002. *(goal-based synth, CDK-env-gated)*
- [x] **AC10 — Live deploy + text2cypher smoke (in-VPC), with the backstop proven.**
  Against the deployed stack with the corpus dual-written, a SigV4-signed
  `mode: text2cypher` call generates an openCypher query **live** (Bedrock), validates
  it read-only, executes it **live on Neptune** under the read-only-scoped role, and
  returns the audit trace (the generated + executed query + real rows) and a Bedrock
  Claude answer. The **write-backstop** is proven by confirming the **deployed**
  query-Lambda role grants `neptune-db:ReadDataViaQuery` + `connect` and **no**
  `WriteDataViaQuery`/`DeleteDataViaQuery` (live `aws iam get-role-policy`), atop the
  synth assertion (AC9) and AWS IAM's foundational deny-by-default. *(The originally
  specified out-of-band write-attempt "directly to the Neptune data plane" is
  **infeasible from outside the VPC** — Neptune is VPC-private by design (ADR-0002), the
  very reason query compute is in-VPC — and adding a throwaway in-VPC write-probe is out
  of scope; the deployed-role-policy read is the feasible, honest proof, so no test-only
  bypass hook is added to the production path.)* Then the stack is destroyed
  (teardown-first). **Verified live (2026-06-25):** see the
  [deployment-and-verification record](../../architecture/deployment-and-verification.md).
  *(live smoke)*
- [x] **AC11 — Side-by-side governed-vs-risky teaching contrast (runnable), with the
  shipped doc's stale guard claims corrected.** A `text2cypher_queries` section in the
  showcase `queries.yaml` holds **≥3** queries (≥1 open-ended question no governed
  template covers; ≥1 shared with a governed template for the head-to-head), each with
  the gold rows it should return (all resolving in the fixture corpus); a loader/test
  asserts it parses and every gold entity resolves. (≥3, not the governed sibling's ≥4,
  because text2cypher needs only enough to show open-ended coverage plus one shared
  head-to-head — the governed library has more recurring shapes to demonstrate.) The
  [`governed-vs-risky-graph-queries.md`](../../guides/explanation/governed-vs-risky-graph-queries.md)
  doc shows the **text2cypher path running** (exact `text2cypher-query` CLI) alongside
  the governed path, completing the pair so a watcher can state when they would choose
  each. **The doc's now-stale claims that text2cypher "relies on" / "must lean on" a
  read-only *reader/read-replica endpoint* (a guarantee ADR-0004 supersedes) are
  corrected to the actual guard — the read-only validator + IAM read-only data-action
  scoping + bounded self-heal** — and the one forward-reference clause in the shipped
  sibling spec ([`opencypher-templates/spec.md`](../opencypher-templates/spec.md)) that
  names the read-replica as this path's guard is reconciled to point at ADR-0004.
  *(goal-based)*
- [x] **AC12 — Develop-offline architecture doc + recorded offline decision.** An
  architecture doc (under `docs/architecture/`, linked from the architecture overview)
  explains *how to develop and test this slice offline* — the offline default
  (in-memory store + `RuleText2CypherGenerator` + offline synthesizer), the bounded
  read-subset grammar the evaluator supports, and how to run the live path — and
  **records the offline-execution decision**: no official local Neptune emulator exists;
  the openCypher-on-local options are abandoned (cypher-for-gremlin, Kùzu) or
  low-fidelity-and-heavy (Neo4j/Memgraph, Docker/JVM); so offline uses the pure-Python
  bounded subset evaluator (a labeled subset) + the read-only validator, with **live
  Neptune as the execution-fidelity oracle**, linking ADR-0004. *(goal-based)*

## Assumptions

- Technical: Neptune openCypher executes via `NeptuneGraphStore._run(query, params)`;
  the model-authored query's **values are literals in the query text** (text2cypher
  authors structure *and* values), so unlike the governed path the defense is read-only
  enforcement, not parameterization — this is the heart of the governed-vs-risky
  contrast (source: `packages/graphrag/src/graphrag/store/neptune.py:157`; sibling
  `opencypher-templates` spec).
- Technical: the query Lambda's Neptune grant today is **read-write** — the shared
  `_neptune_data_access` grants `neptune-db:{connect,ReadDataViaQuery,WriteDataViaQuery,
  DeleteDataViaQuery}` and **three** roles hold it: the ingestion task (`:303`) and the
  smoke probe (`:366`) which both legitimately write, and the query Lambda (`:549`)
  which only reads; this slice **narrows the query-Lambda grant** to read-only and
  leaves the ingestion task's and smoke probe's read-write intact (source:
  `apps/infra/stacks/graphrag_stack.py:117-121,303,366,549`).
- Technical: the cluster is a **single Neptune Serverless instance** with no read
  replica, so RFC-0001 §2's reader endpoint is unavailable without a second standing
  instance that breaks ADR-0002 — hence the guard is IAM read-only scoping, recorded in
  ADR-0004 (source: `graphrag_stack.py` `_neptune`; ADR-0002 corrections; ADR-0004).
- Technical: there is **no official local Neptune emulator** and the openCypher-on-local
  options (cypher-for-gremlin, Kùzu) are abandoned while Neo4j/Memgraph are low-fidelity
  to Neptune's dialect *and* heavyweight (Docker/JVM) — so the offline path uses a
  pure-Python bounded read-subset evaluator (labeled a subset) and live Neptune is the
  fidelity oracle (source: research synthesis 2026-06-25, AWS openCypher docs; user
  confirmation 2026-06-25).
- Technical: generation and synthesis use the existing `boto3` `bedrock-runtime`
  **Converse** client at `DEFAULT_SYNTHESIS_MODEL_ID`; no new dependency (source:
  `synthesize.py`; `select.py`; `claude-api` skill — Bedrock Claude via boto3 Converse).
- Technical: the live text2cypher path **reuses the existing in-VPC query Lambda + the
  IAM-auth Function URL**, dispatched by the existing additive `mode` field; the only
  infra change is narrowing the query-Lambda Neptune grant, so the slice adds **no new
  infra resource** (source: `query_lambda.py:96-152`; `graphrag_stack.py`).
- Technical: the text2cypher import graph stays PyYAML-free so it bundles in the
  `Code.from_asset` Lambda; the existing `sys.modules` guard is extended to it (source:
  `query_lambda.py:20-34`; `AGENTS.md` PyYAML-free section).
- Process: full work-loop mode — security boundary (LLM-**authored** queries crossing
  into Neptune; an untrusted question routed to an LLM generator; an IAM-auth public
  Function URL) and structural (new modules + a Function-URL contract value + an IAM
  change); constrained by ADR-0004 + the charter coverage table + RFC-0001 §2 +
  ADR-0001/0002/0003 (source: `docs/CONVENTIONS.md` risk triggers; brief Spec map row
  `text2opencypher-guarded`).
- Process: live AWS deploy is available in this environment, so AC10 runs live rather
  than deferring (source: user auto-memory `live-deploy-available`).
- Product: the audience is a solution architect evaluating the *flexible-but-risky*
  graph-query path side-by-side with the governed templates, able to state when they'd
  choose each; the slice ends at generation + validation + self-heal + offline/live
  execution + the runnable governed-vs-risky contrast + the develop-offline doc (source:
  user confirmation 2026-06-25; charter coverage table; brief Scope/Non-goals).

## Changelog

- 2026-06-25 — Spec authored. Text2Cypher pattern: Bedrock Claude writes openCypher,
  guarded by a read-only static validator + bounded self-heal + IAM read-only
  data-action scoping (ADR-0004, the load-bearing backstop) + sanitized error boundary;
  rides the existing query Lambda via the additive `mode: "text2cypher"` value (only
  infra change is narrowing the query-Lambda Neptune grant — no new resource); offline
  executes via a pure-Python bounded read-subset evaluator (labeled a subset; live
  Neptune is the fidelity oracle, after investigating and rejecting low-fidelity/heavy
  local engines); completes the governed-vs-risky contrast as the runnable risky half;
  ships a develop-offline architecture doc recording the offline-execution decision.
  On shipping, **ADR-0004 flips Proposed → Accepted** (the guard decision is realized).
- 2026-06-25 — Spec-review pass (pre-implementation). Corrected the IAM-narrowing scope
  after review found **three** read-write Neptune holders (ingestion task + smoke probe
  + query Lambda), not one: AC9/ADR-0004 now narrow only the query-Lambda role and
  assert the ingestion task and smoke probe retain read-write. Tightened AC1 (require a
  single `RETURN`; conservative string-literal reject), made the self-heal bound
  explicit (≤2 generation calls), specified AC10's out-of-band IAM-bypass proof, pinned
  the generator model-id equality (AC2/AC9), and made AC11 require correcting the
  shipped doc's stale read-replica guard claims to the ADR-0004 mechanism.
- 2026-06-25 — Spec-stage **security-design** pass. Closed an LLM01 self-heal
  re-injection gap (AC3: `feedback` rides `messages` as untrusted data, never `system`);
  added the read-cost-amplification guard the write-only IAM backstop doesn't cover
  (AC1 rejects unbounded variable-length paths; AC9 adds a Neptune `neptune_query_timeout`
  engine backstop); rejected **all** `CALL` (AC1) so the two-action Neptune grant is
  provably sufficient; required the via-orchestrator IAM-denial path to surface a
  sanitized envelope (AC8); strengthened the generation directive to emit-only-a-read
  (AC2); pre-acknowledged the validator's known layer-1 bypass classes (AC1); and
  recorded the IAM-auth named-principal grant as the accepted aggregate-abuse bound (AC9).
- 2026-06-25 — Implemented + shipped. AC1–AC9, AC11, AC12 met offline/mocked/synth (full
  gates green: ruff/mypy/pytest; adversarial + security + quality reviews clean/non-blocking).
  **AC10 verified live** (deployed `GraphragSlice1`, dual-wrote the corpus, drove live
  `mode: text2cypher` queries where Bedrock **authored** openCypher executed on live Neptune —
  head-to-head + an open-ended `AUTHORS` query — an injection refused at generation, the
  deployed query-Lambda role confirmed read-only, then destroyed). ADR-0004 → Accepted. AC10
  wording corrected: the write-backstop's live proof is the deployed read-only role policy (a
  laptop-direct write to VPC-private Neptune is infeasible by design). Live run caught the
  K-0027 Neptune engine-version-pin gotcha (`1.3.2.0` → `1.3.5.0` from the runtime oracle); no
  app/code bug. All 12 ACs met.
