# iam.tf — the 3 in-tier IAM roles, their trust policies, the
# AWSLambdaVPCAccessExecutionRole attachments, and every least-privilege inline policy
# (Neptune / OpenSearch / Bedrock / S3).
#
# Translated from apps/infra/stacks/graphrag_stack.py: IngestionTaskRole (:349),
# VectorProbeRole (:352), QueryRole (:848), and the _neptune_data_access (:392),
# _neptune_read_only_access (:399), _opensearch_data_access (:672), _bedrock_invoke
# (:677), _bedrock_synthesis_invoke (:692) statements plus the bucket.grant_read /
# grant_put calls (:568-575).
#
# Load-bearing invariants (spec AC6-AC8):
#   - QueryRole Neptune grant is READ-ONLY: connect + ReadDataViaQuery only (ADR-0011).
#     IAM is allow-union, so QueryRole gets NO policy carrying Write/DeleteDataViaQuery.
#   - No data-plane action carries Resource = "*". ARNs are constructed from the fixed
#     names + account id so no role policy self-references the domain/cluster (which
#     would create a Terraform dependency cycle).
#   - SmokeProbeRole is created in the compute tier (infra-terraform-compute), not here.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Neptune IAM-auth data-plane ARN. cluster_resource_id is known-after-apply, so this
  # renders as an unknown value in `terraform plan` — never the literal "*" (spec AC8).
  neptune_cluster_arn = "arn:aws:neptune-db:${var.aws_region}:${local.account_id}:${aws_neptune_cluster.main.cluster_resource_id}/*"

  # OpenSearch domain ARN — constructed from the FIXED domain name (not the resource
  # attribute) so neither the access policy nor the role policies self-reference the
  # domain (avoids a dependency cycle). Mirrors CDK _opensearch_domain_arn (:665).
  opensearch_domain_arn = "arn:aws:es:${var.aws_region}:${local.account_id}:domain/graphrag-vectors/*"

  # Bedrock Titan v2 embeddings model (foundation-model ARNs carry no account id).
  titan_model_arn = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"

  # Bedrock synthesis (Claude Sonnet) is a cross-region inference profile: scope BOTH
  # the account+region-qualified inference-profile ARN AND each underlying regional
  # foundation-model ARN it routes to — never a wildcard Resource (spec AC7/AC8).
  # Mirrors CDK _bedrock_synthesis_invoke (:692).
  # Single source of truth for the synthesis model id: BOTH the query_role bedrock grant
  # (the inference-profile ARN below) and the QueryLambda's SYNTHESIS_MODEL_ID env var
  # (lambda.tf) reference this local. If they drift, query_role grants profile X while the
  # Lambda invokes profile Y -> runtime AccessDenied on the query path.
  synthesis_model_id = "us.anthropic.claude-sonnet-4-6"

  synthesis_arns = concat(
    ["arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/${local.synthesis_model_id}"],
    [for r in ["us-east-1", "us-east-2", "us-west-2"] :
    "arn:aws:bedrock:${r}::foundation-model/anthropic.claude-sonnet-4-6"]
  )

  # es:ESHttp* data-plane verbs (identity-side), scoped to the domain (least privilege).
  # Mirrors CDK _OPENSEARCH_DATA_ACTIONS (:117).
  opensearch_data_actions = [
    "es:ESHttpGet",
    "es:ESHttpPut",
    "es:ESHttpPost",
    "es:ESHttpDelete",
    "es:ESHttpHead",
  ]

  # ── Inline policy documents (shared where more than one role holds the grant) ──
  neptune_rw_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "neptune-db:connect",
        "neptune-db:ReadDataViaQuery",
        "neptune-db:WriteDataViaQuery",
        "neptune-db:DeleteDataViaQuery",
      ]
      Resource = local.neptune_cluster_arn
    }]
  })

  # ADR-0011 backstop (carries forward the proven read-only control): connect + ReadDataViaQuery only, never Write/Delete.
  neptune_readonly_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["neptune-db:connect", "neptune-db:ReadDataViaQuery"]
      Resource = local.neptune_cluster_arn
    }]
  })

  opensearch_data_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = local.opensearch_data_actions
      Resource = local.opensearch_domain_arn
    }]
  })

  # Read-only OpenSearch policy for the MCP tool server (retrieval-only path).
  # Excludes ESHttpPut and ESHttpDelete — the MCP Lambda never writes to the vector index.
  # ESHttpPost is retained: kNN _search queries use POST.
  opensearch_readonly_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["es:ESHttpGet", "es:ESHttpPost", "es:ESHttpHead"]
      Resource = local.opensearch_domain_arn
    }]
  })

  bedrock_titan_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock:InvokeModel"
      Resource = local.titan_model_arn
    }]
  })

  bedrock_synthesis_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
      Resource = local.synthesis_arns
    }]
  })

  # S3 read (CDK bucket.grant_read): GetObject on objects + ListBucket on the bucket.
  s3_read_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.corpus.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.corpus.arn
      },
    ]
  })

  # Three separate key/prefix-scoped PutObject grants (CDK bucket.grant_put x3),
  # never a bucket-wide PutObject.
  s3_put_manifest_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = "${aws_s3_bucket.corpus.arn}/manifest.json"
    }]
  })

  s3_put_trace_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = "${aws_s3_bucket.corpus.arn}/schema_extraction_trace.txt"
    }]
  })

  s3_put_silver_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = "${aws_s3_bucket.corpus.arn}/silver/*"
    }]
  })

  # Read-only access to the CodePipeline git mirror bucket (ADR-0016).
  # GetObject on objects + ListBucket on the bucket — mirrors s3_read_policy above
  # but scoped to the mirror bucket. Kept separate from corpus read so policy names
  # remain self-describing and least-privilege audit is straightforward.
  s3_read_git_mirror_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.git_mirror.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.git_mirror.arn
      },
    ]
  })
}

