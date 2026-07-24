# Spec: neptune-sparql-engine

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md)
  (Neptune SPARQL/RDF engine + Text2SPARQL read-only guard — supersedes ADR-0004);
  [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition model);
  `apps/infra-tf/neptune.tf`, `apps/infra-tf/iam.tf`, `apps/infra-tf/lambda.tf` (files being updated);
  `apps/infra-tf/tests/test_plan.py` (plan-assertion suite)
- **Shape:** infra update (comment / description alignment to ADR-0011; no new resources)
- **Initiative:** ini-002 wave 1

## Objective

Update the existing Neptune Terraform configuration in `apps/infra-tf/` so that every
ADR-0004 and text2cypher reference is replaced by ADR-0011 and text2sparql/SPARQL.
Neptune clusters serve SPARQL at `/sparql` and openCypher at `/openCypher` without a
separate Terraform flag; no new AWS resources are required. The change is a **comment
and description alignment**: all four touched files must cite ADR-0011 as the governing
authority for the Neptune read-only backstop. The `mcp_lambda_role` introduction and
`query_role` rename are Wave 4 work (`infra-tf/mcp-otel-lambda`).

## Boundaries

### Always do

- Update **all four files** so `grep -rn "ADR-0004\|text2cypher" apps/infra-tf/{neptune.tf,iam.tf,lambda.tf,tests/test_plan.py}` returns zero matches on standalone references (only superseded-by context lines are permitted).
- `neptune.tf` (4 sites): header comment; parameter group `description`; parameter-group comment; inline cluster comment.
- `iam.tf` (3 sites): load-bearing invariants header; `neptune_readonly_policy` comment; QueryRole section comment.
- `lambda.tf` (3 sites): load-bearing invariants header; QueryLambda description comment (text2cypher → SPARQL); ADR-0004 inline reference.
- `test_plan.py` (3 sites): `test_neptune_query_timeout_backstop_is_set`, `test_query_role_neptune_grant_is_read_only`, `test_ingestion_and_smoke_roles_retain_neptune_rw` docstrings.
- Record the behavioral-proof deferral and the audit-logging gap as backlog entries in `workspace.toml [backlog].open` (see Deferred Criteria below).

### Never do

- Add `mcp_lambda_role` IAM role here — belongs in `infra-tf/mcp-otel-lambda` (Wave 4).
- Rename `query_role` to `mcp_lambda_role` in Terraform — destructive (destroy + recreate); do it atomically when the Lambda is introduced. Wave-4 spec must reuse `local.neptune_readonly_policy` for the new role.
- Update `tests/fixtures/plan.json` — no assertion checks `description`; the fixture stays valid.
- Change any Terraform resource definitions, types, counts, or functional attributes.

## Testing Strategy

All tasks verified via **goal-based check**:

- Positive: `grep "ADR-0011" apps/infra-tf/neptune.tf | wc -l` returns ≥4.
- Positive: `grep "ADR-0011" apps/infra-tf/iam.tf | wc -l` returns ≥3.
- Positive: `grep "ADR-0011\|SPARQL" apps/infra-tf/lambda.tf | wc -l` returns ≥3.
- Positive: `grep -c "ADR-0011" apps/infra-tf/tests/test_plan.py` returns ≥3.
- Negative: `grep -rn "text2cypher\|ADR-0004" apps/infra-tf/{neptune.tf,iam.tf,lambda.tf,tests/test_plan.py} | grep -v "supersedes ADR-0004"` returns 0 lines (only the one permitted neptune.tf supersedes-context line is allowed).
- Suite: `TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json pytest apps/infra-tf/tests/ -q` exits 0.
- IaC scan: `trivy config --exit-code 1 --severity HIGH,CRITICAL --skip-dirs tests apps/infra-tf/` exits 0 (CI gate; local Trivy optional).

## Acceptance Criteria

- [x] **AC1 — `neptune.tf` has ≥4 ADR-0011 references; zero text2cypher; zero standalone ADR-0004.**
  `grep -c "ADR-0011" apps/infra-tf/neptune.tf` ≥ 4; `grep -c "text2cypher" apps/infra-tf/neptune.tf` = 0;
  `grep "ADR-0004" apps/infra-tf/neptune.tf | grep -v "supersedes ADR-0004" | wc -l` = 0.
  *(goal-based check)*

