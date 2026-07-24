# api_gateway_mcp.tf — HTTP API Gateway (v2) for human / IDE MCP ingress.
#
# Auth model: stage-level throttling ONLY (5 rps, burst 10). HTTP API v2 does not
# natively support usage plans or API keys, so there is no API key enforcement at this
# layer. The x-api-key header passes through to the Lambda as a convention header, but
# the Lambda does not currently validate it — this is an open, rate-limited endpoint.
# (ADR-0014 §7: "request identification and throttling, not authentication.")
# Sensitive operations are protected by the IAM-auth Function URL (mcp_lambda.tf);
# the API Gateway path is intentionally lighter for human/IDE ergonomics.
# WAF or a Lambda authorizer can be added later without a Lambda code change.
#
# Load-bearing invariants:
#   - protocol_type = "HTTP" (HTTP API v2; never REST API v1 aws_api_gateway_rest_api).
#   - integration timeout_milliseconds = 29000 (under the 30 s APIGW hard limit; ADR-0014).
#   - auto_deploy = true on the $default stage — no manual deployment step needed.

# ── HTTP API (v2) ──────────────────────────────────────────────────────────────
resource "aws_apigatewayv2_api" "mcp" {
  name          = "graphrag-mcp"
  protocol_type = "HTTP"
  description   = "MCP tool server — human/IDE ingress (stage throttling only; x-api-key header not enforced at edge)"
}

# ── $default stage: auto-deploy, throttling. ──────────────────────────────────
# Throttling is the HTTP API v2 equivalent of a REST API usage plan:
#   throttling_burst_limit  — max concurrent requests (token bucket burst)
#   throttling_rate_limit   — sustained requests per second
# Values are sized for demo load; raise in a production deployment.
resource "aws_apigatewayv2_stage" "mcp_default" {
  api_id      = aws_apigatewayv2_api.mcp.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

# ── Lambda proxy integration (payload format 2.0, 29 s timeout). ───────────────
# timeout_milliseconds must be <= 29000; the HTTP API hard limit is 30 s and this
# leaves 1 s headroom for APIGW overhead before the Lambda is invoked.
resource "aws_apigatewayv2_integration" "mcp_lambda" {
  api_id                 = aws_apigatewayv2_api.mcp.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.mcp_lambda.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

# ── $default catch-all route (all methods, all paths). ────────────────────────
resource "aws_apigatewayv2_route" "mcp_default" {
  api_id    = aws_apigatewayv2_api.mcp.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.mcp_lambda.id}"
}

# ── Lambda invoke permission for API Gateway. ──────────────────────────────────
# Scoped to this API's ARN (source_arn = arn:aws:execute-api:...) — never "*".
resource "aws_lambda_permission" "mcp_apigw_invoke" {
  statement_id  = "AllowMcpApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mcp_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mcp.execution_arn}/*"
}
