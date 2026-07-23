# ── Required variables (no default; must be supplied at plan/apply time) ──────

variable "budget_alarm_email" {
  type        = string
  description = "Email address that receives the AWS Budgets cost alarm."
}

variable "invoker_role_arn" {
  type        = string
  description = "IAM role ARN permitted to invoke the query Function URL (SigV4)."

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/.+", var.invoker_role_arn))
    error_message = "invoker_role_arn must be a role ARN of the form arn:aws:iam::<account-id>:role/<name>. Root, wildcard, and non-role principals are not permitted."
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
