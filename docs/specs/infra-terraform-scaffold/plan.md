# Plan: infra-terraform-scaffold

- **Spec:** [`spec.md`](spec.md)
- **Status:** Done <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy for the Terraform scaffold
> tier. It may change as implementation proceeds; note substantial changes in the
> changelog.

## Approach

Author the five scaffold files for `apps/infra-tf/` from scratch, following the
generate-iac skill's four-file provider-contract shape plus outputs shell. All
files are HCL; `terraform fmt` is applied before commit. The CDK's three
`CfnParameter` instances translate directly to required Terraform variables; the
CDK's `_GOVERNANCE_TAG_DEFAULTS` dict translates to optional variables applied via
`default_tags`. ADR-0010 is authored first (it is the governance record that
legitimizes this work). The `bootstrap.sh` helper handles the one-time S3 backend
creation that CDK did not require.

The riskiest part is the variable validation for `s3_prefix_list_id`
(**superseded 2026-07-22 by `infra-terraform-network` SEC-2**: this variable was
removed; the S3 managed prefix list is now resolved via a data source, so the
validation described below no longer exists): the CDK
`AllowedPattern` is a CloudFormation-layer check; the Terraform equivalent is a
`validation` block with `can(regex(...))`. Both reject a CIDR or free-form value at
their respective validation layers.

## Constraints

- generate-iac skill hard rules: never emit `terraform apply`; never commit
  `*.tfvars` with real values; no DynamoDB state locking; provider version pinned;
  `terraform fmt -check` clean; `terraform validate` exits 0.
- ADR-0010: Terraform ≥ 1.11, AWS provider ~> 5.0, S3 backend.
- ADR-0002: teardown-first topology is unchanged; the scaffold does not provision
  resources — that is subsequent specs.
- AGENTS.md: `apps/infra-tf/` is a new directory under `apps/` (not a new
  top-level directory; no RFC needed).

## Design (LLD)

### Variable → CDK parameter mapping

| Terraform variable | CDK CfnParameter | Type | Default |
|---|---|---|---|
| `budget_alarm_email` | `BudgetAlarmEmail` | `string` | (required) |
| `invoker_role_arn` | `InvokerRoleArn` | `string` | (required) |
| `s3_prefix_list_id` | `S3PrefixListId` | `string` | (superseded: infra-terraform-network SEC-2 — removed; now a data source) |
| `aws_region` | (implicit in CDK env) | `string` | `"us-east-1"` |
| `environment` | CDK context `environment` | `string` | `"demo"` |
| `project` | CDK context `project` | `string` | `"graphrag-aws-template"` |
| `department` | CDK context `department` | `string` | `"unspecified"` |
| `application` | CDK context `application` | `string` | `"graphrag"` |
| `user` | CDK context `user` | `string` | `"unspecified"` |

### `default_tags` application

The AWS provider's `default_tags` block applies the five governance tags to every
resource that supports tags without per-resource `tags = {}`. The CDK equivalent
was `Tags.of(self).add(key, value)` walking the construct tree. The Terraform
approach is more direct: one block in the provider, zero per-resource blocks.

```hcl
provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Environment = var.environment
      Project     = var.project
      Department  = var.department
      Application = var.application
      User        = var.user
    }
  }
}
```

### Backend configuration shape

```hcl
terraform {
  backend "s3" {
    # Values supplied via -backend-config=backend.hcl at init time
    # or via environment variables. Never hardcoded here.
    encrypt = true
    # No dynamodb_table — native S3 locking (Terraform >= 1.11)
  }
}
```

The `backend.hcl.example` documents:
```hcl
bucket = "your-terraform-state-bucket"
key    = "graphrag-aws-template/terraform.tfstate"
region = "us-east-1"
```

### File layout

```
apps/infra-tf/
├── versions.tf          # required_version + required_providers
├── provider.tf          # aws provider + default_tags
├── backend.tf           # s3 backend block (values via -backend-config)
├── backend.hcl.example  # example backend config (no real values)
├── variables.tf         # all input variables
├── outputs.tf           # output shells (stubs until subsequent specs fill them)
└── scripts/
    └── bootstrap.sh     # one-time S3 state bucket creation
```

## Tasks

