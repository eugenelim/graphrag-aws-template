> **Partially superseded — ini-002 in progress.**
> The trust boundaries, IAM roles, and VPC topology below reflect the openCypher/CDK-era
> stack. The current architecture is defined in
> [`biz-ops-knowledge-graph/design.md`](biz-ops-knowledge-graph/design.md) (IAM roles table,
> VPC topology diagram, MCP endpoint trust boundary). This doc will be rewritten for
> SPARQL/MCP/OTEL boundaries once ini-002 implementation lands.

# Security posture

> The consolidated security view for the demo stack. Living doc; updated as
> slices land. Slice 1 (`graph-ingestion-resolution`) stands up the first VPC,
> IAM roles, and data store, so this doc starts here. Decisions live in
> [ADR-0002](../adr/0002-ephemeral-vpc-store-topology.md) /
> [ADR-0003](../adr/0003-iac-tool-aws-cdk-python.md); the design doc's Risks
> section is the threat narrative.

## Trust boundaries (slice 1)

| Boundary | Control |
| --- | --- |
| Untrusted corpus files → parser | `yaml.safe_load` only (no `yaml.load`); a `!!python/object` tag parses inert (CWE-502). Enforced by ruff `S506`. Malformed front matter is skipped, not executed. |
| Ingestion compute → Neptune | In-VPC only; SigV4 + IAM-auth; **parameterized** openCypher (no value/relationship-type interpolation); `https://` with TLS verification on. |
| Compute → AWS APIs | All egress via VPC endpoints (`s3`, `ecr.api`, `ecr.dkr`, `logs`, `sts`) — **no NAT**, no public egress. |
| Credentials | Resolved via the default botocore provider chain (the Fargate task role) — never read from env/argv at a call site. |
| Internet → data stores | Neptune VPC-resident (private subnet group, no public endpoint); S3 bucket public-access-blocked, encrypted, TLS-only. |

## Trust boundaries (slice 2 — vector half)

| Boundary | Control |
| --- | --- |
| Compute → OpenSearch (k-NN) | In-VPC only; SigV4 + IAM-auth for service `es`; **body-parameterized** index/k-NN/delete (no value interpolated into path/query string); `https://` with TLS verification on. The domain's own **access policy** restricts to the task + probe role ARNs (resource-side IAM, not `AllPrincipals`). |
| Compute → Bedrock (Titan v2) | Via the `bedrock-runtime` VPC endpoint; default botocore-chain TLS client (no `verify=False`, no plaintext-HTTP `endpoint_url`). |
| Internet → OpenSearch | Domain is VPC-resident in the private isolated subnets, **not public**; encryption at rest + node-to-node encryption + enforce-HTTPS. |
| Retrieved corpus text → output | **Display-only** in this slice — chunk text is embedded and rendered, never fed to an LLM as instructions and no tool execution, so OWASP LLM01 is out of reach. It becomes control-bearing the moment slice 3 routes it into Claude synthesis (isolate-and-no-instruction there). |

## Trust boundaries (slice 3 — hybrid orchestration)

| Boundary | Control |
| --- | --- |
| Internet → query path | The in-VPC query Lambda's **only** public ingress is its **Function URL with `AuthType=AWS_IAM`** (never `NONE`) — every request must be SigV4-signed. The invoke permission is additionally **scoped to a named principal** (the `InvokerRoleArn` CfnParameter, the deploying/CLI role), never `Principal: *`/account-root: IAM auth gates *that a request is signed*, the scoped grant gates *who may invoke*. The Lambda sits in private isolated subnets and keeps the no-egress (VPC-endpoint-only, no NAT) guarantee. |
| Untrusted retrieved content → Claude (LLM01/LLM08) | The question and retrieved corpus Markdown are passed to Bedrock Claude (Converse) as **data, not instructions** — placed in the `messages` content, never concatenated into the `system` block; the `system` block carries an explicit **defensive directive** that any instructions embedded in the data must not be followed. The synthesized answer is **display-only** (no caller evaluates it, shells out on it, or feeds it into a tool call). A bounded `inferenceConfig.maxTokens` caps a runaway generation. |
| Compute → Bedrock (Claude synthesis) | Via the `bedrock-runtime` VPC endpoint; default botocore-chain TLS client (no `verify=False`, no plaintext `endpoint_url`); the synthesis model id rides the request body, never string-interpolated. |
| Public Function URL → error responses | On any failure the handler returns a **sanitized envelope** (a correlation id + a generic message; **no internal endpoint / ARN / stack text**) and logs the real detail to CloudWatch only — the loud-raise-with-body posture stays in-VPC (CLI/adapter side), never crossing the public ingress (information-disclosure boundary). An over-long question (> ~8 KB) is rejected before any orchestration runs. |

