# Plan: infra-tf P0 gap remediation

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Tests first, HCL second, live cycle last. The plan-assertion suite
(`apps/infra-tf/tests/test_plan.py`) is the construction-test layer: extend it
to encode the new contract (8 endpoints, 10 ingestion egress rules, SNS +
EventBridge + DynamoDB + 3 new IAM policies), watch it fail in fresh-plan mode,
then land the HCL across the existing topical files (`network.tf`,
`security_groups.tf`, `iam.tf`, `compute.tf`, `outputs.tf`) plus two new ones
(`dynamodb.tf`, `alerting.tf`). The Terraform authoring runs under the
`generate-iac` skill. The riskiest part is the live cycle (apply → multi-hop
alert smoke → destroy): the alert smoke deliberately runs the ingestion task
with no container image pushed, so the task fails to start and exercises the
`TaskFailedToStart` branch of the rule end-to-end without a Docker build.

Files touched: `apps/infra-tf/{network,security_groups,iam,compute,outputs}.tf`,
new `apps/infra-tf/{dynamodb,alerting}.tf`, `apps/infra-tf/tests/test_plan.py`,
`apps/infra-tf/tests/fixtures/plan.json` (regenerated), `workspace.toml`
(backlog entry), `docs/specs/README.md`. Not changing: `packages/graphrag`,
`apps/infra/` (CDK reference), Neptune/OpenSearch/Lambda resources, budget.

Declined temptations: tempted to add a `comprehend` endpoint while in
`network.tf` (design lists it as optional) — declining, deferred until
Comprehend-backed PII detection is enabled. Tempted to wire the entrypoint to
write status items so the table isn't dead infra — declining, app code is out
of this slice (AC8 deferral). Tempted to add a separate `alert_email` variable
— declining, the design pins the Budgets-alarm address and a second required
variable taxes every operator.

## Constraints

- ADR-0002: no NAT/IGW; closed egress via explicit SG rules; teardown-first.
- ADR-0010: Terraform is the IaC layer; CDK stack is a frozen reference.
- ADR-0016: Gold artifacts under `gold/` in the corpus bucket.
- design.md § "Ingestion status registry and failure alerting" — the registry
  schema and alerting semantics this plan provisions for.
- Verification conventions from `docs/specs/infra-terraform-verification/`:
  plan-assertion suite in fresh-plan mode; fixture regenerated from applied
  state; `scripts/probe.sh` as the live probe.

## Construction tests

Per-task tests live under Tasks. Cross-cutting:

**Integration tests:** the full plan-assertion suite in fresh-plan mode (T4)
verifies the assembled plan, not per-file fragments.
**Manual verification:** the live cycle (T5) — convergent apply, endpoint/table
state checks, the deliberate failed-task alert probe, `probe.sh`, destroy.

## Design (LLD)

### Design decisions

- **Textract joins the `interface_endpoints` for_each map** in `network.tf`
  (key `Textract`), inheriting the endpoint+SG+ingress pattern — no bespoke
  endpoint resource. Traces to: AC1.
- **DynamoDB is a gateway endpoint** (route-table-associated, no hourly cost),
  mirroring the S3 gateway; ingestion egress uses the AWS-managed DynamoDB
  prefix list resolved via `data.aws_ec2_managed_prefix_list` (same SEC-2
  posture as S3 — never operator-supplied). Traces to: AC4.
- **`textract:DetectDocumentText` gets `Resource: "*"`** — Textract supports no
  resource-level permissions; single action, commented as the documented
  exception to the no-wildcard rule. Traces to: AC1.
- **The EventBridge → SNS grant is a topic resource policy** (`sns:Publish`
  for `events.amazonaws.com`, `aws:SourceArn` = rule ARN) — SNS targets use
  resource policies, not target roles. Traces to: AC3.
