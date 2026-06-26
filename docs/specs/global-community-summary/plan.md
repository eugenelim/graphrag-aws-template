# Plan: global-community-summary

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Global Community Summary reuses the `GraphStore` seam (read the resolved entity graph
back), the `Synthesizer` seam (summaries + map-reduce), the in-VPC query Lambda + the
IAM-auth Function URL, and the slice-4 `visibility`/`Clearance` model. The genuinely new
mechanisms are three: an **ingest-side detection + summarization phase** (Louvain via
`networkx`, run in the Fargate task — ADR-0005), a **`Community` node write-back** to the
existing Neptune Database behind a new `CommunityStore` seam, and a **query-side bounded
map-reduce** (`global_query`) over the stored summaries.

The shape: a pure **`community_detect.py`** module turns `(nodes, edges, seed)` into a
stable Louvain partition (`CommunitySpec`s, each tagged with its composed visibility
tier) and `summarize_communities(...)` turns each spec + its member subgraph into a
`Community` (one synthesizer call each). A new **`CommunityStore`** seam (ABC +
`NeptuneCommunityStore` for live, `MemoryCommunityStore` for CI/offline,
backend-identical) owns the `Community`-labeled node write-back, the member
`Entity.communityId` stamp, and the clearance-gated `all_communities` read. A query-side
**`globalsearch.py`** (`global_query` + `GlobalSearchResult`) wires
clearance-filter → per-community **map** (drop `NOT RELEVANT`) → **reduce** → trace. The
full-ingest Fargate path gains a `_community_writeback` phase after the graph write; a
CLI verb (`global-query`) and an additive `mode: "global"` branch on the query Lambda
expose it offline and live.

The riskiest parts are (1) keeping **`networkx` out of the query Lambda import graph** —
detection is ingest-only, imported lazily, and proven out by the extended `sys.modules`
guard; (2) the **permission gate** — a corpus-wide summary blends tiers, so it must be
gated **whole** by its composed member tier, fail-closed, and the gate must run **before**
the map so an above-clearance community never reaches the synthesizer or the trace; and
(3) **reproducibility** — Louvain is randomized, so the seed is pinned and a two-run
identical-partition test guards it. The one IaC change is additive and narrow: the ingest
task role gains the existing `_bedrock_synthesis_invoke()` Converse grant; **no new
resource**, Budgets held at `150`.

## Constraints

- **ADR-0005** — community detection runs **in the Fargate ingest task** with **Louvain
  via `networkx`** (seeded), written back to the **existing** Neptune Database as
  `Community` nodes; **no Neptune Analytics / no standing service**; `networkx` is the
  one new (ingest-only) dependency, kept out of the Lambda; the ingest task role gains
  `bedrock:Converse`.
- **Charter coverage table (*Global Community Summary* row) + the Louvain-vs-Leiden
  honesty note** — this slice ships that row and **states its algorithm is Louvain, not
  Leiden**, naming Neptune Analytics (managed Louvain) as the deliberately-unused
  alternative.
- **RFC-0001 feasibility §1** — Neptune Analytics ships Louvain not Leiden and is
  avoidable; compute in Fargate, write back.
- **ADR-0001** — reuse the `Synthesizer` seam + the retrieval-trace posture.
- **ADR-0002** — ride the existing on-demand Fargate task + the existing Neptune cluster
  + the in-VPC query Lambda; `Community` nodes add no billable resource; Budgets `150`.
- **ADR-0003** — IaC stays AWS CDK Python.
- **ADR-0004** — the query Lambda's read-only Neptune grant already permits reading
  `Community` nodes; **no query-side grant change**.
- **`packages/graphrag/AGENTS.md`** — runtime deps stay `pyyaml` + `boto3` (+ the new
  ingest-only `networkx`); the query import graph stays PyYAML-free **and networkx-free**;
  the community store's in-memory backend applies the identical clearance predicate
  (backend-identical).

## Construction tests

Most tests live under their task. Cross-cutting:

**Integration tests:**
- Offline end-to-end (detect → summarize → `global_query` over the fixture corpus with
  the in-memory stores + `TemplateSynthesizer`) on an exemplar corpus-wide question —
  asserts the communities considered, the per-community map verdicts, and `.render()`
  emitting question → communities → map verdicts → reduced answer in order (T4).
- Backend-identical community read: for a given clearance, the in-memory
  `all_communities` and the Neptune adapter `all_communities` (mock HTTP client) produce
  the **same** clearance-gated `Community` set, and the Neptune body writes the
  `Community` node + `Entity.communityId` via the parameter map (T2).
