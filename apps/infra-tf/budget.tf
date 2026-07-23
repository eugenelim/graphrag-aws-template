# budget.tf — the monthly cost guardrail (CDK budgets.CfnBudget "CostBudget", :913).
#
# $150/mo ACTUAL-spend alarm at 80% — the standing-cost backstop (Neptune Serverless min
# NCU + single-node OpenSearch + the bedrock-runtime interface endpoint) that catches a
# forgotten deploy without firing on a same-day deploy/destroy.
#
# Provider-5.x shape (contract-acquisition): email subscribers are a
# `subscriber_email_addresses` set INSIDE the `notification` block — not a nested
# `subscriber { subscription_type, address }` block (that is the CFN/CDK shape).

resource "aws_budgets_budget" "monthly" {
  name         = "graphrag-monthly-cost"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = "150"
  limit_unit   = "USD"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_alarm_email]
  }
}
