# neptune.tf — Neptune Serverless cluster: VPC-resident (subnet group), min capacity,
# IAM-auth, encrypted, with the pinned query-timeout parameter group.
#
# Translated from apps/infra/stacks/graphrag_stack.py `_neptune()` (:489). Engine
# version 1.3.5.0 and parameter-group family neptune1.3 are PINNED per ADR-0011 (SPARQL
# read-cost backstop; ADR-0011 supersedes ADR-0004); auto_minor_version_upgrade=false
# prevents a silent minor bump from drifting the pinned pair. skip_final_snapshot=true +
# no prevent_destroy make the cluster destroyable (teardown-first, ADR-0002).

resource "aws_neptune_subnet_group" "main" {
  name_prefix = "graphrag-neptune-"
  description = "graphrag neptune (private isolated subnets)"
  subnet_ids  = aws_subnet.private[*].id # >=2 AZs — a Neptune subnet group requires it
}

# The SPARQL read-cost backstop (ADR-0011): pins neptune_query_timeout so a runaway
# model-authored SPARQL traversal is killed by the engine. The family MUST match the
# engine version below.
resource "aws_neptune_cluster_parameter_group" "main" {
  name_prefix = "graphrag-neptune-"
  family      = "neptune1.3"
  description = "graphrag neptune - read-cost backstop (query timeout) for text2sparql (ADR-0011)"

  parameter {
    name  = "neptune_query_timeout"
    value = "20000"
  }

  # Audit logging: records every connection and query against the cluster. Static
  # parameter — a change only takes effect after an instance reboot, and the cluster's
  # enable_cloudwatch_logs_exports below is inert until it is active.
  parameter {
    name  = "neptune_enable_audit_log"
    value = "1"
  }
}

resource "aws_neptune_cluster" "main" {
  cluster_identifier_prefix = "graphrag-"
  engine                    = "neptune"
  engine_version            = "1.3.5.0" # pinned to match the parameter-group family

  neptune_subnet_group_name = aws_neptune_subnet_group.main.name
  # Load-bearing: without this the cluster uses the default parameter group and the
  # ADR-0011 SPARQL query-timeout backstop is inert (defaults to 120s).
  neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.main.name
  vpc_security_group_ids               = [aws_security_group.neptune_sg.id]

  iam_database_authentication_enabled = true # IAM-enforced access (ADR-0002)
  storage_encrypted                   = true
  skip_final_snapshot                 = true # ephemeral — no snapshot to retain

  # Ship the audit log to CloudWatch Logs. Load-bearing pair with
  # neptune_enable_audit_log=1 in the parameter group above: without the parameter the
  # engine writes no audit records, and without this export they never leave the cluster.
  enable_cloudwatch_logs_exports = ["audit"]

  # Serverless at minimum capacity — scales down when idle (not to zero).
  serverless_v2_scaling_configuration {
    min_capacity = 1.0
    max_capacity = 2.5
  }
}

resource "aws_neptune_cluster_instance" "main" {
  cluster_identifier         = aws_neptune_cluster.main.id
  instance_class             = "db.serverless"
  auto_minor_version_upgrade = false # intentional hardening (CDK default is true)
}
