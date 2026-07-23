# Spec: infra-terraform-scaffold

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0010](../../adr/0010-terraform-migration.md) (migrate IaC from CDK to Terraform — this spec ships the first slice of that decision); [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (the ephemeral VPC topology; teardown is a feature); generate-iac skill (`SKILL.md`) — the governing IaC authoring skill
- **Shape:** data (infrastructure scaffolding; no application logic)

> **Spec contract:** this document defines what "done" means for the Terraform
> scaffold tier. The implementing PR must match this spec, or update it.
> Verification must be derivable from it.

> **Terraform scaffold** for the GraphRAG AWS demo — provider config, backend,
> variable definitions, and an outputs shell — that forms the foundation every
> subsequent Terraform spec builds on. ADR-0010 records the decision to migrate;
> this spec ships the first artifact of that decision: a valid, `terraform
> validate`-clean root module under `apps/infra-tf/`.

## Objective

Establish the Terraform root module skeleton for `apps/infra-tf/` so that every
subsequent implementation spec (network, data + IAM, compute, verification) has a
clean, versioned, reproducible foundation to build on. The deliverables are five
files: `versions.tf`, `provider.tf`, `backend.tf`, `variables.tf`, `outputs.tf`.
ADR-0010 is authored alongside (it records the decision; the scaffold is the first
tangible implementation artifact of that decision).

The scaffold is **deploy-ready at the foundation tier**: `terraform init` and
`terraform validate` pass on a clean checkout with no resources defined yet;
`terraform fmt -check` exits 0; all required variable names match the CDK
`CfnParameter` names (mapping documented in the plan).

## Boundaries

### Always do

- **Use Terraform ≥ 1.11 and AWS provider ~> 5.0.** Native S3 state locking (GA in
  1.11) removes the DynamoDB table bootstrap; no `dynamodb_table` in the backend
  block. Pin the exact provider version in `versions.tf`.
- **Translate every CDK `CfnParameter` to a Terraform variable.** Three parameters
  map to required variables (no default, must be supplied): `budget_alarm_email`
  (string), `invoker_role_arn` (string), `s3_prefix_list_id` (string, validated by
  regex `^pl-[0-9a-f]+$`). The regex on `s3_prefix_list_id` is load-bearing (mirrors
  the CDK `AllowedPattern`; rejects a CIDR or free-form value at plan time).
- **Declare all five governance tag variables with defaults matching
  `_GOVERNANCE_TAG_DEFAULTS`.** `environment = "demo"`, `project =
  "graphrag-aws-template"`, `department = "unspecified"`, `application =
  "graphrag"`, `user = "unspecified"`. Apply via `default_tags` in `provider.tf`
  so every resource inherits them without per-resource `tags = {}` blocks.
- **S3 backend with native state locking.** Backend block configures `bucket`,
  `key`, `region`, `encrypt = true`. Supply a companion `backend.hcl.example`
  with placeholder values so the first-time bootstrap is documented.
- **`outputs.tf` shell.** Declare all 12 output names matching the CDK
  `CfnOutput` names (`CorpusBucketName`, `NeptuneEndpoint`, `EcsClusterName`,
  `IngestionTaskDefArn`, `IngestionSecurityGroupId`, `PrivateSubnetId`,
  `IngestionRepoUri`, `SmokeProbeName`, `OpenSearchEndpoint`,
  `VectorSmokeProbeName`, `QueryFunctionUrl`, `QueryLambdaName`) with placeholder
  `value = null` bodies; filled in by subsequent specs.
- **`terraform fmt -check` must pass** — HCL formatting is non-negotiable.

### Ask first

- Changing `required_version` below 1.11 (removes native state locking).
- Adding a second cloud provider (not in scope for this repo; ADR-0002 is AWS-only).
- Changing the backend type away from S3 (changes the bootstrap procedure).

### Never do

- **Never emit `terraform apply` or `terraform destroy`** — this skill's deliverable
  stops at a clean `plan` (generate-iac SKILL.md hard rule).
- **Never commit `*.tfvars` with real values** — `backend.hcl.example` is the only
  tfvars-shaped file and carries only placeholder values.
- **Never use DynamoDB state locking** — superseded by native S3 locking in
  Terraform ≥ 1.11 (generate-iac skill anti-pattern).
- **Never hardcode region or account ID** — both are inputs (region via the provider
  `region` variable; account implicit from the caller's credentials).

## Testing Strategy

All ACs in this spec are **goal-based check** mode — the validation artifact is a
successful CLI invocation, not a unit test file.

- **AC1–AC6 — goal-based.** Each file's structure is verified by `terraform
  validate` (schema correctness) and `terraform fmt -check` (formatting). A
  `terraform plan -var-file=backend.hcl.example` with dummy var values confirms the
  variable validations fire correctly (the `s3_prefix_list_id` regex rejects a CIDR
  at plan time).
- **AC7 — goal-based.** `terraform init` with the S3 backend configured to a real
  or mock bucket exits 0; `terraform validate` exits 0; `terraform fmt -check` exits
  0. The backend bootstrap script is a deliverable, not a test — it is documented and
  manually verified on the first live deploy.

Gates: `terraform fmt -check`, `terraform validate` (run from `apps/infra-tf/`
after `terraform init -backend=false`).

## Acceptance Criteria

- [ ] **AC1 — `versions.tf`: pinned Terraform + provider versions.** *(goal-based
  check)* `required_version = ">= 1.11"` and `required_providers { aws = { source =
  "hashicorp/aws", version = "~> 5.0" } }` are present. No other provider is
  declared.

- [ ] **AC2 — `provider.tf`: AWS provider with default governance tags.** *(goal-based
  check)* The `provider "aws" {}` block configures `default_tags { tags = { ... } }`
  using the five governance tag variables (`var.environment`, `var.project`,
  `var.department`, `var.application`, `var.user`) matching the CDK
  `_GOVERNANCE_TAG_DEFAULTS` keys and defaults. Region is `var.aws_region`.

- [ ] **AC3 — `backend.tf`: S3 backend with native state locking, no DynamoDB.** *(goal-based
  check)* The `terraform { backend "s3" { ... } }` block is present with `encrypt =
  true` and no `dynamodb_table` key. A companion `backend.hcl.example` documents the
  bootstrap values (`bucket`, `key`, `region`) with placeholder strings.

- [ ] **AC4 — `variables.tf`: all required + governance variables with correct types and
  validation.** *(goal-based check)* Required variables: `budget_alarm_email` (string,
  no default, description matches CDK parameter), `invoker_role_arn` (string, no
  default), `s3_prefix_list_id` (string, no default, `validation { condition =
  can(regex("^pl-[0-9a-f]+$", var.s3_prefix_list_id)) }`). Governance variables:
  `environment`, `project`, `department`, `application`, `user`, `aws_region` — all
  string with defaults matching `_GOVERNANCE_TAG_DEFAULTS`. Supplying `s3_prefix_list_id =
  "0.0.0.0/0"` causes `terraform validate` to reject it (regex mismatch).

- [ ] **AC5 — `outputs.tf`: shell output blocks for all 12 CDK CfnOutput names.** *(goal-based
  check)* All 12 output names from the CDK stack are declared: `corpus_bucket_name`,
  `neptune_endpoint`, `ecs_cluster_name`, `ingestion_task_def_arn`,
  `ingestion_security_group_id`, `private_subnet_id`, `ingestion_repo_uri`,
  `smoke_probe_name`, `opensearch_endpoint`, `vector_smoke_probe_name`,
  `query_function_url`, `query_lambda_name`. Each body references the resource
  output that later specs will fill in (stubs use `null` until the resource is
  defined).

- [ ] **AC6 — `terraform fmt -check` exits 0 across all scaffold files.** *(goal-based
  check)* No formatting violations on any `.tf` file in `apps/infra-tf/`.

- [ ] **AC7 — `terraform init && terraform validate` exits 0 from `apps/infra-tf/`.** *(goal-based
  check)* With a `.terraform` directory initialized against a stub backend (local or
  S3 mock) and the five required variables supplied, `terraform validate` exits 0 and
  reports "Success! The configuration is valid."

## Assumptions

- Technical: Terraform CLI ≥ 1.11 is available in the deploy environment; the AWS
  provider hashicorp/aws ~> 5.0 is the target (source: generate-iac SKILL.md;
  ADR-0010).
- Technical: native S3 state locking (no DynamoDB) is available in Terraform ≥ 1.11
  (source: Terraform 1.11 release notes; generate-iac skill anti-patterns section).
- Technical: the S3 backend bucket is pre-provisioned (out of scope for this spec;
  documented in `bootstrap.sh`); the backend.hcl.example documents the values
  needed (source: generate-iac skill bootstrap-sequence reference).
- Technical: the `apps/infra-tf/` directory is created alongside `apps/infra/` (the
  CDK app), not replacing it; CDK is archived after all Terraform specs pass their
  live ACs (source: ADR-0010 decision text).
- Technical: the five governance tag variable names are lowercase
  (`environment`, `project`, `department`, `application`, `user`) matching the CDK
  `_GOVERNANCE_TAG_DEFAULTS` keys lowercased (source:
  `apps/infra/stacks/graphrag_stack.py` `_GOVERNANCE_TAG_DEFAULTS`).
- Process: this spec ships in full mode (new dependency on Terraform, structural
  change, governance boundary); ADR-0010 is the governance record (source:
  CONVENTIONS.md risk triggers).

## Changelog

- 2026-07-22 — Spec authored. Terraform scaffold tier for the CDK→Terraform migration
  (ADR-0010): versions.tf, provider.tf, backend.tf, variables.tf, outputs.tf shell.
  Goal-based ACs; validation by `terraform init && validate && fmt -check`. Five
  subsequent specs build on this foundation.
