# security_groups.tf — the 8 compute/store security groups (6 compute + 2 store)
# and their exact closed-egress + peer-ingress rules.
#
# notebook_sg is the 6th compute SG. It and its two egress rules are declared in
# notebook.tf (ADR-0019) rather than here, so that the Graph Explorer stack stays
# reviewable as one file — but it counts against the totals below.
#
# Translated from apps/infra/stacks/graphrag_stack.py: the SecurityGroup(...
# allow_all_outbound=False) constructs, the `_allow_egress()` helper, and the
# store-SG `add_ingress_rule` calls. The authoritative egress specification is
# the plan-assertion suite's `_TF_COMPUTE_SG_EGRESS` table
# (apps/infra-tf/tests/test_plan.py). Every SG sets egress=[] so no implicit
# allow-all 0.0.0.0/0 rule is created; egress is added ONLY as explicit
# aws_vpc_security_group_egress_rule resources below (defence-in-depth, ADR-0002).
#
# Egress-rule totals (must match _TF_COMPUTE_SG_EGRESS exactly, set equality):
#   ingestion_task_sg = 10, smoke_probe_sg = 3, vector_smoke_sg = 4,
#   query_lambda_sg = 5, mcp_lambda_sg = 5, notebook_sg = 7
#   => 34 egress rules total, owned only by the 6 compute SGs.
# The 2 store SGs and 7 endpoint SGs (network.tf) own ZERO egress rules.

# ── Store security groups (VPC-internal only) ──────────────────────────────────
resource "aws_security_group" "neptune_sg" {
  name_prefix = "graphrag-neptune-"
  description = "Neptune - VPC-internal only"
  vpc_id      = aws_vpc.main.id
  egress      = []

  tags = { Name = "graphrag-neptune" }
}

resource "aws_security_group" "opensearch_sg" {
  name_prefix = "graphrag-opensearch-"
  description = "OpenSearch - VPC-internal only"
  vpc_id      = aws_vpc.main.id
  egress      = []

  tags = { Name = "graphrag-opensearch" }
}

# ── Compute security groups (closed egress: allow_all_outbound=False) ───────────
resource "aws_security_group" "ingestion_task_sg" {
  name_prefix = "graphrag-ingestion-"
  description = "Fargate ingestion"
  vpc_id      = aws_vpc.main.id
  egress      = []

  # egress rules are managed by aws_vpc_security_group_egress_rule resources below;
  # ignore_changes prevents plan drift on subsequent applies.
  lifecycle { ignore_changes = [egress] }

  tags = { Name = "graphrag-ingestion" }
}

resource "aws_security_group" "smoke_probe_sg" {
  name_prefix = "graphrag-smoke-"
  description = "Neptune smoke probe"
  vpc_id      = aws_vpc.main.id
  egress      = []

  lifecycle { ignore_changes = [egress] }

  tags = { Name = "graphrag-smoke" }
}

resource "aws_security_group" "vector_smoke_sg" {
  name_prefix = "graphrag-vector-smoke-"
  description = "OpenSearch+Bedrock vector smoke probe"
  vpc_id      = aws_vpc.main.id
  egress      = []

  lifecycle { ignore_changes = [egress] }

  tags = { Name = "graphrag-vector-smoke" }
}

resource "aws_security_group" "query_lambda_sg" {
  name_prefix = "graphrag-query-"
  description = "query lambda - in-VPC compute (egress to stores + VPC endpoints)"
  vpc_id      = aws_vpc.main.id
  egress      = []

  lifecycle { ignore_changes = [egress] }

  tags = { Name = "graphrag-query" }
}

# ── Store-SG ingress: exact peer-SG set (CDK add_ingress_rule calls) ────────────
# Neptune accepts 8182 from ingestion, smoke, query. OpenSearch accepts 443 from
# ingestion, vector-smoke, query. No other SG may reach the stores; no public CIDR.
resource "aws_vpc_security_group_ingress_rule" "neptune_from_ingestion" {
  security_group_id            = aws_security_group.neptune_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.ingestion_task_sg.id
  description                  = "ingestion to neptune 8182"
}

resource "aws_vpc_security_group_ingress_rule" "neptune_from_smoke" {
  security_group_id            = aws_security_group.neptune_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.smoke_probe_sg.id
  description                  = "smoke probe to neptune 8182"
}

resource "aws_vpc_security_group_ingress_rule" "neptune_from_query" {
  security_group_id            = aws_security_group.neptune_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.query_lambda_sg.id
  description                  = "query lambda to neptune 8182"
}

