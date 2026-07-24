# ADR-0004: Read-only guard for LLM-authored openCypher: IAM data-action scoping over a read-replica endpoint

- **Status:** Superseded by [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) <!-- openCypher anchoring superseded; read-only control carried forward under SPARQL grammar -->
- **Date:** 2026-06-25
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** [RFC-0001 feasibility note §2](../rfc/0001-notes/aws-feasibility.md) (named the reader endpoint as the text2cypher guardrail); [ADR-0002](0002-ephemeral-vpc-store-topology.md) (the single-node, teardown-first cost posture this decision must not break); [ADR-0001](0001-hybrid-orchestration-seed-and-expand.md) (the synthesizer + query-Lambda seam reused); the [`text2opencypher-guarded`](../specs/text2opencypher-guarded/spec.md) slice this guard ships under; the [`opencypher-templates`](../specs/opencypher-templates/spec.md) governed half it contrasts with

## Context

The `text2opencypher-guarded` slice ships the **Text2Cypher** pattern: Bedrock
Claude **writes** the openCypher query from the question and a schema description,
and that model-authored string is executed against Neptune. This crosses a
security boundary the governed `opencypher-templates` slice does not — there, the
executable surface is a fixed, reviewed library and the LLM only *selects* an id;
here the executable surface is **whatever the model emits**. The classic injection
defense (bind every value through the parameter map, never interpolate) does not
apply, because the model authors the query *structure*, not just values. The
threat is a generated — or prompt-injected — **mutation** (`CREATE`/`MERGE`/`SET`/
`DELETE`/`REMOVE` / a mutating `CALL`) or a runaway unbounded traversal reaching
the live graph.

Three constraints shape the guard:

- **[RFC-0001 §2](../rfc/0001-notes/aws-feasibility.md) named the reader endpoint.**
  Neptune's reader/read-replica endpoint is *read-only-enforced* — write mutations
  are blocked at the engine regardless of session config — and the feasibility note
  flagged it as the text2cypher guardrail. That is the starting hypothesis this ADR
  tests against the deployed topology.
- **The deployed cluster is a single Neptune Serverless instance.** Per
  [ADR-0002](0002-ephemeral-vpc-store-topology.md) the stack runs **one** serverless
  instance (single-node is a deliberate cost/teardown choice, not HA). A reader
  endpoint that enforces read-only requires a **read-replica instance**; with no
  replica, Neptune's reader endpoint resolves to the writer and offers no read-only
  guarantee. Provisioning a replica adds a second standing billable instance — a
  ~2× idle-cost increase on the dominant cost line — which breaks the teardown-first,
  bounded-idle-cost posture (charter principle 4, ADR-0002).
- **The query Lambda's Neptune grant is currently read-write.** The shared
  `_neptune_data_access` IAM statement grants `neptune-db:ReadDataViaQuery`,
  `WriteDataViaQuery`, **and** `DeleteDataViaQuery`. **Three roles hold it today:** the
  ingestion Fargate task (`graphrag_stack.py:303`), the on-demand smoke probe Lambda
  (`:366`, which legitimately upserts then reads back a probe node/edge —
  `smoke_lambda.py`), and the query Lambda (`:549`). The ingestion task and the smoke
  probe **legitimately write**; the **query Lambda is the over-grant** — hybrid +
  governed are both read-only, so it carries Write/Delete it never uses. Routing
  model-authored queries through the query-Lambda role today would let a generated
  mutation that escaped an app-layer check actually write.

A teaching template must make the guarantee **legible** and **not depend on the
completeness of our own openCypher parser** — a watcher has to be able to say *why*
the model can't damage the graph, and the answer can't be "because our regex caught
every mutating clause."

## Decision

> We will guard LLM-authored openCypher with **layered defense whose primary,
> engine-independent backstop is IAM read-only data-action scoping on the
> query-Lambda execution role** — not a Neptune read-replica endpoint. The
> reader-endpoint approach is documented as the named managed alternative for a
> multi-instance cluster, explicitly **not adopted** here because the single-node
> Serverless topology (ADR-0002) cannot provide it without a standing replica that
> breaks the cost posture.

Concretely, the guard is four layers, outermost first:

1. **App-layer read-only static validation (pre-execution).** Before any query
   reaches Neptune, the generated openCypher is validated: it must contain **no**
   mutating clause (`CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE`/`DETACH`/`DROP`,
   word-boundary, case-insensitive), **no `CALL` at all** (the demo needs no
   procedure; rejecting every `CALL` removes the read-vs-write-procedure ambiguity and
   makes the two-action grant in layer 3 provably sufficient), must be a **single**
   `RETURN`-bearing statement, must contain **no unbounded variable-length path**
   (`[*]`/`[*..]`/`[*N..]` — the read-cost guard), and must carry a bounded `LIMIT`
   (injected/capped if absent). A failure refuses the query — it is never sent — and
   feeds the **bounded self-heal** loop. This is **layer 1, not the guarantee**: classes
   it cannot reliably catch (Unicode/`\u`-escaped clause text, backtick-quoted or
   dynamically-constructed identifiers) are stopped by layers 3 (writes) and the engine
   query timeout below (runaway reads), not by the validator.
