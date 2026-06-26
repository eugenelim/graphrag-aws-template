# Plan: text2opencypher-guarded

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

The risky path is the structural mirror of the governed slice, with the LLM's
authority *widened* (it writes the query, not just picks one) and the guard moved
from construction-time (a reviewed library) to run-time (validation + IAM). The
shape: a **generator** seam (`generate.py`) writes openCypher — a Bedrock Converse
call (`BedrockText2CypherGenerator`) for the live path, a deterministic
non-semantic generator (`RuleText2CypherGenerator`) for CI/offline — given a fixed
graph-schema description and the question as untrusted data. A **read-only static
validator** (`validate.py`, `validate_read_only`) is the first guard: it rejects any
mutating clause/procedure, rejects multi-statement input, and enforces a bounded
`LIMIT`. A **bounded read-subset evaluator** (`cypher_eval.py`, `eval_read_query`)
runs the validated query offline over the in-memory store for the supported read
grammar (labeled a subset), while `NeptuneGraphStore.run_read_query` runs genuinely
arbitrary openCypher live. An orchestrator (`text2cypher.py`, `text2cypher_query`)
wires generate → validate → **bounded self-heal** → execute → synthesize into a
`Text2CypherResult` whose `.render()` is the audit trace. A CLI verb
(`text2cypher-query`) and the existing additive `mode: "text2cypher"` branch in the
query Lambda expose it offline and live.

The one infra change is the **ADR-0004 backstop**: split the shared
`_neptune_data_access` into a read-only statement (the query Lambda) and the
existing read-write statement (the ingestion task), so the query Lambda physically
cannot write — the read-only guarantee no longer rests on the validator's
completeness. This is a *narrowing*, adds no resource, and holds Budgets at `150`.

The riskiest parts are (1) the **bounded read-subset evaluator** — a tiny
pattern-matched interpreter that must execute exactly the read shapes the offline
generator emits and refuse everything else with a typed `UnsupportedOfflineQuery`,
mitigated by scoping the grammar to ~4 shapes and pinning the exemplar; and (2)
keeping the text2cypher import graph **PyYAML-free** so it bundles in the Lambda,
mitigated by importing only yaml-free modules and extending the `sys.modules` guard.

## Constraints