resource "aws_vpc_security_group_ingress_rule" "opensearch_from_ingestion" {
  security_group_id            = aws_security_group.opensearch_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.ingestion_task_sg.id
  description                  = "ingestion to opensearch 443"
}

resource "aws_vpc_security_group_ingress_rule" "opensearch_from_vector_smoke" {
  security_group_id            = aws_security_group.opensearch_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.vector_smoke_sg.id
  description                  = "vector smoke probe to opensearch 443"
}

resource "aws_vpc_security_group_ingress_rule" "opensearch_from_query" {
  security_group_id            = aws_security_group.opensearch_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.query_lambda_sg.id
  description                  = "query lambda to opensearch 443"
}

# ── ingestion_task_sg egress (10): neptune, opensearch, Bedrock, EcrApi, EcrDocker, ──
# ── Logs, Sts, S3, Textract, DynamoDB ──
resource "aws_vpc_security_group_egress_rule" "ingestion_to_neptune" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.neptune_sg.id
  description                  = "IngestionSg egress to neptune 8182"
}

resource "aws_vpc_security_group_egress_rule" "ingestion_to_opensearch" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.opensearch_sg.id
  description                  = "IngestionSg egress to opensearch 443"
}

resource "aws_vpc_security_group_egress_rule" "ingestion_to_bedrock" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["BedrockRuntime"].id
  description                  = "IngestionSg egress to BedrockRuntime 443"
}

resource "aws_vpc_security_group_egress_rule" "ingestion_to_ecr_api" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["EcrApi"].id
  description                  = "IngestionSg egress to EcrApi 443"
}

resource "aws_vpc_security_group_egress_rule" "ingestion_to_ecr_docker" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["EcrDocker"].id
  description                  = "IngestionSg egress to EcrDocker 443"
}

resource "aws_vpc_security_group_egress_rule" "ingestion_to_logs" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["CloudWatchLogs"].id
  description                  = "IngestionSg egress to CloudWatchLogs 443"
}

resource "aws_vpc_security_group_egress_rule" "ingestion_to_sts" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["Sts"].id
  description                  = "IngestionSg egress to Sts 443"
}

resource "aws_vpc_security_group_egress_rule" "ingestion_to_s3" {
  security_group_id = aws_security_group.ingestion_task_sg.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_ec2_managed_prefix_list.s3.id
  description       = "IngestionSg egress to s3 prefix list 443"
}

# Textract OCR path (infra-tf-p0-gap-remediation AC1): scanned-PDF extraction.
resource "aws_vpc_security_group_egress_rule" "ingestion_to_textract" {
  security_group_id            = aws_security_group.ingestion_task_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["Textract"].id
  description                  = "IngestionSg egress to Textract 443"
}

# Status-registry path (infra-tf-p0-gap-remediation AC4): DynamoDB rides the
# gateway endpoint via the AWS-managed prefix list (same shape as S3 above).
resource "aws_vpc_security_group_egress_rule" "ingestion_to_dynamodb" {
  security_group_id = aws_security_group.ingestion_task_sg.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_ec2_managed_prefix_list.dynamodb.id
  description       = "IngestionSg egress to dynamodb prefix list 443"
}

# ── smoke_probe_sg egress (3): neptune, Logs, Sts ──────────────────────────────
resource "aws_vpc_security_group_egress_rule" "smoke_to_neptune" {
  security_group_id            = aws_security_group.smoke_probe_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.neptune_sg.id
  description                  = "SmokeSg egress to neptune 8182"
}

resource "aws_vpc_security_group_egress_rule" "smoke_to_logs" {
  security_group_id            = aws_security_group.smoke_probe_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["CloudWatchLogs"].id
  description                  = "SmokeSg egress to CloudWatchLogs 443"
}

resource "aws_vpc_security_group_egress_rule" "smoke_to_sts" {
  security_group_id            = aws_security_group.smoke_probe_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["Sts"].id
  description                  = "SmokeSg egress to Sts 443"
}

# ── vector_smoke_sg egress (4): opensearch, Bedrock, Logs, Sts ─────────────────
resource "aws_vpc_security_group_egress_rule" "vector_smoke_to_opensearch" {
  security_group_id            = aws_security_group.vector_smoke_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.opensearch_sg.id
  description                  = "VectorSmokeSg egress to opensearch 443"
}

resource "aws_vpc_security_group_egress_rule" "vector_smoke_to_bedrock" {
  security_group_id            = aws_security_group.vector_smoke_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["BedrockRuntime"].id
  description                  = "VectorSmokeSg egress to BedrockRuntime 443"
}

