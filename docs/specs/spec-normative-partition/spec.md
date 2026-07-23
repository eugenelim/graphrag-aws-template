# Spec: spec-normative-partition

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition and asymmetric retrieval semantics — primary decision); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine; `mcp_lambda_role` read-only IAM grant); [ADR-0013](../../adr/0013-multi-strategy-server-side-routing.md) (`get_policies` is always `normative_exhaustive` — no routing step); [ADR-0014](../../adr/0014-mcp-tool-server.md) (`get_policies` tool contract)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** retrieval

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.normative` module implements `NormativeRetriever` — the exhaustive retrieval logic for the `get_policies` MCP tool. It returns **all** applicable Policy resources from `urn:graph:normative` for a given context, using two complementary legs that cannot conflict:

1. **SPARQL leg** — `SELECT` over `urn:graph:normative` with optional domain and effective-date filters. Returns all matching policies; there is no top-k limit. This is the primary leg.

2. **Vector-threshold leg** — OpenSearch kNN query against the normative partition (`named_graph = "urn:graph:normative"`) with a minimum similarity threshold (default 0.7). Can only **add** policies not already returned by the SPARQL leg (based on document URI deduplication); it never gates or removes SPARQL results.

Hard-fail semantics: if Neptune is unavailable, `get_policies` raises `NormativeUnavailable` — it does not degrade gracefully. A partial normative result (SPARQL leg fails, vector leg returns some results) is worse than no result, because a missing policy is a compliance gap that cannot be detected.

This module owns the retrieval logic for the normative partition only. The `get_policies` tool definition, routing bypass, and response formatting are the MCP tool server's scope. The named-graph partition structure (what goes into `urn:graph:normative`) is the ingestion pipeline's scope.

## Boundaries

### Always do

- Execute the SPARQL leg first; execute the vector-threshold leg second; deduplicate on document URI; return the union.
- Raise `NormativeUnavailable` (not log and return partial results) if the SPARQL leg fails due to Neptune unavailability. Log the failure with the exception detail before raising.
- Apply the PII filter (`biz:hasPII false`) by default on both legs. The `include_pii` parameter must be explicitly passed `True` to include PII-flagged documents.
- Apply the effective-date filter (`biz:effectiveDate <= today`) by default on the SPARQL leg. The `include_future` parameter overrides this.
- Use the `mcp_lambda_role` IAM grant — `ReadDataViaQuery` only — for the SPARQL SELECT. Never attempt a SPARQL Update from this module.
- Scope the SPARQL SELECT to `FROM NAMED <urn:graph:normative>` — this is a hard structural constraint, not a hint.

### Ask first

- Changing the vector similarity threshold (default 0.7) — affects recall/precision balance for the supplementary vector leg; impacts the honesty-constraint residual documented in ADR-0012.
- Adding a second SPARQL-side filter (e.g. journey filter, visibility filter) — filters can create silent gaps if not also applied to the vector leg.

### Never do

- Apply top-k limit to the SPARQL normative leg — the guarantee is exhaustive recall from the structured partition.
- Return a partial result silently when Neptune is unavailable — raise `NormativeUnavailable` without returning a partial response.
- Allow the vector-threshold leg to remove or rank-order the SPARQL results — the vector leg is additive only.
- Run a SPARQL Update statement from this module — `mcp_lambda_role` is read-only; any write attempt is both an IAM failure and a structural violation.
- Skip the named-graph scope (`FROM NAMED`) and run an unscoped SELECT across all graphs — this would mix normative and descriptive results, breaking the partition guarantee.

## Testing Strategy

- **TDD** — SPARQL leg basic retrieval (AC1): fixture Neptune store seeded with three normative policies, two with `biz:inDomain biz:Finance`, one with `biz:inDomain biz:HR`. `NormativeRetriever.retrieve(context="x", domain="Finance")` returns exactly the two Finance policies and no HR policy.
- **TDD** — vector-threshold leg union (AC3–AC4): fixture where SPARQL returns policy A; vector threshold returns policy A (deduplicated) and policy B (not in SPARQL result). Final result contains both A and B.
- **TDD** — hard fail on Neptune unavailability (AC2): patch Neptune client to raise `ConnectionError`; assert `NormativeUnavailable` is raised and no partial result is returned.
- **TDD** — PII filter (AC5): fixture containing one PII-flagged policy and one clean policy. `retrieve(context="x")` returns only the clean policy. `retrieve(context="x", include_pii=True)` returns both.
- **TDD** — effective-date filter (AC6): fixture with one policy with `biz:effectiveDate` in the past and one future policy. Default call returns only the past policy. `retrieve(context="x", include_future=True)` returns both.
- **TDD** — no top-k on SPARQL leg (AC8): fixture with 20 normative policies matching the query; assert result contains all 20 — no truncation.
- **Goal-based check** — import isolation: `python -c "import graphrag.normative"` exits 0; the module uses `mcp_lambda_role` permissions (ReadDataViaQuery); no SPARQL Update keywords appear in any query string in the module.
- **Goal-based check** — SPARQL query syntax: each query in the module is executed against `rdflib` in-memory SPARQL (the Neptune offline substitute) in the test suite; a malformed query raises a parse error that fails the test before any live Neptune call.

## Acceptance Criteria

- [ ] `NormativeRetriever.retrieve(context="onboarding checklist", domain="HR")` against a fixture Neptune store returns all normative knowledge instances (`biz:Policy`, `biz:Standard`, `biz:Guideline`) in `urn:graph:normative` that have `biz:inDomain biz:HR` — 0, 1, or N items depending on fixture, with no top-k truncation. The spec objective's "Policy resources" shorthand refers to all three normative document classes; the SPARQL filter is `?type IN (biz:Policy, biz:Standard, biz:Guideline)`.
- [ ] When Neptune is unreachable (connection refused), `retrieve()` raises `NormativeUnavailable`; no partial result is returned; the exception includes the underlying cause.
- [ ] The vector-threshold leg runs against `urn:graph:normative` only (`named_graph` filter applied as a mandatory `bool.filter`); results with `named_graph = "urn:graph:descriptive"` are never returned.
- [ ] Results from the vector-threshold leg are added to the SPARQL result only when their document URI is not already present in the SPARQL result (deduplication by `doc_uri`).
- [ ] Documents with `biz:hasPII true` are excluded by default; `retrieve(context="x", include_pii=True)` returns them. Documents where `biz:hasPII` is absent are treated as non-PII and returned by default — the SPARQL filter uses `OPTIONAL { ?doc biz:hasPII ?hasPII }` with `FILTER(!bound(?hasPII) || ?hasPII = false)`.
- [ ] Documents where `biz:effectiveDate > today` (not-yet-effective / future policies) are excluded by default; `retrieve(context="x", include_future=True)` returns them.
- [ ] When the default PII filter excludes one or more policies, the response envelope includes a `pii_withheld_count` field with the count of withheld documents — so the caller knows the result may be incomplete from a compliance standpoint if they hold a role that should see PII-flagged policies.
- [ ] A fixture with 20 matching normative policies returns all 20 — the SPARQL SELECT carries no `LIMIT` clause.
- [ ] The SPARQL SELECT is scoped with `FROM NAMED <urn:graph:normative>`; an unscoped equivalent query that would also return descriptive results is never constructed.
- [ ] `NormativeUnavailable` is raised — not a graceful degrade — when Neptune is unavailable. A log line at ERROR level is emitted with the exception detail before raising.
- [ ] The `get_policies` MCP tool response carries `strategy = "normative_exhaustive"` and `decided_by = "none"` in its `StrategyTrace`. The trace is constructed by the routing dispatch layer (owner: `graphrag.routing.route_get_policies`) **before** `NormativeRetriever.retrieve()` is called — `retrieve()` itself does not construct or return a `StrategyTrace`. The MCP tool handler attaches the trace to the `get_policies` response envelope alongside the retrieval results.
- [ ] Each result item carries: `uri`, `title`, `doc_type`, `domain`, `effective_date`, `scope`, `pii_flagged`, `relevance` (similarity score from the vector leg for vector-added items; `null` for SPARQL-only items), `git_commit` (SHA from `biz:gitCommitSHA`), and `git_path` (repo-relative path from `biz:gitPath`).
- [ ] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/normative/` with zero errors.

