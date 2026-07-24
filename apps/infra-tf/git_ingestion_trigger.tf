# git_ingestion_trigger.tf — CodePipeline/S3-mirror source + EventBridge ECS trigger.
#
# Implements ADR-0016 §2 & §3: a CodePipeline pipeline with a GitHub/CodeStar source
# action mirrors the repository to S3 on each push; an EventBridge rule fires on
# CodePipeline SUCCEEDED and triggers the Fargate ingestion task via ecs:RunTask.
#
# No NAT gateway — the Fargate task reads git content from the S3 mirror bucket via the
# existing S3 gateway VPC endpoint (ADR-0002). The git mirror bucket is separate from the
# corpus bucket so CodePipeline permissions are isolated from Silver/Gold artifact grants.
#
# Operator prerequisite: the CodeStar connection (codestar_connection_arn variable)
# must be PENDING → AVAILABLE via the AWS Console GitHub OAuth flow before the pipeline
# can pull from GitHub. Terraform provisions the pipeline resources but cannot complete
# the OAuth handshake.

# ── S3 git mirror bucket (CodePipeline artifact store) ─────────────────────────────────
# Separate from the corpus bucket so CodePipeline's artifact-store permissions are
# isolated from the Silver/Gold corpus grants. Versioning is required by CodePipeline.

resource "aws_s3_bucket" "git_mirror" {
  bucket_prefix = "graphrag-git-mirror-"
  force_destroy = true # empties + removes on `terraform destroy` (teardown-first, ADR-0002)
}

resource "aws_s3_bucket_public_access_block" "git_mirror" {
  bucket                  = aws_s3_bucket.git_mirror.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "git_mirror" {
  bucket = aws_s3_bucket.git_mirror.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# TLS-deny bucket policy (mirrors corpus bucket pattern — Deny on aws:SecureTransport=false).
# Principal:"*" is required and legitimate in a Deny statement; this never grants access.
resource "aws_s3_bucket_policy" "git_mirror_tls" {
  bucket = aws_s3_bucket.git_mirror.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.git_mirror.arn,
        "${aws_s3_bucket.git_mirror.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.git_mirror]
}

# CodePipeline requires versioning on the artifact store bucket.
resource "aws_s3_bucket_versioning" "git_mirror" {
  bucket = aws_s3_bucket.git_mirror.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Expire noncurrent versions so storage does not grow unbounded (storage-cost creep).
# CodePipeline writes a new version of latest/repo.zip on every run; 7-day retention
# mirrors the log group retention elsewhere in this stack.
resource "aws_s3_bucket_lifecycle_configuration" "git_mirror" {
  bucket = aws_s3_bucket.git_mirror.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {} # empty filter = apply to all objects in the bucket

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.git_mirror]
}

# ── CodePipeline IAM role ──────────────────────────────────────────────────────────────

resource "aws_iam_role" "codepipeline_role" {
  name_prefix = "graphrag-codepipeline-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codepipeline.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# CodePipeline needs S3 read/write on the artifact store bucket.
resource "aws_iam_role_policy" "codepipeline_s3" {
  name = "s3-artifact-store"
  role = aws_iam_role.codepipeline_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:GetBucketVersioning",
          "s3:PutObject",
        ]
        Resource = [
          aws_s3_bucket.git_mirror.arn,
          "${aws_s3_bucket.git_mirror.arn}/*",
        ]
      },
    ]
  })
}

# CodePipeline must call UseConnection to pull from GitHub via the CodeStar connection.
resource "aws_iam_role_policy" "codepipeline_codestar" {
  name = "codestar-connection-use"
  role = aws_iam_role.codepipeline_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "codestar-connections:UseConnection"
      Resource = var.codestar_connection_arn
    }]
  })
}

# ── CodePipeline pipeline ──────────────────────────────────────────────────────────────
# Single Source stage: GitHub via CodeStar connection → ZIP artifact stored in the mirror
# bucket. The Fargate task reads from this bucket (GIT_MIRROR_BUCKET env var) using the
# S3 gateway VPC endpoint. No CodeBuild stage — the ingestion task handles ZIP extraction.

