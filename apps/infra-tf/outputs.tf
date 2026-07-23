# Output values — snake_case translations of the CDK CfnOutput names. The data-tier
# outputs (corpus_bucket_name, neptune_endpoint, opensearch_endpoint) are wired to live
# resource attributes by the data + IAM tier (infra-terraform-data-and-iam); the
# remaining compute-tier outputs stay value = null stubs until infra-terraform-compute
# provisions their resources.

output "corpus_bucket_name" {
  description = "Name of the S3 corpus bucket."
  value       = aws_s3_bucket.corpus.id
}

output "neptune_endpoint" {
  description = "HTTPS endpoint for the Neptune cluster (port 8182)."
  value       = "https://${aws_neptune_cluster.main.endpoint}:8182"
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
  value       = "https://${aws_opensearch_domain.graphrag_vectors.endpoint}"
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
