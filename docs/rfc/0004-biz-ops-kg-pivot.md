# RFC-0004: Business-operations knowledge-graph pivot

- **Status:** Accepted
- **Author:** eugenelim
- **Approver:** eugenelim
- **Date opened:** 2026-07-23
- **Date closed:** 2026-07-23
- **Decision weight:** heavy <!-- reverses frozen ADR-0001/0008, re-scopes the security-relevant ADR-0004, changes the charter mission, and re-ingesting existing graph data as RDF is a one-way door -->
- **Related:** [Intent `rfc-0004-biz-ops-kg-pivot`](../product/intents/rfc-0004-biz-ops-kg-pivot.md); [biz-ops architecture `design.md`](../architecture/biz-ops-knowledge-graph/design.md); [CHARTER](../CHARTER.md); [`security.md`](../architecture/security.md); reverses [ADR-0001](../adr/0001-hybrid-orchestration-seed-and-expand.md), [ADR-0008](../adr/0008-automatic-engine-routing-local-vs-global.md); re-scopes [ADR-0004](../adr/0004-text2cypher-read-only-guard.md) (openCypher anchoring superseded, read-only *control* carried forward); carries forward [ADR-0002](../adr/0002-ephemeral-vpc-store-topology.md), [ADR-0009](../adr/0009-access-control-synthetic-labels-not-real-authz.md) (revisit clause discharged, §Security posture); relates to [ADR-0005](../adr/0005-community-detection-in-fargate-louvain.md), [ADR-0007](../adr/0007-silver-cache-content-and-config-addressed.md) (mechanism supersession tracked in follow-ons, not this RFC); follow-on ADRs 0011–0016; [RFC-0003](0003-medallion-staging.md) (medallion precedent)

## Reviewer brief

- **Decision:** Approve pivoting the project from a Kubernetes GraphRAG *contrast* demo (openCypher over a labelled property graph, LPG) to a generic **business-operations knowledge platform** — typed RDF (data as subject–predicate–object triples), retrieval partitioned by knowledge type, queried through MCP (Model Context Protocol — the open standard for connecting LLMs to tools and data).
- **Recommended outcome:** Accept.
- **Change if accepted:**
  - Charter mission changes from "openCypher/Neptune implementation of the graphrag.com catalog" to a deployable-on-your-corpus, correctness-typed knowledge platform.
  - Graph store swaps engine (openCypher/property-graph → SPARQL/RDF, the W3C — World Wide Web Consortium — query language and data model for triples); retrieval splits into a **normative** (exhaustive-recall) and a **descriptive** (best-match) partition; the interface becomes an **MCP tool server**.
  - ADR-0001/0008 are reversed; ADR-0004's read-only query-role guard is **re-ratified** (as the Text2SPARQL guard — an LLM writing SPARQL from a question), only its openCypher anchoring superseded; the existing graph corpus is re-ingested as RDF.
