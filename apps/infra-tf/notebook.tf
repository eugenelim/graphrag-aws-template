# notebook.tf — Neptune Graph Explorer on a private-subnet SageMaker notebook.
#
# ADR-0019. This codifies a capability that was deployed out-of-band on 2026-08-07 and
# lived only in remote state; the shape here is deliberately NOT the as-deployed shape.
# What changed and why is in the ADR — in short, the deployed version sat in a public
# subnet behind an internet gateway with root access, `neptune-db:*`, and
# AmazonSageMakerFullAccess.
#
# NOT CODIFIED, deliberately: the dedicated public subnet (10.0.2.0/24), the internet
# gateway, and its route table. Those exist in live state and must be removed when this
# lands — see the reconciliation note in docs/architecture/deployment-and-verification.md.
#
# PREREQUISITE. The explorer image must be mirrored into this account before the notebook
# starts, because ECR Public has no PrivateLink endpoint and this notebook has no route to
# the internet (ADR-0002 no-NAT). The lifecycle script below pulls from the private repo:
#
#   docker pull  public.ecr.aws/neptune/graph-explorer:sagemaker-3.2.0
#   docker tag   public.ecr.aws/neptune/graph-explorer:sagemaker-3.2.0 <repo_url>:sagemaker-3.2.0
#   docker push  <repo_url>:sagemaker-3.2.0
#
# Without it the notebook starts and the OnStart script fails loudly in
# /var/log/sagemaker/ — a visible failure, not a silent one.

locals {
  # Pinned explorer version. Bumping this is a two-step change: re-mirror, then apply.
  graph_explorer_tag = "sagemaker-3.2.0"
}

# ── Mirrored image ───────────────────────────────────────────────────────────
#
# force_delete mirrors aws_ecr_repository.ingestion: teardown-first (ADR-0002), so
# `terraform destroy` does not strand a repo full of layers.

resource "aws_ecr_repository" "graph_explorer" {
  name         = "graphrag-graph-explorer"
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ── IAM ──────────────────────────────────────────────────────────────────────
#
# Read-only by construction, not by naming convention. The out-of-band role carried an
# inline policy called "neptune-data-readonly" that granted neptune-db:* — the name was
# wrong in the direction that matters. These four actions are the read surface Graph
# Explorer needs: run queries, read engine/query status, and fetch the graph summary it
# uses to draw the schema panel. No write, no delete, no reset.

resource "aws_iam_role" "notebook_role" {
  name_prefix = "graphrag-notebook-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "notebook_neptune_readonly" {
  name = "neptune-data-readonly"
  role = aws_iam_role.notebook_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "neptune-db:ReadDataViaQuery",
        "neptune-db:GetEngineStatus",
        "neptune-db:GetQueryStatus",
        "neptune-db:GetGraphSummary",
      ]
      Resource = local.neptune_cluster_arn
    }]
  })
}

# Replaces AmazonSageMakerFullAccess. A notebook that pulls one image and writes logs does
# not need s3:*, iam:PassRole, or the training/endpoint surface that managed policy brings.
resource "aws_iam_role_policy" "notebook_minimal" {
  name = "notebook-minimal"
  role = aws_iam_role.notebook_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PullMirroredExplorerImage"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = aws_ecr_repository.graph_explorer.arn
      },
      {
        Sid      = "EcrAuthToken"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*" # GetAuthorizationToken is not resource-scopable
      },
      {
        Sid    = "NotebookLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/sagemaker/NotebookInstances",
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/sagemaker/NotebookInstances:log-stream:*",
        ]
      },
    ]
  })
}

# ── Network ──────────────────────────────────────────────────────────────────
#
# egress = [] then explicit rules, matching every other SG here (ADR-0002
# defence-in-depth). Two destinations only: Neptune on 8182, and 443 inside the VPC for
# the interface endpoints (ECR, logs, STS, SageMaker API). No 0.0.0.0/0.

resource "aws_security_group" "notebook_sg" {
  name_prefix = "graphrag-notebook-"
  description = "Graph Explorer notebook - egress to Neptune + in-VPC endpoints only"
  vpc_id      = aws_vpc.main.id
  egress      = []

  tags = { Name = "graphrag-notebook" }
}

resource "aws_vpc_security_group_egress_rule" "notebook_to_neptune" {
  security_group_id            = aws_security_group.notebook_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.neptune_sg.id
  description                  = "NotebookSg egress to neptune 8182"
}