- PyYAML-free **and networkx-free** import-graph guard: blocks `import yaml` **and**
  `import networkx`, then imports `globalsearch` + `store.community_neptune` + the query
  Lambda (extends `test_query_lambda.py`) (T7).
- Reproducibility: `detect_communities` run twice with the pinned seed over the fixture
  graph yields the identical partition (T1).

**Manual verification:** AC10 live deploy + global smoke (run if live AWS is available;
otherwise deferred — see Rollout).

## Design (LLD)

Stack: Python 3.11+, `networkx` (ingest-only Louvain), `boto3` `bedrock-runtime` Converse
(synthesis), `botocore` SigV4 to Neptune over signed HTTPS and to the Function URL, AWS
CDK Python. Conforms to the existing `packages/graphrag` module stereotypes (a pure logic
module + an injectable store seam with an in-memory + a Neptune backend + an orchestrator
+ a CLI verb), mirroring `parentchild.py`/`selfquery.py` + `store/parentchild_*`.

### Design decisions
- **Detection in the Fargate ingest task, Louvain via `networkx`, written back as
  `Community` nodes (ADR-0005).** *Rejected:* Neptune Analytics (standing service — breaks
  teardown/cost) and `leidenalg`/`igraph` (C-extension; would diverge the algorithm from
  the managed alternative). Traces to: AC1, AC5, AC8.
- **A dedicated `CommunityStore` seam, not new methods on the `GraphStore` ABC.**
  `Community` is a distinct node label and a distinct concern (summaries), so it gets its
  own store family (the parent-child precedent) — `GraphStore` and its six consumers stay
  untouched. The member `Entity.communityId` stamp lives on the `CommunityStore` (it is a
  community-detection write, even though it targets `Entity` nodes). *Rejected:* bloating
  `GraphStore` with community methods every consumer inherits. Traces to: AC2.
- **Membership canonical on the `Community` node (`entity_ids`); `communityId` additionally
  stamped on members.** Both derive from the one Louvain partition in one pass (cannot
  disagree); the node `entity_ids` is what the query reads, the per-entity stamp is the
  narratable trace + literal "write communityId back" affordance. *Rejected:* per-entity
  `communityId` only (no node membership — the query would have to scan all entities).
  Traces to: AC2, AC5.
- **Corpus-wide summary gated WHOLE by its composed member tier, fail-closed, before the
  map.** A summary blends all members, so `tier = compose(*member tiers)`; served only if
  `tier ∈ clearance.allowed`. *Rejected:* per-member redaction of a summary (a partial
  summary still leaks; the summary is generated once over all members) and gating after
  the map (an above-clearance community would reach the synthesizer). Traces to: AC2, AC4.
- **Bounded map-reduce reusing the `Synthesizer` seam unchanged, via a `NOT RELEVANT`
  sentinel.** The map asks the synthesizer what each community contributes; a `NOT
  RELEVANT` result is dropped; the reduce combines survivors. No protocol change;
  deterministic offline. *Rejected:* adding a `rate_relevance` method to the `Synthesizer`
  protocol (bloats it for all synthesizers); reduce-only with no map (not the catalog
  pattern). Per-community numeric scores + the Louvain level hierarchy are named scale-out
  extensions. Traces to: AC4.
- **Detection runs on `all_nodes()`/`all_edges()` from any `GraphStore`.** So the in-memory
  backend runs the same Louvain offline (networkx is in the dev env) — the offline-first
  invariant. Traces to: AC1, AC5, AC6.
- **Live path rides the existing Function URL via an additive `mode: "global"`.**
  Back-compat (absent ⇒ `hybrid`); no new endpoint/IAM; the Lambda builds a **read-only**
  community store and detects nothing. Traces to: AC6, AC7, AC8.

### Data & schema
- `CommunitySpec(id: str, entity_ids: tuple[str, ...], size: int, tier: str)` — the
  detection output (pre-summary); `id` is `community-{n}` (stable by sorted member order),
  `tier = compose(*member visibilities)`.