## Trust boundaries (slice 4 — permission-filtered retrieval)

> **These visibility labels are a *synthetic teaching stand-in* for access control — not
> real authorization** (charter principle 5; an explicit Non-goal below). They demonstrate
> *where* authorization rides a GraphRAG retrieval path; the controls below are about the
> *mechanism* being correct as a teaching artifact, not production-grade authz.

| Boundary | Control |
| --- | --- |
| Persona (untrusted input) → query filter | The `persona` is a query-time input. It resolves through `visibility.resolve_clearance` **fail-closed**: an unknown persona raises (CLI: non-zero exit; Lambda: a sanitized `unknown persona` envelope), never a silent fall-through to unrestricted. The resolved clearance's `allowed` tier set rides the openCypher `parameters` map (`$allowed`) and an OpenSearch `terms` filter — **never string-interpolated** (the `ruff S` ruleset stays on; injection-safe by construction). |
| Forbidden entity → traversal (the leak guard) | The graph filter is applied **DURING traversal, on edges** (`GraphStore.neighbors`/`neighbors_batch`: in-memory and a parameterized Neptune `WHERE r.visibility IN $allowed AND b.visibility IN $allowed`), **not** as a post-filter on the final node set. A forbidden node therefore never enters the frontier, never appears in the hop trace, and cannot bridge to a node reachable only through it. A redundant independent guard also drops any above-clearance node from the final merged set. |
| Default-when-no-persona = unrestricted | Omitting the persona yields unfiltered retrieval (slice-1–3 behavior). Read as an authz mechanism this is the textbook **fail-open default** a real ACL must invert (default-deny) — it is safe **here only** because the labels are non-authz **and** the query ingress is the IAM-auth, scoped-principal Function URL (the caller is the trusted deploying/CLI role, not an end-user). Named so the seam is never copied into a context where the persona is the security principal. |
| Opt-in **default-deny** demonstration (`--default-deny`) | `visibility.resolve_clearance_or_default_deny` lets the demo *show* the inversion the row above names: with the flag on, an **absent** principal resolves to the **empty** `Clearance` (sees nothing) instead of `None` (unrestricted) — fail-**closed**. It is **additive and opt-in**: off, every shipped mode is byte-identical; on, it governs **only** the absent-principal cell (a present persona resolves the same either way, an unknown one still raises). Still a **synthetic teaching stand-in, not real authz** — it makes the fail-open→fail-closed flip legible in code (`graphrag … --default-deny` prints `clearance allows: []` and returns nothing), it does not turn the labels into an access-control system (security-hardening-followups AC7/AC8). |
| Filtered-out trace → caller | The trace names the *identity* of filtered items (an enumeration oracle in a real ACL system) as a **teaching observability aid**, explicitly labeled "a real ACL would not reveal this." This disclosure is **contained by the trusted-caller ingress**: the filtered-out trace crosses the IAM-auth, scoped-principal Function URL only to the trusted operator role — *never* to the persona as an authenticated end-user. Surfacing filtered IDs to a less-trusted caller (a multi-tenant fork, an end-user endpoint) is out of scope and would require re-deciding this boundary. |

The labels are written to **both** stores at ingest (Neptune node + edge properties;
OpenSearch chunk metadata) from the same dual-write, so the filter is consistent across
modes. The OpenSearch `visibility` keyword field lands only on a **fresh** index
(teardown-first rebuild; a re-deploy over a live domain does not migrate the mapping).

## Trust boundaries (opencypher-templates — the governed query path)

> The governed half of the governed-vs-risky pair. Its security property is **injection-safe
> and read-only by construction**, so — unlike the LLM-authored `text2opencypher-guarded`
> path — it does **not** rely on a run-time read-only guard. (RFC-0001 §2 named Neptune's
> read-replica for text2cypher; that path instead guards with **IAM read-only data-action
> scoping** per [ADR-0004](../adr/0004-text2cypher-read-only-guard.md) — see the
> [governed-vs-risky explanation](../guides/explanation/governed-vs-risky-graph-queries.md).)

