# notebook.tf — Neptune Graph Explorer on a private-subnet SageMaker notebook.
#
# ADR-0019. This codifies a capability that was deployed out-of-band on 2026-08-07 and
# lived only in remote state; the shape here is deliberately NOT the as-deployed shape.
# What changed and why is in the ADR — in short, the deployed version sat in a public
# subnet behind an internet gateway with root access, `neptune-db:*`, and
# AmazonSageMakerFullAccess.
#
# NOT CODIFIED, deliberately: the dedicated public subnet (10.0.2.0/24), the internet
# gateway, and its route table. Those were removed from the account during the 2026-09-12
# reconcile; nothing here should reintroduce them.
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

  # Notebook name is FIXED so the proxy hostname is computable without referencing the
  # instance itself — the lifecycle config is consumed BY the notebook, so reading
  # aws_sagemaker_notebook_instance.graph_explorer.url here would be a dependency cycle.
  # Same trick as the fixed domain name in opensearch.tf.
  graph_explorer_notebook_name = "graphrag-neptune-explorer"
  graph_explorer_host          = "${local.graph_explorer_notebook_name}.notebook.${var.aws_region}.sagemaker.aws"
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

  # Without this, replacing the SG deadlocks: Terraform destroys the old group first,
  # but neptune_from_notebook still references it (DependencyViolation), and that rule
  # cannot be repointed until the replacement group exists. The destroy then retries for
  # 15 minutes and fails. name_prefix makes create-before-destroy safe — the two groups
  # get distinct generated names rather than colliding. Hit for real on 2026-09-11.
  # ignore_changes = [egress] matches every other compute SG here: egress lives in
  # aws_vpc_security_group_egress_rule resources, and without this the SG resource's
  # `egress = []` fights them on every plan — a perpetual diff that deletes the rules and
  # recreates them forever. Omitting it was an oversight caught by a post-apply plan.
  lifecycle {
    create_before_destroy = true
    ignore_changes        = [egress]
  }
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
  # MUST return within 5 minutes or SageMaker marks the instance Failed — pulling a
  # ~170 MB image over VPC endpoints does not reliably fit in that budget, and the first
  # attempt at this timed out and failed the notebook outright. So the hook only detaches
  # a background worker and exits; the pull and run continue after it returns. This is
  # AWS's documented pattern for long-running lifecycle work.
  #
  # Progress and failures land in /var/log/graph-explorer-start.log on the instance,
  # which is where to look if the UI is not up a few minutes after the notebook reports
  # InService.
  on_start = base64encode(<<-EOT
    #!/bin/bash
    set -eux

    # The worker is written to a FILE and then invoked, rather than passed to sudo as a
    # multi-line string. `sudo -u ec2-user -i bash -c '<script>'` joins its arguments and
    # re-parses them through a login shell: newlines collapse, every comment swallows the
    # line after it, and variable assignments vanish. That produced
    # "NOTEBOOK_HOST: unbound variable" with the script body echoed into the log. A file
    # has none of those problems.
    #
    # It must run as ec2-user because AL2023 Docker is ROOTLESS and owned by that user —
    # as root, docker talks to /var/run/docker.sock and fails with
    # "Cannot connect to the Docker daemon". `-i` gives the login shell that sets up the
    # rootless DOCKER_HOST.
    cat > /usr/local/bin/start-graph-explorer.sh <<'WORKER_EOF'
    #!/bin/bash
    set -eux

    REPO="${aws_ecr_repository.graph_explorer.repository_url}"
    TAG="${local.graph_explorer_tag}"
    NEPTUNE="https://${aws_neptune_cluster.main.endpoint}:8182"
    REGION="${var.aws_region}"
    NOTEBOOK_HOST="${local.graph_explorer_host}"
    # Hoisted to its own line purely so the allowlist pragma has somewhere to live:
    # detect-secrets scores this path as a high-entropy base64 string, and the pragma
    # cannot sit on the `docker run` line below because that line ends in a shell
    # continuation backslash. Not a secret — a URL path.
    EXPLORER_ROOT="/proxy/9250/explorer" # pragma: allowlist secret

    for i in 1 2 3 4 5 6 7 8 9 10; do
      if aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$${REPO%%/*}"; then
        break
      fi
      echo "ecr login attempt $i failed; retrying in 15s"
      sleep 15
    done

    for i in 1 2 3 4 5; do
      if docker pull "$REPO:$TAG"; then
        break
      fi
      echo "pull attempt $i failed; retrying in 20s"
      sleep 20
    done

    docker rm -f graph-explorer 2>/dev/null || true

    # PUBLIC_OR_PROXY_ENDPOINT must carry the /proxy/9250 base path, or the UI builds a
    # Default Connection whose every request is routed to the SageMaker Jupyter proxy and
    # returns 403 with a Jupyter HTML body. GRAPH_TYPE=sparql because this cluster is
    # RDF/SPARQL (ADR-0011). Verified end-to-end in a browser.
    docker run -d \
      --name graph-explorer \
      --restart always \
      -p 9250:9250 \
      -e "PUBLIC_OR_PROXY_ENDPOINT=https://$NOTEBOOK_HOST/proxy/9250" \
      -e "GRAPH_EXP_ENV_ROOT_FOLDER=$EXPLORER_ROOT" \
      -e "GRAPH_CONNECTION_URL=$NEPTUNE" \
      -e "GRAPH_TYPE=sparql" \
      -e "SERVICE_TYPE=neptune-db" \
      -e "USING_PROXY_SERVER=true" \
      -e "IAM=true" \
      -e "AWS_REGION=$REGION" \
      -e "PROXY_SERVER_HTTPS_CONNECTION=false" \
      -e "GRAPH_EXP_HTTPS_CONNECTION=false" \
      -e "LOG_LEVEL=info" \
      "$REPO:$TAG"
    WORKER_EOF

    chmod 0755 /usr/local/bin/start-graph-explorer.sh

    # setsid + closed stdin so the hook returns immediately. SageMaker fails the instance
    # if OnStart has not returned within 5 minutes, and the image pull alone can exceed it.
    setsid nohup sudo -u ec2-user -i /usr/local/bin/start-graph-explorer.sh \
      > /var/log/graph-explorer-start.log 2>&1 < /dev/null &

    echo "lifecycle hook returning after $${SECONDS}s"
    exit 0
  EOT
  )
}

# Private subnet, no public IP, no outbound internet. The operator still reaches the UI
# through the SageMaker-hosted proxy URL (a control-plane path), so disabling direct
# internet access costs no accessibility — see ADR-0019.
resource "aws_sagemaker_notebook_instance" "graph_explorer" {
  name          = local.graph_explorer_notebook_name
  instance_type = "ml.t3.medium"
  role_arn      = aws_iam_role.notebook_role.arn
  volume_size   = 20

  subnet_id              = aws_subnet.private[0].id
  security_groups        = [aws_security_group.notebook_sg.id]
  direct_internet_access = "Disabled"
  root_access            = "Disabled"

  # platform_identifier is left unset ON PURPOSE, and the reason is not cosmetic.
  #
  # SageMaker now accepts only notebook-al2023-v1 here — AL2 platforms are retired and
  # CreateNotebookInstance rejects notebook-al2-v3 with "not supported for this service".
  # The pinned provider (hashicorp/aws 5.100.0) in turn validates this field against a
  # client-side allowlist that predates AL2023 and refuses the one value AWS accepts, so
  # the field cannot be set at all until the provider pin moves. Unset lets AWS apply
  # al2023-v1, which is the only option anyway.
  #
  # AL2023 runs Docker ROOTLESS under ec2-user, which is why the lifecycle script below
  # does its work via `sudo -u ec2-user -i` rather than as root.
  lifecycle_config_name = aws_sagemaker_notebook_instance_lifecycle_configuration.graph_explorer.name

  tags = { Name = "graphrag-neptune-explorer" }
}
