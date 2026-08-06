# Spec: infra-tf P0 gap remediation (Textract, gold/*, failure alerting, status registry)

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** ADR-0002 (no NAT / closed egress), ADR-0010 (Terraform), ADR-0016 (medallion / Gold artifacts), context-ontology gap inventory P0 (2026-08-05)
- **Contract:** none
- **Shape:** integration

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

Mode: full (risk triggers: security boundary — IAM grants + VPC endpoints; infra-flavored with live apply/destroy).

## Objective

The `apps/infra-tf` stack closes the four P0 functional gaps from the
2026-08-05 context-ontology gap inventory, so a context-ontology ingestion run
can complete and fail loudly:

1. **Scanned-PDF OCR works from the private subnet.** The Fargate ingestion
   task reaches Amazon Textract through a `textract` interface VPC endpoint and
   holds the `textract:DetectDocumentText` IAM action — the extractor at
   `packages/graphrag/src/graphrag/ingestion/_extraction/_textract.py` runs
   instead of timing out / being denied.
2. **Gold artifacts are writable.** `ingestion_task_role` holds a key-scoped
   `s3:PutObject` grant on `gold/*` in the corpus bucket, symmetric with the
   existing `silver/*` grant (ADR-0016 Gold layer).
3. **Ingestion failures alert the operator.** An EventBridge rule scoped to the
   `graphrag` ECS cluster matches stopped tasks with a non-zero container exit
   code or `stopCode = TaskFailedToStart`, and publishes to an SNS topic with an
   email subscription (same address as the Budgets alarm). No silent
   mid-pipeline failures.
4. **The ingestion status registry exists.** A DynamoDB table
   (`graphrag-ingestion-status`, on-demand, single `pk` string key) is
   provisioned with a DynamoDB gateway VPC endpoint, an ingestion-role RW grant
   scoped to the table ARN, and an `INGESTION_STATUS_TABLE` env var on the task
   definition. App-side writes (run/doc items) are a named deferral — this
   slice provisions the infrastructure only.

Success: the full stack applies cleanly, the alerting chain fires end-to-end on
a deliberately failed task, and the stack tears down cleanly.

## Boundaries

### Always do

- Preserve the closed-egress posture: every new network path is an explicit
  `aws_vpc_security_group_egress_rule` on a compute SG; endpoint SGs own zero
  egress (ADR-0002).
- Keep test_plan.py's pinned counts, `_TF_COMPUTE_SG_EGRESS`, the suffix/prefix
  maps, and the `security_groups.tf` header totals in lockstep with the HCL.
- Scope every new IAM grant to a constructed ARN. The single exception is
  `textract:DetectDocumentText`, which supports no resource-level permissions —
  `Resource: "*"` on that one action, with a comment naming the exception.
- Keep teardown-first: no deletion protection on new resources; the DynamoDB
  table must not block `terraform destroy`.
- Reuse `var.budget_alarm_email` as the SNS alert recipient.

### Ask first

- Any change to Neptune, OpenSearch, or existing Lambda resources.
- Any new required (no-default) Terraform variable.
- Widening any existing IAM grant beyond the four named additions.

### Never do

- No NAT gateway or internet gateway (ADR-0002 — hard rule).
- No changes to `packages/graphrag` app code in this slice (registry wiring is
  the named deferral, not scope creep).
- No new Terraform providers or modules; no `comprehend` endpoint (deferred
  until Comprehend-backed PII detection is enabled).
- No edits to the archived CDK reference (`apps/infra/`).

## Testing Strategy

