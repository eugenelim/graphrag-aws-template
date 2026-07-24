# Plan: spec-normative-partition

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Three tasks. T1 (SPARQL leg) is the primary retrieval path — pure Neptune SPARQL with the `rdflib` offline substitute. T2 (vector-threshold leg) depends on T1 because its deduplication step merges against T1's result. T3 (response envelope + `NormativeUnavailable` hard-fail semantics) depends on T1 and T2 — it assembles the final result shape and handles the error path.

The riskiest part is the hard-fail semantics: the test must confirm that a Neptune connection failure raises `NormativeUnavailable` rather than returning a partial result from the vector leg alone. This is tested before T2 exists (using a mocked vector leg in T1's tests) to confirm the error contract is independent of the vector leg's availability.

No AWS credentials are needed for T1 or T2 unit tests (both use in-memory substitutes). Live Neptune and OpenSearch are required only for the live-AC confirmation gate (tagged `@pytest.mark.live_aws`, skipped offline).

## Constraints

- ADR-0012: SPARQL SELECT over `urn:graph:normative` only — `FROM NAMED <urn:graph:normative>` is a hard constraint, not a hint.
- ADR-0012: no top-k on the SPARQL leg; exhaustive recall.
- ADR-0012: honesty constraint residual #2 (intra-partition attribute mismatch — wrong domain tag) is out of scope here; documented as a known gap.
- ADR-0013: `get_policies` never invokes a strategy router; `strategy = normative_exhaustive` is a constant.
- ADR-0011: `mcp_lambda_role` is `ReadDataViaQuery` + `connect` only — no SPARQL Update.
- Ruff + mypy CI gates must stay green.

## Construction tests

**T1 (SPARQL leg):**
- Fixture `rdflib` store with 3 policies: 2 Finance, 1 HR, 1 PII-flagged Finance.
- `retrieve(context="x", domain="Finance")` returns 2 non-PII Finance policies.
- `retrieve(context="x", domain="Finance", include_pii=True)` returns all 3 Finance policies.
- `retrieve(context="x")` (no domain filter) returns all 3 non-PII policies (2 Finance + 1 HR).
- Neptune raises `ConnectionError` → `NormativeUnavailable` raised immediately, no partial result.

**T2 (vector leg):**
- Fixture vector store: policy A (already in SPARQL result), policy B (not in SPARQL result, similarity 0.82).
- Union result contains both A and B; B has `relevance=0.82`; A has `relevance=null`.
- Policy C with similarity 0.65 (below threshold) is not in the result.

**T3 (response envelope):**
- Response carries `pii_withheld_count` in the `NormativeResponse` envelope.
- Response items carry `uri`, `title`, `doc_type`, `domain`, `effective_date`, `scope`, `pii_flagged`, `relevance`.
- `strategy="normative_exhaustive"` / `decided_by="none"` StrategyTrace is deferred to
  the MCP tool handler / routing layer (owner: `graphrag.routing.route_get_policies`).

## Design (LLD)

### Design decisions

- **Dependency injection for store clients.** `NormativeRetriever(neptune_client, opensearch_client, embedder)` — all three clients are injected, none constructed internally. This enables `rdflib` + `store/vector_memory.py` substitution in unit tests without patching.
- **SPARQL query template, not string formatting.** The domain filter, effective-date filter, and PII filter are added as SPARQL `FILTER()` clauses appended to a base query template string — not via Python f-string interpolation of user-supplied values. The `domain` parameter is validated against known `biz:BusinessDomain` values before substitution; an unknown domain raises `ValueError`, not a SPARQL injection.
- **Vector-threshold leg is additive.** The SPARQL result is computed first and its `doc_uri` set is passed to the vector leg. The vector leg deduplicates at the document URI level before returning candidates. This ensures the SPARQL leg is never filtered by the vector leg.
- **`NormativeUnavailable` is a module-level exception, not a subclass of `RuntimeError`.** Callers catch it specifically; it carries the original cause as `__cause__` for logging. Defined in `_types.py`.
- **Effective-date default filter.** `biz:effectiveDate <= TODAY` is expressed as `FILTER(?effectiveDate <= "%(today)s"^^xsd:date)`. `today` is injected at query-build time from `datetime.date.today()` — not at module load time — so tests can override with `freezegun`.

### Data & schema

```python
# graphrag/normative/_types.py
class NormativeUnavailable(Exception):
    """Raised when Neptune normative partition is unreachable; carries original cause."""

from dataclasses import dataclass

@dataclass
class NormativeResult:
    uri: str
    title: str
    doc_type: str
    domain: str | None
    effective_date: str | None   # ISO date string or None
    scope: str | None
    pii_flagged: bool
    relevance: float | None      # similarity score from vector leg; None for SPARQL-only hits
    git_commit: str | None
    git_path: str | None
```

**SPARQL base query (illustrative — full form in `_sparql.py`):**

```sparql
SELECT ?doc ?title ?type ?domain ?effectiveDate ?scope ?hasPII ?sha ?path
FROM NAMED <urn:graph:normative>
WHERE {
  GRAPH <urn:graph:normative> {
    ?doc a ?type .
    OPTIONAL { ?doc schema:name ?title }           -- OPTIONAL: exhaustive recall even without title
    OPTIONAL { ?doc biz:gitCommitSHA ?sha }        -- OPTIONAL: git-ingestion not yet shipped
    OPTIONAL { ?doc biz:gitPath ?path }
    OPTIONAL { ?doc biz:hasPII ?hasPII }           -- OPTIONAL: absent hasPII treated as non-PII
    OPTIONAL { ?doc biz:inDomain ?domain }
    OPTIONAL { ?doc biz:effectiveDate ?effectiveDate }
    OPTIONAL { ?doc biz:scope ?scope }
    -- NOTE: PII exclusion is applied in Python (not SPARQL) so pii_withheld_count
    -- can be computed without a second COUNT query (see _retriever.py).
    FILTER(?type IN (biz:Policy, biz:Standard, biz:Guideline))
    -- domain filter injected here if domain != None
    -- effective-date filter injected here if include_future=False
  }
}
-- Results are deduplicated by ?doc URI after retrieval to handle multi-valued
-- OPTIONAL properties (e.g. multiple biz:inDomain values).
```

### Component / module decomposition

```
packages/graphrag/src/graphrag/normative/
├── __init__.py          # exports: NormativeRetriever, NormativeUnavailable
├── _types.py            # NormativeUnavailable, NormativeResult
├── _sparql.py           # SPARQL query builder; _sparql_leg(client, ...) → list[NormativeResult]
├── _vector.py           # vector threshold leg; _vector_leg(client, embedder, ...) → list[NormativeResult]
└── _retriever.py        # NormativeRetriever.retrieve() — orchestrates both legs + deduplication

packages/graphrag/tests/normative/
├── test_sparql_leg.py
├── test_vector_leg.py
└── test_normative_retriever.py
```

### Failure, edge cases & resilience

- **Neptune returns an empty result.** Legitimate case — no policies match the domain/date filter. `retrieve()` proceeds to the vector leg; if that also returns nothing, returns an empty list. **Not** an error.
- **Vector leg Bedrock embedding fails.** Log WARNING; skip the vector leg for this call; return the SPARQL-only result. The SPARQL leg alone satisfies the exhaustive-recall guarantee from the structured partition.
- **Zero policies in `urn:graph:normative`.** The graph is new or empty. `retrieve()` returns an empty list; the caller receives `[]` — valid for a freshly deployed stack with no normative corpus. The hard-fail semantics only fire when Neptune itself is unavailable, not when the partition is empty.
- **Domain value not in known `biz:BusinessDomain` list.** Raise `ValueError` before constructing the SPARQL query. This prevents unknown domain strings from leaking into the SPARQL template and producing unexpected results (the SPARQL engine would simply return 0 results, silently).

### Quality attributes (NFRs)

- **No SPARQL injection.** Domain parameter is validated against a known set; effective-date is a datetime object formatted to ISO — not user-supplied string interpolation.
- **Mypy-clean**: full type annotations on all public functions and dataclasses.
- **Offline CI**: all unit tests run against `rdflib` + `store/vector_memory.py` — no AWS credentials.

## Tasks

### T1: SPARQL leg + `NormativeUnavailable` hard-fail

**Depends on:** `packages/graphrag/neptune-sparql-store` (work queue dep — the Neptune SPARQL client; uses `rdflib` substitute in unit tests)

**Touches:**
- `packages/graphrag/src/graphrag/normative/__init__.py`
- `packages/graphrag/src/graphrag/normative/_types.py`
- `packages/graphrag/src/graphrag/normative/_sparql.py`
- `packages/graphrag/tests/normative/test_sparql_leg.py`

**Tests (TDD):**
1. Fixture rdflib store with 2 Finance + 1 HR policy: `retrieve(domain="Finance")` returns 2.
2. `retrieve(include_pii=True, domain="Finance")` returns 3 (PII-flagged Finance included).
3. `retrieve()` (no domain filter) returns all non-PII policies.
4. Neptune raises `ConnectionError` → `NormativeUnavailable` raised.
5. Empty `urn:graph:normative` → returns `[]`, no exception.
6. No `LIMIT` clause: assert the SPARQL string produced does not contain `LIMIT`.
7. `FROM NAMED <urn:graph:normative>`: assert the SPARQL string contains the named-graph scope.

**Done when:** 7 tests pass; `ruff check` and `mypy` clean.

---

### T2: Vector-threshold leg + deduplication

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/normative/_vector.py`
- `packages/graphrag/tests/normative/test_vector_leg.py`

**Tests (TDD):**
1. Policy A (score 0.82, in SPARQL result) → deduplicated out of vector additions; A's `relevance` stays `None` (SPARQL-sourced).
2. Policy B (score 0.82, not in SPARQL result) → added to union with `relevance=0.82`.
3. Policy C (score 0.65, below threshold 0.7) → not in union.
4. Bedrock `InvokeModel` raises → `_vector_leg` returns empty list (vector leg graceful degrade); SPARQL result returned as-is.

**Done when:** 4 tests pass; `ruff check` and `mypy` clean.

---

### T3: `NormativeRetriever` + response envelope

**Depends on:** T1, T2

**Touches:**
- `packages/graphrag/src/graphrag/normative/_retriever.py`
- `packages/graphrag/tests/normative/test_normative_retriever.py`

**Tests (TDD):**
1. Full retrieve: SPARQL 2 results + vector 1 new result → total 3.
2. Each `NormativeResult` has `uri`, `title`, `doc_type`, `pii_flagged`, `relevance` (None for SPARQL items, float for vector items), `git_commit`, and `git_path`.
3. Effective-date filter: fixture with future-dated policy excluded by default; included with `include_future=True`.
4. `pii_withheld_count`: fixture with 1 PII-flagged and 2 clean policies; default call returns 2 results and `pii_withheld_count=1` in the response envelope; `include_pii=True` returns 3 results and `pii_withheld_count=0`.

**Done when:** 4 tests pass; full test suite green; `ruff check` and `mypy` clean.

## Rollout

- **Delivery:** no flag — `graphrag.normative` is a new module with no existing callers until `spec-mcp-tool-server` imports it.
- **Infrastructure:** uses the Neptune SPARQL endpoint and OpenSearch cluster already provisioned; the `bedrock-runtime` VPC endpoint is already in place for embeddings.
- **Deployment sequencing:** depends on `packages/graphrag/neptune-sparql-store` (work queue).

## Risks

- **SPARQL query domain-injection vector.** If `domain` is passed as a raw string into the SPARQL template, an adopter could supply a value containing SPARQL injection (e.g. `"Finance } SELECT * { ?s ?p ?o`). Mitigated: validate `domain` against the `biz:BusinessDomain` instance list before query construction; raise `ValueError` on unknown domain.
- **Effective-date type mismatch.** Neptune stores `biz:effectiveDate` as `xsd:date`; Python's `datetime.date.today()` formats to `YYYY-MM-DD`. SPARQL FILTER comparison between `^^xsd:date` literals should work, but Neptune's SPARQL 1.1 compliance on date comparison edge cases (timezone-aware vs. naive) needs a live-AWS confirmation test.
- **Rdflib `FROM NAMED` semantics.** `rdflib` supports named graphs but its `FROM NAMED` handling in SPARQL queries differs slightly from Neptune's. Ensure the offline tests use the same graph-loading pattern as the Neptune client (`ConjunctiveGraph` with named graphs, not `Dataset`) to avoid false-positive test passes.

## Changelog

- 2026-07-23: initial plan
