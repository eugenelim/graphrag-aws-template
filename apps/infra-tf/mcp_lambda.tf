# mcp_lambda.tf — MCP tool-server Lambda function, its execution role (mcp_lambda_role),
# security group (mcp_lambda_sg), stack-managed log group, IAM-auth Function URL,
# and the scoped invoke permission for automation + AgentCore (SigV4/IAM).
#
# Two ingress paths from ADR-0014:
#   - IAM-auth Function URL  (this file)  — automation + AgentCore (SigV4)
#   - HTTP API Gateway        (api_gateway_mcp.tf) — human / IDE (rate-limited, no enforced key)
#
# Load-bearing invariants:
#   - mcp_lambda_role Neptune grant is READ-ONLY (connect + ReadDataViaQuery only) — ADR-0011.
#   - Function URL authorization_type = AWS_IAM (never NONE).
#   - Invoke permission principal = var.mcp_invoker_role_arn (never "*").
#   - ADOT Lambda layer is NOT attached here — deferred to infra-tf/mcp-otel-lambda (ADR-0015).

# ── Stack-managed log group (destroyed by `terraform destroy`) ─────────────────
resource "aws_cloudwatch_log_group" "mcp_lambda" {
  name              = "/graphrag/mcp-tool-server"
  retention_in_days = 7
}

# ── mcp_lambda_role: trust Lambda service; VPC-access managed policy. ──────────
resource "aws_iam_role" "mcp_lambda_role" {
  name_prefix = "graphrag-mcp-lambda-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "mcp_vpc_access" {
  role       = aws_iam_role.mcp_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Neptune READ-ONLY (ADR-0011 backstop): connect + ReadDataViaQuery only.
# Reuses local.neptune_readonly_policy from iam.tf — single source of truth.
resource "aws_iam_role_policy" "mcp_neptune_readonly" {
  name   = "neptune-data-readonly"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.neptune_readonly_policy
}

# Read-only OpenSearch: the MCP tool server is a retrieval-only path (kNN search + GET).
# Uses opensearch_readonly_policy (ESHttpGet + ESHttpPost + ESHttpHead) — never Put/Delete.
resource "aws_iam_role_policy" "mcp_opensearch" {
  name   = "opensearch-data"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.opensearch_readonly_policy
}

resource "aws_iam_role_policy" "mcp_bedrock_titan" {
  name   = "bedrock-titan"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.bedrock_titan_policy
}

resource "aws_iam_role_policy" "mcp_bedrock_synthesis" {
  name   = "bedrock-synthesis"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.bedrock_synthesis_policy
}

# ── MCP Lambda: FastMCP tool server behind Mangum ASGI adapter. ───────────────
# py3.12, 512 MB, 120 s (Lambda timeout; API Gateway hard-cuts at 30 s on that path).
# Concurrency cap: 10 (blast-radius cost ceiling, same as query_lambda).
# VPC-attached (private isolated subnets) — reaches Neptune + OpenSearch inside VPC.
# ADOT layer not attached here — deferred to infra-tf/mcp-otel-lambda (ADR-0015).
resource "aws_lambda_function" "mcp_lambda" {
  function_name = "graphrag-mcp-tool-server"
  runtime       = "python3.12"
  handler       = "graphrag.mcp._lambda.handler"
  role          = aws_iam_role.mcp_lambda_role.arn
  timeout       = 120
  memory_size   = 512
  # Blast-radius cap: same rationale as query_lambda. 10 concurrent executions is
  # generous for demo query load; prevents runaway spend if the API key leaks.
  reserved_concurrent_executions = 10

  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.mcp_lambda_sg.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.mcp_lambda.name
  }

  environment {
    variables = {
      NEPTUNE_SPARQL_ENDPOINT = local.neptune_endpoint_url
      OPENSEARCH_ENDPOINT     = local.opensearch_endpoint_url
      # Single-source synthesis model (same local as query_lambda env var + IAM grant).
      SYNTHESIS_MODEL_ID = local.synthesis_model_id
    }
  }

  depends_on = [aws_iam_role_policy_attachment.mcp_vpc_access]
}

# ── IAM-auth Function URL — automation + AgentCore ingress (SigV4). ───────────
# Never NONE (spec "Never do" and ADR-0014 §7).
resource "aws_lambda_function_url" "mcp_url" {
  function_name      = aws_lambda_function.mcp_lambda.function_name
  authorization_type = "AWS_IAM"
}

# Invoke permission scoped to a single named principal; never "*" or account root.
resource "aws_lambda_permission" "mcp_url_invoke" {
  statement_id           = "AllowMcpInvokerRoleFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.mcp_lambda.function_name
  principal              = var.mcp_invoker_role_arn
  function_url_auth_type = "AWS_IAM"
}
