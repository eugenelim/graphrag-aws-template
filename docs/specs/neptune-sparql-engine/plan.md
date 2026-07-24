# Plan: neptune-sparql-engine

- **Status:** Shipped
- **Spec:** [`spec.md`](spec.md)

> Mode: full (security boundary — Neptune IAM read-only backstop; infra-flavored IaC).
> Implements wave 1 of ini-002; governed by ADR-0011 (supersedes ADR-0004).

## Self-coverage disposition record

Opened: 2026-07-23

| Disposition | Item |
|---|---|
| Resolve | ADR-0011 supersedes ADR-0004 — confirmed in ADR text lines 6, 11, 27, 66 |
| Resolve | Neptune description field not tested — confirmed by grep across test_plan.py |
| Resolve | plan.json fixture stays valid — test assertions check name/value, not description |
| Resolve | lambda.tf has 3 stale references (lines 15, 141, 143) — added to scope |
| Defer (anchor) | Behavioral IAM proof → `neptune-sparql-dropgraph-iam-action-verify` (spec-text2sparql-guarded) |
| Defer (anchor) | Neptune audit-log-export → `neptune-audit-log-export` backlog entry |

## Tasks

### Task 1 — Update `neptune.tf` (4 sites: ADR-0004 → ADR-0011, text2cypher → text2sparql)
- **Verification mode:** goal-based check
- **Done when:** `grep -c "ADR-0011" apps/infra-tf/neptune.tf` ≥ 4 AND `grep -c "text2cypher" apps/infra-tf/neptune.tf` = 0 AND `grep "ADR-0004" apps/infra-tf/neptune.tf | grep -v "supersedes ADR-0004" | wc -l` = 0
- **Depends on:** none

**Approach:**
- Line 5 header: "PINNED per ADR-0004 (the read-cost backstop)" → "PINNED per ADR-0011 (SPARQL read-cost backstop; ADR-0011 supersedes ADR-0004)"
- Line 16 parameter-group comment: "The engine read-cost backstop (ADR-0004):" → "The SPARQL read-cost backstop (ADR-0011):"
- Line 22 description: "read-cost backstop (query timeout) for text2cypher" → "read-cost backstop (query timeout) for text2sparql (ADR-0011)"
- Line 37 inline cluster comment: "ADR-0004 20s query-timeout backstop is inert" → "ADR-0011 SPARQL query-timeout backstop is inert"

### Task 2 — Update `iam.tf` (3 sites: ADR-0004 → ADR-0011)
- **Verification mode:** goal-based check
- **Done when:** `grep -c "ADR-0011" apps/infra-tf/iam.tf` ≥ 3 AND `grep -c "ADR-0004" apps/infra-tf/iam.tf` = 0
- **Depends on:** none

**Approach:**
- Line 12 load-bearing invariants header: "connect + ReadDataViaQuery only (ADR-0004)" → "connect + ReadDataViaQuery only (ADR-0011)"
- Line 77 neptune_readonly_policy comment: "ADR-0004 backstop:" → "ADR-0011 backstop (carries forward the proven read-only control):"
- Line 273 QueryRole section comment: "No Write/Delete Neptune action on this role (ADR-0004)." → "No Write/Delete Neptune action on this role (ADR-0011)."

### Task 3 — Update `lambda.tf` (3 sites: ADR-0004/text2cypher → ADR-0011/SPARQL)
- **Verification mode:** goal-based check
- **Done when:** `grep -c "ADR-0011\|SPARQL" apps/infra-tf/lambda.tf` ≥ 3 AND `grep -c "text2cypher\|ADR-0004" apps/infra-tf/lambda.tf` = 0
- **Depends on:** none

**Approach:**
- Line 15 load-bearing invariants header: "query_role stays read-only (ADR-0004, defined in iam.tf, untouched here)." → "query_role stays read-only (ADR-0011 backstop, defined in iam.tf)."
- Line 141 QueryLambda description: "hybrid + text2cypher query path" → "hybrid + SPARQL query path"
- Line 143 QueryLambda ADR comment: "ADR-0004 + OpenSearch + Bedrock" → "ADR-0011 + OpenSearch + Bedrock"

### Task 4 — Update `test_plan.py` (3 docstrings: ADR-0004 → ADR-0011)
- **Verification mode:** goal-based check + test suite run
- **Done when:** `grep -c "ADR-0011" apps/infra-tf/tests/test_plan.py` ≥ 3 AND `grep -c "ADR-0004" apps/infra-tf/tests/test_plan.py` = 0; suite exits 0
- **Depends on:** none

**Approach:**
- Line 104 `test_neptune_query_timeout_backstop_is_set` docstring: "ADR-0004 read-cost backstop:" → "ADR-0011 SPARQL read-cost backstop:"
- Line 370 `test_query_role_neptune_grant_is_read_only` docstring: "(ADR-0004 backstop)" → "(ADR-0011 backstop)"
- Line 438 `test_ingestion_and_smoke_roles_retain_neptune_rw` docstring: "(ADR-0004: two roles keep full RW)" → "(ADR-0011: two roles keep full RW)"

### Task 5 — Add deferred backlog entries + mark spec ACs done
- **Verification mode:** goal-based check
- **Done when:** `neptune-audit-log-export` entry exists in `workspace.toml [backlog].open`; spec ACs updated to [x]; spec status → Shipped
- **Depends on:** Tasks 1–4

**Approach:**
- Add `neptune-audit-log-export` to `workspace.toml [backlog].open` (Concern 3 from security review)
- Verify `neptune-sparql-dropgraph-iam-action-verify` already in backlog (it is — noted in workspace.toml)
- Mark spec ACs [x], update Status to Shipped
- Move `infra-tf/neptune-sparql-engine` from `["ini-002".work].queue` to `shipped`

## Gates

```bash
# Verify no stale references remain (suppress the one permitted "supersedes ADR-0004" line in neptune.tf)
grep -rn "ADR-0004\|text2cypher" apps/infra-tf/neptune.tf apps/infra-tf/iam.tf apps/infra-tf/lambda.tf apps/infra-tf/tests/test_plan.py | grep -v "supersedes ADR-0004"
# Expected: 0 lines

# Run plan-assertion tests (cred-free via committed fixture)
TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json pytest apps/infra-tf/tests/ -q
# Expected: all pass, exit 0

# Trivy (CI gate; local optional)
trivy config --exit-code 1 --severity HIGH,CRITICAL --skip-dirs tests apps/infra-tf/
```