**infra/deploy mode** (work-loop's layered GATES sequence), with the
plan-assertion pytest suite as the construction-test layer:

- **Static preflight:** `terraform fmt -check` + `terraform validate`.
- **Plan / preview (TDD-shaped):** the plan-assertion suite
  (`apps/infra-tf/tests/test_plan.py`, fresh-plan mode) is updated *first* to
  encode the new contract — endpoint count 8, extended egress table, new
  resource assertions (SNS topic + subscription, EventBridge rule pattern,
  DynamoDB table, three new ingestion-role policies) — red before the HCL
  lands, green after.
- **Idempotent convergent apply:** `terraform apply` against the live backend;
  a follow-up `terraform plan` shows zero changes.
- **Active end-to-end smoke (multi-hop):** deliberately run the ingestion task
  with no pushed image → it fails to start → the EventBridge rule matches →
  SNS publish observed via CloudWatch metrics (`AWS/Events Invocations` ≥ 1 on
  the rule / `AWS/SNS NumberOfMessagesPublished` ≥ 1). Plus: DynamoDB
  `put-item`/`get-item` round-trip, all 8 VPC endpoints `available`, existing
  `scripts/probe.sh` exits 0.
- **Rollback:** `terraform destroy` is the named rollback path (teardown-first
  stack); known stall workaround: re-issue delete and verify via API.

## Acceptance Criteria

- [x] **AC1 — Textract path.** The plan contains a `textract` interface
  endpoint (with its own SG accepting 443 from the VPC CIDR), an
  `ingestion_to_textract` egress rule (443, endpoint SG), and an
  `ingestion_task_role` inline policy allowing exactly
  `textract:DetectDocumentText` (`Resource: "*"`, commented as the documented
  no-resource-scoping exception). Plan-assertion tests cover all three.
- [x] **AC2 — Gold grant.** `ingestion_task_role` carries a dedicated inline
  policy `s3:PutObject` on `${corpus}/gold/*`; no bucket-wide PutObject
  appears. The pre-existing allowlist test
  (`test_ingestion_task_can_write_manifest_scoped_to_manifest_key`) has
  `"gold/"` appended to `_allowed_keys`; a new plan-assertion test covers the
  grant itself.
- [x] **AC3 — Failure alerting.** SNS topic `graphrag-ingestion-alerts` with an
  email subscription to `var.budget_alarm_email`; a topic policy allowing only
  `events.amazonaws.com` scoped by `aws:SourceArn` to the rule; an EventBridge
  rule (cluster-scoped, `lastStatus=STOPPED`, `$or`: any container
  `exitCode != 0` | `stopCode=TaskFailedToStart`) targeting the topic **via an
  `input_transformer`** that emits only cluster ARN, task ARN, stop code, and
  stopped reason — never the raw ECS event (task-role ARN, image URIs, and
  container-override env values stay out of plaintext email; per-container
  exit codes are deliberately omitted so an unresolvable JSONPath can't break
  the `TaskFailedToStart` branch, which carries no `exitCode`). The topic is **deliberately unencrypted**: SSE with the AWS-managed
  `aws/sns` key breaks EventBridge publishing (its key policy is unmodifiable
  and grants `events.amazonaws.com` nothing), and a CMK is the gap inventory's
  P2 #15, out of this slice — recorded as a `.trivyignore` AVD-AWS-0095 entry
  with this rationale. Plan-assertion tests cover topic, subscription, rule
  pattern, target, and transformer.
- [x] **AC4 — Status registry.** DynamoDB table `graphrag-ingestion-status`
  (`PAY_PER_REQUEST`, hash key `pk` type S, no deletion protection); DynamoDB
  gateway endpoint associated with both private route tables **and carrying an
  endpoint policy scoped to the status-table ARN** (defense-in-depth beyond
  the S3-gateway parity default); an `ingestion_to_dynamodb` egress rule (443,
  DynamoDB managed prefix list); an ingestion-role inline policy allowing
  `dynamodb:PutItem`, `UpdateItem`, `GetItem`, `Query` on the table ARN only;
  `INGESTION_STATUS_TABLE` env var on the task definition. Plan-assertion
  tests cover all six.
- [x] **AC5 — Gates.** `terraform fmt -check`, `terraform validate`,
  `trivy config --exit-code 1 --severity HIGH,CRITICAL apps/infra-tf/` (with
  the documented AVD-AWS-0095 ignore), and the full plan-assertion suite pass
  in fresh-plan mode; `test_has_6_vpc_endpoints` is updated to assert 8;
  `_TF_COMPUTE_SG_EGRESS.ingestion_task_sg` counts 10; the
  `security_groups.tf` header enumerates all **five** compute SGs (ingestion
  10, smoke 3, vector-smoke 4, query 5, mcp 5 = **27** — correcting the
  pre-existing header that omitted `mcp_lambda_sg`), reads **7**
  compute/store SGs on line 1 (5 compute + 2 store), and reads **6** endpoint
  SGs (Textract joins the interface-endpoint set).
- [x] **AC6 — Live cycle.** `terraform apply` converges (follow-up plan: no
  changes); all 8 VPC endpoints reach `available`; the DynamoDB table is
  `ACTIVE` and round-trips a put/get; **both** alert branches fire live —
  (a) `run-task` with no image in ECR → `TaskFailedToStart`, then (b) after
  pushing a minimal shell image, `run-task` with a non-zero-exit command
  override → `exitCode != 0` — each observed as rule `Invocations` ≥ 1 and
  topic `NumberOfMessagesPublished` ≥ 1 (**publish observed; email delivery
  is unverified** — the subscription stays pending-confirmation, per the
  Assumptions); `scripts/probe.sh` exits 0; `terraform destroy` completes
  with zero orphaned resources (the pending email subscription expires
  AWS-side ≤ 3 days — documented, not an orphan).
- [x] **AC7 — Fixture refresh.** `tests/fixtures/plan.json` is regenerated from
  applied state (post-apply `terraform plan` → `show -json`) per the
  infra-terraform-verification convention.
- [ ] **AC8 — Registry app wiring.** (deferred: ingestion-status-registry-app-wiring)
  The entrypoint writes of run/doc items are recorded in
  `workspace.toml [backlog].open`; this slice provisions the infrastructure only.

## Assumptions

- Technical: Terraform >= 1.11 / AWS provider ~> 5.0; S3 backend via
  `-backend-config=backend.hcl` (source: `apps/infra-tf/versions.tf`,
  `backend.tf`, `backend.hcl.example`)
- Technical: the Textract extractor calls the synchronous
  `detect_document_text` API with inline bytes — the only IAM action needed is
  `textract:DetectDocumentText` (source:
  `packages/graphrag/src/graphrag/ingestion/_extraction/_textract.py:28`)
- Technical: the EventBridge pattern
  `STOPPED + $or(exitCode anything-but 0 | TaskFailedToStart)` matches failed
  and failed-to-start events and rejects exit-0 (source: probe —
  `aws events test-event-pattern`, 2026-08-05: True / False / True)
- Technical: `com.amazonaws.us-east-1.dynamodb` managed prefix list and
  `com.amazonaws.us-east-1.textract` Interface endpoint service exist (source:
  probe — `describe-managed-prefix-lists` / `describe-vpc-endpoint-services`,
  2026-08-05)
- Technical: no `gold/*` writes and no DynamoDB calls exist in
  `packages/graphrag` today — grants and table are forward-provisioning per
  ADR-0016 / design.md; app wiring is the AC8 deferral (source: grep,
  2026-08-05)
- Technical: `aws_sns_topic_subscription` `protocol = "email"` creates a
  pending-confirmation subscription; unconfirmed subscriptions expire ≤ 3 days
  after destroy (source: provider docs knowledge; confirmed at apply as part
  of AC6 — local schema probe unavailable pre-init)
- Process: plan-assertion counts and the `security_groups.tf` header are
  contract surfaces that move in lockstep with the HCL (source:
  `apps/infra-tf/tests/test_plan.py:71,260`; `security_groups.tf:12`)
- Product: the SNS alert recipient is the Budgets-alarm address — no new
  variable (source: design.md § Ingestion status registry and failure
  alerting; user instruction 2026-08-05)
- Process: live deploy + teardown is authorized in this environment (source:
  user instruction 2026-08-05; memory live-deploy-available)
