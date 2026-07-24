# compute.tf — ECS/Fargate ingestion task + ECR repository + ECS task execution role +
# the ingestion CloudWatch log group.
#
# Translated from apps/infra/stacks/graphrag_stack.py `_ingestion_task()` (:542):
#   ecs.Cluster                -> aws_ecs_cluster.main
#   ecr.Repository (empty_on_delete=True) -> aws_ecr_repository.ingestion (force_delete=true)
#   auto-created ecsTaskExecutionRole      -> aws_iam_role.ecs_task_execution_role (+ attach)
#   logs.LogGroup "IngestionLogs"          -> aws_cloudwatch_log_group.ingestion
#   ecs.FargateTaskDefinition              -> aws_ecs_task_definition.ingestion
#
# Teardown-first (ADR-0002): ECR force_delete removes images so `terraform destroy`
# needs no manual `ecr delete-repository --force`; the log group is a managed resource
# (no force_destroy arg exists on aws_cloudwatch_log_group in provider 5.x) that destroy
# deletes by default. No prevent_destroy anywhere.

resource "aws_ecs_cluster" "main" {
  name = "graphrag"
}

# empty_on_delete=True (CDK) -> force_delete=true: destroy removes the repo + all images.
# Tag is deliberately mutable (:latest, re-pushed by CI); image_tag_mutability=IMMUTABLE is
# intentionally NOT set (would break the re-push workflow) — suppressed in .trivyignore.
resource "aws_ecr_repository" "ingestion" {
  name         = "graphrag-ingestion"
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# In CDK the Fargate execution role is auto-generated; in Terraform it is explicit.
# name_prefix (not a fixed name) matches the iam.tf convention and avoids an
# EntityAlreadyExists collision on the teardown-first apply/destroy/re-apply cycle.
resource "aws_iam_role" "ecs_task_execution_role" {
  name_prefix = "graphrag-ecs-exec-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ECR pull + CloudWatch Logs push for the Fargate agent (the AWS-managed execution policy).
resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Ingestion container log group (CDK "IngestionLogs"). retention 7d; stack-managed so
# `terraform destroy` deletes it and its events.
resource "aws_cloudwatch_log_group" "ingestion" {
  name              = "/graphrag/ingestion"
  retention_in_days = 7
}

# Fargate ingestion task. 2048 CPU / 8192 MiB required for docling model weights (~2.4 GB). Task role
# is the data+IAM tier's ingestion_task_role (Neptune RW + OpenSearch + Bedrock + S3 grants);
# execution role is the ECR-pull/Logs role above. Container env is byte-identical to CDK
# (:590) — self-configured so `aws ecs run-task` needs no env overrides; AWS_REGION is
# injected by the Fargate agent, not set here.
resource "aws_ecs_task_definition" "ingestion" {
  family                   = "graphrag-ingestion"
  # 2048 CPU / 8192 MiB required for docling model weights (~2.4 GB PyTorch stack).
  # spec-ingestion-extraction-cleanse AC10.
  cpu                      = "2048"
  memory                   = "8192"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  task_role_arn            = aws_iam_role.ingestion_task_role.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn

  container_definitions = jsonencode([{
    name      = "ingestion"
    image     = "${aws_ecr_repository.ingestion.repository_url}:latest"
    essential = true
    environment = [
      { name = "NEPTUNE_ENDPOINT", value = local.neptune_endpoint_url },
      { name = "OPENSEARCH_ENDPOINT", value = local.opensearch_endpoint_url },
      { name = "CORPUS_BUCKET", value = aws_s3_bucket.corpus.id },
      { name = "SCHEMA_EXTRACTION", value = "false" },
      # Prevent docling from downloading model weights at runtime — weights are baked
      # into the Docker image at build time. spec-ingestion-extraction-cleanse AC11.
      { name = "TRANSFORMERS_OFFLINE", value = "1" },
      { name = "HF_DATASETS_OFFLINE",  value = "1" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ingestion.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ingestion"
      }
    }
  }])
}
