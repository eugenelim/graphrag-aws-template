# ── Required variables (no default; must be supplied at plan/apply time) ──────

variable "budget_alarm_email" {
  type        = string
  description = "Email address that receives the AWS Budgets cost alarm."

  validation {
    # Fail-fast on a malformed/empty address (symmetric with invoker_role_arn) rather than
    # surfacing it only at apply time when aws_budgets_budget rejects the subscriber. Basic
    # shape check (local@domain.tld), not full RFC 5322.
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_alarm_email))
    error_message = "budget_alarm_email must be a valid email address of the form local@domain.tld."
  }
}

variable "invoker_role_arn" {
  type        = string
  description = "IAM role ARN permitted to invoke the query Function URL (SigV4)."

  validation {
    # End-anchored, and the role-name/path body admits only the IAM-legal character set
    # (alphanumeric + `+=,.@_-` and `/` for paths) — so a wildcard body like `role/*`,
    # a `:root` ARN, or a non-role principal is rejected. A non-anchored `.+` body would
    # have let `role/*` through despite the error message (defence-in-depth: the Lambda
    # AddPermission API also rejects a wildcard principal at apply time).
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.invoker_role_arn))
    error_message = "invoker_role_arn must be a role ARN of the form arn:aws:iam::<account-id>:role/<name>. Root, wildcard, and non-role principals are not permitted."
  }
}


variable "mcp_invoker_role_arn" {
  type        = string
  description = "IAM role ARN permitted to invoke the MCP tool-server Function URL (SigV4 — automation and AgentCore)."

  validation {
    # Same end-anchored role-ARN validation as invoker_role_arn: rejects wildcard, root, and
    # non-role principals. The Lambda AddPermission API also rejects wildcards at apply time —
    # this is defence-in-depth, not a substitute for that check.
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.mcp_invoker_role_arn))
    error_message = "mcp_invoker_role_arn must be a role ARN of the form arn:aws:iam::<account-id>:role/<name>. Root, wildcard, and non-role principals are not permitted."
  }
}

variable "opensearch_master_user_arn" {
  type        = string
  description = "IAM role ARN mapped to the OpenSearch fine-grained-access-control master user (all_access). Operator-supplied so the master identity is an explicit choice, not a silent default."

  validation {
    # Same end-anchored role-ARN validation as the invoker variables. An IAM ARN here is
    # what keeps master_user_options off the internal user database — a username/password
    # master is the "master user is not IAM" finding this variable exists to prevent.
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.opensearch_master_user_arn))
    error_message = "opensearch_master_user_arn must be a role ARN of the form arn:aws:iam::<account-id>:role/<name>. Root, wildcard, and non-role principals are not permitted."
  }
}
# NOTE: the former `s3_prefix_list_id` variable was removed by the
# infra-terraform-network tier (SEC-2 hardening). The AWS-managed S3
# gateway-endpoint prefix list is now resolved declaratively from the account via
# `data "aws_ec2_managed_prefix_list" "s3"` in network.tf, so no operator-supplied
# (and potentially wrong/wide) prefix-list id can widen the closed-egress posture.

# ── AWS provider variable ──────────────────────────────────────────────────────

variable "aws_region" {
  type        = string
  description = "AWS region to deploy into."
  default     = "us-east-1"
}

# ── Governance tag variables (defaults match CDK _GOVERNANCE_TAG_DEFAULTS) ────
# Applied via provider default_tags so every resource inherits them without
# per-resource tags = {} blocks.

variable "environment" {
  type        = string
  description = "Deployment environment tag value."
  default     = "demo"
}

variable "project" {
  type        = string
  description = "Project tag value."
  default     = "graphrag-aws-template"
}

variable "department" {
  type        = string
  description = "Department tag value."
  default     = "unspecified"
}

variable "application" {
  type        = string
  description = "Application tag value."
  default     = "graphrag"
}

variable "user" {
  type        = string
  description = "User tag value (owner of the deployment)."
  default     = "unspecified"
}

