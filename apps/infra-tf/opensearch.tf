# opensearch.tf — single-node, VPC-resident, encrypted OpenSearch domain with an
# IAM-scoped resource access policy.
#
# Translated from apps/infra/stacks/graphrag_stack.py `_opensearch()` (:722). The domain
# name is FIXED ("graphrag-vectors") so its ARN is computable without a self-reference
# (avoids a dependency cycle in access_policies). Single data node -> exactly one subnet,
# no zone awareness (ADR-0002 single-node cost posture, not HA).
#
# access_policies names EXACTLY 2 principals (IngestionTaskRole + VectorProbeRole),
# matching the CDK (:363 passes [task_role, vector_probe_role]). QueryRole is NOT here —
# it reaches OpenSearch via its identity policy (iam.tf query_opensearch), relying on
# same-account IAM allow-union. Never Principal:"*", never account-root (spec AC4).

resource "aws_opensearch_domain" "graphrag_vectors" {
  domain_name    = "graphrag-vectors"
  engine_version = "OpenSearch_2.11"

  cluster_config {
    instance_count         = 1
    instance_type          = "t3.small.search"
    zone_awareness_enabled = false
  }

  ebs_options {
    ebs_enabled = true
    volume_size = 10
    volume_type = "gp3"
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https = true
    # Intentional hardening beyond CDK parity (the L2 default floor is TLS 1.0):
    # reject TLS 1.0/1.1 negotiation on the ENI path. All in-VPC clients (boto3 /
    # urllib) negotiate TLS 1.2+, so this breaks nothing. (security-reviewer 2026-07-23.)
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  vpc_options {
    subnet_ids         = [aws_subnet.private[0].id] # single data node -> one subnet
    security_group_ids = [aws_security_group.opensearch_sg.id]
  }

  # Resource-side IAM enforcement: only the ingestion task + vector-probe roles may call
  # the domain via the resource policy. A VPC network path alone is not sufficient.
  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = [
          aws_iam_role.ingestion_task_role.arn,
          aws_iam_role.vector_probe_role.arn,
        ]
      }
      Action   = "es:ESHttp*"
      Resource = local.opensearch_domain_arn
    }]
  })
}
