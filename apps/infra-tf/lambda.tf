# lambda.tf — the 3 in-VPC Lambda functions (SmokeProbe, VectorSmokeProbe, QueryLambda),
# their stack-managed log groups, the SmokeProbe execution role, and the QueryLambda's
# IAM-auth Function URL + scoped invoke permission.
#
# Translated from apps/infra/stacks/graphrag_stack.py:
#   _smoke_lambda()        (:629)  -> aws_lambda_function.smoke_probe        (+ role/logs)
#   _vector_smoke_lambda() (:769)  -> aws_lambda_function.vector_smoke_probe (+ logs)
#   _query_lambda()        (:815)  -> aws_lambda_function.query_lambda + function URL + perm
#
# Load-bearing invariants (spec AC5-AC7, AC11):
#   - Function URL authorization_type = AWS_IAM (never NONE).
#   - Invoke permission principal = var.invoker_role_arn (never "*"); the var is regex-guarded
#     in variables.tf (end-anchored role ARN, rejects wildcard/root/non-role — AC11).
#   - SmokeProbe gets full Neptune R/W (local.neptune_rw_policy — insert+retrieve+cleanup);
#     query_role stays read-only (ADR-0011 backstop, defined in iam.tf).
#   - All functions are VPC-attached (private isolated subnets) with the network tier's
#     per-compute SGs; each points logging_config at a stack-managed log group so
#     `terraform destroy` removes it (no auto-created /aws/lambda/<fn> group survives).

locals {
  # Pre-built Lambda package (CI-owned build; spec Assumptions). Path is relative to this
  # module dir (apps/infra-tf) -> apps/graphrag/dist/graphrag.zip. source_code_hash
  # validates presence + change detection, not contents.
  lambda_zip = "${path.module}/../graphrag/dist/graphrag.zip"

  # Single-source endpoint URL construction — any port/scheme change is a one-line edit.
  # Referenced from compute.tf (Fargate env), lambda.tf (Lambda env), and outputs.tf.
  neptune_endpoint_url    = "https://${aws_neptune_cluster.main.endpoint}:8182"
  opensearch_endpoint_url = "https://${aws_opensearch_domain.graphrag_vectors.endpoint}"
}

# ── Stack-managed log groups (CDK SmokeProbeLogs / VectorSmokeProbeLogs / QueryLambdaLogs).
# retention 7d; deleted by `terraform destroy` (no force_destroy arg on log groups). ──
resource "aws_cloudwatch_log_group" "smoke_probe" {
  name              = "/graphrag/smoke-probe"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "vector_smoke_probe" {
  name              = "/graphrag/vector-smoke-probe"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "query_lambda" {
  name              = "/graphrag/query-lambda"
  retention_in_days = 7
}

# ── SmokeProbe execution role (CDK auto-generates it via add_to_role_policy; explicit here).
# Trust lambda.amazonaws.com; AWSLambdaVPCAccessExecutionRole for the ENI lifecycle; the
# Neptune grant reuses local.neptune_rw_policy (iam.tf) — one source of truth, so a future
# tightening there never skips the probe. name_prefix avoids re-apply collisions. ──
resource "aws_iam_role" "smoke_probe_role" {
  name_prefix = "graphrag-smoke-probe-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "smoke_probe_vpc_access" {
  role       = aws_iam_role.smoke_probe_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Full Neptune R/W (connect + Read + Write + Delete) scoped to the cluster resource ARN:
# the probe inserts a test node, retrieves it, then deletes it (cleanup). CDK parity
# (graphrag_stack.py:661 uses the full _neptune_data_access).
resource "aws_iam_role_policy" "smoke_probe_neptune" {
  name   = "smoke-probe-neptune-full-rw"
  role   = aws_iam_role.smoke_probe_role.id
  policy = local.neptune_rw_policy
}

# ── SmokeProbe Lambda: Neptune live insert+retrieve. py3.12, 60s, VPC (smoke_probe_sg). ──
resource "aws_lambda_function" "smoke_probe" {
  function_name = "graphrag-smoke-probe"
  runtime       = "python3.12"
  handler       = "graphrag.smoke_lambda.lambda_handler"
  role          = aws_iam_role.smoke_probe_role.arn
  timeout       = 60

  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.smoke_probe_sg.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.smoke_probe.name
  }

  environment {
    variables = {
      NEPTUNE_ENDPOINT = local.neptune_endpoint_url
    }
  }

  # Ensure the VPC-access policy is attached before the function creates ENIs.
  depends_on = [aws_iam_role_policy_attachment.smoke_probe_vpc_access]
}

# ── VectorSmokeProbe Lambda: embed -> index -> retrieve. py3.12, 120s, VPC (vector_smoke_sg).
# role=vector_probe_role (OpenSearch + Bedrock Titan grants, data+IAM tier). ──
resource "aws_lambda_function" "vector_smoke_probe" {
  function_name = "graphrag-vector-smoke-probe"
  runtime       = "python3.12"
  handler       = "graphrag.vector_smoke_lambda.lambda_handler"
  role          = aws_iam_role.vector_probe_role.arn
  timeout       = 120

  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.vector_smoke_sg.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.vector_smoke_probe.name
  }

  environment {
    variables = {
      OPENSEARCH_ENDPOINT = local.opensearch_endpoint_url
    }
  }

  depends_on = [aws_iam_role_policy_attachment.vector_probe_vpc_access]
}

# ── QueryLambda: hybrid + SPARQL query path behind the IAM-auth Function URL.
# py3.12, 120s, memory 512, VPC (query_lambda_sg). role=query_role (Neptune READ-ONLY per
# ADR-0011 + OpenSearch + Bedrock, data+IAM tier). ──
resource "aws_lambda_function" "query_lambda" {
  function_name = "graphrag-query-lambda"
  runtime       = "python3.12"
  handler       = "graphrag.query_lambda.lambda_handler"
  role          = aws_iam_role.query_role.arn
  timeout       = 120
  memory_size   = 512
  # Blast-radius cap: the Function URL is IAM-auth-gated, but a broad invoker role
  # could exhaust account Lambda concurrency and drive spend beyond the $150 ACTUAL
  # alarm ceiling. 10 concurrent executions is generous for demo query load.
  # CDK parity: CDK sets no cap; this is defence-in-depth (backlog: terraform-query-lambda-concurrency-cap).
  reserved_concurrent_executions = 10

  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.query_lambda_sg.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.query_lambda.name
  }

  environment {
    variables = {
      NEPTUNE_ENDPOINT    = local.neptune_endpoint_url
      OPENSEARCH_ENDPOINT = local.opensearch_endpoint_url
      # Shared with query_role's bedrock-synthesis grant (iam.tf local.synthesis_model_id)
      # so the granted inference-profile and the invoked model can never drift apart.
      SYNTHESIS_MODEL_ID = local.synthesis_model_id
    }
  }

  depends_on = [aws_iam_role_policy_attachment.query_vpc_access]
}

# ── IAM-auth Function URL — the ONLY public ingress. Never NONE (spec "Never do"). ──
resource "aws_lambda_function_url" "query_url" {
  function_name      = aws_lambda_function.query_lambda.function_name
  authorization_type = "AWS_IAM"
}

# Invoke scoped to a single named principal (var.invoker_role_arn), never "*" / account
# root. function_url_auth_type = AWS_IAM binds the permission to the URL's SigV4 path.
resource "aws_lambda_permission" "query_url_invoke" {
  statement_id           = "AllowInvokerRoleFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.query_lambda.function_name
  principal              = var.invoker_role_arn
  function_url_auth_type = "AWS_IAM"
}