| Boundary | Control |
| --- | --- |
| Untrusted question → LLM template selector (LLM01/LLM08) | The Bedrock Claude (Converse) selector receives the template catalog + the question as **data** in `messages` (never `system`); the `system` block carries the defensive untrusted-data directive; `maxTokens` is bounded. Its output is **validated against the fixed template set** — an id outside the library (or malformed/empty JSON) resolves to a governed *no-match*, never a fabricated query. The LLM selects an id only; it never authors query text. |
| Selected template → Neptune (the executable surface) | The executable surface is a **fixed, reviewed, read-only library** (`templates.py`): a static check asserts every template is read-only (no mutating clause / `CALL`) and binds every value through a declared `$param` (no value-interpolation token). Read-only is guaranteed by **review + lint**, not by endpoint enforcement — the teaching contrast with text2cypher. |
| Question → bound parameters (the governance boundary) | Parameter **values** are extracted **deterministically** (`params.py`), never taken as free LLM text: entity slots resolve through the slice-1 normalizers and are **confirmed against the store** (an unconfirmed candidate is dropped, not bound); enum slots are checked against a declared set; int slots are parsed + bounded. A bad required slot → governed no-match (no query runs). Values ride the openCypher `parameters` map (`NeptuneGraphStore.run_template_query`) — **never string-interpolated** (`ruff S` stays on). |
| Untrusted rows → Claude synthesis | Same posture as the hybrid path: returned rows ride Converse `messages` as data, the answer is display-only, the client is the default-TLS botocore chain. |
| Live ingress + IAM | The governed path rides the **existing** IAM-auth, scoped-principal Function URL via an additive `mode` field; it adds **no new resource and no new IAM statement** — selection reuses the already-granted `bedrock:Converse` on the synthesis model and the existing Neptune data-access (a *different* selection model would be the only thing that widens the grant). |
| Audit envelope (cypher + params + dropped candidates) → caller | The governed **success** envelope returns the executed cypher, the bound parameter map, and `dropped_candidates` (graph node ids that matched a slot's kind but failed store confirmation) — by design: the audit trace *is* the governed path's pedagogy. This relaxes the slice-3 "no internal detail crosses the Function URL" rule for the success path, contained by the **same trusted-ingress argument as slice 4's filtered-out trace**: the envelope crosses the IAM-auth, scoped-principal Function URL only to the trusted operator role, never to an end-user. `dropped_candidates` is a node-enumeration oracle if that ingress is ever widened to a less-trusted caller — re-decide this boundary first if so. (The *error* envelope stays fully sanitized.) |

## Trust boundaries (text2opencypher-guarded — the flexible query path)

> The risky half of the governed-vs-risky pair: the LLM **writes** the openCypher, so the
> executable surface is whatever it emits. Safety is **layered defense**
> ([ADR-0004](../adr/0004-text2cypher-read-only-guard.md)), and the load-bearing layer is
> **below the app** — the validator is layer 1, *not* the guarantee.

| Boundary | Control |
| --- | --- |
| Untrusted question → LLM query writer (LLM01/LLM05/LLM08) | The Bedrock Claude (Converse) generator receives the schema + question + any self-heal feedback as **data** in `messages` (never `system`); the `system` block directs it to emit **only a read query**, regardless of embedded instructions; `maxTokens` is bounded. The self-heal feedback is partly attacker-influenced/schema-bearing, so it too rides `messages` as untrusted data — the self-heal is not a prompt-injection amplifier. |
| Model-authored query → execution (layer 1: validation) | `validate.py` rejects any mutating clause / **any `CALL`** / multi-statement / `RETURN`-less / **unbounded variable-length path** and bounds the `LIMIT`, before the query is ever sent. Conservative (a forbidden keyword inside a string literal rejects). Known classes it cannot catch (Unicode-escape, backtick/dynamic identifier) are explicitly left to the backstops below. |
| Model-authored query → write (the backstop, the real guarantee) | The query Lambda's Neptune grant is **read-only** — `neptune-db:ReadDataViaQuery` + `connect` only, **no** `WriteDataViaQuery`/`DeleteDataViaQuery`. A write the validator missed is denied by **AWS IAM before the engine runs it**, so the read-only guarantee does not depend on the parser's completeness. (RFC-0001 §2 named the read-replica reader endpoint; ADR-0004 records why this single-node Serverless topology guards with IAM scoping instead — a reader endpoint needs a standing replica that breaks the cost posture.) Proven live by an out-of-band IAM-deny on a direct mutating call under the query-Lambda role. |
| Model-authored query → runaway read (read-cost backstop) | `LIMIT` bounds rows *returned*, not rows *expanded*. The validator rejects unbounded `[*]` paths (layer 1) and the Neptune **engine `neptune_query_timeout`** (cluster parameter group) kills a runaway traversal — the read analog of IAM-for-writes. |
| Neptune error / refusal → caller | A store execution error — including the IAM `AccessDenied` when the write backstop fires on a validator-missed write — surfaces as a **sanitized envelope** (`_serialize_text2cypher`): the generated queries + validation verdicts are returned (the audit value), but the raw error / ARN is **never** crossed to the caller (logged in-VPC, fed only to the internal self-heal). Generated-query *text* the model wrote IS returned — by design, the trace is the pedagogy; same trusted-ingress argument as the governed envelope. |
| Live ingress + IAM | Rides the **existing** IAM-auth, scoped-principal Function URL via the additive `mode: "text2cypher"` value. Generation reuses the already-granted synthesis-model `bedrock:Converse` (no widened Bedrock grant — the generator's default model id equals `DEFAULT_SYNTHESIS_MODEL_ID`). The only IaC change is the query-Lambda Neptune grant **narrowing** to read-only + the query-timeout parameter group; no new billable/compute resource (Budgets held at 150). The IAM-auth named-principal grant is the accepted aggregate-abuse bound for the demo. |

## Trust boundaries (metadata-filtering — the self-query path)

> The graphrag.com **Metadata Filtering / Self-Query** pattern: the LLM reads a structured
> filter out of the question and the vector search applies it during the ANN scan. The
> load-bearing security property is that the model's authority is **bounded by construction**
> — it can only produce a filter over a fixed, declared schema, and every value is
> deterministically re-validated before it touches OpenSearch. See the
> [self-query explanation](../guides/explanation/metadata-self-query-filtering.md).

| Boundary | Control |
| --- | --- |
| Untrusted question → LLM filter extractor (LLM01/LLM08) | The Bedrock Claude (Converse) extractor receives the question as **data** in `messages` (never `system`); the `system` block instructs extraction of a JSON filter over **only** the declared fields and carries the defensive untrusted-data directive; `maxTokens` is bounded; the client is the default-TLS botocore chain. The model produces *only* a filter — it never authors a query. |
| Extracted filter → OpenSearch (the governance boundary) | The model's raw output is run through the single deterministic `validate_filter` chokepoint (`selfquery.py`): a `source` value is kept only if in the closed enum (`community`/`enhancements`); an `entity_ids` value is resolved through the **pure** `link_question` resolver to a normalized graph-node id; an **undeclared field or unresolvable value is dropped and recorded**, never bound as free-form text. The validated values ride the OpenSearch request-body `terms` clause — **never string-interpolated** (`ruff S` stays on; injection-safe by construction). |
| Self-query filter ∧ permission clearance | The self-query `terms` and the slice-4 visibility `terms` are **independent** clauses on the same `knn` call, so a self-query filter can only **narrow**, never re-admit a chunk above a persona's clearance. The fail-closed `None`-vs-empty-`Clearance` semantics survive the merge (an empty clearance still matches nothing, regardless of the self-query filter). The self-query filter is **not authorization** — it is relevance scoping; the permission filter remains the (synthetic) authz stand-in. |
| Live ingress + IAM | Rides the **existing** IAM-auth, scoped-principal Function URL via the additive `mode: "selfquery"` value. Extraction reuses the already-granted synthesis-model `bedrock:Converse` (no widened grant — the extractor's default model id equals `DEFAULT_SYNTHESIS_MODEL_ID`); the filter uses the existing OpenSearch data-access. The path **builds no Neptune store** (entity validation is pure), so it adds **no Neptune grant**. The only store change is the k-NN index **method engine** (`nmslib` → `lucene` HNSW), an app-side mapping change on a fresh index — no new billable/compute resource (Budgets held at 150). |
| Audit envelope (extracted + validated filter, dropped) → caller | The success envelope returns the validated filter and what the validator dropped (the audit value — exactly which structured filter the model produced and how it was bounded), contained by the **same trusted-ingress argument** as the governed/text2cypher envelopes: it crosses the IAM-auth, scoped-principal Function URL only to the trusted operator role. The *error* envelope stays fully sanitized (correlation id, no internal detail). |

## Trust boundaries (parent-child-retrieval — the parent-child path)

> The graphrag.com **Parent-Child Retriever** pattern: a small child chunk's vector is matched
> (precise) on a nested `knn_vector` index, and the larger parent document body is returned for
> synthesis. It is a **vector-only** path — no graph store. See the
> [parent-child explanation](../guides/explanation/parent-child-retrieval.md).

| Boundary | Control |
| --- | --- |
| Untrusted question → LLM synthesizer (LLM01/LLM08) | Synthesis reuses the audited `BedrockClaudeSynthesizer`: the question + the retrieved **parent bodies** ride `messages` as **data** (never `system`); the `system` block carries the defensive untrusted-data directive; `maxTokens` is bounded; the client is the default-TLS botocore chain; the answer is display-only. No new LLM path. |
| Nested k-NN query → OpenSearch | The query vector, `k`, and the visibility filter values ride the request **body** (a nested `knn` over `children.vector` + a parent-level `terms` clause) — **never string-interpolated** into a path/query (`ruff S` stays on; injection-safe). HTTPS enforced; TLS verify defaults on; SigV4 via the default botocore chain. The parent body is app-stored and read back from `_source` — no cross-document `has_child` join (RFC-0001 §3). |
| Parent-child query ∧ permission clearance | The visibility `terms` rides the same nested query as a parent-level `bool.filter` composed AND with the child match, so parent-child can only **narrow** — a document above a persona's clearance is never returned. The fail-closed `None`-vs-empty-`Clearance` semantics hold (an empty clearance matches nothing). A parent's visibility is its document's single composed tier. |
| Live ingress + IAM | Rides the **existing** IAM-auth, scoped-principal Function URL via the additive `mode: "parentchild"` value. Synthesis reuses the granted `bedrock:Converse`; the child embedding reuses the granted Titan `bedrock:InvokeModel`; the nested index uses the existing OpenSearch `es:ESHttp*` data-access. The path **builds no Neptune store** (vector-only), so it adds **no Neptune grant**. The only store change is a **new index** on the existing domain (`graphrag-parents`), created app-side at `create_index` — no new billable/compute resource (Budgets held at 150). |
| Audit envelope (matched children + returned parents) → caller | The success envelope returns the matched child ids (the precise match) and the returned parent ids (the units synthesized over) — **never the raw parent body prose** (the trace shows the body by character count, not inline) — contained by the same trusted-ingress argument as the sibling envelopes. The *error* envelope stays fully sanitized (correlation id, no internal detail). |

## Trust boundaries (global-community-summary — the corpus-wide path)

> The graphrag.com **Global Community Summary** pattern (MS GraphRAG *global*): detect
> communities over the entity graph (Louvain, **in the Fargate ingest task** — not a standing
> Neptune Analytics service, ADR-0005), summarize each via Bedrock, and answer corpus-wide
> questions by a **map-reduce** over the summaries. See the
> [global community summary explanation](../guides/explanation/global-community-summary.md).

| Boundary | Control |
| --- | --- |
| Untrusted question + persisted summaries → LLM (LLM01/LLM04/LLM08) | Both the ingest **summarize** step and the query **map + reduce** reuse the audited `BedrockClaudeSynthesizer`: all community-derived content (member subgraph, summaries, partials) rides Converse `messages` as **data** (never `system`); the defensive directive + bounded `maxTokens` apply; the answer is display-only. `globalsearch.global_query` builds **no system prompt of its own**. The map drop is matched by **stripped equality** with the `NOT RELEVANT` sentinel (not a substring), so a persisted summary that *embeds* the literal string cannot suppress its own community (LLM04→LLM01 sentinel-collision). |
| Corpus-wide summary ∧ permission clearance (the leak boundary) | A summary blends **all** its members, so it is gated **whole** by its composed (most-restrictive) member tier (`compose` of member visibilities; an unlabeled member → `public`, the named teaching default). `all_communities` filters by `tier ∈ clearance.allowed` **before** the map step (`None` ⇒ unrestricted; **empty** ⇒ none — fail-closed), so an above-clearance community never reaches the synthesizer, the trace, the member-derived `title`, the map verdicts, or the citations. Citations are composed in `global_query` from surviving community ids + member `doc_paths` (a subset of in-clearance members — never exceeds the gate), **never** the synthesizer's chunk citations. A teaching stand-in for an ACL, not real authz. |
| `Community` node write/read → Neptune | At ingest the task writes `Community` nodes + stamps `communityId` on member `Entity` nodes via **parameterized** openCypher (every value in the parameter map, labels are fixed constants — **never interpolated**, `ruff S` on); HTTPS enforced, TLS verify on, SigV4 via the default botocore chain (the `store.neptune` posture). At query the Lambda reads `Community` nodes through a **read-only** store. |
| Detection compute (networkx) | Runs **only** in the Fargate ingest task (`community_detect`, networkx imported lazily); a `sys.modules` guard proves networkx never enters the query Lambda import graph. Louvain is seeded for reproducibility. |
| Live ingress + IAM | Rides the **existing** IAM-auth, scoped-principal Function URL via the additive `mode: "global"` value, dispatched **after** the shared `resolve_clearance` block (unknown persona → client error before any read). The one IaC change adds `bedrock:Converse` to the **ingest task role** (the existing `_bedrock_synthesis_invoke` grant, scoped, no wildcard) for summarization; the **query-Lambda Neptune grant is unchanged read-only** (ADR-0004 — reading `Community` nodes is a read); `Community` nodes ride the existing cluster — **no new billable/compute resource** (Budgets held at 150). |
| Delta staleness (named residual) | Communities are recomputed on **full ingest / `--rebuild` only**; a delta that raises a member's visibility leaves `Community.tier` stale-low — a down-classification residual, mitigated by requiring a full re-ingest after a visibility-label change (deferred: `global-community-summary-delta-tier-refresh`). Consistent with the synthetic-labels-are-not-authz posture (charter principle 5). |

## Trust boundaries (schema-guided-extraction — the ingest-time LLM extraction path)

> The **schema-guided LLM extraction** pattern (ADR-0006): an ingest-time Bedrock pass reads the
> corpus prose and proposes **triples constrained to a fixed schema**, made safe + narratable by a
> closed-schema validator + an entity-grounding check + distinguishable provenance. This is the
> first slice that routes **untrusted corpus prose into an LLM whose output crosses into the graph**
> that every retrieval pattern then reads — so the guard is load-bearing. See the
> [ingestion pattern-axis explanation](../guides/explanation/ingestion-patterns-and-retrieval-patterns.md).

| Boundary | Control |
| --- | --- |
| Untrusted prose → Claude extractor (LLM01/LLM05/LLM08) | The prose body + the schema ride Converse `messages` as **data**, never the `system` block; the `system` block carries a **pinned** defensive directive — embedded instructions are content, emit only schema-conforming triples, never invent an entity. `maxTokens` is bounded; the client is the default-TLS botocore chain. The output is **only candidate triples to validate** — never an instruction, never a tool call, never fed back as a command. |
| Model-authored triple → graph (the governance boundary, two guards) | Every candidate is **validated** against the closed schema (`validate_triple` — off-schema / deterministic-only / malformed → rejected, never written) **and grounded** (`ground_triple` — both endpoints must resolve via the existing `normalize` functions to a node already in the graph, of the expected kind; ungrounded/ambiguous → dropped). The model may relate **known** entities; it can never invent one. Every candidate — accepted, rejected, or dropped — is recorded with its source span in the replayable trace. |
| Model-asserted edge ∧ distinguishability (the honesty boundary) | Accepted edges carry `extraction_method: "schema-guided-llm"` at write (set authoritatively, never `setdefault`-merged) **and** the read path surfaces the method per traversed hop (`query.expand_neighborhood`/`traverse`/governed templates), so a model-asserted edge is never blended **silently** into an answer. The LLM-extractable edge kinds are **disjoint** from the deterministic kinds (a load-bearing invariant), so an LLM edge can never collide on a `(src, kind, dst)` key and mislabel a deterministic fact. |
| Per-doc amplification (LLM10:2025 Unbounded Consumption) | A single prose body yields at most a bounded number of candidate triples (`MAX_CANDIDATES_PER_DOC`), so a large/adversarial document cannot amplify into unbounded Bedrock calls or graph writes (denial-of-wallet at ingest). The corpus is operator-supplied / trusted-origin, but the per-doc cap is the explicit guard. |
| Trace artifact write → S3 (CWE-23) | The per-triple trace artifact key is derived **server-side** (`CORPUS_PREFIX` + a constant filename) — **never** from a doc path, span, triple, or any model-supplied text — so a poisoned doc/span cannot write outside the corpus prefix (the `write_manifest` confinement pattern). |
| Live ingress + IAM | The pass is an **additive, default-off** Fargate ingest phase (`SCHEMA_EXTRACTION` flag; `MODE=full`/`rebuild` only) that **reuses** the ingest task role's existing scoped `bedrock:Converse` grant at the synthesis model (`BedrockTripleExtractor`'s default `model_id == DEFAULT_SYNTHESIS_MODEL_ID`) — **no new grant, no new resource**; the trace rides the existing corpus bucket; Budgets held at 150. A *different* extraction model would be the only thing that widens the grant (an ADR-0006 amendment, gated). The LLM edges are read by the existing **read-only** query grant (ADR-0004) — no query-side write grant. |
| Additive resilience (fail-safe) | A Bedrock/extractor failure logs and **leaves the deterministic graph intact** — a failed LLM pass must never corrupt the deterministic graph (the pass is additive, and default-off). |

## Least privilege

The Fargate **task role** and the **vector probe role** grant only: scoped `s3`
read on the corpus bucket (task only), `neptune-db:*` data actions on the specific
cluster (task only), **`es:ESHttp*` scoped to the one OpenSearch domain ARN**, and
**`bedrock:InvokeModel` scoped to the one Titan v2 model ARN** — **no wildcard
`Resource`** (asserted by `apps/infra/tests/test_stack.py`). The task role additionally
holds **`bedrock:Converse` scoped to the synthesis Claude model** (global-community-summary:
the Fargate task generates per-community summaries in-task, ADR-0005 — the same scoped grant
the query Lambda holds, no wildcard). The `es` IAM prefix and
the adapter's SigV4 signing service come from a single `"es"` constant so they can't
drift. The execution role's `ecr:GetAuthorizationToken` is the one legitimate `"*"`
(an AWS requirement) and is out of that assertion's scope.

The slice-3 **query Lambda role** carries scoped grants (`es:ESHttp*` on the domain,
Titan `bedrock:InvokeModel`) **plus** the synthesis Claude grant, and — since
`text2opencypher-guarded` — its Neptune grant is **read-only** (`neptune-db:ReadDataViaQuery`
+ `connect` only, **no** `WriteDataViaQuery`/`DeleteDataViaQuery`; the ingestion task and
smoke probe retain the full read-write set, both legitimately write). This is the ADR-0004
write backstop, asserted per-role by `test_stack.py`. The synthesis Claude grant: `bedrock:InvokeModel` + `bedrock:Converse` scoped to **both**
the cross-region inference-profile ARN (`inference-profile/us.anthropic.claude-sonnet-4-6`,
account+region-qualified) **and** each underlying regional foundation-model ARN
(`foundation-model/anthropic.claude-sonnet-4-6` in the profile's routing regions) — a
cross-region inference profile needs both, with **no wildcard `Resource`** (asserted by
`test_stack.py`). The CDK `_SYNTHESIS_MODEL_ID` is asserted equal to the library
`DEFAULT_SYNTHESIS_MODEL_ID` so the grant scope can't drift from the runtime default.

## Cost as a security-adjacent control

Neptune/OpenSearch do not scale to zero, so a cloned-and-forgotten stack is a
wallet-DoS footgun. Controls: one-command `cdk destroy`, min-capacity stores, and a
Budgets alarm with a threshold + subscriber (charter principle 4).

## Out of scope this slice (named, not forgotten)

- **Production authorization.** Slice 4 ships the synthetic visibility labels + the
  permission filter (see the slice-4 trust-boundary table above), but those are a
  *teaching stand-in for ACLs*, never real IAM / multi-tenancy / data authz (charter
  principle 5). Real authorization is **not** built here.
- **Prompt injection from retrieved Markdown** (OWASP LLM01/08). **Now a control as
  of slice 3** (see the slice-3 trust-boundary table): retrieved chunks reach Claude as
  data-not-instruction with a defensive system directive, the answer is display-only,
  and `maxTokens` is bounded. *Accepted residual:* the corpus is public/benign and the
  output is display-only, so a successful injection can at worst produce a misleading
  display string — it routes to `security-reviewer` if the demo ever ingests private
  data or wires the output into a tool.
- **SAST/SCA scanners, cdk-nag, pip-audit.** **Discharged** by
  `security-hardening-followups`: CI (`.github/workflows/ci.yml`) now runs
  `pip-audit` (fails on a known vuln; reason+expiry suppressions in
  `.pip-audit-ignore`) and `cdk synth` with **cdk-nag** as a **hard** gate (an
  unsuppressed `AwsSolutions` finding fails the build; accepted residuals carry
  reason-signed `NagSuppressions`), and `.github/dependabot.yml` covers the `pip`
  + `github-actions` ecosystems. ruff `S` stays on. Remaining open follow-on
  (its own backlog item, now unblocked): a repo-wide secret scanner +
  `shellcheck` job — see `infra-secret-scan-ci`.
- **Live IAM/SG evaluation.** **Discharged** by `security-hardening-followups`
  AC9: the tightened egress was evaluated on a clean live deploy (both smoke
  probes + Fargate ingest + hybrid Function-URL query all succeeded with no
  silent egress block), the deployed SG-egress/IAM posture captured, then torn
  down. See "Live SG-egress posture (AC9)" below.
- **Uniform least-privilege SG *egress*.** **Discharged** by
  `security-hardening-followups` (AC1/AC2): every in-VPC compute SG —
  `IngestionSg`, `SmokeSg`, `VectorSmokeSg`, `QuerySg` — now sets
  `allow_all_outbound=False` with explicit egress to **exactly** the in-VPC
  stores (Neptune 8182 / OpenSearch 443) and VPC endpoints (443) it calls (the
  S3 corpus read via the managed S3 prefix list). A per-SG set-equality synth
  assertion holds the minimal set; the no-NAT topology (ADR-0002) is preserved,
  now with the SG as a defence-in-depth layer too rather than allow-all.

## Live SG-egress posture (AC9)

Live evaluation of the tightened closed-egress compute SGs on a clean deploy
(account `752989493306`, `us-east-1`, stack `GraphragSlice1`, 2026-06-30), then
`cdk destroy`. **Result: every live path succeeded under `allow_all_outbound=False`
— no silent egress block (the documented Bedrock-hang regression did not occur).**

| Live path | Exercises | Egress validated (no block) | Result |
| --- | --- | --- | --- |
| Neptune smoke Lambda | `SmokeSg` | Neptune 8182, Logs/STS 443 | `{"ok": true, …, "neighbors":[…]}` |
| Vector smoke Lambda | `VectorSmokeSg` | OpenSearch 443, **Bedrock 443**, Logs/STS | `{"ok": true, …, "dims": 256}` (real Titan embed) |
| Fargate ingest task | `IngestionSg` | S3 (prefix list), ECR, **Bedrock**, Neptune, OpenSearch, Logs/STS | exit 0; parsed 10 docs, 22 nodes/28 edges, 14 vector chunks, 6 parents, 3 community summaries (Bedrock Converse), 6 cross-source resolutions |
| Hybrid Function-URL query (SigV4) | `QuerySg` | Neptune 8182, OpenSearch 443, **Bedrock 443**, Logs/STS | full result — OpenSearch seeds → 2-hop Neptune traversal → Bedrock Claude synthesis |

**Deployed egress (from `describe-security-groups`), confirming AC1/AC2 live:**

| Compute SG | Rendered egress (live) |
| --- | --- |
| `IngestionSg` | `8182`→Neptune SG; `443`→OpenSearch SG + 5 interface-endpoint SGs (Bedrock/EcrApi/EcrDocker/Logs/STS); `443`→S3 managed prefix list `pl-63a5400a` |
| `SmokeSg` | `8182`→Neptune SG; `443`→Logs + STS endpoint SGs |
| `VectorSmokeSg` | `443`→OpenSearch SG + Bedrock/Logs/STS endpoint SGs |
| `QuerySg` | `8182`→Neptune SG; `443`→OpenSearch SG + Bedrock/Logs/STS endpoint SGs |

**No compute SG carries a `0.0.0.0/0` egress rule** (only the AWS-managed
interface-endpoint SGs and the unused VPC `default` SG do — endpoints do not
initiate outbound, so this is out of the closed-egress scope). The **`QueryRole`
Neptune grant is read-only live** — `neptune-db:connect` + `ReadDataViaQuery`,
no Write/Delete (ADR-0004 preserved; not weakened to pass any gate). The stack was
torn down with `destroy.sh`; Budgets held at 150.
