# mcp_lambda.tf — MCP tool server Lambda (graphrag-mcp), its IAM role + policies,
# CloudWatch log group, IAM-auth Function URL, and ADOT OTEL instrumentation.
#
# ADR-0015: ADOT Lambda Python layer; OTLP→X-Ray traces; EMF metrics; content-capture
# policy (DENY_SET ∪ AUTO_CAPTURE_KEYS stripped by the ADOT collector attribute
# processor before spans reach X-Ray). ADR-0011: mcp_lambda_role Neptune grant is
# READ-ONLY (connect + ReadDataViaQuery only; no Write/Delete).
#
# Security group (mcp_lambda_sg) and its egress rules are defined in security_groups.tf
# alongside the other compute SGs for consistency. The ingress rules on neptune_sg /
# opensearch_sg from mcp_lambda_sg are also in security_groups.tf.

# ── CloudWatch log group — 30 days retention (ADR-0015 item 4: Lambda log group
# retention 30 days for structured-log correlation with traces and EMF metrics). ──
resource "aws_cloudwatch_log_group" "mcp_lambda" {
  name              = "/graphrag/mcp-lambda"
  retention_in_days = 30
}

# ── MCP Lambda IAM role — trust lambda.amazonaws.com ──────────────────────────
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

# VPC-access managed policy (ENI lifecycle for in-VPC Lambda execution).
resource "aws_iam_role_policy_attachment" "mcp_lambda_vpc_access" {
  role       = aws_iam_role.mcp_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# X-Ray trace ingest (ADR-0015 item 2). AWSXRayDaemonWriteAccess covers:
#   xray:PutTraceSegments, xray:PutTelemetryRecords,
#   xray:GetSamplingRules, xray:GetSamplingTargets.
# These actions do not support resource-level scoping (same rationale as
# spec-otel-observability Assumptions: the four-action managed policy is
# effectively equivalent to an inline policy of the same four actions).
resource "aws_iam_role_policy_attachment" "mcp_lambda_xray" {
  role       = aws_iam_role.mcp_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# Neptune READ-ONLY (ADR-0011 backstop: connect + ReadDataViaQuery only).
# mcp_lambda_role must never hold WriteDataViaQuery or DeleteDataViaQuery.
resource "aws_iam_role_policy" "mcp_lambda_neptune_readonly" {
  name   = "neptune-data-readonly"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.neptune_readonly_policy
}

# OpenSearch kNN vector search — search-only (Get/Post/Head; no Put/Delete).
# Uses opensearch_search_policy (not opensearch_data_policy) to avoid granting
# write access on a query-only path (ADR-0011 read-only-on-query principle).
resource "aws_iam_role_policy" "mcp_lambda_opensearch" {
  name   = "opensearch-search"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.opensearch_search_policy
}

# Bedrock Titan v2 embeddings (query embedding for hybrid retrieval).
resource "aws_iam_role_policy" "mcp_lambda_bedrock_titan" {
  name   = "bedrock-titan"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.bedrock_titan_policy
}

# Bedrock synthesis (Claude Sonnet) — cross-region inference profile grant.
resource "aws_iam_role_policy" "mcp_lambda_bedrock_synthesis" {
  name   = "bedrock-synthesis"
  role   = aws_iam_role.mcp_lambda_role.id
  policy = local.bedrock_synthesis_policy
}

# ── MCP Lambda function ────────────────────────────────────────────────────────
#
# OTEL instrumentation (ADR-0015):
#   - ADOT Lambda Python layer provides AWSOpenTelemetryDistro auto-instrumentation.
#   - AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument activates the ADOT layer before
#     the handler module runs, installing the global TracerProvider + OTLP pipeline.
#   - OTEL_EXPORTER_OTLP_ENDPOINT points to the ADOT layer's bundled collector
#     (localhost:4317). The collector exports to X-Ray via awsxray exporter.
#   - OPENTELEMETRY_COLLECTOR_CONFIG_FILE references a custom collector config
#     (otel-collector-config.yaml, bundled in the Lambda ZIP at /var/task/) that
#     includes the attributes/deny_content processor — the load-bearing content-
#     capture control on the ADOT-owned Lambda export path. The file must be
#     deployed as graphrag package data at /var/task/graphrag/otel-collector-config.yaml;
#     the canonical source is packages/graphrag/src/graphrag/otel-collector-config.yaml.
#   - Capture-off env vars suppress content capture at the instrumentation level
#     (primary control); the collector attribute processor is the backstop.
#
# Blast-radius cap: 10 concurrent executions (same as query_lambda).

resource "aws_lambda_function" "mcp_lambda" {
  function_name = "graphrag-mcp-lambda"
  runtime       = "python3.12"
  handler       = "graphrag.mcp._lambda.handler"
  role          = aws_iam_role.mcp_lambda_role.arn
  timeout       = 120
  memory_size   = 512
  # Blast-radius cap — the Function URL is IAM-auth-gated, but a broad invoker
  # could exhaust account concurrency and drive spend past the $150 ACTUAL alarm.
  reserved_concurrent_executions = 10

  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)

  # ADOT Lambda Python layer. var.adot_layer_arn is region+version pinned;
  # update when a new ADOT version ships (no auto-update mechanism — ADR-0015).
  layers = [var.adot_layer_arn]

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.mcp_lambda_sg.id]
  }

  # Structured JSON log output (ADR-0015 item 4); log group is stack-managed.
  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.mcp_lambda.name
  }

  # Active X-Ray tracing — the Lambda handler root span is captured by the ADOT
  # layer; active mode enables sampling of downstream service calls in the trace.
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      # ── Backend store endpoints ──
      NEPTUNE_SPARQL_ENDPOINT = local.neptune_endpoint_url
      OPENSEARCH_ENDPOINT     = local.opensearch_endpoint_url
      # Note: AWS_REGION is a Lambda-reserved key and must NOT be set here;
      # the runtime injects it automatically. The ADOT collector config reads
      # ${env:AWS_REGION} directly from the runtime-injected environment.

      # ── ADOT / OTEL wiring (ADR-0015 items 1-2) ──
      # Activates the ADOT layer auto-instrumentation before the handler runs.
      AWS_LAMBDA_EXEC_WRAPPER = "/opt/otel-instrument"
      # OTEL service identity (appears on every span + EMF metric).
      OTEL_SERVICE_NAME = "graphrag-mcp"
      # ADOT bundled collector gRPC listener (exports to X-Ray via awsxray exporter).
      OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4317"
      OTEL_TRACES_EXPORTER        = "otlp"
      # Custom collector config with attributes/deny_content processor.
      # Deployed as graphrag package data at /var/task/graphrag/otel-collector-config.yaml
      # (see root pyproject.toml [tool.setuptools.package-data]). Canonical source:
      # packages/graphrag/src/graphrag/otel-collector-config.yaml
      OPENTELEMETRY_COLLECTOR_CONFIG_FILE = "/var/task/graphrag/otel-collector-config.yaml"

      # ── Auto-instrumentation content-capture suppression (ADR-0015 item 6) ──
      # Primary control: stop the boto3/urllib3 auto-instrumentation from setting
      # content-bearing span attributes. The ADOT collector attribute processor
      # (otel-collector-config.yaml) strips any that slip through.
      #
      # Suppresses HTTP body and extended request attributes for botocore
      # (Bedrock prompt content, Bedrock response content).
      OTEL_PYTHON_BOTOCORE_SUPPRESS_HTTP_INSTRUMENTATION = "true"
      # Suppresses gen-AI prompt/completion content capture (opentelemetry-instrumentation-genai).
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "false"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.mcp_lambda_vpc_access,
    aws_iam_role_policy_attachment.mcp_lambda_xray,
  ]
}

# ── IAM-auth Function URL — the IAM-gated ingress path for human/IDE use ──────
# SigV4 required; no CORS (IDE/automation clients, not browser).
resource "aws_lambda_function_url" "mcp_url" {
  function_name      = aws_lambda_function.mcp_lambda.function_name
  authorization_type = "AWS_IAM"
}

# Invoke permission scoped to var.invoker_role_arn (never "*" / root).
resource "aws_lambda_permission" "mcp_url_invoke" {
  statement_id           = "AllowInvokerRoleMcpFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.mcp_lambda.function_name
  principal              = var.invoker_role_arn
  function_url_auth_type = "AWS_IAM"
}