### T1: Author ADR-0010 + update ADR-0003 status

**Depends on:** none
**Touches:** `docs/adr/0010-terraform-migration.md`, `docs/adr/0003-iac-tool-aws-cdk-python.md`
**Tests:** goal-based — ADR-0010 exists with `Status: Accepted`; ADR-0003's status
  field reads `Superseded by ADR-0010` (body unchanged, per CONVENTIONS.md § 2 —
  status fields change, bodies do not).
**Approach:** ADR-0010 already exists at `Status: Accepted`. Update ADR-0003's
  status line from `Accepted` to `Superseded by ADR-0010` (one-line status field
  change; body is frozen and untouched).
**Done when:** ADR-0010 is `Status: Accepted`; ADR-0003 is `Status: Superseded by ADR-0010`.
**Status: Done** — ADR-0010 existed; ADR-0003 status updated in this PR.

---

### T2: Create `apps/infra-tf/` directory + `versions.tf`

**Depends on:** T1
**Touches:** `apps/infra-tf/versions.tf`
**Tests:** goal-based — `terraform validate` exits 0 from `apps/infra-tf/` after `terraform init -backend=false`;
  `grep 'required_version' apps/infra-tf/versions.tf` shows `>= 1.11` (no extra quotes around version number);
  `grep 'version.*~> 5' apps/infra-tf/versions.tf` shows `~> 5.0` for the aws provider.
**Approach:** Create `apps/infra-tf/versions.tf` with `terraform { required_version = ">= 1.11"` and
  `required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } } }`. No other providers.
  The constraint string is `">= 1.11"` — the version number itself is not additionally quoted.
**Done when:** `terraform init -backend=false && terraform validate` exits 0; `terraform fmt -check` exits 0.

---

### T3: Create `provider.tf` with default governance tags

**Depends on:** T2
**Touches:** `apps/infra-tf/provider.tf`, `apps/infra-tf/variables.tf` (stub for tag vars)
**Tests:** goal-based — `grep 'default_tags' apps/infra-tf/provider.tf` shows the block;
  `terraform validate` still exits 0 after adding the provider block with variable
  references (requires the governance variables to exist in variables.tf).
**Approach:** Write `provider "aws" { region = var.aws_region default_tags { tags = {
  Environment = var.environment ... } } }`. Stub the six provider-referenced variables
  (`aws_region`, `environment`, `project`, `department`, `application`, `user`) in
  `variables.tf` with defaults; the two required vars (`budget_alarm_email`,
  `invoker_role_arn`) are added in T5 (a third, `s3_prefix_list_id`, was added here
  then superseded 2026-07-22 by `infra-terraform-network` SEC-2 — see the T5 note).
**Done when:** `terraform validate` exits 0 with the provider block referencing the stub vars.

---

### T4: Create `backend.tf` + `backend.hcl.example`

**Depends on:** T2
**Touches:** `apps/infra-tf/backend.tf`, `apps/infra-tf/backend.hcl.example`
**Tests:** goal-based — `grep 'dynamodb_table' apps/infra-tf/backend.tf` returns empty
  (no DynamoDB locking); `grep 'encrypt' apps/infra-tf/backend.tf` shows `encrypt = true`;
  `backend.hcl.example` exists with placeholder values for `bucket`, `key`, `region`.
**Approach:** Write the `terraform { backend "s3" { encrypt = true } }` block. The
  actual bucket/key/region values live in `backend.hcl.example` and are supplied via
  `-backend-config=backend.hcl` at `terraform init` time — never hardcoded.
**Done when:** Both files exist; `terraform fmt -check` exits 0 on both.

---

### T5: Create `variables.tf` with all required + governance variables

**Depends on:** T3
**Touches:** `apps/infra-tf/variables.tf`
**Tests:** goal-based — `terraform validate` exits 0 with the full `variables.tf` in
  place (structural check); the validation block is present and syntactically correct.
  Note: `terraform validate` does not evaluate variable validation conditions — the
  regex rejection (CIDR → error, `pl-abc123ef` → success) fires at `terraform plan`
  time and is confirmed in subsequent spec live ACs.