- `Community(id: str, title: str, summary: str, entity_ids: tuple[str, ...], tier: str,
  size: int)` — the stored unit; `summary` is the synthesized text, `title` a stable label
  (e.g. the largest member's name + size).
- `GlobalSearchResult(question, communities_considered: list[Community], map_verdicts:
  list[MapVerdict], answer: str, citations: list[str], clearance: Clearance | None)`;
  `MapVerdict(community_id, relevant: bool, partial: str)`.
- New Neptune `Community` node label: properties `id`/`title`/`tier` (keyword-ish scalars),
  `summary` (string), `entity_ids` (JSON-string list — Neptune has no set property, the
  `_DOC_PATHS_PROP` precedent), `size` (int). Member entities gain a `communityId` scalar
  property on the existing `Entity` label. Traces to: AC1, AC2.

### Interfaces & contracts
- Internal Python seams only (no repo-root `contracts/`). New `CommunityStore` ABC:
  `create()`, `upsert_community(c)`, `set_community_id(entity_id, community_id)`,
  `all_communities(*, allowed_labels=None) -> list[Community]`, `count()`, `clear()`. The
  Function URL request gains the `mode: "global"` value (additive); the global response
  envelope is `{communities (ids+tier+size), map_verdicts, answer, citations, trace}`.
  Traces to: AC2, AC6, AC7.

### Component / module decomposition
- New: `community_detect.py` (`CommunitySpec`, `detect_communities` [lazy `networkx`],
  `summarize_communities`, `DEFAULT_SEED`), `globalsearch.py` (`global_query`,
  `GlobalSearchResult`, `MapVerdict`, the `NOT RELEVANT` sentinel, `DEFAULT_TOP_N`),
  `store/community_base.py` (`Community`, `CommunityStore` ABC),
  `store/community_neptune.py` (`NeptuneCommunityStore` — reuses the
  `HttpClient`/`_UrllibClient`/SigV4 `_run` posture from `store.neptune`),
  `store/community_memory.py` (`MemoryCommunityStore`).
- Reused: `model.Node`/`Edge`, `store.base.GraphStore.all_nodes/all_edges`,
  `synthesize.*`, `visibility.compose`/`Clearance`.
- Modified: `apps/ingestion/entrypoint.py` (`_community_writeback` after the graph write;
  `run`/injection gain the community store), `cli.py` (`global-query` +
  `detect-communities` verbs), `query_lambda.py` (mode dispatch + `_serialize_global`),
  `apps/infra/stacks/graphrag_stack.py` (add `_bedrock_synthesis_invoke()` to `task_role`).
  Traces to: AC1–AC8.

### State & control flow
- **Ingest** (`_community_writeback`, MODE=full/rebuild only): `store.all_nodes()` +
  `store.all_edges()` → `detect_communities(nodes, edges, seed=DEFAULT_SEED)` →
  `summarize_communities(specs, graph, synthesizer)` → `community_store.clear()` then
  `upsert_community` each + `set_community_id` per member. No-op when no community store /
  live config. Traces to: AC5.
- **Query** (`global_query`): `community_store.all_communities(allowed_labels=
  clearance.allowed if clearance else None)` → top-N by size → per community
  `synthesizer.synthesize(map_question, [summary_as_VectorHit], [])` → drop `NOT RELEVANT`
  → `synthesizer.synthesize(question, [partial_i as VectorHit], [])` reduce →
  `GlobalSearchResult`. `.render()` order: question → communities considered (id/tier/size)
  → per-community map verdict → reduced answer + citations. Traces to: AC4.

### Behavior & rules
- Detection: undirected `networkx.Graph` from entity nodes (vertices) + edges; Louvain
  `louvain_communities(G, seed=seed)`; communities sorted (largest first, then by sorted
  member id) for stable `community-{n}` ids; an isolated node → singleton. Tier =
  `compose(*member node visibilities)`, where each member's visibility is read with the
  same expression the graph path uses — `node.props.get("visibility", DEFAULT_VISIBILITY)`,
  importing only `DEFAULT_VISIBILITY`/`compose` from the **pure** `visibility` module (not
  from `hybrid`, which would drag the query/vector import surface into this pure ingest-side
  module and break the networkx-isolation) — so an **unlabeled/unknown** member composes as
  `public` (the deliberate teaching default), named so the down-classification is reviewed,
  not silent.
- Summarization input: member entities (id, kind, title) + the **intra-community** edges
  (relationships among members), formatted as the untrusted-data context; one
  `synthesize` call per community.
- Map step: `map_question` wraps the user question + a directive "using only this
  community summary, state what it contributes; if nothing, reply with exactly
  `NOT RELEVANT`"; a community is dropped **only when its map answer, stripped, *equals*
  `NOT RELEVANT`** (sole-token match, **not** a substring `in` check — a persisted summary
  that merely embeds the literal string still participates; LLM04→LLM01 sentinel-collision
  robustness). Offline `TemplateSynthesizer` never emits the bare sentinel, so all
  in-clearance communities pass (deterministic).
- Map + reduce Converse calls place all community-derived content (summaries, partials) in
  `messages` as **data, never `system`**, with the existing defensive directive + bounded
  `maxTokens` (`synthesize.py` posture) — `global_query` composes both call sites itself, so
  this is new call-site code, not a `hybrid_query` passthrough.
- Clearance: `all_communities(None)` ⇒ all; `allowed_labels=frozenset()` ⇒ none
  (fail-closed); else `tier ∈ allowed_labels`. Applied in the store, **before** the map.
- Citations: composed **in `global_query`** = surviving `community:<id>` + the deduped
  member-document `doc_paths` (from the surviving communities' member `Node.doc_paths`).
  The map/reduce wraps each summary as a `VectorHit` for the *prompt context only*; the
  synthesizer's chunk-derived `.citations` are **discarded** for global mode (no synthetic
  provenance leaks into the answer), and the citation set is a subset of the served
  communities' member documents (never exceeds the clearance gate).

### Failure, edge cases & resilience
- No communities (empty graph) ⇒ empty result, trace says "(no communities)", a graceful
  "no corpus-wide context" answer — not an error.
- All communities filtered out by clearance ⇒ same graceful empty path (fail-closed).
- A singleton community is summarized normally (one entity, no intra-edges).
- Lambda: over-long question rejected pre-orchestration; any failure ⇒ generic sanitized
  envelope + correlation id; unknown `mode` ⇒ client error; unknown persona ⇒ client
  error. Traces to: AC4, AC7.

### Quality attributes (NFRs / security)
- Parameterization: every value in the openCypher `Community` write/read rides the
  parameter map (no interpolation) — pinned by the AC2 adapter test. `ruff` `S` enabled.
- Untrusted-input at the Claude boundary (summaries + map + reduce): question + community
  context as Converse `messages` data, defensive system directive, bounded `maxTokens`,
  default-TLS client, display-only answer (reuse `synthesize.py` posture). Traces to: AC3,
  AC4, AC7.
- Permission: corpus-wide summary gated whole by composed tier, fail-closed, before the
  map — never widens, never partially leaks. Traces to: AC2, AC4.
- PyYAML-free **and networkx-free** query import graph (Lambda bundle). Traces to: AC7.
- Reproducibility: pinned Louvain seed; two-run identical-partition test. Traces to: AC1.

### Dependencies & integration
- One new runtime dependency: **`networkx`** (ingest-only, optional `ingest` extra;
  recorded in `packages/graphrag/AGENTS.md`; imported lazily; absent from the Lambda). No
  other new dependency (Converse via existing `bedrock-runtime`; SigV4 via `botocore`).
  One IaC change: the ingest task role gains the existing `_bedrock_synthesis_invoke()`
  Converse grant; no new resource; Budgets `150`. Traces to: AC8.

## Tasks

### T1: Community detection — Louvain in-process, seeded, pure (AC1)
**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/community_detect.py, packages/graphrag/tests/test_community_detect.py, pyproject.toml, packages/graphrag/AGENTS.md
**Tests:**
- `# STUB: AC1`: `detect_communities(nodes, edges, seed=DEFAULT_SEED)` over a fixture
  graph returns `CommunitySpec`s partitioning every node; the **same seed** yields the
  **identical** partition across two runs; an isolated node is its own singleton; a
  community's `tier == compose(*member visibilities)`; community ids are stable
  (`community-{n}`, largest-first then sorted member id).
- `# STUB: AC1` visibility default: a member with an **absent/unknown** `visibility` prop
  composes as `public` (does not raise the tier) via `node.props.get("visibility",
  DEFAULT_VISIBILITY)` from the pure `visibility` module; a `restricted` member raises the
  community tier to `restricted`. (`community_detect` imports nothing from `hybrid`.)
- `# STUB: AC1`: `import graphrag.community_detect` does **not** import `networkx` at
  module load (lazy); calling `detect_communities` does.
**Approach:**
- Define `CommunitySpec` + `DEFAULT_SEED` + `detect_communities` (build an undirected
  `networkx.Graph` lazily; `louvain_communities(G, seed=seed)`; sort for stable ids;
  compose tiers). Add `networkx>=3.0` to a new `[project.optional-dependencies] ingest`
  group; record it in `packages/graphrag/AGENTS.md` (ingest-only, Lambda-excluded).
**Done when:** detection + reproducibility + lazy-import tests green; `ruff`/`mypy` clean.

### T2: `Community` node + `CommunityStore` seam (AC2)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/store/community_base.py, packages/graphrag/src/graphrag/store/community_neptune.py, packages/graphrag/src/graphrag/store/community_memory.py, packages/graphrag/tests/test_store_community.py
**Tests:**
- `# STUB: AC2` write: `NeptuneCommunityStore.upsert_community(c)` issues a parameterized
  `MERGE (n:Community {id:$id}) SET …` (values in the parameter map, never interpolated;
  `entity_ids` as a JSON string) and `set_community_id` issues a parameterized
  `MATCH (n:Entity {id:$id}) SET n.communityId=$cid` (asserted via the mock HTTP client).
- `# STUB: AC2` read + clearance: `all_communities(allowed_labels=…)` parses `Community`
  nodes back and applies `tier ∈ allowed` (`None` ⇒ all; empty set ⇒ none, fail-closed);
  the in-memory store returns the **same** clearance-gated set (backend-identical).
**Approach:**
- `community_base.py`: `Community` dataclass + `CommunityStore` ABC. `community_neptune.py`:
  reuse `store.neptune` `HttpClient`/`_UrllibClient`/SigV4 `_run` (thin shared helper);
  `Community` label + JSON-string `entity_ids`. `community_memory.py`: dict of `Community`s
  + the clearance predicate.
**Done when:** write/read/clearance + backend-identical tests green; gates clean.

### T3: Per-community summaries via the `Synthesizer` seam (AC3)
**Depends on:** T1
**Touches:** packages/graphrag/src/graphrag/community_detect.py, packages/graphrag/tests/test_community_detect.py
**Tests:**
- `# STUB: AC3`: `summarize_communities(specs, graph, synthesizer)` calls
  `synthesizer.synthesize` once per community with the **member subgraph** as context
  (asserted via a spy — member entities + intra-community edges, not unrelated entities);
  the returned `Community.summary` is the synthesized text, `tier == compose(*member
  tiers)`; `TemplateSynthesizer` yields a deterministic non-semantic summary.
**Approach:**
- Build, per spec, the member `Node` list + the intra-community `Edge` list; format the
  relationships into the synthesis context (members as `graph_facts`, relationships as a
  data block); call `synthesize`; assemble `Community` (stable `title`).
**Done when:** summarization + spy + offline-determinism tests green; gates clean.

### T4: Global map-reduce orchestration + trace (AC4)
**Depends on:** T2, T3
**Touches:** packages/graphrag/src/graphrag/globalsearch.py, packages/graphrag/tests/test_globalsearch.py
**Tests:**
- `# STUB: AC4`: `global_query` over a fixture community store reads clearance-filtered
  communities, runs the map (a `NOT RELEVANT` partial is dropped — asserted with a stub
  synthesizer that emits the sentinel for one community), reduces the survivors, and
  `.render()` emits question → communities considered → per-community map verdict →
  reduced answer + citations in order.
- `# STUB: AC4` clearance: with a clearance excluding a tier, those communities are absent
  from the map, reduce, **and** trace (incl. their member-derived `title`); an empty
  `Clearance.allowed` ⇒ zero communities.
- `# STUB: AC4` citations: `result.citations` are `community:<id>` + real member
  `doc_paths` composed in `global_query`, contain **no** synthetic-chunk provenance, and
  are a **subset** of the served communities' member documents.
- `# STUB: AC4` sentinel collision: a community whose summary **embeds** the literal
  `NOT RELEVANT` (but whose map answer is a real contribution) is **still mapped** (drop is
  stripped-equality, not substring); a community whose map answer *equals* `NOT RELEVANT` is
  dropped.
- `# STUB: AC4` injection isolation: an instruction injected into a community summary
  (`"ignore previous instructions and …"`) does not alter the reduce call's structure — the
  summary content rides `messages`, never `system` (asserted via a spy synthesizer capturing
  the call shape).
**Approach:**
- `global_query(question, *, community_store, synthesizer, clearance=None,
  top_n=DEFAULT_TOP_N)`: filter (in the store) → top-N by size → map (wrap each summary as
  a `VectorHit` for the prompt, ask the contribution question, drop `NOT RELEVANT`) →
  reduce (survivors as `VectorHit`s) → compose `citations` **in `global_query`**
  (`community:<id>` + deduped surviving-member `doc_paths`; discard the synthesizer's
  chunk citations) → `GlobalSearchResult`. Define `MapVerdict`, the sentinel, `.render()`.
**Done when:** orchestration + clearance + narratability tests green; gates clean.

### T5: Ingest detect + summarize + write-back on full ingest (AC5)
**Depends on:** T2, T3
**Touches:** apps/ingestion/entrypoint.py, apps/ingestion/tests/test_entrypoint.py
**Tests:**
- `# STUB: AC5`: the full-ingest path, given an injected `MemoryGraphStore` (populated) +
  `MemoryCommunityStore` + a **counting** synthesizer, reads the graph back, detects
  (seeded), summarizes (one synthesize call per community — asserted), writes `Community`
  nodes + stamps `communityId`; the community store holds one `Community` per detected
  community with members, tier, size.
- `# STUB: AC5`: absent both an injected community store and `NEPTUNE_ENDPOINT`, the
  write-back is a **no-op** (a vector-only deploy is unchanged).
- `# STUB: AC5`: `MODE=delta` does **not** recompute communities (asserted — no community
  write); `MODE=full`/`rebuild` do. (The delta-staleness down-classification residual — a
  member visibility change under delta leaves `Community.tier` stale-low — is documented as a
  Never-do + the `global-community-summary-delta-tier-refresh` backlog token, not fixed here;
  a visibility-label change requires a full re-ingest.)
**Approach:**
- Add `_community_writeback(env, store, community_store, synthesizer)` called in the
  MODE=full (and rebuild) branch after the graph write; thread an optional
  `community_store` (and reuse the synthesizer resolution) through `run`. The live trigger
  is **`NEPTUNE_ENDPOINT` set** (mirroring how `_vector_dual_write` keys off
  `OPENSEARCH_ENDPOINT`): on deploy it resolves `NeptuneCommunityStore` +
  `BedrockClaudeSynthesizer` (deploy-only `pragma: no cover`); tests inject both. The
  no-op branch (neither injected nor `NEPTUNE_ENDPOINT`) is the only line the goal-based
  reviewer confirms is covered, not `pragma`'d.
**Done when:** write-back + no-op + delta-scoped-out tests green; gates clean.

### T6: CLI verbs `global-query` + `detect-communities` (AC6)
**Depends on:** T4, T3
**Touches:** packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/test_cli.py
**Tests:**
- `# STUB: AC6`: `global-query` offline builds the in-memory graph + community store (detect
  + summarize the fixture with `TemplateSynthesizer`), prints the trace + the non-semantic
  label; `--function-url` builds a SigV4 POST whose body carries `mode: "global"`;
  `--persona` rides the body / resolves a clearance fail-closed; `detect-communities` prints
  the partition + summaries offline.
**Approach:**
- Add `_cmd_global_query` + `_cmd_detect_communities` + parsers (`--q`, corpus args,
  `--k`/`--top-n`, `--bedrock`, `--function-url`, `--region`, `--persona`); offline default
  (in-memory stores + `TemplateSynthesizer`), `--bedrock` ⇒ `BedrockClaudeSynthesizer`.
  Reuse `_function_url_query(..., mode="global")` (extend its docstring mode-list).
**Done when:** `test_cli.py` green; gates clean.

### T7: Query Lambda global dispatch — PyYAML-free + networkx-free (AC7)
**Depends on:** T4
**Touches:** packages/graphrag/src/graphrag/query_lambda.py, packages/graphrag/tests/test_query_lambda.py
**Tests:**
- `# STUB: AC7`: `mode="global"` with mocked community store + synthesizer returns the
  trace envelope (communities, map verdicts, answer, citations, trace); unknown `mode` ⇒
  client error; over-long question still rejected; unknown persona ⇒ client error
  **before any community read** (the global branch sits **after** the shared
  `resolve_clearance` block, like `selfquery`/`parentchild`, so a supplied persona never
  silently resolves to `clearance=None`); the `sys.modules` guard now blocks `yaml` **and**
  `networkx` and still imports `globalsearch` + `store.community_neptune` + the handler; the
  Lambda builds a read-only community store and **detects nothing**.
**Approach:**
- Extend `_extract_mode` docstring + dispatch: place the `global` branch **after** the
  shared persona→`resolve_clearance` block (the filtering-mode position, not above it),
  build `NeptuneCommunityStore` (read-only) + `BedrockClaudeSynthesizer`, run `global_query`,
  `_serialize_global(result)`.
**Done when:** `test_query_lambda.py` green (incl. the extended import guard); gates clean.

### T8: IaC — Converse grant on the ingest task role, no new resource, cost held (AC8)
**Depends on:** T5
**Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py
**Tests:**
- `# STUB: AC8`: synth assertion — the **ingest task role** grants `bedrock:Converse`
  scoped to the synthesis model (no wildcard `Resource`); `cdk synth` adds **no** new
  resource (no Neptune Analytics, no second cluster); the **query-Lambda Neptune grant is
  unchanged** (read-only per ADR-0004); no other role's grant widened; Budgets is the
  literal `150`.
**Approach:**
- Add `task_role.add_to_policy(self._bedrock_synthesis_invoke())` in `_ingestion_task`
  (mirrors the query Lambda's grant). Extend `test_stack.py` to assert the grant + the
  no-new-resource / query-grant-unchanged / Budgets-150 invariants.
**Done when:** CDK-env-gated synth test green; gates clean.

### T9: Global showcase set + explanation doc (Louvain-not-Leiden stated) (AC9)
**Depends on:** T1, T4
**Touches:** packages/graphrag/src/graphrag/showcase/queries.yaml, packages/graphrag/src/graphrag/showcase/__init__.py, packages/graphrag/tests/test_showcase.py, docs/guides/explanation/global-community-summary.md
**Tests:**
- `# STUB: AC9`: `load_global_showcase()` parses ≥3 corpus-wide entries; each names the
  expected contributing communities + the corpus-wide theme; the test asserts every named
  entity/community resolves in the fixture corpus after detection.
**Approach:**
- Add `global_queries` to `queries.yaml` (id, query, expected_communities, theme,
  highlight) + `GlobalShowcaseQuery` + `load_global_showcase()`.
- Write the explanation doc: corpus-wide map-reduce over community summaries vs. local
  seed-and-expand; the exact `global-query` / `detect-communities` CLI commands; **the two
  honest divergences** — Louvain not Leiden (charter note), and detection in the Fargate
  task not a standing Neptune Analytics service (ADR-0005).
**Done when:** `test_showcase.py` green; doc renders; gates clean.

### T10: Live deploy + global smoke (AC10) — run-or-defer
**Depends on:** T6, T7, T8
**Tests:**
- Manual/live: deploy, ingest the corpus (the Fargate task detects communities + writes
  `Community` nodes with live Bedrock summaries), a SigV4 `mode: global` call map-reduces
  over them live + returns the trace; a second call with a non-default `persona` asserts
  the live result omits above-clearance communities (composed-tier gate, fail-closed);
  then `apps/infra/scripts/destroy.sh`.
**Approach:**
- If live AWS access is available, run end-to-end via `apps/infra/scripts/deploy.sh` →
  ingest → the two Function-URL calls, record it in `deployment-and-verification.md`, then
  tear down.
- **Otherwise defer, atomically:** in the *same* edit, create the `docs/backlog.md` heading
  `### global-community-summary-live-smoke` **and** set the spec's AC10 checkbox to
  `- [ ] AC10 … (deferred: global-community-summary-live-smoke)` — token and target land
  together (CONVENTIONS § 4). The offline + mocked path proves the orchestration.
**Done when:** live smoke recorded **or** AC10 deferred with the backlog heading + token in
the same edit.

### T11: Spec metadata + drift closure (CONVENTIONS § 4) + architecture docs
**Depends on:** T1, T2, T3, T4, T5, T6, T7, T8, T9
*(Not an AC — realizes the drift-closure metadata invariants: Status flip, AC checkbox
ticks, the deferral register entry if any, ADR-0005 + specs-README rows, the
architecture-docs update. Finalization, not scope creep.)*
**Touches:** packages/graphrag/AGENTS.md, docs/architecture/overview.md, docs/architecture/security.md, docs/architecture/infrastructure.md, docs/adr/README.md, docs/specs/README.md, docs/specs/global-community-summary/spec.md, docs/product/briefs/graphrag-pattern-catalog.md
<!-- docs/CHARTER.md coverage row + brief Spec-map row are AUTO-DERIVED from this spec's Status: by the coverage lint — regenerated, never hand-edited (substantive charter edits route through RFC per AGENTS.md). -->
**Tests:**
- Goal-based: spec-status / coverage lint clean; the brief Spec-map row + the charter
  coverage table `Global Community Summary` row reflect the shipped status (auto-derived);
  AC checkboxes reflect reality; every deferral token resolves to a real backlog heading.
**Approach:**
- Update the `graphrag` AGENTS.md module map (`community_detect` + `globalsearch` +
  `store/community_*`) + invariants (the global import graph PyYAML-free **and**
  networkx-free; the community-store backend-identical clearance predicate; detection
  ingest-only). Add the `networkx` ingest-only dependency note.
- Update `architecture/overview.md` (global path), `security.md` (global posture:
  read-only Neptune, parameterized community write/read, corpus-wide summary gated whole by
  composed tier fail-closed, networkx ingest-only), `infrastructure.md` (the one new grant;
  no new resource). Add the ADR-0005 row to `docs/adr/README.md` and the spec row to
  `docs/specs/README.md`. Tick met ACs; flip Status. The charter coverage table + brief
  Spec-map rows are **auto-derived** from the spec `Status:` — run the lint, don't
  hand-edit.
**Done when:** docs consistent; lints clean; every deferral token resolves to a real
backlog heading.

## Rollout

- **Delivery:** additive at the seams (the CLI gains `global-query` + `detect-communities`;
  the Function URL gains the `mode: "global"` value, absent ⇒ `hybrid`, unchanged; a new
  `Community` node label + a per-`Entity` `communityId` property are written on full
  ingest) with **one new ingest phase** (`_community_writeback`, MODE=full/rebuild only —
  delta does not recompute) and **one narrow IaC grant** (Converse on the ingest task
  role). The existing graph/vector/hybrid/self-query/governed/text2cypher/parent-child
  paths are untouched. Rollback is reverting the PR (and dropping the `Community` nodes /
  `communityId` props).
- **Infrastructure:** **no new resource.** `Community` nodes ride the existing Neptune
  cluster; summaries are on-demand Bedrock Converse calls during ingest (not standing
  cost). The one change is `bedrock:Converse` added to the ingest task role (the existing
  `_bedrock_synthesis_invoke()` helper, scoped to the synthesis model). The query Lambda's
  read-only Neptune grant is unchanged (ADR-0004). Budgets unchanged at `150` (AC8).
- **External-system integration:** Bedrock Claude (Converse) — summaries from the ingest
  task, map-reduce from the query Lambda — and Neptune `Community` node read/write; all on
  already-provisioned services. **`networkx`** is the one new (ingest-only) dependency,
  installed in the Fargate image + dev/test, absent from the Lambda.
- **Deployment sequencing:** none — a single PR. `Community` nodes are populated by the
  full-ingest write-back on a deploy of this branch. **Delta community sync is out of
  scope** — communities are (re)built on full ingest / `--rebuild`; keeping summaries fresh
  under delta is a named future extension. The live smoke (AC10/T10) runs against a deploy
  of this branch if AWS is available, else defers.

## Risks

- **`networkx` leaks into the query Lambda import graph** (breaks the Lambda bundle /
  bloats it). Mitigation: lazy import in `community_detect` only; the extended
  `sys.modules` guard test blocks `networkx` and imports the query modules (T7).
- **Louvain non-determinism** breaks reproducibility (charter principle 3). Mitigation:
  pinned `DEFAULT_SEED`; a two-run identical-partition test (T1).
- **Permission leak via a corpus-wide summary** blending tiers. Mitigation: gate the
  summary **whole** by its composed member tier, fail-closed, **before** the map — an
  above-clearance community never reaches the synthesizer or the trace (T2, T4); re-proven
  live by AC10 (persona compose).
- **Map-reduce cost** (one Converse call per community). Mitigation: bound to top-N by
  size; at demo scale the community count is small; named scale-out extension in the spec.
- **The ingest task lacked the Converse grant** (community summaries would fail live).
  Mitigation: the AC8 synth test asserts the grant is present and scoped (no wildcard).
- **AC10 live access** may be unavailable. Mitigation: the run-or-defer rule (T10) with an
  atomic backlog anchor, consistent with the metadata-filtering / parent-child precedent.
- **Sentinel-collision suppression** (a persisted summary embedding `NOT RELEVANT` silently
  drops its community across every query — LLM04→LLM01). Mitigation: drop on **stripped
  equality**, not substring `in`; a collision test (T4).
- **Reduce-step prompt injection** from persisted, untrusted-origin summaries. Mitigation:
  map + reduce ride community content as `messages` data (never `system`) + the defensive
  directive + bounded `maxTokens`; an injection-isolation test (T4); answer is display-only.
- **Delta-staleness down-classification leak** (a member visibility rise under delta leaves
  `Community.tier` stale-low). Mitigation: communities recompute on full/`--rebuild` only;
  a visibility-label change requires a full re-ingest (spec Never-do); delta-refresh deferred
  (`global-community-summary-delta-tier-refresh`) — an accepted teaching-demo residual.
- **Unbounded ingest summarization fan-out** on a large/pathological corpus (one Converse
  call per community). Mitigation: bounded at the locked demo corpus scale; named LLM10
  scale-out residual (ADR-0005).

## Changelog

- 2026-06-26: initial plan. Global Community Summary: Louvain via `networkx` **in the
  Fargate ingest task** (seeded), summaries via the existing `Synthesizer` seam written
  back as `Community` nodes (+ `communityId` on members) behind a new `CommunityStore`
  seam; a query-side bounded **map-reduce** (`global_query`, `NOT RELEVANT` sentinel)
  gated **whole** by each community's composed member tier (fail-closed); additive CLI
  verbs + Function-URL `mode: "global"`; `networkx` kept ingest-only / out of the Lambda;
  one IaC grant (Converse on the ingest task role), no new resource, Budgets `150`;
  AC10 run-or-defer. Decisions recorded in ADR-0005 (Louvain-not-Leiden,
  in-Fargate-not-Neptune-Analytics).