- **ADR-0004** — the guard is layered defense with **IAM read-only data-action
  scoping** as the load-bearing backstop, *not* a read-replica endpoint (the
  single-node Serverless topology can't provide one without a costly replica). The
  app-layer validator is layer 1; the IAM scope is the guarantee. The synth fitness
  test (T9) is the ADR's named Confirmation.
- **Charter coverage table** (*Text2Cypher* row) — this slice ships that row, the
  risky half of the governed-vs-risky pair.
- **RFC-0001 feasibility §2** — Neptune openCypher VERIFIED; the read-replica is
  *named* the text2cypher guardrail, and ADR-0004 records why this topology guards
  with IAM scoping instead. No mechanism is silently dropped — the divergence is
  documented (T12).
- **ADR-0001** — reuse the `Synthesizer` seam; no new model for generation
  (generation reuses the synthesis Converse model, so no widened Bedrock grant).
- **ADR-0002** — ride the existing in-VPC query Lambda + IAM-auth Function URL; add
  no billable resource; Budgets unchanged at `150`; the IAM change is a narrowing.
- **ADR-0003** — IaC stays AWS CDK Python.
- **`packages/graphrag/AGENTS.md`** — runtime deps stay `pyyaml` + `boto3>=1.35` (no
  local Cypher engine / parser dependency — investigated and rejected, see Risks);
  the query import graph stays PyYAML-free; the offline backend executes behind the
  `GraphStore` seam with a sorted, backend-identical trace.

## Construction tests

Most tests live under their task. Cross-cutting:

**Integration tests:**
- Offline end-to-end (`text2cypher_query` over the fixture corpus with
  `RuleText2CypherGenerator` + in-memory store + offline synthesizer) on the
  `sig:sig-network -OWNS-> KEPs` exemplar — asserts the generated query validates
  read-only, the subset evaluator returns the KEP rows, and `.render()` emits
  question → schema → generated query (+ verdict) → executed query → rows → answer
  in order (T5).
- A **mutating** generated query (forced via a stub generator) is rejected by the
  validator, drives one self-heal attempt, and — if the heal also fails — returns a
  refusal with **no** executed query and no Neptune call (T3/T5).
- PyYAML-free import-graph guard: blocks `import yaml`, then imports
  `text2cypher`/`generate`/`validate`/`cypher_eval` (extends
  `test_query_lambda.py`) (T8).

**Manual verification:** AC10 live deploy + text2cypher smoke (run — live AWS is
available), including the IAM-backstop proof (a validator-bypassed mutating attempt
is rejected by IAM at the engine).

## Design (LLD)

Stack: Python 3.11+, `boto3` `bedrock-runtime` Converse, `botocore` SigV4 to
Neptune openCypher, AWS CDK Python. Conforms to the existing `packages/graphrag`
module stereotypes (a pure logic module + an injectable adapter + an orchestrator +
a CLI verb), mirroring `validate`↔`templates`, `generate`↔`select`,
`text2cypher`↔`governed`.

### Design decisions
- **The model writes the whole query (structure + literal values); the guard is
  read-only enforcement, not parameterization.** This is the deliberate contrast
  with the governed path — there is no `$param` map to bind, because the model
  authored the values too. Safety comes from the validator + the IAM read-only
  scope, never from "the values are bound". *Rejected:* trying to parameterize a
  model-authored query (incoherent — the model chose the literals). Traces to: AC1,
  AC5, AC9.
- **IAM read-only data-action scoping is the load-bearing backstop (ADR-0004),
  the validator is layer 1.** The query Lambda's Neptune grant is narrowed to
  `ReadDataViaQuery` + `connect`; a write that escapes the validator is denied by
  AWS before the engine runs. *Rejected:* a read-replica reader endpoint (no replica
  on the single-node cluster; adding one breaks ADR-0002 cost posture) and
  validator-only (guarantee would rest on parser completeness). Traces to: AC9, AC10.
- **Offline execution via a pure-Python bounded read-subset evaluator, labeled a
  subset; live Neptune is the fidelity oracle.** *Rejected:* a local Cypher engine
  (Neo4j/Memgraph) — low Neptune-dialect fidelity *and* heavyweight Docker/JVM,
  breaking the pure-Python/laptop-runnable/PyYAML-free posture; cypher-for-gremlin
  and Kùzu are abandoned. Documented in the develop-offline doc (T12). Traces to:
  AC4, AC12.
- **Bounded self-heal (default 1 attempt).** A validation/execution error is fed
  back to the generator once, then a refusal. *Rejected:* unbounded retry (cost/DoS
  surface against the LLM and Neptune). Traces to: AC3.
- **Generated queries return nodes under alias `n`.** Both backends decode uniformly
  and the synthesizer summarizes a node list, mirroring `run_template_query`.
  *Rejected:* arbitrary RETURN shapes (would need a general row decoder + a general
  synthesizer; out of scope). The schema prompt instructs `RETURN … AS n`. Traces
  to: AC2, AC6.
- **Live path rides the existing Function URL via the existing additive `mode`
  field** (now `hybrid|governed|text2cypher`). Back-compat; no new endpoint/IAM
  resource. Traces to: AC7, AC8, AC9.

### Data & schema
- `GRAPH_SCHEMA_DESCRIPTION: str` — the fixed schema shown to the model and echoed
  in the trace: node label `Entity` with `id`/`kind` props (kinds = `EntityKind`
  values); a single relationship type `REL` carrying a `kind` property (= `EdgeKind`
  values); the instruction to write one read query returning nodes `AS n`.
- `ValidationResult(ok: bool, query: str, violated_rule: str | None, normalized_query:
  str)` — `normalized_query` carries the LIMIT-injected/capped form actually run.
- `GenerationAttempt(query: str, validation: ValidationResult, error: str | None)` —
  one self-heal attempt's provenance.
- `Text2CypherResult(question, schema, attempts: list[GenerationAttempt],
  executed_query: str | None, rows: list[Node], answer: str, citations: list[str],
  refusal_reason: str | None)` — the audit artifact.
- `UnsupportedOfflineQuery(Exception)` — raised by the offline evaluator for a
  construct outside the subset.

### Interfaces & contracts
- Internal Python seams only (no repo-root `contracts/`). The Function URL request's
  existing optional `mode` field gains the value `"text2cypher"` (additive); the
  response envelope adds `schema`, `attempts`, `executed_query`, `rows`,
  `refusal_reason` to the `{answer, citations, trace}` shape. Traces to: AC7, AC8.

### Component / module decomposition
- New: `validate.py` (`validate_read_only`, `ValidationResult`), `generate.py`
  (`Text2CypherGenerator` protocol + `BedrockText2CypherGenerator` /
  `RuleText2CypherGenerator`, `GRAPH_SCHEMA_DESCRIPTION`), `cypher_eval.py`
  (`eval_read_query`, `UnsupportedOfflineQuery`), `text2cypher.py`
  (`text2cypher_query` + `Text2CypherResult` + `GenerationAttempt`).
- Reused: `synthesize.Synthesizer`/`BedrockClaudeSynthesizer`, `store/*`, `model`.
- Modified: `cli.py` (verb), `query_lambda.py` (mode dispatch + `_serialize_text2cypher`),
  `store/neptune.py` (`run_read_query`), `apps/infra/stacks/graphrag_stack.py`
  (split the Neptune grant), `showcase/queries.yaml` + `showcase/__init__.py`
  (`text2cypher_queries`). Traces to: AC1–AC9, AC11.

### State & control flow
`text2cypher_query`: **initial** `generate(question, schema, feedback=None)` →
`validate_read_only` → if invalid and attempts remain ⇒ `generate(…,
feedback=violated_rule)` (re-generate, up to `max_heal_attempts` times — default 1, so
≤ 2 total generation calls) → if still invalid ⇒ refusal (no query) → else `execute`
(Neptune `run_read_query` live / `eval_read_query` offline); on a Neptune error or
`UnsupportedOfflineQuery`, feed back once within the same cap → `synthesize` over rows
→ `Text2CypherResult`. `render()` order: question → schema → each attempt (query +
verdict) → executed query → rows → answer. Traces to: AC3, AC5.

### Behavior & rules
- Read-only lint (validator): no `CREATE|MERGE|SET|DELETE|REMOVE|DETACH|DROP`
  (word-boundary, case-insensitive, matched over the **whole** query so a
  literal-embedded keyword conservatively rejects) and **no `CALL` at all** (read or
  mutating — the demo needs no procedure, and rejecting all of them makes the two-action
  Neptune grant provably sufficient); reject a second statement (a `;` followed by
  non-whitespace/non-comment); require exactly one `RETURN` (a `RETURN`-less query is
  rejected — nothing to bound/execute); reject an **unbounded variable-length path**
  (`[*]`/`[*..]`/`[*N..]` with no upper bound — the read-cost guard, since `LIMIT`
  bounds returned not expanded rows); a missing `LIMIT` is appended after the
  `RETURN`/`ORDER BY` clause at `max_limit`, an over-bound `LIMIT` is rewritten down to
  `max_limit`. The validator operates on the query text (Neptune has no pre-parse API
  offline); it is conservative — ambiguous ⇒ reject. It is layer 1, **not** the
  guarantee (the IAM write-scope + the engine query timeout are — ADR-0004/T9), so
  false-rejects are acceptable and the known-uncatchable classes (Unicode-escape,
  backtick/dynamic identifier) are IAM/timeout-backstopped. Traces to: AC1.
- Offline subset grammar (evaluator): `MATCH (n:Entity {id: '…'}) RETURN n`;
  `MATCH (n:Entity) WHERE n.kind = '…' RETURN n`; `MATCH (a:Entity {id:'…'})-[:REL
  {kind:'…'}]->(b:Entity) RETURN b AS n` (and the `<-` in-direction); each with
  optional `ORDER BY n.id` / `LIMIT k`; sorted by node id. Anything else ⇒
  `UnsupportedOfflineQuery`. Traces to: AC4.

### Failure, edge cases & resilience
- Generation returns empty/unparseable ⇒ a failed attempt (feeds self-heal).
- Validation fails after the heal cap ⇒ refusal (`refusal_reason` set, no query run).
- Offline evaluator can't run the (valid) query ⇒ refusal labeled "runs live, not in
  the offline subset" (no false rows). Live path has no such limit.
- Neptune execution error (live) ⇒ fed to self-heal once; after the cap, sanitized
  refusal; the raw error is logged in-VPC only.
- Lambda: over-long question rejected pre-orchestration; any failure ⇒ generic
  sanitized envelope + correlation id (reuse existing scaffolding); unknown `mode` ⇒
  client error; raw Neptune error never in the envelope. Traces to: AC3, AC5, AC8.

### Quality attributes (NFRs / security)
- **Write guarantee (defense in depth):** validator rejects mutations/`CALL` (layer 1)
  + IAM `ReadDataViaQuery`-only query-Lambda scope (the backstop, ADR-0004) — the IAM
  layer holds even for the validator's known-uncatchable bypass classes. Synth-asserted
  (T9), live-proven incl. the out-of-band bypass (T11) and the via-orchestrator sanitized
  envelope (T7).
- **Read-cost guarantee (defense in depth):** validator rejects unbounded var-length
  paths (layer 1) + the Neptune `neptune_query_timeout` engine backstop (T9) kills a
  runaway read the validator might miss — the read analog of IAM-for-writes.
- **Self-heal is not a re-injection vector:** the `feedback` (validation rule / Neptune
  error) is attacker-influenced + schema-bearing, so it rides re-generation in `messages`
  as untrusted data under the same defensive directive, never in `system` (T2/T3).
- Untrusted-input at the Claude boundary (generation + synthesis): schema/question/
  feedback/rows as Converse `messages` data, defensive system directive (generation
  additionally: emit-only-a-read), bounded `maxTokens`, default-TLS client, display-only
  answer (reuse `synthesize.py`/`select.py` posture). `ruff` `S` stays enabled. Raw
  Neptune error never crosses the Function URL. Traces to: AC2, AC3, AC5, AC8.
- **Aggregate-abuse bound:** the IAM-auth named-principal invoke grant on the Function
  URL is the accepted aggregate bound for the demo (per-request bounded by AC3);
  reserved-concurrency named as future hardening. Traces to: AC9.
- PyYAML-free text2cypher import graph (Lambda bundle). Traces to: AC8.

### Dependencies & integration
No new runtime dependency (Converse via existing `bedrock-runtime`; SigV4 via
`botocore`; the validator and subset evaluator are pure Python — no parser/engine
dep). No new billable/compute resource; the query Lambda's Neptune grant is *narrowed*,
and the one config-only addition is a free Neptune cluster parameter group setting
`neptune_query_timeout` (the engine read-cost backstop). Traces to: AC9.

## Tasks

### T1: Read-only static validator (AC1)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/validate.py, packages/graphrag/tests/test_validate.py
**Tests:**
- `# STUB: AC1` reject table: each of `CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE`/`DETACH`/
  `DROP` (varied case/spacing), **any** `CALL` (read or mutating — all rejected), a
  two-statement input (`… ; CREATE …`), a query whose only mutation hides after a
  comment, a **`RETURN`-less** query, an **unbounded variable-length path**
  (`MATCH (a)-[*]->(b) … RETURN n`, plus `[*..]` / `[*2..]` with no upper bound), and a
  read query with a mutating keyword **inside a string literal** (e.g.
  `… WHERE n.title CONTAINS 'how to DELETE a KEP' RETURN n LIMIT 5`) — all rejected
  with the rule named (the string-literal case is the conservative false-reject,
  pinned as expected behavior).
- Accept table: bounded single-`RETURN` read queries pass; a **bounded** var-length
  path (`[*1..2]`) passes; a missing `LIMIT` is appended after the `RETURN`/`ORDER BY`
  clause at `max_limit`; an over-bound `LIMIT` is capped; the `normalized_query`
  carries the enforced form.
**Approach:**
- `ValidationResult` dataclass; `validate_read_only(cypher, *, max_limit)` with
  `_has_mutation` (word-boundary regex over the **whole** query — string literals are
  not exempted; the set includes all mutating clauses **and `CALL`**),
  `_is_single_statement`, `_has_single_return`, `_has_unbounded_varlen`
  (`[*]`/`[*..]`/`[*N..]` with no upper bound), `_enforce_limit`.
- Conservative: ambiguous ⇒ reject with a rule name. The validator is layer 1, **not**
  the guarantee (the IAM write-scope + the engine query timeout are — ADR-0004/T9), so a
  false-reject is acceptable; the module docstring names the known-uncatchable classes
  (Unicode-escape, backtick/dynamic identifier) as IAM-backstopped.
**Done when:** `test_validate.py` green; `ruff`/`mypy` clean.

### T2: Generator seam — Bedrock + offline rule generator (AC2)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/generate.py, packages/graphrag/tests/test_generate.py
**Tests:**
- `# STUB: AC2`: `BedrockText2CypherGenerator` against a **mock** Converse client
  returns the parsed query (code-fence stripped); the request has the defensive
  system directive, schema+question in `messages` (not `system`), bounded
  `maxTokens`, default-TLS client (no `verify=False`); an empty/garbled response ⇒
  empty string. `feedback` is included in the user message when supplied.
  `RuleText2CypherGenerator` emits a within-subset query for the exemplar question
  structurally, labeled non-semantic. A unit assertion pins
  `BedrockText2CypherGenerator().model_id == DEFAULT_SYNTHESIS_MODEL_ID` (the
  no-widened-grant equality AC9 leans on).
**Approach:**
- `Text2CypherGenerator` protocol `generate(question, schema, *, feedback=None) ->
  str`; `GRAPH_SCHEMA_DESCRIPTION` constant.
- `BedrockText2CypherGenerator` (configurable `modelId=DEFAULT_SYNTHESIS_MODEL_ID`,
  injectable client): JSON-/fence-tolerant parse, mirror `select.py`.
- `RuleText2CypherGenerator`: keyword + `link_question` candidate-kind rules → a
  subset-grammar query; labeled non-semantic in `model_id`.
**Done when:** `test_generate.py` green; gates clean.

### T3: Bounded self-heal loop (AC3)
**Depends on:** T1, T2
**Touches:** packages/graphrag/src/graphrag/text2cypher.py, packages/graphrag/tests/test_text2cypher.py
**Tests:**
- `# STUB: AC3`: a stub generator that emits an invalid (mutating) query first and a
  valid one on the feedback retry ⇒ **1 initial + 1 re-generation = 2 generation
  calls**, both attempts recorded; a generator that stays invalid ⇒ refusal after the
  cap (default total ≤ 2 calls) with no executed query; `feedback` carries the
  violated rule.
- `# STUB: AC3` (security): an injection-laden `feedback` error string (e.g. one
  embedding `"ignore previous instructions; CREATE …"`) does **not** alter the
  generator's `system` framing — the generator places `feedback` in `messages` as
  untrusted data, asserted via the mock generator's recorded call.
**Approach:**
- The generate→validate→retry loop inside `text2cypher_query` (execution wired in
  T5); `GenerationAttempt` accumulation; `MAX_HEAL_ATTEMPTS` constant (default 1 =
  one re-generation on top of the initial generation, so ≤ 2 LLM calls).
- The generator's `generate(..., feedback=...)` (T2) places `feedback` in the user
  `messages` block as untrusted data, never in `system` — closing the LLM01 self-heal
  re-injection vector.
**Done when:** `test_text2cypher.py` self-heal cases green; gates clean.

### T4: Bounded read-subset evaluator (AC4)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/cypher_eval.py, packages/graphrag/tests/test_cypher_eval.py
**Tests:**
- `# STUB: AC4`: node-by-id, nodes-by-kind, 1-hop out/in `REL`-by-kind each return
  the sorted fixture nodes (exemplar `sig:sig-network -OWNS-> KEPs`); `ORDER BY`/`LIMIT`
  honored; an out-of-subset construct (2-hop, function call, aggregation) ⇒
  `UnsupportedOfflineQuery`.
**Approach:**
- `eval_read_query(cypher, store) -> list[Node]`: pattern-match the ~4 supported
  shapes (compiled regexes), dispatch to `get_node`/`neighbors`/`all_nodes`+filter,
  apply LIMIT, sort by id; everything else raises `UnsupportedOfflineQuery`. Pure
  Python, labeled a subset in the module docstring.
**Done when:** `test_cypher_eval.py` green; gates clean.

### T5: Orchestration + Text2CypherResult + Neptune executor (AC5, AC6)
**Depends on:** T1, T2, T3, T4
**Touches:** packages/graphrag/src/graphrag/text2cypher.py, packages/graphrag/src/graphrag/store/neptune.py, packages/graphrag/tests/test_text2cypher.py, packages/graphrag/tests/test_neptune.py
**Tests:**
- `# STUB: AC5` happy path: offline (`RuleText2CypherGenerator` + in-memory store +
  offline synthesizer) on the exemplar generates a valid query, executes via the
  subset evaluator, returns KEP rows, and `.render()` emits question → schema →
  generated query (+ verdict) → executed query → rows → answer in order; a refusal
  (post-heal-cap, or `UnsupportedOfflineQuery`) returns no executed query.
- `# STUB: AC6`: `NeptuneGraphStore.run_read_query` decodes mocked `RETURN n` rows;
  a row missing `n` ⇒ diagnosable `RuntimeError`.
**Approach:**
- Complete `text2cypher_query(question, *, graph_store, generator, synthesizer,
  schema=GRAPH_SCHEMA_DESCRIPTION, max_limit, max_heal_attempts)`: dispatch execution
  (Neptune `run_read_query` vs `eval_read_query`), synthesize over rows, build the
  result; `execute` catches `UnsupportedOfflineQuery`/Neptune error into the
  self-heal/refusal path.
- Add `NeptuneGraphStore.run_read_query` (reuse `_node_from_result`, the alias-`n`
  diagnostic from `run_template_query`).
- `Text2CypherResult.render()` audit ordering; keep `text2cypher.py` PyYAML-free.
**Done when:** orchestration + executor tests green; gates clean.

### T6: CLI verb `text2cypher-query` (AC7)
**Depends on:** T5
**Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_cli.py
**Tests:**
- `# STUB: AC7`: offline run prints the audit trace + non-semantic label;
  `--function-url` builds a SigV4 POST whose body carries `mode: "text2cypher"`
  (assert via the shared `_function_url_query`); live render path; a non-2xx raises
  with the body.
**Approach:**
- Add `_cmd_text2cypher_query` + parser (`--q`, corpus args, `--neptune-endpoint`,
  `--bedrock`, `--function-url`, `--region`); offline default (in-memory store +
  `RuleText2CypherGenerator` + offline synthesizer), `--bedrock` ⇒
  `BedrockText2CypherGenerator` + `BedrockClaudeSynthesizer`.
- Reuse/extend the shared `_function_url_query` `mode` plumbing the governed verb
  added (no new client).
**Done when:** `test_cli.py` green; gates clean.

### T7: Query Lambda text2cypher-mode dispatch (AC8)
**Depends on:** T5
**Touches:** packages/graphrag/src/graphrag/query_lambda.py, packages/graphrag/tests/test_query_lambda.py
**Tests:**
- `# STUB: AC8`: `mode="text2cypher"` with mocked generator/store/synthesizer returns
  the audit envelope; unknown `mode` ⇒ client-error envelope; over-long question
  still rejected; the raw Neptune error is **not** in the envelope; **a mock store that
  raises an `AccessDenied`-shaped error on execution (the validator-missed-write
  backstop firing via the real path) yields the sanitized envelope + correlation id with
  no raw error text** (concern-3 closure); the PyYAML-free `sys.modules` guard now also
  imports `text2cypher`/`generate`/`validate`/`cypher_eval`.
**Approach:**
- Add the `mode == "text2cypher"` branch (before the `unknown mode` guard): build
  live Neptune store + `BedrockText2CypherGenerator` (same model) +
  `BedrockClaudeSynthesizer`, run `text2cypher_query`, `_serialize_text2cypher(result)`
  (no raw error, no internal detail); log refusal/ok with correlation id (no question
  text).
**Done when:** `test_query_lambda.py` green; gates clean.

### T8: PyYAML-free import-graph guard extension (AC8)
**Depends on:** T5
**Touches:** packages/graphrag/tests/test_query_lambda.py
**Tests:**
- `# STUB: AC8`: the existing `sys.modules`-blocks-`yaml` guard imports
  `graphrag.text2cypher`/`generate`/`validate`/`cypher_eval` with `yaml` blocked and
  asserts no `ImportError`.
**Approach:**
- Extend the existing guard test list (mirrors the governed-modules extension).
**Done when:** the guard test green; gates clean.

### T9: IaC — narrow the query-Lambda Neptune grant to read-only (AC9)
**Depends on:** none
**Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py
**Tests:**
- `# STUB: AC9`: synth assertion — **the query-Lambda execution role's** Neptune
  statement grants `ReadDataViaQuery` + `connect` and **not**
  `WriteDataViaQuery`/`DeleteDataViaQuery`; **the ingestion task role *and* the
  smoke-probe role still grant the full read-write set** (both legitimately write —
  `graphrag_stack.py:303,366`); **no other role's Neptune grant is widened**; the
  query Lambda's Bedrock grant still scopes the synthesis model with no wildcard
  `Resource`; the Budgets value is the literal `150`; no new Lambda/Function-URL
  resource is added. (Assert per-role, e.g. by matching each role's policy statements —
  not a single cluster-wide check, since two peer roles retain write by design.)
- Also assert the **engine read-cost backstop**: a `neptune.CfnDBClusterParameterGroup`
  sets `neptune_query_timeout`, associated with the cluster; synth asserts the parameter
  is present (the analog of IAM-for-writes — kills a runaway read the validator's `[*]`
  guard might miss). A parameter group is free + teardown-first (no billable/compute
  resource), so the "cost held / Budgets 150" assertion is unchanged.
**Approach:**
- Add `_neptune_read_only_access(cluster)` (`connect` + `ReadDataViaQuery`, same
  scoped resource ARN); point **only the query Lambda** role at it (replace the shared
  `_neptune_data_access` call at the query-Lambda site, `:549`); leave the **ingestion
  task** (`:303`) and the **smoke probe** (`:366`) on the full `_neptune_data_access`.
- Add a `CfnDBClusterParameterGroup` with `neptune_query_timeout` (ms) and set
  `db_cluster_parameter_group_name` on the cluster (the engine read-cost backstop).
  This is the ADR-0004 Confirmation fitness test — it fails if a later edit re-broadens
  the query-Lambda grant or drops the timeout.
**Done when:** CDK-env-gated synth test green; gates clean.

### T10: Side-by-side showcase set + completed contrast doc (AC11)
**Depends on:** T1, T5, T6
**Touches:** packages/graphrag/src/graphrag/showcase/queries.yaml, packages/graphrag/src/graphrag/showcase/__init__.py, packages/graphrag/tests/test_showcase.py, docs/guides/explanation/governed-vs-risky-graph-queries.md
**Tests:**
- `# STUB: AC11`: `load_text2cypher_showcase()` parses ≥3 entries (≥1 open-ended,
  ≥1 shared with a governed template); each gold entity resolves in the fixture corpus.
**Approach:**
- Add `text2cypher_queries` to `queries.yaml` (id, query, gold, highlight,
  `shared_with_template?`) + `Text2CypherShowcaseQuery` + `load_text2cypher_showcase()`.
- Complete `governed-vs-risky-graph-queries.md`: replace the "a separate slice"
  framing with the **running** text2cypher path (exact `text2cypher-query` CLI), a
  same-question head-to-head, and the guard explanation (validator + IAM read-only +
  self-heal); keep the "when to choose which" table, now with both paths real.
- **Correct the doc's now-stale read-replica claims.** The shipped doc currently says
  text2cypher "*relies on*" / "*must* lean on" a read-only **reader/read-replica
  endpoint** (≈ lines 36-38, 43-44, 56-60, 72-74) — a guarantee ADR-0004 supersedes.
  Rewrite those to the actual guard (read-only validator + **IAM read-only data-action
  scoping** + bounded self-heal), and reconcile the one forward-reference clause in the
  shipped sibling `opencypher-templates/spec.md` (the line naming the read-replica as
  *this* path's guard) to point at ADR-0004. This correction is load-bearing, not
  cosmetic: leaving it makes a shipped doc contradict the ADR.
**Done when:** `test_showcase.py` green; doc renders the runnable contrast **with no
remaining read-replica guard claim**; sibling spec reconciled; gates clean.

### T11: Live deploy + text2cypher smoke, backstop proven (AC10)
**Depends on:** T6, T7, T9
**Tests:**
- Manual/live: deploy, dual-write the corpus, SigV4 `mode: text2cypher` call →
  Bedrock generates a query → validated read-only → executed live on Neptune (read-only
  role) → audit trace + Claude answer; a forced mutating query is rejected by the
  validator; **the IAM backstop is proven out-of-band** — using the query-Lambda
  execution role's credentials, issue a mutating openCypher statement **directly** to
  the Neptune data plane (a `boto3`/SigV4 `POST /openCypher` that bypasses
  `text2cypher_query`) and assert an IAM `AccessDenied`. Then `scripts/destroy.sh`.
**Approach:**
- Live AWS is available — run the smoke end-to-end and record it in
  `deployment-and-verification.md` (mirror the opencypher-templates AC9 record),
  including the IAM-denial transcript for the out-of-band bypass case (no test-only
  bypass hook is added to the production path); then tear down.
**Done when:** live smoke recorded incl. the backstop proof; teardown leaves no
billable resource.

### T12: Develop-offline architecture doc + drift-closure metadata (AC12 + CONVENTIONS § 4)
**Depends on:** T1, T2, T4, T5, T9, T10
*(AC12 is the develop-offline doc; the rest of this task realizes the drift-closure
metadata invariants — Status flip, AC checkbox ticks, architecture-docs update,
ADR-0004 status. Finalization, not scope creep.)*
**Touches:** docs/architecture/develop-and-test-offline.md, docs/architecture/overview.md, docs/architecture/security.md, packages/graphrag/AGENTS.md, docs/specs/README.md, docs/specs/text2opencypher-guarded/spec.md, docs/adr/0004-text2cypher-read-only-guard.md, docs/adr/README.md
**Tests:**
- Goal-based: the develop-offline doc exists, explains the offline default + the
  subset grammar + the live path, and records the offline-execution decision (no
  local emulator; bounded subset + live oracle) linking ADR-0004; repo spec-status
  lint clean; AC checkboxes reflect reality.
**Approach:**
- Write `docs/architecture/develop-and-test-offline.md` (AC12): how to run offline
  (in-memory + RuleText2CypherGenerator + offline synth), the supported subset
  grammar, how to run `--bedrock`/`--function-url`/live, and the recorded offline
  decision with the local-engine trade-off table → ADR-0004.
- Update `architecture/overview.md` (text2cypher path) + `security.md` (the guard:
  read-only validator + IAM read-only scope + sanitized boundary — the contrast with
  the governed path's construction-time guarantee).
- Update `graphrag` AGENTS.md module map (validate/generate/cypher_eval/text2cypher)
  + invariants (read-only validator; offline subset labeled; text2cypher PyYAML-free).
- Add the spec to `docs/specs/README.md`; tick met ACs; flip Status; flip **ADR-0004
  to Accepted** (decision realized) and update `docs/adr/README.md`.
**Done when:** docs consistent; lints clean; ADR-0004 Accepted.

## Rollout

- **Delivery:** additive. The CLI gains a verb; the Function URL gains the
  `mode: "text2cypher"` value (the `mode` field already exists, default `hybrid`
  unchanged). Reversible — no data migration, no published event. Rollback is
  reverting the PR.
- **Infrastructure:** no new resource. The text2cypher modules ride the existing
  query Lambda's `Code.from_asset` bundle. The **one infra change is a narrowing**:
  the query Lambda's Neptune grant drops to read-only (`ReadDataViaQuery` + `connect`);
  the ingestion task keeps read-write. Budgets unchanged at `150` (AC9). This is the
  ADR-0004 backstop and is the only deploy-time behavior change — it *tightens* the
  query-Lambda role, so hybrid/governed (both read-only) are unaffected.
- **External-system integration:** Bedrock Claude (generation + synthesis) and
  Neptune openCypher — both already wired and granted for the hybrid/governed paths
  (generation reuses the synthesis Converse model, so no widened Bedrock grant).
- **Deployment sequencing:** single PR. The narrowed IAM grant deploys with the code.
  The live smoke (AC10/T11) runs against a deploy of this branch (AWS available).

## Risks

- **Read-only validator incompleteness.** A mutating construct the validator doesn't
  recognize would pass layer 1. Mitigation: the **IAM read-only scope** (ADR-0004) is
  the backstop — a write is denied at the engine regardless; the live smoke proves
  the bypass case (T11). The validator is conservative (ambiguous ⇒ reject).
- **Offline subset evaluator drift / over-claiming.** The evaluator could mislead if
  it appears to run arbitrary cypher. Mitigation: it raises `UnsupportedOfflineQuery`
  for anything outside the ~4 shapes, is labeled a subset everywhere, and live Neptune
  is the fidelity oracle (documented in the develop-offline doc, T12).
- **A local Cypher engine was tempting for offline fidelity.** Investigated and
  rejected: no official Neptune emulator; cypher-for-gremlin (2019) and Kùzu (archived
  2025) are dead; Neo4j/Memgraph are low Neptune-dialect fidelity *and* heavyweight
  (Docker/JVM), breaking the pure-Python/PyYAML-free posture. Recorded in ADR-0004's
  alternatives and the develop-offline doc.
- **PyYAML creeps into the text2cypher import graph** (breaks the Lambda bundle).
  Mitigation: the extended `sys.modules` guard test (T8).
- **Self-heal cost.** Unbounded retry would be a cost/DoS surface. Mitigation:
  `MAX_HEAL_ATTEMPTS` default 1; raising it is an *Ask first* change.

## Changelog

- 2026-06-25: initial plan. Generator seam (Bedrock + offline rule), read-only static
  validator, bounded self-heal, pure-Python bounded read-subset evaluator (live
  Neptune is the fidelity oracle after rejecting low-fidelity/heavy local engines),
  orchestrator + audit trace, CLI verb, additive Function-URL `mode: text2cypher`
  dispatch, the ADR-0004 IAM read-only narrowing (the load-bearing backstop), runnable
  governed-vs-risky contrast, develop-offline architecture doc. AC10 run live.