- [x] **AC2 — `iam.tf` has ≥3 ADR-0011 references and zero ADR-0004 tokens.**
  `grep -c "ADR-0011" apps/infra-tf/iam.tf` ≥ 3; `grep -c "ADR-0004" apps/infra-tf/iam.tf` = 0.
  (Approach text for iam.tf:77 must use "carries forward the proven read-only control" — no ADR-0004 token.)
  *(goal-based check)*

- [x] **AC3 — `lambda.tf` references ADR-0011/SPARQL; zero standalone ADR-0004/text2cypher.**
  `grep -c "ADR-0011\|SPARQL" apps/infra-tf/lambda.tf` ≥ 3; `grep -c "text2cypher\|ADR-0004" apps/infra-tf/lambda.tf` = 0.
  *(goal-based check)*

- [x] **AC4 — Three test docstrings reference ADR-0011; zero standalone ADR-0004 in test file.**
  `grep -c "ADR-0011" apps/infra-tf/tests/test_plan.py` ≥ 3; `grep -c "ADR-0004" apps/infra-tf/tests/test_plan.py` = 0.
  *(goal-based check)*

- [x] **AC5 — All plan-assertion tests pass against the committed fixture.**
  `TFPLAN_JSON_PATH=apps/infra-tf/tests/fixtures/plan.json pytest apps/infra-tf/tests/ -q` exits 0.
  *(goal-based check)*

- [x] **AC6 — Trivy config scan exits 0 with no HIGH/CRITICAL findings.**
  `trivy config --exit-code 1 --severity HIGH,CRITICAL --skip-dirs tests apps/infra-tf/` exits 0.
  *(goal-based check — CI gate; local Trivy optional)*

## Deferred Criteria

- [ ] **AC-DEFER-1 — Behavioral proof that `query_role` IAM rejects SPARQL Update (DROP GRAPH, INSERT DATA).**
  `(deferred: neptune-sparql-dropgraph-iam-action-verify)` — live-smoke AC owned by `spec-text2sparql-guarded`.
  The *shape* of the grant (ReadDataViaQuery + connect only) is verified by `test_query_role_neptune_grant_is_read_only` (AC5). The behavioral proof that IAM actually rejects a SPARQL Update statement at the engine belongs to `spec-text2sparql-guarded`'s live-smoke gate (ADR-0011 §Confirmation signal 3). Already in `[backlog].open` as `neptune-sparql-dropgraph-iam-action-verify`.

- [ ] **AC-DEFER-2 — Neptune CloudWatch audit-log export for detective control.**
  `(deferred: neptune-audit-log-export)` — out of scope for comment-only wave; accepted for ephemeral teardown-first demo per ADR-0002. CloudWatch audit-log-export would detect a future escaped SPARQL Update mutation if the preventive IAM control were ever misconfigured. Note: `trivy config`'s `--severity HIGH,CRITICAL` filter does not surface the MEDIUM-class Neptune audit-logging-disabled finding, so this gap is not covered by AC6.

## Assumptions

- Neptune Serverless clusters serve SPARQL at `/sparql` and openCypher at `/openCypher` on
  the same cluster without a Terraform flag — confirmed in AWS Neptune docs and ADR-0011 §Alternatives.
- The `plan.json` fixture captures the parameter group `description` string (the fixture is
  a full JSON dump), but no test assertion reads `description` — the only description-assertion
  in `test_plan.py` targets `aws_security_group` (line 230), not the parameter group. Changing
  the Neptune parameter group `description` in neptune.tf therefore leaves all test assertions
  passing; the committed fixture retains the stale text2cypher string as an acceptable frozen
  snapshot.
- `mcp_lambda_role` is the eventual role name for the MCP Lambda (Wave 4); the current
  `query_role` already implements the ADR-0011 read-only backstop correctly.
- Wave-4 `infra-tf/mcp-otel-lambda` spec must reuse `local.neptune_readonly_policy`
  from `iam.tf` when introducing `mcp_lambda_role`, not a hand-rolled inline policy.

## Changelog

- 2026-07-23 — Spec authored. Scope: comment/description alignment for ADR-0011 in
  neptune.tf (4 sites), iam.tf (3 sites), lambda.tf (3 sites), and test_plan.py (3
  docstrings). Added negative-assertion ACs and deferred criteria for behavioral IAM
  proof and audit logging. No new Terraform resources. Wave 1, ini-002.
