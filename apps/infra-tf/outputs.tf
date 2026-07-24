# Output values — snake_case translations of the CDK CfnOutput names. The data-tier
# outputs (corpus_bucket_name, neptune_endpoint, opensearch_endpoint) are wired by the
# data + IAM tier; the network tier wired ingestion_security_group_id + private_subnet_id;
# the compute tier (infra-terraform-compute) wired the remaining ECS/ECR/Lambda outputs.
# All 14 outputs reference live resource attributes — no null stubs remain.

output "corpus_bucket_name" {
  description = "Name of the S3 corpus bucket."
  value       = aws_s3_bucket.corpus.id
}

output "neptune_endpoint" {
  description = "HTTPS endpoint for the Neptune cluster (port 8182)."
  value       = local.neptune_endpoint_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster running the Fargate ingestion task."
  value       = aws_ecs_cluster.main.name
}

output "ingestion_task_def_arn" {
  description = "ARN of the Fargate ingestion task definition."
  value       = aws_ecs_task_definition.ingestion.arn
}

output "ingestion_security_group_id" {
  description = "Security group ID attached to the Fargate ingestion task."
  value       = aws_security_group.ingestion_task_sg.id
}

output "private_subnet_id" {
  description = "ID of the first private subnet (used by Fargate and Lambda)."
  value       = aws_subnet.private[0].id
}

output "ingestion_repo_uri" {
  description = "ECR repository URI for the ingestion container image."
  value       = aws_ecr_repository.ingestion.repository_url
}

output "smoke_probe_name" {
  description = "Name of the Neptune smoke-probe Lambda function."
  value       = aws_lambda_function.smoke_probe.function_name
}

output "opensearch_endpoint" {
  description = "HTTPS endpoint for the OpenSearch domain."
  value       = local.opensearch_endpoint_url
}

output "vector_smoke_probe_name" {
  description = "Name of the OpenSearch vector smoke-probe Lambda function."
  value       = aws_lambda_function.vector_smoke_probe.function_name
}

output "query_function_url" {
  description = "Function URL for the IAM-auth query Lambda."
  value       = aws_lambda_function_url.query_url.function_url
}

output "query_lambda_name" {
  description = "Name of the query Lambda function."
  value       = aws_lambda_function.query_lambda.function_name
}

output "mcp_function_url" {
  description = "IAM-auth Function URL for the MCP tool-server Lambda (automation + AgentCore ingress — SigV4)."
  value       = aws_lambda_function_url.mcp_url.function_url
}

output "mcp_api_gateway_url" {
  description = "HTTP API Gateway URL for the MCP tool-server (human / IDE ingress — rate-limited, x-api-key not enforced at edge)."
  value       = aws_apigatewayv2_stage.mcp_default.invoke_url
}
