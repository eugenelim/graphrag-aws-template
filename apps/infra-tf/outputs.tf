# Output shells — stubs with value = null until subsequent specs provision the
# resources. Names are snake_case translations of the CDK CfnOutput names.

output "corpus_bucket_name" {
  description = "Name of the S3 corpus bucket."
  value       = null
}

output "neptune_endpoint" {
  description = "HTTPS endpoint for the Neptune cluster (port 8182)."
  value       = null
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster running the Fargate ingestion task."
  value       = null
}

output "ingestion_task_def_arn" {
  description = "ARN of the Fargate ingestion task definition."
  value       = null
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
  value       = null
}

output "smoke_probe_name" {
  description = "Name of the Neptune smoke-probe Lambda function."
  value       = null
}

output "opensearch_endpoint" {
  description = "HTTPS endpoint for the OpenSearch domain."
  value       = null
}

output "vector_smoke_probe_name" {
  description = "Name of the OpenSearch vector smoke-probe Lambda function."
  value       = null
}

output "query_function_url" {
  description = "Function URL for the IAM-auth query Lambda."
  value       = null
}

output "query_lambda_name" {
  description = "Name of the query Lambda function."
  value       = null
}