resource "aws_vpc_security_group_egress_rule" "vector_smoke_to_logs" {
  security_group_id            = aws_security_group.vector_smoke_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["CloudWatchLogs"].id
  description                  = "VectorSmokeSg egress to CloudWatchLogs 443"
}

resource "aws_vpc_security_group_egress_rule" "vector_smoke_to_sts" {
  security_group_id            = aws_security_group.vector_smoke_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["Sts"].id
  description                  = "VectorSmokeSg egress to Sts 443"
}

# ── query_lambda_sg egress (5): neptune, opensearch, Bedrock, Logs, Sts ────────
resource "aws_vpc_security_group_egress_rule" "query_to_neptune" {
  security_group_id            = aws_security_group.query_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.neptune_sg.id
  description                  = "QuerySg egress to neptune 8182"
}

resource "aws_vpc_security_group_egress_rule" "query_to_opensearch" {
  security_group_id            = aws_security_group.query_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.opensearch_sg.id
  description                  = "QuerySg egress to opensearch 443"
}

resource "aws_vpc_security_group_egress_rule" "query_to_bedrock" {
  security_group_id            = aws_security_group.query_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["BedrockRuntime"].id
  description                  = "QuerySg egress to BedrockRuntime 443"
}

resource "aws_vpc_security_group_egress_rule" "query_to_logs" {
  security_group_id            = aws_security_group.query_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["CloudWatchLogs"].id
  description                  = "QuerySg egress to CloudWatchLogs 443"
}

resource "aws_vpc_security_group_egress_rule" "query_to_sts" {
  security_group_id            = aws_security_group.query_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["Sts"].id
  description                  = "QuerySg egress to Sts 443"
}

# ── mcp_lambda_sg egress (5): neptune, opensearch, Bedrock, Logs, Sts ──────────
# Same egress profile as query_lambda_sg: the MCP tool server is VPC-resident and
# reaches Neptune (SPARQL) + OpenSearch (kNN) + Bedrock (embed + synthesis) + VPC
# endpoints for logs and STS.
resource "aws_security_group" "mcp_lambda_sg" {
  name_prefix = "graphrag-mcp-"
  description = "MCP tool-server lambda - in-VPC compute (egress to stores + VPC endpoints)"
  vpc_id      = aws_vpc.main.id
  egress      = []

  lifecycle { ignore_changes = [egress] }

  tags = { Name = "graphrag-mcp" }
}

resource "aws_vpc_security_group_egress_rule" "mcp_to_neptune" {
  security_group_id            = aws_security_group.mcp_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.neptune_sg.id
  description                  = "McpSg egress to neptune 8182"
}

resource "aws_vpc_security_group_egress_rule" "mcp_to_opensearch" {
  security_group_id            = aws_security_group.mcp_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.opensearch_sg.id
  description                  = "McpSg egress to opensearch 443"
}

resource "aws_vpc_security_group_egress_rule" "mcp_to_bedrock" {
  security_group_id            = aws_security_group.mcp_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["BedrockRuntime"].id
  description                  = "McpSg egress to BedrockRuntime 443"
}

resource "aws_vpc_security_group_egress_rule" "mcp_to_logs" {
  security_group_id            = aws_security_group.mcp_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["CloudWatchLogs"].id
  description                  = "McpSg egress to CloudWatchLogs 443"
}

resource "aws_vpc_security_group_egress_rule" "mcp_to_sts" {
  security_group_id            = aws_security_group.mcp_lambda_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.endpoint["Sts"].id
  description                  = "McpSg egress to Sts 443"
}

# ── Store-SG ingress: MCP Lambda added to existing peer-SG sets ───────────────
# Neptune and OpenSearch each gain one new ingress rule from mcp_lambda_sg.
# neptune_sg now accepts from: ingestion, smoke, query, mcp (4 total).
# opensearch_sg now accepts from: ingestion, vector_smoke, query, mcp (4 total).
resource "aws_vpc_security_group_ingress_rule" "neptune_from_mcp" {
  security_group_id            = aws_security_group.neptune_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
  referenced_security_group_id = aws_security_group.mcp_lambda_sg.id
  description                  = "mcp lambda to neptune 8182"
}

resource "aws_vpc_security_group_ingress_rule" "opensearch_from_mcp" {
  security_group_id            = aws_security_group.opensearch_sg.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.mcp_lambda_sg.id
  description                  = "mcp lambda to opensearch 443"
}