## Assumptions

- Technical: `graphrag.normative` lives in `packages/graphrag/src/graphrag/normative/`; tests in `packages/graphrag/tests/normative/`.
- Technical: The offline substitute for Neptune SPARQL is `store/neptune_sparql_memory.py` (`rdflib` in-memory SPARQL with full named-graph support) — this is the fixture backend for TDD tests. Tests that depend on the vector-threshold leg use `store/vector_memory.py` as the OpenSearch substitute.
- Technical: `NormativeRetriever` is constructed with injectable store clients (Neptune SPARQL client, OpenSearch client, Bedrock embedder) — dependency injection for testability and for the mock server path.
- Technical: The vector similarity threshold (default 0.7) is configurable via a constructor parameter; the default is not an env var to keep the retriever pure and testable.
- Technical: `biz:effectiveDate` is an `xsd:date` literal; "today" in the effective-date filter is computed from `datetime.date.today()` in the retriever, not injected — a known testing seam (use `freezegun` or override the date method in tests that need determinism).
- Product: `NormativeUnavailable` propagates to the `get_policies` MCP tool handler, which surfaces it as an MCP error response to the caller — the caller sees a structured error, not a partial result.
- Product: The vector similarity threshold of 0.7 is the ADR-0012 specification; the rationale (semantically adjacent policies must exceed this threshold to be included as supplements) is documented in the retriever's `_vector_leg()` method docstring.
- Product: PROV-O provenance triples (git commit SHA, extractor used, Silver artifact path) are present in Neptune for each policy; `retrieve()` resolves them per result item by joining against the provenance triples in the same named graph.