2. **Bounded self-heal.** On a validation failure or a Neptune execution error, the
   error is fed back to Claude for **at most N (default 1)** re-generation attempts,
   each re-validated. After the cap, the path returns a narratable refusal — it
   never executes an unvalidated or repeatedly-failing query. The fed-back error is
   partly attacker-influenced and schema-bearing, so it rides re-generation **as
   untrusted data in `messages`, never in `system`** — the self-heal is not a
   prompt-injection amplifier.
3. **IAM read-only data-action scoping (the *write* backstop, the real guarantee).** The
   **query-Lambda role's** Neptune grant is split out to **`neptune-db:ReadDataViaQuery`
   + `neptune-db:connect` only** — no `WriteDataViaQuery`, no `DeleteDataViaQuery`. The
   ingestion Fargate task and the smoke probe Lambda keep the full read-write statement
   (both legitimately write). Even if the app-layer validator is bypassed (a clause it
   doesn't recognize, a novel injection), AWS IAM rejects the write *before the engine
   runs it*. This is the layer that makes the read-only guarantee — **for the
   text2cypher execution path, which runs only on the query-Lambda role** — independent
   of our parser's completeness.
4. **Neptune engine query timeout (the *read-cost* backstop).** IAM scoping blocks
   writes but not an expensive read (a Cartesian product, a deep traversal) — `LIMIT`
   bounds *returned* rows, not rows *expanded*. So a `neptune_query_timeout` is set
   explicitly on the cluster (parameter group) as the engine-level analog of
   IAM-for-writes: a runaway model-authored read is killed by the engine even if the
   validator's `[*]` guard is bypassed. The aggregate-abuse bound is the **IAM-auth
   named-principal invoke grant** on the Function URL (only an authorized principal can
   call it); per-request cost is bounded by the self-heal cap, and reserved-concurrency
   is named as future hardening (this is a teaching demo, not a multi-tenant service).
5. **Untrusted-data + sanitized-error posture at the Claude and Function-URL
   boundaries.** Question, schema, and self-heal feedback ride Converse `messages` as
   data (never the `system` block) with a defensive directive (generation additionally:
   emit only a read query, regardless of embedded instructions) and bounded `maxTokens`
   (OWASP LLM01/LLM05/LLM08); the caller receives a generic sanitized error envelope —
   including when the write backstop fires as an IAM `AccessDenied` on the real path —
   the raw Neptune error (which can leak schema) is logged in-VPC and fed only to the
   internal self-heal, never returned.

This applies to the **text2cypher path only**. The split of `_neptune_data_access`
is a **narrowing** of the query-Lambda grant (it never needed write) — it adds no
resource, widens nothing, and holds Budgets unchanged at `150` (ADR-0002).

## Decision drivers

- **Guarantee must not depend on our parser.** A read-only claim that rests only on
  an app-layer lint is one unknown-clause away from being false; the backstop must
  be enforced below our code.
- **Cost / teardown posture (ADR-0002).** The guard must add no standing billable
  resource — a second Serverless instance for a replica is disqualifying.
- **Narratability (charter principle 1).** A watcher must be able to state, in one
  sentence, why the model can't write — "the role physically cannot."
- **Least privilege.** The query Lambda holding unused Write/Delete is a latent
  over-grant; the guard is also the occasion to fix it.

## Consequences

**Positive:**
- The read-only guarantee is enforced at the AWS auth layer, **independent of the
  app-layer validator's completeness** — defense in depth, not a single point.
- The query-Lambda role becomes **least-privilege** (read-only data actions),
  fixing a latent over-grant that predates this slice and also hardens the
  hybrid/governed paths.
- **No new billable resource, no replica, cost held** — the teardown-first posture
  (ADR-0002, charter principle 4) is preserved; Budgets unchanged at `150`.
- The guard is **legible**: the trace shows the generated query, the validation
  verdict, any self-heal attempts, and the executed query; the IAM scoping is a
  synth-asserted fact.

**Negative:**
- The IAM split makes the read-only guarantee a property of the **deployed IaC**,
  not of the query text — a future edit that re-broadened the query-Lambda grant
  would silently reopen the hole. Mitigated by a synth fitness test (Confirmation).
- We **diverge from RFC-0001 §2's named mechanism** (reader endpoint). The note's
  *intent* (engine-/endpoint-enforced read-only) is honored by a different
  mechanism; the divergence is documented here and in the slice doc rather than
  papered over.
- The app-layer validator and self-heal are **belt-and-suspenders**, not the
  guarantee — they improve UX (clean refusals, fewer dead-ends) and defense depth,
  but the load-bearing control is the IAM scope. We must resist letting the validator
  *look* authoritative.

**Neutral / to revisit:**
- If the template ever moves to a **multi-instance** cluster (real HA), the reader
  endpoint becomes available and could be **added** as a fifth layer (engine-level
  read-only on top of IAM) — a new ADR, not an edit to this one.
- Neptune also exposes fine-grained query-level read-only session controls in some
  access modes; not relied on here (IAM action scoping is coarser but unambiguous
  and synth-verifiable).

## Confirmation

- **Synth fitness test (CDK `aws_cdk.assertions.Template`).** Asserts **the
  query-Lambda execution role's** Neptune statement grants `ReadDataViaQuery` +
  `connect` and **does not** grant `WriteDataViaQuery` or `DeleteDataViaQuery`; that the
  **ingestion task role and the smoke-probe role still grant the full read-write set**
  (they legitimately write); that **no other role's Neptune grant was widened**; that
  Budgets is the literal `150`; that the Neptune cluster parameter group sets
  `neptune_query_timeout` (the read-cost backstop); and that no new billable/compute
  resource is added for the text2cypher path. This is the load-bearing check — it fails
  if a later edit re-broadens the query-Lambda grant or drops the timeout. The grant
  assertion is scoped to the query-Lambda role, not a cluster-wide property — two peer
  roles retain write by design.
- **Unit tests** on the read-only validator (every mutating clause / mutating `CALL`
  / multi-statement rejected; bounded `LIMIT` enforced) and the bounded self-heal
  (cap respected, refusal after N).
- **Live smoke (slice AC).** A model-authored read query executes and returns real
  rows; a (test-forced) mutating query is rejected by the validator **and**, if the
  validator is bypassed in the test, by IAM at the engine — proving the backstop.

## Alternatives considered

- **Neptune read-replica / reader endpoint (RFC-0001 §2's named mechanism).**
  *Rejected against the cost-posture driver:* enforcing read-only at the reader
  endpoint requires a standing read-replica instance; on the single-node Serverless
  topology (ADR-0002) that doubles the dominant idle-cost line and breaks
  teardown-first. Documented as the managed alternative for a multi-instance cluster.
- **App-layer validation only (no IAM split).** *Rejected against the
  guarantee-must-not-depend-on-our-parser driver:* the read-only claim would rest
  entirely on the completeness of our clause/procedure denylist — one unknown
  mutating construct (or an openCypher feature we didn't model) makes it false, with
  no backstop. Kept as **layer 1**, not the guarantee.
- **A dedicated second read-only Lambda + role for text2cypher.** *Rejected against
  cost/simplicity:* a second function and role for a path that reuses the same stores
  and orchestration adds deploy surface and a second cold-start profile for no
  security gain over narrowing the **shared** query-Lambda role (which never needed
  write anyway). The narrowing benefits hybrid/governed too.
- **A separate read-only IAM statement layered on the existing read-write grant.**
  *Rejected:* IAM is allow-union — adding a read-only statement alongside the
  existing read-write one still leaves Write/Delete granted. The grant must be
  **replaced** with read-only, not supplemented.

## References

- [RFC-0001 feasibility note §2 — Neptune openCypher](../rfc/0001-notes/aws-feasibility.md)
- [Neptune IAM data-access actions](https://docs.aws.amazon.com/neptune/latest/userguide/iam-dp-actions.html)
- [Neptune reader endpoint / read replicas](https://docs.aws.amazon.com/neptune/latest/userguide/feature-overview-endpoints.html)
- [OWASP Top 10 for LLM Applications 2025 — LLM01 Prompt Injection, LLM08](https://owasp.org/www-project-top-10-for-large-language-model-applications/)

## Supersession record

**Superseded by:** [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (date: 2026-07-23)

**What was superseded:**
The openCypher read-only guard used IAM data-action scoping (`ReadDataViaQuery` + `connect` only on the query-Lambda role) as the primary backstop against LLM-authored mutations, backed by an app-layer openCypher mutation denylist (`CREATE`/`MERGE`/`SET`/`DELETE`/`REMOVE`/`DETACH`/`DROP`), a bounded self-heal loop, and a Neptune engine query timeout. The mechanism was designed specifically for the openCypher/LPG engine and its mutation keyword grammar.

**What replaces it:**
ADR-0011 re-ratifies the same layered defense — IAM `ReadDataViaQuery` + `connect` scoping on the `mcp_lambda_role` as the load-bearing backstop — re-authored for SPARQL grammar. The app-layer denylist is updated from openCypher mutation keywords to SPARQL Update keywords (`INSERT`, `DELETE`, `DROP`, `CLEAR`, `LOAD`, `CREATE`). The self-heal loop and Neptune query timeout carry forward unchanged.

**What carries forward:**
The IAM read-only backstop principle, the bounded self-heal loop, the Neptune engine query timeout, and the untrusted-data posture at the Bedrock boundary all carry forward verbatim. Only the grammar of the app-layer denylist changes (openCypher mutation keywords replaced by SPARQL Update keywords).