# Image pull: ECR api + dkr for the registry calls, S3 for the layer bucket.
resource "aws_vpc_security_group_egress_rule" "notebook_to_ecr_api" {
  security_group_id            = aws_security_group.notebook_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["EcrApi"].id
  description                  = "NotebookSg egress to EcrApi 443"
}

resource "aws_vpc_security_group_egress_rule" "notebook_to_ecr_docker" {
  security_group_id            = aws_security_group.notebook_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["EcrDocker"].id
  description                  = "NotebookSg egress to EcrDocker 443"
}

resource "aws_vpc_security_group_egress_rule" "notebook_to_s3" {
  security_group_id = aws_security_group.notebook_sg.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_ec2_managed_prefix_list.s3.id
  description       = "NotebookSg egress to s3 prefix list 443"
}

resource "aws_vpc_security_group_egress_rule" "notebook_to_logs" {
  security_group_id            = aws_security_group.notebook_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["CloudWatchLogs"].id
  description                  = "NotebookSg egress to CloudWatchLogs 443"
}

resource "aws_vpc_security_group_egress_rule" "notebook_to_sts" {
  security_group_id            = aws_security_group.notebook_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["Sts"].id
  description                  = "NotebookSg egress to Sts 443"
}

resource "aws_vpc_security_group_egress_rule" "notebook_to_sagemaker_api" {
  security_group_id            = aws_security_group.notebook_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["SageMakerApi"].id
  description                  = "NotebookSg egress to SageMakerApi 443"
}

resource "aws_vpc_security_group_ingress_rule" "neptune_from_notebook" {
  security_group_id            = aws_security_group.neptune_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.notebook_sg.id
  description                  = "Neptune allows Graph Explorer notebook on 8182"
}

# ── Notebook ─────────────────────────────────────────────────────────────────

resource "aws_sagemaker_notebook_instance_lifecycle_configuration" "graph_explorer" {
  name = "graphrag-graph-explorer"

  # No OnCreate pull: the image comes from the account's own ECR, so OnStart both
  # authenticates and pulls. Idempotent — safe across stop/start cycles.
  on_start = base64encode(<<-EOT
    #!/bin/bash
    set -ex

    REPO="${aws_ecr_repository.graph_explorer.repository_url}"
    TAG="${local.graph_explorer_tag}"

    aws ecr get-login-password --region ${var.aws_region} \
      | docker login --username AWS --password-stdin "$${REPO%%/*}"

    docker pull "$${REPO}:$${TAG}"

    docker stop graph-explorer 2>/dev/null || true
    docker rm   graph-explorer 2>/dev/null || true

    docker run -d \
      --name graph-explorer \
      --restart always \
      -p 9250:9250 \
      -e "graph-db-connection-url=https://${aws_neptune_cluster.main.endpoint}:8182" \
      -e "AWS_REGION=${var.aws_region}" \
      -e "SERVICE_TYPE=neptune-db" \
      -e "USING_PROXY_SERVER=true" \
      -e "IAM=true" \
      -e "LOG_LEVEL=info" \
      "$${REPO}:$${TAG}"
  EOT
  )
}

# Private subnet, no public IP, no outbound internet. The operator still reaches the UI
# through the SageMaker-hosted proxy URL (a control-plane path), so disabling direct
# internet access costs no accessibility — see ADR-0019.
resource "aws_sagemaker_notebook_instance" "graph_explorer" {
  name          = "graphrag-neptune-explorer"
  instance_type = "ml.t3.medium"
  role_arn      = aws_iam_role.notebook_role.arn
  volume_size   = 20

  subnet_id              = aws_subnet.private[0].id
  security_groups        = [aws_security_group.notebook_sg.id]
  direct_internet_access = "Disabled"
  root_access            = "Disabled"

  # platform_identifier is deliberately unset. The out-of-band instance runs
  # notebook-al2023-v1, but the pinned provider (hashicorp/aws 5.100.0) validates this
  # field against a client-side allowlist that predates AL2023 and rejects the value.
  # Leaving it unset lets AWS apply its current default rather than pinning the older
  # notebook-al2-v3. Set it explicitly once the provider pin moves — tracked as a
  # follow-up, not worth a provider bump on its own.
  lifecycle_config_name = aws_sagemaker_notebook_instance_lifecycle_configuration.graph_explorer.name

  tags = { Name = "graphrag-neptune-explorer" }
}
