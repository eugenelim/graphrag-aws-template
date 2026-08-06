# alerting.tf — ingestion failure alerting (infra-tf-p0-gap-remediation AC3).
#
# design.md § "Ingestion status registry and failure alerting": an EventBridge
# rule scoped to the graphrag cluster matches stopped tasks that failed (any
# container exitCode != 0, or the task never started) and publishes to an SNS
# topic with an email subscription. Closes the silent-failure gap: EventBridge
# fires ecs:RunTask once (git_ingestion_trigger.tf) with no retry — before this
# rule, a mid-pipeline crash was invisible unless an operator read ECS exit codes.
#
# Pattern semantics probe-verified 2026-08-05 via `aws events test-event-pattern`:
# exitCode 1 → match, exitCode 0 → no match, TaskFailedToStart (no exitCode) → match.

locals {
  # Constructed cluster ARN (fixed cluster name), mirroring the opensearch_domain_arn
  # convention: keeps the event_pattern fully static so the plan-assertion suite can
  # read it in a fresh plan (a reference to aws_ecs_cluster.main.arn would collapse
  # the whole jsonencode to unknown — K-0030).
  ecs_cluster_arn = "arn:aws:ecs:${var.aws_region}:${local.account_id}:cluster/${aws_ecs_cluster.main.name}"
}

# Deliberately unencrypted (trivy AVD-AWS-0095 ignored with rationale, .trivyignore):
# SSE with the AWS-managed aws/sns key silently breaks EventBridge publishing — that
# key's policy is unmodifiable and grants events.amazonaws.com no kms:GenerateDataKey/
# Decrypt. A CMK would work but is the gap inventory's P2 #15 (this stack carries no
# CMKs); the message body is alert metadata only (cluster/task ARN, stop code) —
# corpus content never transits this topic. CMK is the adopter upgrade path.
resource "aws_sns_topic" "ingestion_alerts" {
  name = "graphrag-ingestion-alerts"
}

# Email subscription to the same operator address as the Budgets alarm. The
# subscription stays pending until the operator clicks the confirmation link;
# an unconfirmed subscription cannot be deleted via API and expires AWS-side
# after 3 days (documented teardown residual, spec AC6).
resource "aws_sns_topic_subscription" "ingestion_alerts_email" {
  topic_arn = aws_sns_topic.ingestion_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_alarm_email
}

# Resource policy: only EventBridge may publish, and only on behalf of this rule
# (aws:SourceArn) — never a wide principal, no cross-account confused-deputy.
resource "aws_sns_topic_policy" "ingestion_alerts" {
  arn = aws_sns_topic.ingestion_alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgePublishFromFailureRule"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.ingestion_alerts.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.ecs_task_failed.arn }
      }
    }]
  })
}

# Cluster-scoped failure rule. $or covers both failure shapes:
#   - any container exited non-zero (OOM, Neptune timeout, Bedrock throttle), and
#   - the task never started (image pull failure, ENI/resource init) — which
#     carries NO exitCode, so an exitCode-only pattern would miss it.
resource "aws_cloudwatch_event_rule" "ecs_task_failed" {
  name        = "graphrag-ingestion-task-failed"
  description = "Alert on graphrag ingestion task failure (non-zero exit or failed to start)."

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      clusterArn = [local.ecs_cluster_arn]
      lastStatus = ["STOPPED"]
      "$or" = [
        { containers = { exitCode = [{ "anything-but" = 0 }] } },
        { stopCode = ["TaskFailedToStart"] },
      ]
    }
  })
}

# SNS target with a minimal input_transformer: the raw ECS event over-discloses
# into plaintext email (task-role ARN, image URIs/digests, containerOverrides env
# values). Only cluster/task/stopCode/stoppedReason go out; per-container exit
# codes are deliberately omitted — TaskFailedToStart events carry no exitCode and
# an unresolvable JSONPath must not be able to break that branch's alert.
resource "aws_cloudwatch_event_target" "ecs_task_failed_to_sns" {
  rule = aws_cloudwatch_event_rule.ecs_task_failed.name
  arn  = aws_sns_topic.ingestion_alerts.arn

  input_transformer {
    input_paths = {
      cluster       = "$.detail.clusterArn"
      task          = "$.detail.taskArn"
      stopCode      = "$.detail.stopCode"
      stoppedReason = "$.detail.stoppedReason"
    }
    input_template = <<-EOT
      "graphrag ingestion task failed. stopCode=<stopCode> reason=<stoppedReason> task=<task> cluster=<cluster>"
    EOT
  }
}