# ── Roles + trust policies ─────────────────────────────────────────────────────
resource "aws_iam_role" "ingestion_task_role" {
  name_prefix = "graphrag-ingestion-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role" "vector_probe_role" {
  name_prefix = "graphrag-vector-probe-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role" "query_role" {
  name_prefix = "graphrag-query-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ── AWSLambdaVPCAccessExecutionRole (the VPC-Lambda ENI lifecycle managed policy) ──
resource "aws_iam_role_policy_attachment" "vector_probe_vpc_access" {
  role       = aws_iam_role.vector_probe_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "query_vpc_access" {
  role       = aws_iam_role.query_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# ── IngestionTaskRole inline policies (8): full Neptune R/W, OpenSearch, Bedrock ──
# Titan + synthesis, S3 read + 3 scoped PutObject grants.
resource "aws_iam_role_policy" "ingestion_neptune_rw" {
  name   = "neptune-data-rw"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.neptune_rw_policy
}

resource "aws_iam_role_policy" "ingestion_opensearch" {
  name   = "opensearch-data"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.opensearch_data_policy
}

resource "aws_iam_role_policy" "ingestion_bedrock_titan" {
  name   = "bedrock-titan"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.bedrock_titan_policy
}

resource "aws_iam_role_policy" "ingestion_bedrock_synthesis" {
  name   = "bedrock-synthesis"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.bedrock_synthesis_policy
}

resource "aws_iam_role_policy" "ingestion_s3_read" {
  name   = "s3-read"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.s3_read_policy
}

resource "aws_iam_role_policy" "ingestion_s3_put_manifest" {
  name   = "s3-put-manifest"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.s3_put_manifest_policy
}

resource "aws_iam_role_policy" "ingestion_s3_put_trace" {
  name   = "s3-put-trace"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.s3_put_trace_policy
}

resource "aws_iam_role_policy" "ingestion_s3_put_silver" {
  name   = "s3-put-silver"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.s3_put_silver_policy
}

resource "aws_iam_role_policy" "ingestion_s3_git_mirror_read" {
  name   = "s3-git-mirror-read"
  role   = aws_iam_role.ingestion_task_role.id
  policy = local.s3_read_git_mirror_policy
}

# The ingestion task calls codepipeline:GetPipelineExecution to resolve the HEAD commit
# SHA from the CODEPIPELINE_EXECUTION_ID passed by the EventBridge input_transformer.
# Scoped to the git-mirror pipeline ARN — no wildcard.
resource "aws_iam_role_policy" "ingestion_codepipeline_get_execution" {
  name = "codepipeline-get-execution"
  role = aws_iam_role.ingestion_task_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "codepipeline:GetPipelineExecution"
      Resource = aws_codepipeline.git_mirror.arn
    }]
  })
}

# ── VectorProbeRole inline policies (2): OpenSearch data + Bedrock Titan ──────────
resource "aws_iam_role_policy" "vector_probe_opensearch" {
  name   = "opensearch-data"
  role   = aws_iam_role.vector_probe_role.id
  policy = local.opensearch_data_policy
}

resource "aws_iam_role_policy" "vector_probe_bedrock_titan" {
  name   = "bedrock-titan"
  role   = aws_iam_role.vector_probe_role.id
  policy = local.bedrock_titan_policy
}

# ── QueryRole inline policies (4): Neptune READ-ONLY, OpenSearch, Bedrock Titan + ──
# synthesis. No Write/Delete Neptune action on this role (ADR-0011).
resource "aws_iam_role_policy" "query_neptune_readonly" {
  name   = "neptune-data-readonly"
  role   = aws_iam_role.query_role.id
  policy = local.neptune_readonly_policy
}

resource "aws_iam_role_policy" "query_opensearch" {
  name   = "opensearch-data"
  role   = aws_iam_role.query_role.id
  policy = local.opensearch_data_policy
}

resource "aws_iam_role_policy" "query_bedrock_titan" {
  name   = "bedrock-titan"
  role   = aws_iam_role.query_role.id
  policy = local.bedrock_titan_policy
}

resource "aws_iam_role_policy" "query_bedrock_synthesis" {
  name   = "bedrock-synthesis"
  role   = aws_iam_role.query_role.id
  policy = local.bedrock_synthesis_policy
}
