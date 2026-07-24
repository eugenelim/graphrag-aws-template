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