- **Affected surface:** `docs/CHARTER.md`, `docs/adr/` (ADR-0001/0008 reversed, ADR-0004 re-scoped, six new), most `docs/specs/`, `packages/graphrag/` (graph store, retrieval, ingestion, new MCP server), `infra-tf/` (Neptune engine, MCP Lambda, API Gateway), `docs/architecture/security.md` (posture update).
- **Stakes:** One-way door for existing graph *data* (the two data models cannot share a cluster, so the swap is a full re-ingest, not a config flip). The *decisions* are reversible at fork cost.
- **Carried forward unchanged:** the teardown-first VPC (virtual private cloud) topology (ADR-0002); the read-only query-role guard (ADR-0004's *control*). **Carried forward with its revisit clause discharged:** the synthetic-labels-not-real-authz posture (ADR-0009 — see §Security posture; the pivot voids its public-corpus premise).
- **Review focus:** (1) Is retrieval-correctness-by-knowledge-type the right organising principle — and is the named-graph *partition* (a hard boundary) justified over cheaper query-shape routing? (2) Is the engine swap's cost (re-ingest, less-ergonomic query language, no path-discovery) worth the named-graph + W3C standard-stack payoff? (3) **Security posture (D7):** does the pivot correctly re-ratify the read-only guard, re-open the prompt-injection residual for a private/agent-consumed corpus, discharge ADR-0009's revisit clause, and contain the downgraded API-key ingress?
- **Not in scope:** real authorization/multi-tenancy (stays synthetic per ADR-0009); OWL reasoning; a non-MCP query interface (deferred, not rejected); ingestion sources beyond git (the seam stays pluggable — git-first, not git-only); production high availability (HA).

## The ask

- **Recommendation (BLUF — bottom line up front):** Accept the pivot. Replace the single-retrieval-semantic openCypher demo with a knowledge platform that **types every document as normative or descriptive and routes each type to the retrieval semantic its failure mode demands** — exhaustive recall for policies (a miss is a compliance gap), best-match precision for procedures (a miss is "I don't know") — enforced by RDF **named-graph** partitioning (a hard query-time isolation boundary, not a routing hint) and exposed through a single MCP tool surface for both AI agents and humans in AI IDEs.

- **Why now (Situation–Complication–Question):**
  - **Situation:** The project is a clone-and-deploy AWS reference that shows when graph-augmented retrieval beats plain vector search, built as an openCypher/Neptune contrast demo over two Kubernetes repos.
  - **Complication:** The demo answers "does a graph help?" but not the question enterprises actually block on: *"when I ground an LLM on my policies, am I getting **all** the ones that apply?"* A single retrieval semantic cannot answer that safely — vector similarity (the default retrieval path in RAG, retrieval-augmented generation) is built to return the *nearest* match, and approximate-nearest-neighbour recall is rarely 100% (see Evidence), so a differently-worded policy is silently dropped with no signal to the caller. The ratified product-strategy intent [`rfc-0004-biz-ops-kg-pivot`](../product/intents/rfc-0004-biz-ops-kg-pivot.md) reframes the mission around this correctness problem.
  - **Question:** Do we pivot the project — its mission, graph engine, retrieval model, and interface — to a generic business-operations knowledge platform organised around retrieval correctness by knowledge type?

- **Decisions requested:**

  | ID | Question | Recommendation | Why | Decide by | Reviewer action |
  | --- | --- | --- | --- | --- | --- |
  | D1 | Pivot the mission at all? | **Pivot** | The intent is ratified; "extend" cannot carry asymmetric retrieval semantics | This review | Confirm the mission change; approve reversing ADR-0001/0008 |
  | D2 | Graph engine & data model | **Neptune SPARQL/RDF** (over openCypher/LPG) | Named graphs are a first-class RDF primitive; unlocks the W3C stack (OWL/SHACL/PROV-O — ontology, validation, provenance); AWS recommends SPARQL named graphs for logical partitioning | This review | Rule on accepting the re-ingest + less-ergonomic-query cost (ADR-0011) |
  | D3 | Retrieval-correctness model | **Named-graph partition + asymmetric failure semantics** | Exhaustive recall for normative, best-match for descriptive; a partition cannot leak, a routing hint can | This review | Confirm partition over query-shape routing; accept the honesty constraint (below) |
  | D4 | Ontology formality | **OWL schema-only + SHACL, no reasoner** (Schema.org + SKOS — taxonomy standard — base) | Typed + validated + interoperable without reasoner operational cost | This review | Confirm no runtime reasoner (ADR-0012) |
  | D5 | Primary query interface | **MCP tool server** (+ IAM — identity and access management — Function URL for automation) | One typed surface for agents and humans in AI IDEs | This review | Confirm MCP as *the* interface for this RFC; others deferred, not rejected (ADR-0014) |
  | D6 | Ingestion change-detection | **Git commit-SHA delta + medallion** | Git as canonical source; established diff idiom; RFC-0003 medallion (staged Bronze/Silver/Gold refinement) precedent | This review | Confirm git-first (seam stays pluggable) (ADR-0016) |
  | D7 | Security posture under the pivot | **Re-ratify the read-only guard; re-open the prompt-injection residual; discharge ADR-0009's revisit clause; contain the API-key ingress** | The pivot voids the premises the old posture rested on (public corpus, display-only output) | This review | Confirm the four control decisions in §Security posture |

## Problem & goals

**Diagnosis (the problem, before any solution).** Grounding an LLM on organizational knowledge is not one retrieval problem — it is two, with opposite failure semantics, and today's demo treats them as one:

- A **normative** query — "what policies apply to generating infrastructure code?" — demands **exhaustive recall**: every applicable policy, standard, or guideline. Missing one is a compliance gap that the system creates *silently*, because it returns a plausible answer and gives the caller no signal that it is incomplete.
- A **descriptive** query — "what's the incident-response procedure?" — demands **precision**: the best-matching procedure. Missing an edge case is tolerable; the honest failure is "I don't know."

Vector similarity search is built for the second and unsafe for the first. It optimises for the *nearest* match, and its underlying approximate-nearest-neighbour (ANN) indexes trade recall for speed (see Evidence: vendor benchmarks put practical recall in the 0.5–0.98 band, not 1.0). A policy worded differently from the query — "records disposal" when the caller asked about "retention" — scores below the retrieval threshold and is dropped, with no error. **The system cannot tell the caller the answer is incomplete, because it does not know.** That is the forcing function.

The current architecture cannot express the distinction: one property graph, one hybrid retrieval path, one semantic. The fix is not "add more vector tuning" — it is to **type the knowledge and match the retrieval contract to the type**, and to make the boundary between types a *hard* one that cannot silently leak.

**Goals.**
1. An adopter can point the platform at *their own* business-operations corpus (policies, standards, SOPs, job aids, transcripts) and trust that normative queries are exhaustive and descriptive queries are best-fit — with the retrieval strategy for each named in a visible trace.
2. Retrieval correctness is guaranteed *structurally* (by partition), not *behaviourally* (by a router that can misfire).
3. The platform is queryable by both AI agents and humans in AI IDEs through one interface, without the caller knowing which retrieval path ran.
4. Every design choice remains explainable live (charter principle 1: no black-box hop) and deployable teardown-first in a single AWS account (ADR-0002 posture carried forward).

**Non-goals** (could-have-been-goals deliberately dropped):
- **Real authorization / multi-tenancy.** Visibility labels stay synthetic teaching stand-ins (ADR-0009 carried forward, revisit clause discharged in §Security posture). PII (personally identifiable information) handling is *flag-and-surface*, not enforced access control.
- **OWL reasoning / materialised inference.** The ontology is a schema/vocabulary only; validation is SHACL, not a reasoner.
- **A non-MCP query interface** (raw SPARQL endpoint, GraphQL). Deferred — open to add later, not decided or rejected here.
- **Ingestion sources beyond git.** The pipeline is git-commit-delta *first*; the ingestion seam stays pluggable for other source patterns as a future extension (consistent with the charter's existing pluggable-seam language) — git-first, not git-only.
- **Preserving the vector-vs-graph *contrast* demo.** The three-mode side-by-side race is intentionally retired; its teaching value is replaced (see below), not dropped.
- **Production HA / scale / latency tuning** beyond demoable (ADR-0002).

**The teaching reframe (why this is a better lesson, not just a different corpus).** The old story was a *performance* claim — "graph-augmented retrieval beats plain vector search." The new story is a *correctness* claim: **a single retrieval semantic cannot safely serve both compliance and operational queries.** Vector and graph are not competitors ranked against each other; they are assigned by the failure semantics the knowledge type demands:

| | Vector (OpenSearch k-NN — k-nearest-neighbour search) | Graph (Neptune SPARQL) |
|---|---|---|
| Semantic | Precision / best-fit (nearest match) | Exhaustive / structural (all in scope) |
| Right for | **Descriptive** — "the best procedure" | **Normative** — "all applicable policies" |
| A miss means | "I don't know" (tolerable) | a compliance gap (unacceptable) |
| Failure mode | graceful degrade | fail-closed |

The pivotal insight an architect takes away: **approximate-nearest-neighbour is a *feature* for descriptive knowledge and a *hazard* for normative knowledge.** The same "settle for close enough" property that makes vector search fast and forgiving for "which SOP fits this task" is exactly what silently drops a differently-worded policy. That is vector search working as designed, pointed at a problem it was never safe for — not a bug to tune away. This is why the platform types the knowledge and never mixes the two paths.

## Proposal

The design is specified in full in [`docs/architecture/biz-ops-knowledge-graph/design.md`](../architecture/biz-ops-knowledge-graph/design.md); this section states the load-bearing decisions a reviewer approves. Detail cascades into the follow-on ADRs named per decision.

### D1 — Pivot the mission

The charter mission changes from "*see, and reproduce, when graph-augmented retrieval beats plain vector search … an openCypher/Neptune implementation of the graphrag.com catalog*" to a generic, deployable **business-operations knowledge platform** whose organising principle is retrieval correctness by knowledge type. The charter (which mandates its own changes go through an RFC) is amended on acceptance. ADR-0001 (openCypher seed-and-expand hybrid as *the* orchestration) and ADR-0008 (automatic local-vs-global engine routing) are reversed because each is anchored to the openCypher/property-graph model this replaces. ADR-0004 (the read-only guard for LLM-authored queries) is *re-scoped*, not deleted — see §Security posture.

### D2 — Neptune SPARQL/RDF over openCypher/LPG

The graph store swaps its data model from a **labelled property graph** (LPG — nodes and edges carrying key/value properties, queried with openCypher, a query language for property graphs) to **RDF** (Resource Description Framework — data as subject–predicate–object *triples*, queried with SPARQL, the W3C query language for RDF). Both run on Amazon Neptune, but **not on the same data**: a single Neptune cluster can host either model, and the two are non-interoperable — you cannot query property-graph data with SPARQL or RDF data with openCypher ([AWS Neptune FAQ](https://aws.amazon.com/neptune/faqs/)). The swap is therefore a **full re-ingest** of the corpus as RDF, not a configuration change. This is the one-way door for existing *data*; the VPC topology, IAM auth, and subnet placement are unchanged (ADR-0002 carried forward).

Why RDF: **named graphs are a first-class RDF primitive** — Neptune associates every triple with a named graph and supports `FROM NAMED` / `GRAPH {}` scoping ([AWS: SPARQL standards compliance](https://docs.aws.amazon.com/neptune/latest/userguide/feature-sparql-compliance.html)). That primitive is the mechanism D3 rests on, and LPG has no equivalent. RDF also unlocks the W3C standard stack the rest of the design uses (OWL, SHACL, PROV-O). Recorded in **ADR-0011**.

**How the vector store relates after the swap.** The vector store (Amazon OpenSearch, using k-nearest-neighbour / k-NN search over Lucene HNSW — Hierarchical Navigable Small World — indexes) is *not* replaced — both stores stay and cooperate. They are stitched by one shared key, the RDF URI (uniform resource identifier): each OpenSearch chunk document is keyed by its `urn:chunk:…` URI, carries its parent `doc_uri`, and gains a **`named_graph` field**. That field is a **mandatory filter on every k-NN query**, so the normative/descriptive partition boundary is enforced *inside the vector store too*, not only in Neptune — a descriptive k-NN search structurally cannot return a normative chunk. The seed-and-expand mechanic survives from ADR-0001 (vector k-NN seeds chunk URIs → SPARQL property-paths expand from them); only the graph model underneath it changes.

### D3 — Named-graph partition with asymmetric failure semantics

All knowledge is partitioned into named graphs by retrieval semantics: `urn:graph:normative` (policies, standards, guidelines), `urn:graph:descriptive` (SOPs, job aids, transcripts), plus `taxonomy`, `ontology`, and `quarantine`. A SPARQL query scoped to one partition **cannot** touch another — the boundary is a hard constraint, not a routing hint. Retrieval strategy is then asymmetric:

- **Normative** (`get_policies`) → an exhaustive SPARQL `SELECT` over the normative partition, narrowed by a structured filter (domain + effective-date), **UNION** a vector threshold leg that can only *add* semantically-adjacent policies — it can never gate or drop one. Fail-closed: if the exhaustive leg is unavailable, `get_policies` hard-fails rather than return a partial set.
- **Descriptive** → top-k vector k-NN → SPARQL graph expand. Graceful degrade: a miss is "I don't know."

**Honesty constraint (written into the RFC at the Approver's direction).** Named-graph scoping guarantees two things and no more: (1) **partition isolation** — a normative query never leaks descriptive results or vice versa; (2) **no top-k drop within the partition scan** — the SPARQL `SELECT` returns *every* triple matching its filter, not a ranked top-k. It does **not** make the platform omniscient, and the guarantee is bounded in three honest ways:
- **Ingest-time classification** must be correct — a policy mis-typed as descriptive lands in the wrong partition and no normative query will find it.
- **Intra-partition attribute mismatch** — because `get_policies` narrows by a structured filter (domain + date), a policy that *is* correctly in the normative partition but tagged with a different domain than the query falls through the exhaustive leg to the vector-threshold leg — which retains ANN recall limits. This reproduces, at the attribute-tag layer, the same silent-gap failure mode the RFC condemns in vector search, and it is a genuine residual, not an eliminated risk.
- The claim about vector search is therefore precise: *vector similarity alone cannot **guarantee** exhaustive recall*, not "vector is bad." Vector is asymmetric — it *beats* lexical search for paraphrase/synonym queries and *loses* on exact-entity/out-of-vocabulary terms (see Evidence).

The platform **reduces** silent-gap risk — it does not eliminate it — and moves the residual from an untyped similarity threshold to an inspectable, structured classification + filter that a trace can expose. Recorded (with the partition data model) in **ADR-0012**; routing in **ADR-0013**.

### D4 — OWL schema-only + SHACL, no reasoner

Knowledge is typed by an OWL ontology (Web Ontology Language) used **as a vocabulary/schema only — no reasoning engine runs at query or ingest time.** Base classes anchor to Schema.org (`CreativeWork`, `DigitalDocument`) and SKOS (Simple Knowledge Organization System — the W3C standard for taxonomies/concept schemes; the domain/journey taxonomy is expressed as `skos:Concept` instances, addable at runtime without a schema change). The data contract — what a valid triple emission must contain — is enforced by **SHACL** (Shapes Constraint Language, the W3C standard for validating RDF graphs), run in-process with `pyshacl` before the Neptune load, with `inference="none"`. Provenance uses **PROV-O** (the W3C provenance ontology: `prov:Entity`, `prov:wasDerivedFrom`, `prov:wasGeneratedBy`). Recorded in **ADR-0012**.

### D5 — MCP tool server as the primary interface

The interface changes from a CLI over a query API to a **Model Context Protocol (MCP) tool server** — MCP being the open protocol (introduced by Anthropic, Nov 2024) for connecting LLM applications and agents to tools and data, natively supported by the AI IDEs the target users work in (Claude Code, Cursor, VS Code). Six generic typed tools (`ask`, `search`, `search_graph`, `get_policies`, `query`, `summarize`) cover the retrieval surface; the server is built on the official `mcp` Python SDK (`FastMCP`). Two ingresses, for two principal types: an **API-Gateway HTTP API with an API key** for humans/IDEs (the key is *identification and throttling, not authentication* — an AWS-acknowledged limitation), and an **IAM-authenticated Lambda Function URL** (SigV4-signed — AWS Signature Version 4 request signing) for automation/agent workflows and Bedrock AgentCore (AWS's managed agent runtime) connectivity. **Additional interfaces (raw SPARQL, GraphQL) are deliberately deferred, not rejected.** MCP is young (~18 months) and fast-moving; the thin transport proxy and the stable IAM Function URL fallback contain that risk. Recorded in **ADR-0014**.

### D6 — Git commit-SHA delta + medallion ingestion

Change detection swaps from hashing raw document bytes in S3 (ADR-0007) to **git commit-SHA delta**: the pipeline diffs the source repository between the last-ingested commit SHA and HEAD, processing only added/modified/deleted files. Git is the canonical source (Bronze). The pipeline follows the **medallion** architecture (a staged data-refinement pattern: Bronze → Silver → Gold → Serving) established for this project in RFC-0003. This is git-*first*: the ingestion seam stays pluggable for other source patterns later. **Egress constraint (a security decision, not a deployment detail):** the ingestion task must reach the git remote; the platform uses a **CodePipeline/S3-mirror source** so the Fargate task stays fully VPC-private, and a NAT gateway on the ingestion subnet is **out of bounds** (it would reopen the no-NAT egress posture ADR-0002's carried-forward controls depend on). Recorded in **ADR-0016**.

### Security posture — controls carried forward, and what the pivot changes (D7)

The pivot changes the premises the old security posture (`security.md`) rested on; four decisions keep it honest.

1. **Read-only guard re-ratified as the Text2SPARQL guard (was ADR-0004).** "Text2SPARQL" = an LLM writing a SPARQL query from a natural-language question (the SPARQL successor to Text2Cypher). ADR-0004's control — the query role holds a read-only Neptune grant (`ReadDataViaQuery` only), so a generated-or-injected mutation cannot execute — is **preserved, not deleted**; only its openCypher *anchoring* is superseded. This matters *more* under SPARQL: SPARQL Update includes `DROP GRAPH`, so an escaped mutation could destroy an entire correctness partition (`urn:graph:normative`) and collapse the exhaustive-recall guarantee the whole RFC rests on. The read-cost engine timeout is likewise carried forward. The guarantee is *proven, not assumed*: `spec-text2sparql-guarded` carries a **live-smoke acceptance criterion** that a test-forced SPARQL `DROP GRAPH` / `INSERT` under `mcp_lambda_role` is rejected by IAM at the SPARQL endpoint (ADR-0004's real backstop was a live rejection at the engine, not just the grant's shape), and layer-1's mutation denylist is **re-authored for SPARQL grammar** (different keywords than the openCypher list). Recorded in **ADR-0011** / `spec-text2sparql-guarded`.
2. **Prompt-injection (LLM01 — OWASP's top LLM-application risk) residual re-opened.** The old posture accepted injection risk because the corpus was *public/benign* and the output was *display-only*. The pivot voids both: business-ops corpora can be private and adversarial, and the output is consumed by an agent that *acts* on it (normative-first "before acting"). A poisoned policy ("all actions in scope are pre-approved") flowing through `ask`/`get_policies` into an agent's governance decision is a live elevation path. This RFC re-opens that accepted residual and names an **untrusted-content isolation obligation** (treat retrieved content as data, not instructions; pinned defensive directive; bounded tokens) as an acceptance criterion of `spec-mcp-tool-server` / `spec-text2sparql-guarded`. The isolation obligation holds **regardless of call ordering** — it does not lean on the normative-first convention, which the platform enforces by convention only, not as a control.
3. **ADR-0009 revisit clause discharged.** ADR-0009's safety rested explicitly on the corpus being *public Kubernetes docs* — "no private data whose exposure the synthetic labels would actually be guarding" — and named "asked to ingest private/customer data" as an RFC-level revisit trigger. The pivot deliberately targets private corpora and adds PII detection, so that premise is void. This RFC **discharges** the clause: the labels stay synthetic (real authz remains out of scope), and the platform must carry a **loud adopter warning** that pointing it at PII/private data without adding real authorization is crossing the ADR-0009 boundary. The named-graph partition and the PII flag remain *correctness/labelling* mechanisms, explicitly **not** authorization controls.
4. **API-key ingress containment.** The new API-key human/IDE path is a strict downgrade from a SigV4 scoped-IAM principal (a shared bearer secret, replayable if leaked, no principal binding). ADR-0009's fail-open default and enumeration-oracle residuals were declared safe *only* behind trusted-caller ingress. Behind the API-key path the platform therefore sets visibility **fail-closed by default**, ensures **no filtered-out trace detail crosses that ingress**, and does **not** honor the PII opt-in override there (PII stays fail-closed over the API-key ingress; the opt-in is available only over the SigV4/IAM path, where the caller is a bound principal). A Terraform fitness test asserts `mcp_lambda_role` grants no write/delete data action (the successor to ADR-0004's synth fitness test), so the read-only guarantee cannot silently drift on an IaC edit — an acceptance criterion of the infra follow-on.

### Migration path

1. Stand up the Neptune SPARQL engine (`infra-tf/neptune-sparql-engine`) and run the mandatory latency de-risk spike (see Experiment / validation) **before** the feature build waves.
2. Build the RDF/OWL store layer and the offline `rdflib` mock in parallel (the mock unblocks CI and local dev before live infra lands).
3. Re-ingest the corpus as RDF via the new git+medallion pipeline; the existing openCypher graph data is discarded (not migrated — the models are non-interoperable).
4. Reverse ADR-0001/0008, re-scope ADR-0004; archive the Kubernetes-specific specs; amend the charter and `security.md`.

The wave-ordered shape and work queues already exist in `workspace.toml` (initiative `ini-002`, the tracking record for this pivot), gated on this RFC.

## Options considered

Enumerated per decision along a stated axis, each grounded in prior art, do-nothing always included. The recommended option is **starred**.

**D1 — mission (axis: relationship to the current charter mission).** ★ **Pivot** / Extend (add a biz-ops corpus under the existing openCypher arch — cannot express asymmetric retrieval semantics; a corpus swap, not a platform) / **Do-nothing** (keep the K8s contrast demo — leaves the compliance-recall problem the intent names unaddressed).

**D2 — graph data model (axis: graph model).** ★ **Neptune SPARQL/RDF** / **Do-nothing: openCypher/LPG** (keeps landed code; no named-graph primitive; note LPG is *not* legacy — it is now an ISO standard, GQL / ISO/IEC 39075:2024 — so this is a real alternative, not a strawman) / Neptune Analytics (the Poseidon engine offers LPG+RDF interop but is a different product with an analytics full-scan posture, over-scoped for an interactive teardown-first demo) / a non-Neptune triple store (leaves the managed-AWS posture).

**D3 — correctness model (axis: how correctness is guaranteed).** ★ **Named-graph partition + asymmetric failure semantics** / Query-shape routing only, no hard partition (a misroute leaks silently; isolation is a hint) / **Do-nothing: one vector+graph path for everything** (silent recall gaps on compliance queries).

**D4 — ontology formality (axis: schema formality / reasoning).** ★ **OWL schema-only + SHACL, no reasoner** / OWL + runtime reasoner (full inference; reasoner operational + latency cost) / **Do-nothing: no formal ontology** (no typed contract, no interoperability, no validation).

**D5 — interface (axis: consumer interface).** ★ **MCP tool server** (+ Function URL) / REST API (universal but agents/IDEs hand-wire tool schemas) / **Do-nothing: CLI over query API** (not the native surface for humans in AI IDEs; two interfaces to maintain).

**D6 — ingestion change-detection (axis: source of the change signal).** ★ **Git commit-SHA delta + medallion** (repo-native; exact add/modify/delete set from `git diff`) / S3 object-event delta (event-driven, but the corpus is git-tracked upstream — S3 is a mirror, so this detects the mirror's churn, not the authoring signal) / webhook-triggered full rescan (simple, but re-processes the whole corpus each push — no delta) / **Do-nothing: S3 content-hash delta** (treats the corpus as opaque blobs; no repo-native change signal). Git-SHA delta and full-rescan bracket the axis (finest vs. coarsest change unit); S3-event and content-hash are the mirror-side variants.

## Risks & what would make this wrong

**Pre-mortem (assume it shipped and failed):**
- **Neptune Serverless cold-start blows the interactive budget.** The `ask` synthesis path (embed → vector → graph expand → LLM call) must complete inside the **30-second** integration ceiling of the API Gateway **HTTP API** (a hard, non-adjustable maximum for HTTP APIs — distinct from REST APIs' 29 s); AWS publishes no cold-start/scale-up latency figure for Neptune Serverless, and it does *not* scale to zero (minimum 1.0 NCU — Neptune Capacity Unit — standing cost). *Mitigation:* the mandatory de-risk spike (below) measures this early; the SPARQL smoke probe warms the cluster; the automation Function URL path (15-min budget) is unaffected by the 30 s ceiling.
- **Ingest mis-classification (or attribute mis-tagging) defeats the correctness guarantee.** A policy typed as descriptive — or correctly normative but tagged with the wrong domain — is invisible to the exhaustive `get_policies` leg (see D3's honesty constraint). *Mitigation:* SHACL validation gate + quarantine graph (never silently dropped); classification/tagging quality is a named acceptance criterion of the ingestion spec, and the trace exposes which filter narrowed the result.
- **A generated/injected SPARQL mutation destroys a partition.** SPARQL `DROP GRAPH` under an over-broad grant collapses `urn:graph:normative`. *Mitigation:* the re-ratified read-only guard (§Security posture) + the Terraform read-only fitness test.
- **Poisoned corpus content drives an agent action** (LLM01). *Mitigation:* the untrusted-content isolation obligation (§Security posture).
- **MCP churn breaks the interface.** MCP is ~18 months old and fast-moving. *Mitigation:* the thin `mcp_proxy` isolates transport; the IAM Function URL is a stable fallback ingress.

**Key assumptions (falsifiable — point at one and say "that's wrong, because…"):**
1. Neptune SPARQL named-graph queries meet interactive MCP latency on Serverless within the 30 s HTTP-API ceiling. *(Unverifiable from docs — this is the spike.)*
2. An OWL schema-only ontology (no reasoner) is sufficient to type the corpus without inference. *(Practitioner-grounded, not spec-mandated — see the caveat in Evidence.)*
3. MCP is an acceptable *primary* interface for both agents and humans in AI IDEs — raw SPARQL access is not required by either consumer.
4. Git-tracked corpora are the dominant ingestion pattern for the target segments.
5. Neptune's `ReadDataViaQuery` grant rejects SPARQL Update forms (`INSERT` / `DELETE` / `DROP GRAPH` / `CLEAR` / `LOAD`) the same way it blocked openCypher mutations. *(Load-bearing for the §Security posture read-only guard; pending live-smoke verification in ADR-0011 / `spec-text2sparql-guarded` — tracked in the backlog. If false, the read-only guard needs an app-layer denylist as the primary control, not a backstop.)*

**Drawbacks (what it costs — "none" is not allowed):**
- **Full re-ingest**; existing openCypher graph data is discarded.
- **SPARQL is less ergonomic** than openCypher for typical traversals, and **RDF has no path-discovery** — Neptune/AWS flags that SPARQL cannot report *which* path a traversal took (acceptable here: the design uses reachability/expansion, not path reporting, but it is a real giveup).
- **Idle cost crosses the budget alert.** Two standing stores (Neptune min 1 NCU ~$110/mo + OpenSearch ~$26/mo) plus interface VPC endpoints (~$90/mo) give a ~$226/mo floor *before any traffic*. That **exceeds 80% of the $250/mo Budgets alarm ($200)** — so as currently specified the alarm fires at idle on day one (a cloned-and-forgotten footgun, charter principle 4). The infra follow-on must raise the Budgets threshold above the standing floor (or lower the floor); flagged as a correction the ingestion/infra spec carries.
- **Dependency on a young protocol** (MCP) for the primary interface, and a security surface (Text2SPARQL, agent-consumed output, API-key ingress) materially larger than the public-corpus demo's.

## Evidence & prior art

**Spike / de-risk result (riskiest assumption at the query-semantics layer).** Using `rdflib` (the design's offline substitute for Neptune SPARQL), a two-partition dataset was loaded (three policies in `normative`, two SOPs in `descriptive`) and queried. It confirms exactly two things — the two the D3 guarantee actually claims — and no more: (1) a `descriptive`-scoped query returned **zero** policies (**partition isolation**); (2) an unfiltered `SELECT` over the `normative` partition returned **all three** policies, including one named "Records Disposal Rule" against a "retention"-keyed query (**no top-k drop on a partition scan** — the vocabulary-mismatch case that sinks a *ranked* vector query). It does **not** exercise the domain+date filter `get_policies` applies, so it does not prove `get_policies` is unconditionally exhaustive — that residual is named in D3's honesty constraint. The remaining risk — that Neptune Serverless matches these semantics *within interactive latency* — is what the Experiment (below) measures against live infrastructure.

**Repo precedent.**
- The **charter** mandates that changes to itself go through an RFC — this RFC is the required instrument for the mission change.
- **ADR-0001/0008** are the frozen decisions reversed (both anchored to openCypher/LPG); **ADR-0004** is re-scoped, its read-only control carried forward (§Security posture). **ADR-0002** (teardown-first, no-HA cost posture) and **ADR-0009** (synthetic labels, not real authz — revisit clause discharged here) are carried forward.
- **RFC-0003** established the medallion staging pattern D6 extends; **ADR-0007** (silver cache) is renamed to the medallion Gold layer.
- The full target design is already drafted in [`biz-ops-knowledge-graph/design.md`](../architecture/biz-ops-knowledge-graph/design.md); this RFC is its "should we" gate.

**External prior art (fetched and confirmed to contain the cited claim).**
- **Vector recall is bounded, not exhaustive.** ANN/HNSW recall trades against latency and rarely reaches 1.0: [Pinecone HNSW](https://www.pinecone.io/learn/series/faiss/hnsw/) (recall/latency continuum, "never reaches 1.0"), [Weaviate ANN benchmarks](https://docs.weaviate.io/weaviate/benchmarks/ann) (96–98% recall@10), [OpenSearch performance tuning](https://docs.opensearch.org/1.1/search-plugins/knn/performance-tuning/) (approximate results omit vectors "not encountered during graph traversal"), [Elastic ANN overview](https://www.elastic.co/blog/understanding-approximate-nearest-neighbor-search) (ANN "settles for close enough"). Peer-reviewed grounding: [BEIR, Thakur et al. 2021](https://arxiv.org/abs/2104.08663) (dense retrieval's zero-shot recall gaps; hybrid/re-ranking generalise best).
- **Exhaustive recall is a distinct retrieval objective in compliance/legal IR.** [TREC Legal Track 2007](https://trec-legal.umiacs.umd.edu/guidelines/main07b.html) ("recall is of central concern"). Recent preprints (not yet peer-reviewed) frame this directly for regulated domains: [Controlling Authority Retrieval, arXiv:2604.14488](https://arxiv.org/abs/2604.14488) (retrieving the complete active-authority set, explicitly distinct from `argmax` similarity — claim confirmed on fetch) and [Citation-Closure Retrieval, arXiv:2605.29742](https://arxiv.org/abs/2605.29742) (citation-closure retrieval + per-rule attribution over an operational knowledge graph for regulatory-compliance QA — cited for that framing only; its "liability" wording was not confirmed and is not relied on).
- **Neptune graph-model facts (AWS primary):** [engines are non-interoperable on one cluster](https://aws.amazon.com/neptune/faqs/); [named-graph / SPARQL 1.1 support](https://docs.aws.amazon.com/neptune/latest/userguide/feature-sparql-compliance.html); [Serverless min 1.0 NCU, no scale-to-zero](https://docs.aws.amazon.com/neptune/latest/userguide/neptune-serverless-capacity-scaling.html); [Well-Architected: LPG for path knowledge, RDF for federation/named-graph partitioning](https://docs.aws.amazon.com/prescriptive-guidance/latest/neptune-well-architected-framework/performance-efficiency-pillar.html).
- **W3C standard stack (primary specs):** [SHACL](https://www.w3.org/TR/shacl/) ("full RDFS inferencing is not required" — supports `inference="none"`), [SKOS](https://www.w3.org/TR/skos-reference/), [PROV-O](https://www.w3.org/TR/prov-o/), [Schema.org `DigitalDocument`](https://schema.org/DigitalDocument). **Caveat:** "OWL-as-vocabulary-without-a-reasoner" has *no explicit normative W3C blessing* — it is a well-established practitioner pattern; the [W3C OWL Reference](https://www.w3.org/TR/owl-ref/) acknowledges non-reasoning tools as valid, and SHACL (closed-world) is the actual contract enforcer. Cited as practitioner-grounded, not spec-mandated.
- **Interface / ingestion:** [MCP (Anthropic, Nov 2024)](https://www.anthropic.com/news/model-context-protocol) + [official Python SDK / FastMCP](https://github.com/modelcontextprotocol/python-sdk) + [IDE ecosystem](https://modelcontextprotocol.io/introduction); [medallion architecture (Databricks)](https://docs.databricks.com/aws/en/lakehouse/medallion); git-diff delta idiom ([bazel-diff](https://github.com/Tinder/bazel-diff), [git-diff](https://git-scm.com/docs/git-diff)); [API Gateway HTTP-API integrations](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations.html) (30 s integration ceiling) vs [Lambda Function URL 15-min budget](https://docs.aws.amazon.com/lambda/latest/dg/furls-http-invoke-decision.html).

## Experiment / validation

**Mandatory heavy-weight de-risk spike — Neptune Serverless interactive latency.** Sequenced as the **first** work item (rides on `infra-tf/neptune-sparql-engine`), before the feature build waves. It is an early *implementation-phase* de-risk — it gates the transport/interface choice (D5), **not** acceptance of the pivot.

- **Hypothesis:** the `ask` synthesis path completes inside the HTTP API's 30 s ceiling on a *warm* Neptune Serverless cluster (min 1 NCU), and cold-start scale-up is bounded and warmable via the SPARQL smoke probe.
- **What we measure:** end-to-end `ask` latency (cold and warm), the exhaustive `get_policies` SPARQL `SELECT` latency against a seeded normative partition, and cold-start scale-up time from idle at min 1 NCU.
- **Success / failure criteria:** *Success* — warm `ask` p95 (95th-percentile) latency comfortably under 30 s and cold-start warmable below the interactive budget. *Failure* — warm path cannot fit 30 s → route the human/IDE path to streaming or the Function URL, or raise the min NCU floor (cost trade), and reopen D5's transport choice.

Live AWS deploy is available in this environment, so the spike runs against real infrastructure rather than an estimate. Results go to a linked spike note, not this body.

## Open questions

1. **Neptune Serverless interactive latency within the 30 s ceiling** — *recommended default:* proceed; the mandatory spike (above) runs first and gates the transport choice, not the pivot. · owner: eugenelim · decide-by: before feature wave 2.
2. **Formal supersession record for ADR-0005/0007** — this RFC directly reverses ADR-0001/0008 and re-scopes ADR-0004 (all in `workspace.toml`'s supersedes list). ADR-0005 (Louvain community detection) and ADR-0007 (silver cache) are *mechanism* ADRs the pivot obsoletes, but no follow-on ADR records their supersession yet, and `workspace.toml` does not list them. *Recommended default:* do **not** claim supersession in this RFC; record it in the follow-on ADR that replaces each mechanism (ADR-0016 / Gold layer for 0007; `summarize` + multi-strategy routing for 0005) and track it in the backlog. · owner: eugenelim · decide-by: this review.

## Follow-on artifacts

Filled on acceptance. Already wave-ordered in `workspace.toml` (`ini-002`), gated on `shape:rfc-0004-biz-ops-kg-pivot`:
- **ADRs:** 0011 (Neptune SPARQL + re-ratified read-only Text2SPARQL guard), 0012 (OWL schema-only + named-graph partition), 0013 (multi-strategy routing), 0014 (MCP tool server), 0015 (OTEL / OpenTelemetry observability — a design-cascade detail, not a standalone D-decision in this RFC), 0016 (git + medallion ingestion). Supersession records: ADR-0001/0008 marked superseded-by RFC-0004; ADR-0004 re-scoped; ADR-0005/0007 supersession recorded by their follow-on replacements (tracked in the backlog), not by this RFC.
- **Specs (with the §Security posture obligations as acceptance criteria):** `spec-rdf-owl-ontology`, `spec-multi-strategy-routing`, `spec-normative-partition`, `spec-text2sparql-guarded` (read-only guard + untrusted-content isolation), `spec-git-ingestion` (CodePipeline/S3 mirror; NAT out of bounds; Budgets threshold correction), `spec-ingestion-extraction-cleanse`, `spec-provenance-citations`, `spec-shacl-validation`, `spec-mcp-tool-server` (API-key fail-closed containment; prompt-injection isolation), `spec-otel-observability`. Infra follow-on: Terraform read-only fitness test for `mcp_lambda_role`.
- **Convention/charter change:** amend `docs/CHARTER.md` mission + scope; update `docs/architecture/security.md` (LLM01 residual re-opened, ADR-0009 revisit discharged); archive the Kubernetes-specific specs.
- **Spike note:** the Neptune Serverless latency de-risk result (Experiment, above).