resource "aws_codepipeline" "git_mirror" {
  name     = "graphrag-git-mirror"
  role_arn = aws_iam_role.codepipeline_role.arn

  artifact_store {
    location = aws_s3_bucket.git_mirror.id
    type     = "S3"
  }

  stage {
    name = "Source"
    action {
      name             = "GitSource"
      category         = "Source"
      owner            = "AWS"
      provider         = "CodeStarSourceConnection"
      version          = "1"
      output_artifacts = ["source_output"]
      configuration = {
        ConnectionArn        = var.codestar_connection_arn
        FullRepositoryId     = var.github_repo_id
        BranchName           = var.github_branch
        OutputArtifactFormat = "CODE_ZIP"
      }
    }
  }

  # CodePipeline requires ≥2 stages. This Deploy stage archives the ZIP artifact to a
  # stable S3 key (latest/repo.zip) so the Fargate ingestion task always has a
  # predictable path — CodePipeline artifact keys are content-addressed and opaque.
  # The Fargate task reads s3://${GIT_MIRROR_BUCKET}/latest/repo.zip and extracts the
  # repository tree from there.
  stage {
    name = "Deploy"
    action {
      name            = "ArchiveToLatest"
      category        = "Deploy"
      owner           = "AWS"
      provider        = "S3"
      version         = "1"
      input_artifacts = ["source_output"]
      configuration = {
        BucketName = aws_s3_bucket.git_mirror.id
        ObjectKey  = "latest/repo.zip"
        Extract    = "false"
      }
    }
  }
}

# ── EventBridge IAM role (ecs:RunTask + iam:PassRole) ─────────────────────────────────

resource "aws_iam_role" "eventbridge_ecs_trigger" {
  name_prefix = "graphrag-eb-ecs-trigger-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ADR-0016: EventBridge must call ecs:RunTask on the ingestion task definition and
# iam:PassRole for both the task role (ingestion_task_role) and the execution role
# (ecs_task_execution_role). Resources scoped to specific ARNs — not "*".
resource "aws_iam_role_policy" "eventbridge_run_task" {
  name = "ecs-run-task"
  role = aws_iam_role.eventbridge_ecs_trigger.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecs:RunTask"
        Resource = aws_ecs_task_definition.ingestion.arn
        Condition = {
          ArnLike = {
            "ecs:cluster" = aws_ecs_cluster.main.arn
          }
        }
      },
      {
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          aws_iam_role.ingestion_task_role.arn,
          aws_iam_role.ecs_task_execution_role.arn,
        ]
        Condition = {
          StringLike = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
    ]
  })
}

# ── EventBridge rule: CodePipeline SUCCEEDED → ECS RunTask ────────────────────────────
# Fires when the git-mirror pipeline completes successfully. The EventBridge target
# launches the Fargate ingestion task in the existing private subnets with the ingestion
# security group — no public IP (ADR-0002: private-isolated subnets only).

resource "aws_cloudwatch_event_rule" "git_ingestion_trigger" {
  name        = "graphrag-git-ingestion-trigger"
  description = "Trigger Fargate ingestion on CodePipeline git mirror success (ADR-0016)."

  event_pattern = jsonencode({
    source        = ["aws.codepipeline"]
    "detail-type" = ["CodePipeline Pipeline Execution State Change"]
    detail = {
      state    = ["SUCCEEDED"]
      pipeline = [aws_codepipeline.git_mirror.name]
    }
  })
}

resource "aws_cloudwatch_event_target" "git_ingestion_task" {
  rule     = aws_cloudwatch_event_rule.git_ingestion_trigger.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge_ecs_trigger.arn

  # Pass the pipeline executionId into the container so the ingestion task can call
  # codepipeline:GetPipelineExecution to resolve the HEAD commit SHA.
  # The Fargate task reads CODEPIPELINE_EXECUTION_ID and calls GetPipelineExecution;
  # the source revision (commit SHA) is returned in the response.
  #
  # Security note: the <execution_id> substitution is literal (no JSON escaping).
  # Safe here because $.detail.executionId is an AWS-generated UUID the caller
  # cannot shape — it never contains " or special chars. A future edit widening
  # input_paths to a user-influenced field would be a security red flag.
  input_transformer {
    input_paths = {
      execution_id = "$.detail.executionId"
    }
    input_template = <<-EOT
      {"containerOverrides":[{"name":"ingestion","environment":[{"name":"CODEPIPELINE_EXECUTION_ID","value":"<execution_id>"}]}]}
    EOT
  }

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.ingestion.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = aws_subnet.private[*].id
      security_groups  = [aws_security_group.ingestion_task_sg.id]
      assign_public_ip = false
    }
  }
}
