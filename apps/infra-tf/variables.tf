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

variable "s3_prefix_list_id" {
  type        = string
  description = "AWS-managed S3 gateway-endpoint prefix list id (com.amazonaws.<region>.s3) the in-VPC compute SGs allow 443 egress to. No default (region-specific); deploy.sh resolves it per-region."

  validation {
    condition     = can(regex("^pl-[0-9a-f]+$", var.s3_prefix_list_id))
    error_message = "s3_prefix_list_id must match the AWS prefix list format ^pl-[0-9a-f]+ (e.g. pl-63a5400a). A CIDR or free-form value is not valid."
  }
}

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