# ── Asset-inventory attribution tag (optional) ────────────────────────────────
#
# Many organisations run an asset-inventory or CMDB tool that expects every cloud
# resource to carry a tag attributing it to an owning application, and raise a finding
# when one is missing. The tag KEY and VALUE are both deployer-specific, so both are
# variables with empty defaults: this template stays neutral, and adopters supply their
# own convention in the gitignored terraform.tfvars. Leave them unset to emit no tag.

variable "asset_inventory_tag_key" {
  type        = string
  description = "Tag key your asset-inventory tooling reads to attribute a resource to an owning application. Leave empty to emit no attribution tag. Case-sensitive — match your tooling exactly."
  default     = ""
}

variable "asset_inventory_tag_value" {
  type        = string
  description = "Value for asset_inventory_tag_key — typically an application identifier. Treated as sensitive so it stays out of plan output and committed fixtures; keep the real value in the gitignored terraform.tfvars."
  default     = ""
  sensitive   = true
}

# ── OTEL / ADOT variables ──────────────────────────────────────────────────────

variable "adot_layer_arn" {
  type        = string
  description = <<-DESC
    AWS ADOT Lambda Python 3.12 layer ARN for the deployment region.
    Find the per-region ARN at: https://aws-otel.github.io/docs/getting-started/lambda/lambda-python
    Format: arn:aws:lambda:<region>:901920570463:layer:aws-otel-python-amd64-ver-<X>-<Y>-<Z>:<build>
    Default: us-east-1 ADOT 1.24.0 (Python 3.12 amd64). Update when a new ADOT version ships;
    there is no auto-update mechanism (ADR-0015 Negative consequences).
  DESC
  default     = "arn:aws:lambda:us-east-1:901920570463:layer:aws-otel-python-amd64-ver-1-24-0:1"

  validation {
    condition     = can(regex("^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:layer:[a-zA-Z0-9_-]+:[0-9]+$", var.adot_layer_arn))
    error_message = "adot_layer_arn must be a valid Lambda layer ARN (arn:aws:lambda:<region>:<account>:layer:<name>:<version>)."
  }
}

# ── Git ingestion trigger variables (CodePipeline + EventBridge, ADR-0016) ────

variable "codestar_connection_arn" {
  type        = string
  description = <<-DESC
    ARN of the AWS CodeStar Connections connection to GitHub. Created via the AWS Console
    (Connections → Create connection → GitHub) and must be in AVAILABLE status before the
    CodePipeline pipeline can pull from GitHub. Terraform provisions the pipeline but
    cannot complete the OAuth handshake.
    AWS renamed the service to CodeConnections, and connections created since carry
    `arn:aws:codeconnections:...`. Both spellings are live and valid — the deployed
    connection for this stack uses the newer one — so both are accepted.
    Format: arn:aws:codeconnections:<region>:<account-id>:connection/<uuid>
        or: arn:aws:codestar-connections:<region>:<account-id>:connection/<uuid>
  DESC

  validation {
    condition     = can(regex("^arn:aws:(codeconnections|codestar-connections):[a-z0-9-]+:[0-9]{12}:connection/[0-9a-f-]+$", var.codestar_connection_arn))
    error_message = "codestar_connection_arn must be a valid CodeConnections/CodeStar connection ARN: arn:aws:codeconnections:<region>:<account-id>:connection/<uuid> (the legacy codestar-connections prefix is also accepted)."
  }
}

variable "github_repo_id" {
  type        = string
  description = "GitHub repository to mirror (format: owner/repo, e.g. acme/biz-ops-docs). The CodePipeline source action mirrors this repo to S3 on each push."

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repo_id))
    error_message = "github_repo_id must be in owner/repo format (e.g. acme/biz-ops-docs)."
  }
}

variable "github_branch" {
  type        = string
  description = "Branch to mirror from the GitHub repository (default: main)."
  default     = "main"

  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]+$", var.github_branch))
    error_message = "github_branch must use git-ref-legal characters (A-Z a-z 0-9 . _ / -)."
  }
}