**Approach:** Write the full `variables.tf`:
  - `budget_alarm_email`: string, no default, description matching CDK parameter.
  - `invoker_role_arn`: string, no default.
  - `s3_prefix_list_id`: string, no default, `validation { condition =
    can(regex("^pl-[0-9a-f]+$", var.s3_prefix_list_id)) error_message = "..." }`.
    *(Superseded 2026-07-22 by `infra-terraform-network` SEC-2: this variable was
    removed; the S3 managed prefix list is now resolved via a data source.)*
  - Governance + provider vars: string with defaults from `_GOVERNANCE_TAG_DEFAULTS`.
**Done when:** Validation passes on a valid prefix-list id; validation fails on a CIDR.
  (The `s3_prefix_list_id` clause is superseded — see the note above.)

---

### T6: Create `outputs.tf` shell

**Depends on:** T2
**Touches:** `apps/infra-tf/outputs.tf`
**Tests:** goal-based — `grep -c 'output "' apps/infra-tf/outputs.tf` returns 12;
  `terraform validate` exits 0 with stub null values.
**Approach:** Write 12 output blocks with `value = null` stubs and descriptions
  matching the CDK `CfnOutput` descriptions. Names use snake_case matching Terraform
  convention (CDK's PascalCase → snake_case: `CorpusBucketName` → `corpus_bucket_name`).
  Exception: `OpenSearchEndpoint` → `opensearch_endpoint` (OpenSearch is one product-name
  token, not two words — per AC5 in the spec).
**Done when:** 12 output stubs present; `terraform validate` exits 0.

---

### T7: Create `scripts/bootstrap.sh` + run format + validate

**Depends on:** T4, T5, T6
**Touches:** `apps/infra-tf/scripts/bootstrap.sh`, all `.tf` files (fmt pass)
**Tests:** goal-based — `terraform fmt -check .` from `apps/infra-tf/` exits 0;
  `terraform validate` exits 0 after `terraform init -backend=false`;
  `bootstrap.sh` contains `aws sts get-caller-identity` guard and `aws s3api create-bucket`
  and `terraform init`; `bootstrap.sh` is `chmod +x` (tracked in git as executable).
**Approach:** Write `bootstrap.sh` that: (1) checks `aws sts get-caller-identity` and
  exits with a helpful message if unauthenticated; (2) creates the S3 state bucket using
  `aws s3api create-bucket` without `--create-bucket-configuration` for `us-east-1`
  (any other region requires `--create-bucket-configuration LocationConstraint=<region>`
  — the script must branch on region); (3) runs `terraform init -backend-config=backend.hcl`.
  Run `terraform fmt -recursive .` on all `.tf` files to canonicalize. Run
  `terraform validate` with `-backend=false` to confirm schema validity without a real backend.
  Commit `bootstrap.sh` with executable bit (`git add --chmod=+x`).
**Done when:** `terraform fmt -check` exits 0; `terraform validate -backend=false` exits 0;
  `bootstrap.sh` exists, is `chmod +x`, and contains the sts-identity guard.

## Rollout

This spec produces no AWS resources — it is the foundation tier only. No deploy/destroy
cycle is required for this spec's ACs. The first live deploy happens in the subsequent
`infra-terraform-compute` spec's live AC. The S3 state backend must be bootstrapped once
per AWS account (via `scripts/bootstrap.sh`) before any `terraform apply` in subsequent
specs.

## Risks

- **Backend bootstrap chicken-and-egg:** the S3 backend bucket must exist before
  `terraform init`. `scripts/bootstrap.sh` uses the AWS CLI to create it if absent;
  if the CLI is unauthenticated the script fails loudly. Mitigation: `bootstrap.sh`
  checks `aws sts get-caller-identity` first and exits with a helpful message.
- **`outputs.tf` stubs cause `terraform apply` to fail** with "value must not be
  null" if applied before subsequent specs fill them in. Mitigation: document that
  the scaffold tier is not intended to be applied alone; `plan` will show the null
  placeholders; apply requires subsequent specs.

## Changelog

- 2026-07-22 — Plan authored for infra-terraform-scaffold spec. Seven tasks:
  ADR-0010, versions.tf, provider.tf + tag vars, backend.tf + example, variables.tf
  with validation, outputs.tf shell, bootstrap.sh + fmt + validate gate.
