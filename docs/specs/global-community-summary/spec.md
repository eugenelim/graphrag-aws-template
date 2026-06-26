# Spec: global-community-summary

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0005](../../adr/0005-community-detection-in-fargate-louvain.md) (community detection runs in the Fargate ingest task with **Louvain via networkx**, written back to the existing Neptune Database as `Community` nodes — **no Neptune Analytics / no standing service**; the algorithm choice + dependency are decided there), [Charter — Pattern coverage table, *Global Community Summary* row + the Louvain-vs-Leiden honesty note](../../CHARTER.md#pattern-coverage-against-the-graphragcom-catalog) (the coverage contract this slice ships; the slice **states which clustering algorithm it used**), [RFC-0001 feasibility note §1](../../rfc/0001-notes/aws-feasibility.md) (Neptune Analytics ships Louvain not Leiden, and is **avoidable** — compute in Fargate, write back), [ADR-0001](../../adr/0001-hybrid-orchestration-seed-and-expand.md) (reuses the `Synthesizer` seam + retrieval-trace posture), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (rides the existing on-demand Fargate task, the existing Neptune cluster, and the in-VPC query Lambda behind the IAM-auth Function URL; teardown-first; adds **no** billable resource), [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python), [ADR-0004](../../adr/0004-text2cypher-read-only-guard.md) (the query Lambda's read-only Neptune grant already permits reading `Community` nodes — no query-side grant change)
- **Brief:** [`docs/product/briefs/graphrag-pattern-catalog.md`](../../product/briefs/graphrag-pattern-catalog.md)
- **Contract:** none (new internal Python interfaces — a `community_detect` module, a `globalsearch` orchestrator, and a `CommunityStore` seam — plus an additive `mode: "global"` value on the existing in-VPC Function URL; no repo-root `contracts/` API surface, consistent with the sibling pattern slices)
- **Shape:** mixed

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

> The **Global Community Summary** pattern from the [graphrag.com](https://graphrag.com)
> catalog (Microsoft GraphRAG's *global* search), implemented on AWS — **detect
> communities over the resolved entity graph, generate one LLM summary per community,
> store the summaries in Neptune, and answer corpus-wide "summarize across everything"
> questions by map-reducing over those summaries.** The seed-and-expand hybrid
> (ADR-0001) answers *local* questions — it needs a seed entity to expand from. A
> question like *"what are the major themes across all the SIGs and KEPs?"* has **no
> seed**: the answer is the shape of the **whole corpus**, not a neighborhood. Global
> Community Summary serves exactly that class. Community detection runs **Louvain (via
> `networkx`) in the on-demand Fargate ingest task** — **not** a standing Neptune
> Analytics service (ADR-0005) — and writes `communityId` + Bedrock-generated
> summaries back into the **existing** Neptune Database as `Community` nodes. The slice
> **states its algorithm is Louvain, not Leiden** (the charter honesty note): Louvain
> matches the managed alternative (Neptune Analytics) so the self-compute-vs-managed
> trade is apples-to-apples, and the divergence from Microsoft GraphRAG's Leiden is
> named, not hidden. `Depends on:` the graph slice
> ([`graph-ingestion-resolution`](../graph-ingestion-resolution/spec.md)) for the
> resolved entity graph + the `GraphStore` seam, the hybrid slice
> ([`hybrid-orchestration`](../hybrid-orchestration/spec.md)) for the `Synthesizer`
> seam + the in-VPC query Lambda + IAM-auth Function URL, and the permission slice
> ([`permission-filtered-retrieval`](../permission-filtered-retrieval/spec.md)) for the
> `visibility`/`Clearance` model the corpus-wide summaries are gated by. It ships as an
> **additive new retrieval mode** (`mode: "global"`) + a new ingest phase + a new
> `Community` node label — the existing graph, vector, hybrid, self-query, governed,
> text2cypher, and parent-child modes are untouched.

## Objective

A solution architect evaluating GraphRAG needs to *see* the **Global Community
Summary** pattern: a user asks a **corpus-wide** question that has no entity to seed a
graph hop and no single passage to retrieve — *"what are the major areas of work
across all the Kubernetes SIGs, and how do they relate?"* — and the system answers it
from a **map-reduce over per-community summaries** rather than from local retrieval.
This is the question class the seed-and-expand hybrid structurally cannot serve (no
seed). The slice delivers it on the same stores and query path as the other modes, and
**states plainly that it detected communities with Louvain, not Leiden**, with the
managed alternative (Neptune Analytics) named and deliberately not adopted (ADR-0005).

The load-bearing engineering points are three. **First, where detection computes:**
Louvain runs **in the on-demand Fargate ingest task** (via `networkx`, seeded for
reproducibility), reading the resolved entity graph back from the `GraphStore`, and
writes results into the **existing** Neptune Database as `Community` nodes — **no
Neptune Analytics service, no standing service of any kind** is provisioned, so the
teardown-first cost posture (ADR-0002, charter principle 4) holds. **Second, the query
algorithm:** corpus-wide answers are produced by a bounded **map-reduce** — a *map*
step asks the synthesizer, per community, what that community contributes to the
question (a community that contributes nothing is dropped), and a *reduce* step
combines the surviving partials into a final grounded answer — the recognizable
Microsoft GraphRAG *global* shape, reusing the existing `Synthesizer` seam unchanged.
**Third, the permission boundary:** a community summary is generated over **all** its
member entities and can therefore **blend visibility tiers**, so it is a real leak
vector — each community is tagged at ingest with its **composed (most-restrictive)
member tier** (`compose(*member_tiers)`), and at query time a summary is served **only
if the persona's clearance dominates that tier** (`tier ∈ clearance.allowed`),
**fail-closed** — a community that blends a restricted entity is omitted entirely for a
lower-clearance persona, never partially leaked.

The whole path is **narratable** (charter principle 1): the global-search result
carries a **trace** naming the communities considered (id, tier, size), each
community's map verdict (contributed / not relevant), and the reduced answer with its
community + document citations — so the corpus-wide answer is never a black-box hop.
The whole path runs **offline by default** (the in-memory `GraphStore` + the in-memory
`CommunityStore` + the deterministic offline `TemplateSynthesizer`, all labeled
non-semantic) for credential-free CI and a laptop demo, and **live** against the
deployed Neptune cluster + Bedrock through the existing query Lambda.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off before
proceeding; *Never do* is a hard rule, even under time pressure.

### Always do

- **Compute communities in the Fargate ingest task, never a standing service.** Louvain
  runs in-process (via `networkx`) on the entity graph read back from the `GraphStore`
  (`all_nodes()` / `all_edges()`); results are written into the **existing** Neptune
  Database. No Neptune Analytics graph, no second cluster, no standing compute is
  provisioned (ADR-0005, ADR-0002).
- **Use Louvain, seeded, and say so.** The clustering algorithm is **Louvain** (not
  Leiden), run with a **pinned random seed** so the partition reproduces across runs on
  the locked corpus (charter principle 3). The slice's docs + showcase **state the
  algorithm is Louvain** and name Leiden as the Microsoft-GraphRAG divergence (charter
  honesty note).
- **Generate one summary per community via the existing `Synthesizer` seam.** Each
  community's summary is generated from its **member entities and the relationships
  among them** (the community subgraph) through `BedrockClaudeSynthesizer` (live) /
  `TemplateSynthesizer` (offline) — the same untrusted-data-in-`messages`, bounded
  `maxTokens`, default-TLS-client posture as every other synthesis path. No new model
  client, no `anthropic` SDK.
- **Write summaries back as `Community` nodes; stamp `communityId` on members.** Each
  community is a `Community`-labeled Neptune node carrying `id`, `title`, `summary`,
  `entity_ids` (membership), the composed `tier`, and `size`; each member `Entity` node
  is stamped with its `communityId`. Both derive from the **one** Louvain partition in
  the **one** detection pass, so they cannot disagree.
- **Tag each community with its composed (most-restrictive) member tier and gate the
  summary by it.** A community's `tier = compose(*member visibilities)`, where each
  member's visibility is read with the **same expression** the graph path uses —
  `node.props.get("visibility", DEFAULT_VISIBILITY)`, importing **only**
  `DEFAULT_VISIBILITY`/`compose` from the **pure** `visibility` module (never from
  `hybrid`, whose import surface would defeat the pure ingest-side detection module's
  networkx-isolation) — so an **unlabeled or unknown** member tier composes as **`public`**
  — the deliberate teaching default (`visibility.py` `rank`), named here because for a
  corpus-wide summary it is a *down*-classification leak vector if left unstated. At query time a community
  summary is served **only if** `tier ∈ clearance.allowed`. `clearance=None`
  ⇒ unrestricted (the teaching default behind the IAM-auth ingress); an **empty**
  `Clearance.allowed` ⇒ **zero** communities (fail-closed) — exactly the slice-4
  `None`-vs-empty semantics.
- **Answer with a bounded map-reduce that returns a trace.** `global_query` filters
  communities by clearance, runs the *map* step per in-clearance community (bounded to
  the top-N by size), drops communities the map marks not-relevant, runs the *reduce*
  over the survivors, and returns a `GlobalSearchResult` whose `.render()` narrates, in
  order, **question → communities considered (id, tier, size) → per-community map
  verdict → reduced answer + citations**.
- **Pair every store and synthesizer with an offline equivalent.** Detection runs over
  the in-memory `GraphStore` offline (networkx is in the dev env); the in-memory
  `CommunityStore` mirrors the Neptune one (same `Community` records, same clearance
  predicate); the offline path uses `TemplateSynthesizer` — so the offline backend
  returns the same community set + the same clearance-gated result shape (the slice-4
  backend-identical invariant, `packages/graphrag/AGENTS.md`).
- **Keep `networkx` out of the query Lambda import graph.** Detection imports
  `networkx` **lazily** on the ingest path only; the query-side `globalsearch` +
  `store/community_*` modules import **no `networkx`** and **no `yaml`** (the
  PyYAML-free Lambda discipline, extended to networkx). The existing `sys.modules`
  guard test is extended.
- **Reuse the existing query Lambda + Function URL for the live path.** Global search is
  dispatched by an **additive, backward-compatible** `mode: "global"` value on the
  existing IAM-auth Function URL — no new endpoint, no new ingress. The Lambda builds a
  **read-only** `NeptuneCommunityStore` (reads `Community` nodes — the existing
  read-only Neptune grant per ADR-0004 suffices) + the synthesizer; it **detects
  nothing** and builds no networkx.
- **Keep teardown a feature** (charter principle 4): `Community` nodes ride the
  **existing** Neptune cluster; summaries are on-demand Bedrock calls during ingest, not
  standing cost; the slice adds **no** billable resource and Budgets stays at the
  literal `150`.

### Ask first

- **Adding any runtime dependency beyond `networkx` (the ADR-0005 ingest-only
  dependency) + the existing `pyyaml` + `boto3`.** `networkx` is the one new dependency,
  scoped to the `ingest` extra and recorded in `packages/graphrag/AGENTS.md`; reach for
  any other library (e.g. `leidenalg`/`python-igraph`) only with sign-off.
- **Changing the clustering algorithm away from Louvain** (e.g. to Leiden via
  `leidenalg`), the **pinned seed**, or the single-flat-partition choice (vs. the
  Louvain level hierarchy) — these are teaching-surface + reproducibility decisions
  (ADR-0005), not implementation details.
- **Pinning or changing the synthesis model id away from the default**, or changing the
  community-summary input (member entities + their relationships) to include full
  document bodies (a cost/scale decision).
- **Changing the Function-URL request/response contract beyond the additive
  `mode: "global"` value, or the global-search result/trace schema once a consumer
  depends on it.**

### Never do

- **Never provision Neptune Analytics or any standing service for community detection.**
  The verified, charter-aligned mechanism is in-Fargate Louvain written back to the
  existing cluster (ADR-0005, RFC-0001 §1); a standing analytics service is the
  explicitly-rejected non-mechanism (it breaks teardown-first / bounded idle cost).
- **Never serve a community summary to a persona whose clearance does not dominate the
  community's composed tier.** The summary blends all members; gating it by the
  most-restrictive member tier is the leak guard. A summary is served whole or not at
  all — **never** partially redacted, **never** widened past clearance. Fail-closed:
  empty clearance ⇒ zero communities.
- **Never let the global-search query (or its trace) emit a community summary, member
  list, `title`, or citation for an above-clearance community.** Clearance filters the
  community set **before** the map step, so an above-clearance community never reaches the
  synthesizer, the `communities_considered` list (incl. its member-derived `title`), the
  map verdicts, or the citations. Because `title` and the document citations are derived
  **only from member entities** — all of which are within clearance once the composed-tier
  gate passes (correct composition per *Always do*) — the citation set is always a
  **subset of in-clearance member documents**; it never exceeds the gate.
- **Never source the global answer's citations from a synthetic chunk's invented
  provenance.** The map/reduce wraps each community summary as a synthesis-context
  `VectorHit` for the *prompt only*; the `GlobalSearchResult.citations` are composed in
  `global_query` itself = the surviving community ids (`community:<id>`) + the deduped
  member-document `doc_paths` (from the members' `Node.doc_paths`) — **not** taken from the
  synthesizer's `_citations` over a fabricated chunk. The synthesized `.answer` is used;
  its chunk-derived `.citations` are discarded for global mode.
- **Never run community detection (`networkx`) on the query path or in the query
  Lambda.** Detection is ingest-only; the query path reads pre-computed `Community`
  nodes. The `sys.modules` guard proves `networkx` stays out of the Lambda import graph.
- **Never treat a `Community.tier` as fresh after a delta re-ingest that changed member
  visibility.** Communities are detected + summarized + tier-tagged on **full ingest /
  `--rebuild` only** (delta does not recompute them). A delta that *raises* a member's
  visibility (`public` → `restricted`) leaves the persisted `Community.tier` stale-low **and**
  the summary already generated over the then-lower-tier member — a **down-classification
  leak** the query-time gate cannot catch. So a **visibility-label change requires a full
  re-ingest** (`MODE=full` / `--rebuild`) to refresh community tiers + summaries; this is
  stated in the spec + the explanation doc, with the delta-refresh path named as a deferred
  residual (`global-community-summary-delta-tier-refresh`), consistent with how slice-4
  documents its fail-open teaching defaults (synthetic labels are not real authz, charter
  principle 5).
- **Never string-interpolate a community id, entity id, or filter value into an
  openCypher query string** — always the parameter map (the `neptune.py`
  parameterization posture; `ruff` `S` ruleset stays enabled). The `Community` node
  label / `MEMBER` are fixed constants, never interpolated.
- **Never claim a semantic quality result from the offline path.** The
  `TemplateSynthesizer` summaries + map/reduce are deterministic and **non-semantic**;
  the CLI labels them as such (as the vector/hybrid/self-query/parent-child offline
  paths do). The honest semantic claim is the live Bedrock path (AC9).
- **Never add a new top-level directory or module boundary** beyond the existing
  `packages/graphrag/`, `apps/ingestion/`, `apps/infra/`, `docs/guides/` surfaces
  (AGENTS.md: top-level directories need an RFC). New code lands as modules inside those
  (the new store family is `packages/graphrag/src/graphrag/store/community_*`).
- **Never expose a public, unauthenticated endpoint or weaken the Function URL below
  IAM-auth.**

## Testing Strategy

The mix targets the test pyramid (≈80% unit). Verification mode per criterion:

- **AC1 — TDD.** Pure / deterministic functions over the community data model:
  `detect_communities(nodes, edges, *, seed)` builds an undirected `networkx` graph and
  returns a stable Louvain partition (`CommunitySpec` value types: member ids, size,
  composed tier); a pinned seed yields the **identical** partition across two runs;
  isolated nodes form singleton communities; the composed tier is `compose(*member
  tiers)`. No store, no network. `import graphrag.community_detect` is the **only**
  module that pulls in `networkx`.
- **AC2 — TDD + goal-based mapping check.** `Community` value type + the `CommunityStore`
  seam: a static check asserts the `NeptuneCommunityStore` writes a `Community`-labeled
  node with `id`/`title`/`summary`/`entity_ids`/`tier`/`size` and stamps member
  `Entity.communityId`, all via the **parameter map** (asserted against a mock HTTP
  client — no interpolation); `all_communities(allowed_labels)` reads them back and
  applies the clearance predicate (`tier ∈ allowed`; `None` ⇒ all; empty ⇒ none); the
  in-memory `CommunityStore` returns the **same** clearance-gated community set
  (backend-identical).
- **AC3 — TDD.** Summary generation: `summarize_communities(specs, graph, synthesizer)`
  builds, per community, the member-entities + intra-community-relationships input and
  calls `synthesizer.synthesize` (asserted via a spy — the **member subgraph** is the
  context, the summary text is the result); the offline `TemplateSynthesizer` yields a
  deterministic non-semantic summary; a community's `tier` on the resulting `Community`
  equals `compose(*member tiers)`.
- **AC4 — TDD + narratability check.** `global_query(question, *, community_store,
  synthesizer, clearance=None, top_n)` filters communities by clearance, runs the
  per-community *map* (a community whose map result carries the `NOT RELEVANT` sentinel
  is dropped), *reduces* the survivors into the final answer, and returns a
  `GlobalSearchResult` whose `.render()` emits, in order, **question → communities
  considered (id, tier, size) → per-community map verdict → reduced answer + citations**.
  With a clearance excluding a tier, those communities are **absent from the map, the
  reduce, and the trace**; an empty `Clearance.allowed` ⇒ zero communities (fail-closed).
- **AC5 — TDD.** Ingest writes communities from the full-ingest path: after the graph is
  written (`MODE=full` / `--rebuild`), `_community_writeback` reads the graph back,
  detects (Louvain, seeded), summarizes (one Converse call per community — asserted via a
  counting/spy synthesizer), writes `Community` nodes + stamps `communityId`, and is a
  **no-op** when neither an injected `CommunityStore` nor `NEPTUNE_ENDPOINT` is present
  (a vector-only deploy is unchanged); **delta** re-ingest does **not**
  recompute communities (scoped out — asserted).
- **AC6 — TDD.** CLI verb `global-query`: runs **offline** by default (in-memory graph +
  in-memory community store built by detecting+summarizing the fixture corpus with
  `TemplateSynthesizer`) and prints the ordered trace, labeling the synthesizer
  **non-semantic**; `--bedrock` switches to `BedrockClaudeSynthesizer`; `--function-url`
  builds a SigV4 POST whose **body** carries `mode: "global"` (persona rides the body);
  `--persona` resolves a clearance fail-closed. A companion `detect-communities` verb (or
  a `--show-communities` flag) prints the detected partition + per-community summaries
  offline for the demo.
- **AC7 — TDD with mock.** In-VPC query Lambda `mode: "global"` dispatch: with the
  community store + synthesizer mocked, `lambda_handler` runs the path end-to-end and
  returns the trace envelope; an unknown `mode` is a client error; the over-long-question
  guard and the generic sanitized error envelope apply as for hybrid; the optional
  persona resolves a clearance fail-closed; a `sys.modules` assertion proves the
  global-search import graph stays **PyYAML-free and networkx-free**; the Lambda builds a
  **read-only** community store and **detects nothing**.
- **AC8 — goal-based (`cdk synth` + `aws_cdk.assertions.Template`), CDK-env-gated.** The
  IaC delta is exactly the **ingest task role** gaining `bedrock:Converse` scoped to the
  synthesis model (the existing `_bedrock_synthesis_invoke()` helper) with **no wildcard
  `Resource`**; the synthesized stack adds **no** new resource (no Neptune Analytics, no
  second cluster), the **query-Lambda Neptune grant is unchanged** (still read-only per
  ADR-0004), no other role's grant is widened, and the Budgets value is asserted
  **unchanged at the literal `150`**.
- **AC9 — goal-based (global-search showcase set + explanation doc).** A
  `global_queries` section holds **≥3** corpus-wide queries, each labeled with the
  expected contributing communities and the corpus-wide theme it surfaces; a loader/test
  asserts it parses and every named entity/community resolves in the fixture corpus after
  detection. A doc under `docs/guides/` walks the global path and states the contrast
  (corpus-wide map-reduce over community summaries vs. local seed-and-expand) **and the
  Louvain-not-Leiden divergence** + the in-Fargate-not-Neptune-Analytics compute choice.
- **AC10 — live deploy + global smoke (active end-to-end).** Against the deployed stack
  (corpus ingested so the Fargate task detected communities and wrote `Community` nodes
  with live Bedrock summaries), a SigV4-signed `mode: global` call map-reduces over the
  live community summaries and returns the trace (communities considered + per-community
  map verdict + reduced answer with citations) — **proving no standing service was
  needed** (detection happened in the transient ingest task). Given the labeled corpus
  yields **≥1 above-`public` community** (guaranteed by the AC9 showcase set), a second
  call with a **`public-reader` persona** asserts the live community set is a **strict
  subset** of the default call's set — the above-clearance community is omitted (the
  composed-tier gate, fail-closed) — not merely an equal set. Then the stack is destroyed
  (teardown-first).

Gates: `ruff` (lint+format, `S` security ruleset), `mypy` (typecheck), `pytest`
(tests). Already wired into `tools/hooks/pre-pr.py`.

## Acceptance Criteria

- [ ] **AC1 — Community detection: Louvain in-process, seeded, pure (`networkx`-isolated).**
  A `graphrag.community_detect` module provides `detect_communities(nodes: list[Node],
  edges: list[Edge], *, seed: int = DEFAULT_SEED) -> list[CommunitySpec]` that builds an
  **undirected** `networkx` graph from the entity nodes + edges and partitions it with
  **Louvain** (`networkx.algorithms.community.louvain_communities`, the pinned `seed`),
  returning `CommunitySpec(id, entity_ids, size, tier)` where `tier =
  compose(*member visibilities)` and each member's visibility is read with the same
  expression the graph path uses — `node.props.get("visibility", DEFAULT_VISIBILITY)`,
  importing only `DEFAULT_VISIBILITY`/`compose` from the **pure** `visibility` module (not
  from `hybrid` — that would pull the query/vector surface into this pure module) — so a
  member with an **absent or unknown `visibility`** composes as **`public`** — the
  deliberate teaching default, asserted by an edge-case test (an unlabeled member does not
  *raise* the tier; a `restricted` member *does*). The **same seed** yields the
  **identical** partition across two runs (reproducibility, charter principle 3); an
  isolated node is its own singleton community. `community_detect` is the **only** module
  that imports `networkx`, and it imports it **lazily** (so importing the package does not
  pull it in). *(TDD)*
- [ ] **AC2 — `Community` node + `CommunityStore` seam (write-back to the existing
  cluster, clearance-gated read).** A `graphrag.store.community_base` module declares
  `Community(id, title, summary, entity_ids, tier, size)` and the `CommunityStore` ABC
  (`create()`, `upsert_community(c)`, `set_community_id(entity_id, community_id)`,
  `all_communities(*, allowed_labels: frozenset[str] | None = None) -> list[Community]`,
  `count()`, `clear()`); `NeptuneCommunityStore` writes a `Community`-labeled node + the
  member `Entity.communityId` stamp via the **parameter map** (never interpolated; the
  label/`MEMBER` are fixed constants) and reads `all_communities` back applying the
  clearance predicate (`tier ∈ allowed_labels`; `None` ⇒ all, **empty set** ⇒ none —
  fail-closed); `MemoryCommunityStore` mirrors it (same records, same predicate). Offline
  the suite pins the in-memory store's records + predicate and the Neptune adapter's
  request body + parse against a mock HTTP client; full read-back parity is the live AC10
  check. *(TDD + goal-based mapping check)*
- [ ] **AC3 — Per-community summaries via the existing `Synthesizer` seam.**
  `summarize_communities(specs, graph, synthesizer) -> list[Community]` builds, for each
  `CommunitySpec`, the **member entities + the relationships among them** (the community
  subgraph) as the synthesis input and calls `synthesizer.synthesize` once per community
  (asserted via a spy: the member subgraph is the context, **not** unrelated entities),
  producing a `Community` whose `summary` is the synthesized text, `title` is a stable
  label, and `tier` is `compose(*member tiers)`. Offline `TemplateSynthesizer` yields a
  deterministic non-semantic summary. Ingest summarization is **one Converse call per
  detected community**; at the locked demo corpus scale the community count and each
  member-subgraph are small (bounded ingest fan-out + bounded per-community prompt) — an
  unbounded large-corpus fan-out (many singleton communities, or a giant community's prompt)
  is the named scale-out residual (LLM10, ADR-0005; not built here). *(TDD)*
- [ ] **AC4 — Global map-reduce orchestration with a clearance-gated trace.**
  `global_query(question, *, community_store, synthesizer, clearance=None, top_n=DEFAULT_TOP_N)`
  reads `community_store.all_communities(allowed_labels=clearance.allowed if clearance
  else None)`, runs the **map** per community (top-N by size) — `synthesizer.synthesize`
  asked what that community contributes to the question; a community is dropped **only when
  its map answer, stripped, *equals* the `NOT RELEVANT` sentinel** (an exact sole-token
  match, **not** a substring `in` check — so a persisted summary that merely *embeds* the
  literal string still participates; a test pins a summary containing `NOT RELEVANT` as
  still mapped, LLM04→LLM01 sentinel-collision robustness) — then the **reduce** over the
  survivors into a final answer. **Both the map and the reduce Converse calls place all
  community-derived content (summaries, partials) in Converse `messages` as data, never the
  `system` block**, carrying the existing defensive directive + bounded `maxTokens`
  (`synthesize.py` posture); a test asserts an instruction injected into a community summary
  does not alter the reduce's structure (LLM01). The call returns a `GlobalSearchResult`
  whose `.render()` narrates **question →
  communities considered (id, tier, size) → per-community map verdict → reduced answer +
  citations**. The `citations` are composed **in `global_query`** = the surviving community
  ids (`community:<id>`) + the deduped member-document `doc_paths` (from the members'
  `Node.doc_paths`) — **never** the synthesizer's chunk-derived citations over a synthetic
  context chunk; a test asserts they carry real member `doc_paths` + community ids with **no
  synthetic-chunk provenance**, and that the citation set is a **subset of the served
  (in-clearance) communities' member documents** (never exceeds the gate). With a
  `clearance` excluding a tier, those communities are **absent from
  `communities_considered` (including their member-derived `title`), the map, the reduce,
  and the trace**; an empty `Clearance.allowed` ⇒ zero communities (fail-closed survives
  the orchestrator). *(TDD + narratability check)*
- [ ] **AC5 — Ingest detects + summarizes + writes back on full ingest, embed-pass
  untouched, delta scoped out.** The full-ingest Fargate path
  (`apps/ingestion/entrypoint.py`), when a `CommunityStore` is injected (tests) **or
  `NEPTUNE_ENDPOINT` is set** (deploy — the live trigger, mirroring how
  `_vector_dual_write` keys off `OPENSEARCH_ENDPOINT`/an injected store; on deploy the
  community store resolves to `NeptuneCommunityStore` and the synthesizer to
  `BedrockClaudeSynthesizer`), after the graph write reads the graph back,
  `detect_communities` (Louvain,
  seeded), `summarize_communities` (one synthesizer call per community — asserted via a
  counting/spy synthesizer), `upsert_community` each + stamps `communityId`. It is a
  **no-op** when neither an injected community store nor `NEPTUNE_ENDPOINT` is present (a
  vector-only deploy is unchanged). **`MODE=delta` does not recompute
  communities** (scoped out — asserted; full / `--rebuild` rebuild them); the existing
  graph + vector dual-write behavior is unchanged. *(TDD)*
- [ ] **AC6 — CLI verb `global-query`, offline by default, live via SigV4.**
  `graphrag global-query --q "<text>"` runs **offline** (in-memory graph + in-memory
  community store built by detecting + summarizing the fixture corpus with
  `TemplateSynthesizer`) and prints the ordered trace, labeling the synthesizer
  **non-semantic**. `--bedrock` switches to `BedrockClaudeSynthesizer`. `--function-url
  <url>` switches to the thin live client — a SigV4-signed (`service=lambda`) HTTPS POST
  of `{"question": …, "mode": "global"}` (persona rides the body when set) — and renders
  the returned trace; a non-2xx raises with the body. A `detect-communities` verb (or
  `--show-communities`) prints the detected partition + per-community summaries offline.
  `--persona` resolves a clearance fail-closed. *(TDD)*
- [ ] **AC7 — In-VPC query Lambda global dispatch, PyYAML-free + networkx-free,
  sanitized, read-only.** `lambda_handler` reads the optional `mode` and on `"global"`
  builds the live **read-only** `NeptuneCommunityStore` + `BedrockClaudeSynthesizer` from
  the execution role, runs `global_query`, and returns the trace envelope (communities
  considered, per-community map verdict, reduced answer, citations, trace). Because global is
  a **clearance-filtering** mode, its branch is dispatched **after** the shared
  `resolve_clearance` block the other filtering modes (`selfquery`/`parentchild`) use — an
  **unknown persona is a client error before any community read**, and a supplied persona
  can never silently resolve to `clearance=None` (unrestricted) for a global call (no
  fail-open ordering regression). An **unknown mode** is a client error;
  the **over-long-question** guard and the **generic sanitized error envelope**
  (correlation id, no internal endpoint/ARN/stack detail) apply exactly as for hybrid.
  The `_extract_mode` docstring's mode list is extended to include `"global"` (the additive
  back-compat field). The global-search import graph stays **PyYAML-free and networkx-free**
  (the existing `sys.modules` guard is extended to `globalsearch` + `store.community_neptune`);
  the Lambda **detects nothing**. Exercised with the store + synthesizer **mocked** (no
  network); reuses the **same** `global_query` the CLI uses. *(TDD with mock; live in
  AC10)*
- [ ] **AC8 — IaC: one scoped grant added, no new resource, no widened query grant, cost
  held.** The only stack change is the **ingest task role** gaining `bedrock:Converse`
  scoped to the synthesis model (via the existing `_bedrock_synthesis_invoke()` helper)
  with **no wildcard `Resource`** — so the Fargate task can generate summaries. A synth
  assertion confirms `cdk synth` adds **no** new resource (no Neptune Analytics graph, no
  second cluster), the **query-Lambda Neptune grant is unchanged** (still read-only per
  ADR-0004), **no other role's grant is widened**, and the Budgets value is asserted
  **unchanged at the literal `150`**. Per ADR-0002 / ADR-0005. *(goal-based synth,
  CDK-env-gated)*
- [ ] **AC9 — Global-search showcase set + the global teaching framing (Louvain-not-Leiden
  stated).** A `global_queries` section in the showcase `queries.yaml` holds **≥3**
  corpus-wide queries, each labeled with the expected contributing communities and the
  corpus-wide theme it surfaces; a loader/test asserts it parses and every named
  entity/community resolves in the fixture corpus after detection. A doc under
  `docs/guides/` walks the global path with the exact CLI commands and **states the
  contrast** — corpus-wide map-reduce over community summaries vs. local seed-and-expand
  — **and the two honest divergences**: the clustering algorithm is **Louvain, not
  Leiden** (Microsoft GraphRAG uses Leiden; Neptune Analytics ships Louvain — see the
  charter note), and detection runs **in the Fargate ingest task, not a standing Neptune
  Analytics service** (ADR-0005). *(goal-based)*
- [ ] **AC10 — Live deploy + global smoke (in-VPC).** Against the deployed stack with the
  corpus ingested (the Fargate task ran Louvain in-process and wrote `Community` nodes
  with live Bedrock summaries — **no standing service stood up**), a SigV4-signed
  `mode: global` call map-reduces over the live community summaries and returns the trace
  (communities considered + per-community map verdict + reduced answer with community +
  document citations). Given the labeled corpus yields **≥1 above-`public` community**
  (guaranteed by the AC9 showcase set), a second call with a **`public-reader` persona**
  asserts the live community set is a **strict subset** of the default call's set — the
  above-clearance community is omitted (the composed-tier gate composes with the clearance,
  fail-closed) — not merely an equal set. Then the stack is destroyed (teardown-first). Run
  when AWS access is available (live deploy is available in this environment), else deferred
  with a backlog anchor created atomically. *(live smoke)*

## Assumptions

- Technical: clustering computes **in the Fargate ingest task**, **not** a standing
  Neptune Analytics service; Neptune Analytics ships **Louvain** (not Leiden) and the
  managed service is **avoidable** by computing in Fargate and writing back (source:
  ADR-0005; `docs/rfc/0001-notes/aws-feasibility.md` §1).
- Technical: Louvain is available via `networkx.algorithms.community.louvain_communities`
  on `networkx` 3.x; `networkx` is added as an **optional `ingest` dependency** (pure
  Python, no C-extension), installed in the Fargate image + the dev/test env, and kept
  **out of the query Lambda import graph** (source: probe `python -c "import networkx;
  from networkx.algorithms.community import louvain_communities"` → "networkx 3.6.1
  louvain_communities OK", 2026-06-26; ADR-0005).
- Technical: the resolved entity graph (nodes + edges, with slice-4 `visibility` on node
  props) is readable from any `GraphStore` via `all_nodes()` / `all_edges()` on both the
  Neptune and in-memory backends, so detection runs identically live and offline (source:
  `store/base.py`; `store/memory.py`; `store/neptune.py`; `labels.py`).
- Technical: summaries reuse the existing `Synthesizer` seam (`BedrockClaudeSynthesizer`
  Converse / offline `TemplateSynthesizer`) with the untrusted-data-in-`messages`,
  bounded-`maxTokens`, default-TLS-client posture — no new model client, no `anthropic`
  SDK (source: `synthesize.py`).
- Technical: the live query path **reuses the existing in-VPC query Lambda + IAM-auth
  Function URL**, dispatched by the additive back-compat `mode: "global"` field; the
  Lambda's **read-only** Neptune grant (ADR-0004) already permits reading `Community`
  nodes, so the slice adds **no query-side grant** and the Lambda detects nothing (source:
  `query_lambda.py` `_extract_mode` + dispatch; ADR-0004).
- Technical: the **ingest task role** currently holds `_bedrock_invoke()` (Titan embed)
  but **not** `_bedrock_synthesis_invoke()` (Converse); community summarization from
  Fargate requires adding the existing `_bedrock_synthesis_invoke()` grant to the task
  role — the slice's only IaC change; no new billable resource; Budgets unchanged at `150`
  (source: `apps/infra/stacks/graphrag_stack.py:185,425,440-465`; ADR-0002).
- Technical: a community's `tier = compose(*member visibilities)` and the query-time gate
  is `tier ∈ clearance.allowed` (`None` ⇒ unrestricted, empty ⇒ fail-closed) — the
  slice-4 `Visibility`/`compose`/`Clearance` model reused unchanged (source:
  `visibility.py`; user confirmation 2026-06-26).
- Technical: membership lives canonically on the `Community` node's `entity_ids`, with
  `communityId` additionally stamped on member `Entity` nodes (the literal "write
  communityId back" affordance + the entity→community trace); both derive from the **one**
  Louvain partition in the **one** detection pass, so they cannot disagree (source:
  ADR-0005; user confirmation 2026-06-26).
- Product: the audience is a solution architect evaluating the **Global Community
  Summary** pattern; the slice ends at community detection + per-community summaries +
  corpus-wide map-reduce + the trace + the contrast framing (corpus-wide vs. local) +
  the stated Louvain-not-Leiden + in-Fargate-not-Neptune-Analytics divergences (source:
  charter coverage table; brief Scope; user confirmation 2026-06-26).
- Product: corpus-wide community summaries can blend visibility tiers, so a summary is a
  leak vector and is gated **whole** by its composed member tier — served entirely or
  omitted entirely, never partially redacted (source: charter principle 5; user
  confirmation 2026-06-26).
- Product: global search is a bounded **map-reduce** (per-community map → reduce) reusing
  the existing synthesizer unchanged via a `NOT RELEVANT` sentinel for the map's
  relevance signal, bounded to top-N communities by size; the per-community numeric
  relevance score + the Louvain level hierarchy are named scale-out extensions, not built
  here (source: Microsoft GraphRAG global; user confirmation 2026-06-26).
- Security: the map sentinel is matched by **stripped equality** (not substring `in`), so a
  persisted summary that embeds the literal `NOT RELEVANT` cannot suppress its own community
  (LLM04→LLM01 sentinel-collision); both map + reduce ride community content as Converse
  `messages` data, never `system` (source: spec-stage security review 2026-06-26;
  `synthesize.py` posture).
- Security: ingest summarization is **one Converse call per community** and the per-community
  prompt is the member subgraph; at the locked demo corpus both are small, so the ingest
  fan-out + prompt size are bounded — an unbounded large-corpus fan-out is the named LLM10
  scale-out residual (source: spec-stage security review 2026-06-26; ADR-0005).
- Security: communities are detected + tier-tagged on **full ingest / `--rebuild` only**;
  a delta re-ingest does **not** refresh `Community.tier` or summaries, so a member
  visibility change under delta is a **down-classification staleness leak** — mitigated by
  requiring a full re-ingest after a visibility-label change and deferring delta-refresh
  (`global-community-summary-delta-tier-refresh`), an accepted teaching-demo residual
  (synthetic labels are not real authz) (source: spec-stage security review 2026-06-26;
  charter principle 5; slice-4 fail-open-default precedent in `architecture/security.md`).
- Process: no charter/CONVENTIONS edit beyond the **auto-derived** coverage-table /
  brief-Spec-map status flip (those are derived from this spec's `Status:` by the coverage
  lint, not hand-edited); the compute-location + algorithm choice is recorded in **ADR-0005**
  (source: `docs/CHARTER.md` coverage table comment; brief Spec map comment; ADR-0005).
- Process: full work-loop mode — new dependency (`networkx`) + structural (a new store
  family + a new ingest phase + a new query mode) + security boundary (an untrusted
  question routed to an LLM synthesizer; corpus-wide summaries crossing visibility tiers;
  the IAM-auth public Function URL); constrained by ADR-0005 + the charter coverage table
  + RFC-0001 §1 + ADR-0001/0002/0003/0004 (source: `docs/CONVENTIONS.md` risk triggers;
  brief Spec map row `global-community-summary`).
- Process: the live AC (AC10) is run when AWS access is available (live deploy is available
  in this environment), else deferred with a backlog anchor created atomically (source:
  user auto-memory `live-deploy-available`; the metadata-filtering / parent-child AC9
  precedent).

## Changelog

- 2026-06-26 — Spec authored. Global Community Summary pattern (Microsoft GraphRAG
  *global*): detect communities over the resolved entity graph with **Louvain via
  `networkx`, in the on-demand Fargate ingest task** (seeded for reproducibility) — **no
  Neptune Analytics / no standing service** (ADR-0005) — generate one Bedrock summary per
  community, write them back to the **existing** Neptune Database as `Community` nodes
  (+ `communityId` on members), and answer corpus-wide questions by a bounded map-reduce
  over the summaries through the existing `Synthesizer` seam. Corpus-wide summaries gated
  **whole** by their composed (most-restrictive) member tier, fail-closed (the slice-4
  clearance model). Additive `mode: "global"` on the existing query Lambda + a
  `global-query` CLI verb; `networkx` kept out of the Lambda import graph; offline via the
  in-memory stores + `TemplateSynthesizer`. The one IaC change adds `bedrock:Converse` to
  the ingest task role; no new billable resource; Budgets held at `150`. The slice states
  its algorithm is **Louvain, not Leiden** (charter honesty note).