- **The target carries an `input_transformer`** emitting only
  `{clusterArn, taskArn, stopCode, stoppedReason}` — the raw ECS event
  over-discloses (task-role ARN, image URIs, override env values) into
  plaintext email; per-container exit codes are deliberately omitted so an
  unresolvable JSONPath can't break the no-exitCode `TaskFailedToStart`
  branch (security review 2026-08-05 #1). Traces to: AC3.
- **The topic is deliberately unencrypted, with a `.trivyignore` entry.**
  SSE via the AWS-managed `aws/sns` key silently breaks EventBridge→SNS (the
  key policy is unmodifiable and grants `events.amazonaws.com` nothing); a
  CMK is gap-inventory P2 #15, out of slice. AVD-AWS-0095 ignored with this
  rationale; CMK named as the adopter upgrade path (security review
  2026-08-05 #2). Traces to: AC3, AC5.
- **The DynamoDB gateway endpoint carries a table-scoped endpoint policy**
  (`dynamodb:*` on the status-table ARN) — defense-in-depth beyond the
  default-open S3-gateway parity (security review 2026-08-05 #3). Traces to:
  AC4.
- **IAM-policy plan assertions read `configuration.root_module...expressions`,
  not `planned_values`** — jsonencode over any known-after-apply input
  collapses the whole policy string to unknown, so a planned_values assertion
  silently skips (K-0030). Traces to: AC1, AC2, AC4.
- **Table name is fixed** (`graphrag-ingestion-status`), matching the fixed
  OpenSearch domain / ECS cluster naming convention; IAM references the
  resource ARN directly (no cycle — the table doesn't reference the role).
  Traces to: AC4.

### Dependencies & integration

- EventBridge rule pattern depends on ECS Task State Change event shape —
  verified by probe 2026-08-05 (`test-event-pattern`: fail/ok/no-start).
- The alert smoke's branch (a) depends on the ECR repo being empty so
  `RunTask` yields `TaskFailedToStart`. A fresh `terraform apply` after
  `destroy` recreates an empty `graphrag-ingestion` repo, guaranteeing the
  precondition; if a stray image exists, `aws ecr batch-delete-image` restores
  it. Branch (b) then pushes the busybox image deliberately — ordering is
  (a) before (b), never a fallback collapse of one into the other.
- `INGESTION_STATUS_TABLE` env var is consumed by nothing yet (AC8 deferral) —
  additive, no app coupling.

### Failure, edge cases & resilience

- Multi-container future: the pattern matches *any* container with non-zero
  exit (EventBridge array semantics) — correct for alerting.
- Task killed (SIGKILL/137) or stopped by user with non-zero exit: alerts.
  Acceptable — operator-initiated stops of a mid-run task are failure-shaped.
- Unconfirmed SNS email subscription at destroy time: AWS cannot delete
  pending subscriptions; they expire ≤ 3 days. Documented in spec AC6, not an
  orphan.
- Endpoint-count drift: `test_has_6_vpc_endpoints` renamed/updated to 8 so the
  suite, the HCL comment, and the fixture cannot silently diverge.

## Tasks

### T1: Plan-assertion tests encode the new contract (red)

**Depends on:** none
**Touches:** apps/infra-tf/tests/test_plan.py

**Tests:** (this task *is* the test-authoring task — its deliverable)
- `test_has_8_vpc_endpoints` (renamed from 6) — AC1/AC4; asserts a `textract`
  Interface endpoint and a `dynamodb` Gateway endpoint with 2 route-table ids.
- `_TF_COMPUTE_SG_EGRESS["ingestion_task_sg"]` gains
  `("endpoint_Textract", 443)` and `("dynamodb_prefix_list", 443)`;
  `_EGRESS_TARGET_FROM_SUFFIX` gains exactly `"to_textract":
  "endpoint_Textract"` and `"to_dynamodb": "dynamodb_prefix_list"` (mirroring
  `to_s3` → `s3_prefix_list` — a wrong label makes `_classify_egress_rule`
  silently drop the rule from the set-equality check) — AC1/AC4.
- `test_ingestion_task_can_write_manifest_scoped_to_manifest_key`:
  `_allowed_keys` gains `"gold/"` — without it the new grant regresses this
  pre-existing allowlist (adversarial review 2026-08-05 Blocker #1) — AC2.
- New IAM-policy assertions read the `configuration` block expressions, not
  `planned_values` (K-0030) — AC1/AC2/AC4.
- `test_ecs_failure_rule_target_uses_input_transformer` — target carries an
  input_transformer whose template contains no `$.detail.overrides` /
  image-URI paths — AC3.
- `test_dynamodb_gateway_endpoint_policy_scoped` — endpoint policy resource
  is the status-table ARN, never `"*"` unscoped — AC4.
- `test_ingestion_textract_policy` — exactly `textract:DetectDocumentText`,
  Resource `"*"`, and no other action shares that statement — AC1.
- `test_ingestion_gold_put_policy` — `s3:PutObject` on `/gold/*`; and no
  bucket-wide PutObject on any role — AC2.
- `test_ingestion_status_table` — PAY_PER_REQUEST, hash key `pk` (S), no
  deletion protection — AC4.
- `test_ingestion_dynamodb_policy` — exactly Put/Update/Get/Query on the table
  ARN — AC4.
- `test_ingestion_alerts_topic_and_subscription` — topic present; email
  subscription; topic policy principal `events.amazonaws.com` with SourceArn
  condition — AC3.
- `test_ecs_failure_rule_pattern` — rule pattern JSON contains cluster scoping,
  `STOPPED`, `$or` with `anything-but: 0` and `TaskFailedToStart`; target is
  the topic — AC3.
- `test_task_def_has_status_table_env` — `INGESTION_STATUS_TABLE` in container
  env — AC4.

**Approach:** extend the existing helper/table style; keep the CDK-parity
docstring convention (cite the gap-inventory item instead of a CDK test name).

**Done when:** new tests exist and fail against current HCL in fresh-plan mode
(collected, red); pre-existing tests still pass.

### T2: Network + SG HCL — Textract endpoint, DynamoDB gateway, egress rules

**Depends on:** T1
**Touches:** apps/infra-tf/network.tf, apps/infra-tf/security_groups.tf

**Tests:** T1's endpoint + egress assertions go green.
**Approach:**
- `network.tf`: add `Textract = "textract"` to `local.interface_endpoints`;
  add `data.aws_ec2_managed_prefix_list.dynamodb` + `aws_vpc_endpoint.dynamodb_gateway`
  (route-table associated, both RTs).
- `security_groups.tf`: `ingestion_to_textract` (443 → endpoint SG),
  `ingestion_to_dynamodb` (443 → prefix list); rewrite the header totals to
  enumerate all five compute SGs — ingestion 10, smoke 3, vector_smoke 4,
  query 5, mcp 5 = 27 (the pre-existing header claimed 20/4 SGs, omitting
  `mcp_lambda_sg` — adversarial #3 / security #4) — and fix the two sibling
  stale counts in the same pass: line 1 "6 compute/store" → 7, line 15
  "5 endpoint SGs" → 6 (Textract joins the interface set).
- `network.tf`: the DynamoDB gateway endpoint carries a `policy` scoped to
  the status-table ARN.

**Done when:** T1 network/egress tests green; `terraform fmt -check` +
`validate` pass.

### T3: IAM + data-plane HCL — textract, gold, DynamoDB table + policy, alerting

**Depends on:** T1 (parallel-safe with T2)
**Touches:** apps/infra-tf/iam.tf, apps/infra-tf/dynamodb.tf, apps/infra-tf/alerting.tf, apps/infra-tf/compute.tf, apps/infra-tf/outputs.tf

**Tests:** T1's IAM/table/alerting/env assertions go green.
**Approach:**
- `dynamodb.tf` (new): `aws_dynamodb_table.ingestion_status` —
  `graphrag-ingestion-status`, PAY_PER_REQUEST, hash key `pk` (S).
- `alerting.tf` (new): `aws_sns_topic.ingestion_alerts` (unencrypted — see
  Design decisions; comment carries the aws/sns-key rationale),
  `aws_sns_topic_subscription` (email → `var.budget_alarm_email`),
  `aws_sns_topic_policy` (events.amazonaws.com, SourceArn = rule),
  `aws_cloudwatch_event_rule.ecs_task_failed` (probe-verified pattern,
  cluster-scoped), `aws_cloudwatch_event_target` (topic, with
  `input_transformer` — cluster/task/stopCode/stoppedReason only).
- `.trivyignore`: AVD-AWS-0095 with the EventBridge-vs-aws/sns-key rationale
  and the P2 CMK pointer, in `apps/infra-tf/.trivyignore` — the register CI
  reads (`working-directory: apps/infra-tf`). (Corrected post-ship: PR #101
  put it at the repo root, which trivy's CI invocation never reads.)
- `iam.tf`: three new inline policies on `ingestion_task_role`
  (`textract-detect`, `s3-put-gold`, `dynamodb-status-rw`).
- `compute.tf`: `INGESTION_STATUS_TABLE` env var.
- `outputs.tf`: `ingestion_alerts_topic_arn`, `ingestion_status_table_name`.

**Done when:** T1 suite fully green in fresh-plan mode.

### T4: Gates — full static + plan verification

**Depends on:** T2, T3
**Tests:** `terraform fmt -check`, `terraform validate`,
`trivy config --exit-code 1 --severity HIGH,CRITICAL apps/infra-tf/` (if
installed locally; else CI-owned, named skip), full pytest suite (fresh-plan
mode) — all green; zero pre-existing test regressions.
**Approach:** fresh-plan mode needs the K-0029 workaround — a gitignored
`backend_override.tf` (local backend) + `terraform init -reconfigure`, live
AWS creds for `data.aws_availability_zones` / prefix-list lookups; delete the
override before the T5 real deploy. Run the gates; fix anything surfaced.
**Done when:** AC5 checked.

### T5: Live cycle — apply, smoke, fixture, destroy

**Depends on:** T4
**Tests:** (manual/infra verification — recorded observations)
- Convergent apply: `terraform apply` then `terraform plan` → "no changes".
- 8 VPC endpoints `available`; DynamoDB table `ACTIVE` + put/get round-trip.
- Alert chain, **both branches** (adversarial #2): (a) before any image push,
  `aws ecs run-task` → `TaskFailedToStart`; (b) push a minimal shell image
  (docker pull busybox `--platform linux/amd64` → tag → push to the
  `graphrag-ingestion` repo — pull/tag/push only, no buildx), then `run-task`
  with command override `["sh","-c","exit 7"]` → `exitCode != 0`. Each
  observed as rule `Invocations` ≥ 1 / topic `NumberOfMessagesPublished` ≥ 1
  (CloudWatch). Publish, not delivery — the email subscription stays pending.
  If no docker daemon is available in this workspace, branch (b) degrades to
  the static `test-event-pattern` probe with a named skip recorded in the
  summary — never a silent pass.
- `scripts/probe.sh` exits 0.
- Fixture: post-apply plan → `show -json` → `tests/fixtures/plan.json`; suite
  green against the fixture (`TFPLAN_JSON_PATH`).
- `terraform destroy` completes; API sweep confirms no orphans (pending email
  subscription exempt per AC6).
**Approach:** export static credentials first
(`aws configure export-credentials --format env-no-export` — long-run
protection). Build the Lambda zip the stack references
(`apps/graphrag/dist/graphrag.zip`, gitignored): `pip install` the graphrag
package + `mcp`/`mangum` extras into a build dir with
`--platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all:`
(pydantic-core is compiled; the Lambdas are x86_64 python3.12), overlay
`packages/graphrag/src/graphrag`, zip. Create `backend.hcl` from the example
(state bucket `graphrag-tf-state-<redacted>` exists from the prior cycle;
skip bootstrap). Remove `backend_override.tf`, `terraform init
-backend-config=backend.hcl -reconfigure`, then drive the cycle reading real
output; on destroy stall, re-issue delete and verify via API, sweep log
groups.
**Done when:** AC6 + AC7 checked with recorded observations.

### T6: Docs, backlog, ship hygiene

**Depends on:** T5
**Touches:** workspace.toml, docs/specs/README.md, docs/specs/infra-tf-p0-gap-remediation/spec.md

**Tests:** `lint-spec-status.py` passes; AC8's `(deferred:)` slug resolves in
`workspace.toml [backlog].open`.
**Approach:** add `ingestion-status-registry-app-wiring` backlog entry
(cold-start-sufficient comment: entrypoint writes run/doc items per design.md
registry schema; unblocked by this slice's table + env var); add spec to
`docs/specs/README.md`; flip spec status; capture learnings.
**Done when:** finish-time checklist all true.

## Rollout

- **Delivery:** single PR, big bang for a template stack — no live tenants.
  Rollback = `terraform destroy` (the stack is torn down at the end of the
  live cycle anyway; nothing stays deployed).
- **Infrastructure:** everything in this plan is infrastructure; provisioned
  by `terraform apply` against the S3-backend state.
- **External-system integration:** none new. The CodeStar connection is only
  needed for a real ingestion run, not for this slice's smoke (the alert probe
  uses `run-task` directly).
- **Deployment sequencing:** none — one apply carries endpoint + IAM + table +
  alerting together; no consumer exists yet for the table (AC8 deferral).
- **Standing-cost note:** the Textract interface endpoint adds ~$15/mo
  (2 AZs); DynamoDB gateway endpoint and on-demand table at rest are ~$0. No
  budget.tf change (the stack tears down after validation).

## Risks

- **Live apply cost/time:** Neptune + OpenSearch + endpoints run for the cycle
  duration (~1–2 h); teardown is part of T5, and the teardown-stall workaround
  is known (re-issue delete, verify via API, sweep log groups).
- **Fixture churn:** regenerating `plan.json` from applied state produces a
  large diff; reviewers should treat it as generated (convention already
  established by infra-terraform-verification).
- **Backend prerequisites:** `backend.hcl` is gitignored and may not exist in
  this workspace; `scripts/bootstrap.sh` + example file cover cold start.
- **SNS pending subscription:** destroy leaves a ≤3-day pending email
  subscription AWS-side; documented, no action.

## Changelog

- 2026-08-05: initial plan.
- 2026-08-05: pre-EXECUTE review round folded in. Adversarial (1 Blocker):
  `_allowed_keys` gains `"gold/"`; both alert branches fire live (busybox
  push + exit-override); header totals corrected to 5-SG/27; explicit egress
  suffix→label values; AC6 scoped to publish-not-delivery. Security (2
  Concerns, 2 Nits): input_transformer on the SNS target; deliberate
  unencrypted topic + `.trivyignore` AVD-AWS-0095 (aws/sns key breaks
  EventBridge publish; CMK is P2); table-scoped DynamoDB endpoint policy.
  Self-found: Lambda-zip build step, trivy gate in T4, K-0029
  backend_override for fresh-plan gates, K-0030 configuration-expression
  assertion style.
