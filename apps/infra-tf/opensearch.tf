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
#
# FOUR roles reach this domain, not the two named in access_policies. The other two ride
# the same-account allow-union via their identity policies, so they are invisible here:
#   graphrag-ingestion     (opensearch-data,   es:ESHttpGet/Put/Post/Delete/Head)
#   graphrag-vector-probe  (opensearch-data,   es:ESHttpGet/Put/Post/Delete/Head)
#   graphrag-query         (opensearch-data,   es:ESHttpGet/Put/Post/Delete/Head)
#   graphrag-mcp-lambda    (opensearch-search, es:ESHttpGet/Post/Head)
# Confirmed from both directions: the opensearch_sg ingress rules and an IAM policy scan.
# Under fine-grained access control ALL FOUR need an OpenSearch backend-role mapping —
# an identity-policy grant buys nothing once the security plugin is authorizing.

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

  # Fine-grained access control. Authentication for Dashboards/OpenSearch is enforced by
  # the security plugin rather than by the resource policy alone, and the master identity
  # is an IAM role (internal_user_database_enabled = false) so there is no username/password
  # master to rotate or leak.
  #
  # STATE NOTE: FGAC was enabled out-of-band on 2026-09-08 to close a security finding,
  # so this block documents live reality rather than proposing a change. It must stay —
  # removing it makes Terraform plan a disable, which AWS rejects outright.
  #
  # One-way door: AWS does not permit disabling FGAC. Enabling forces a blue/green.
  #
  # NOT MANAGED HERE — the AWS provider cannot express OpenSearch role mappings, and the
  # domain is VPC-only so Terraform cannot reach the security API from outside the VPC.
  # The mappings are applied by an in-VPC Lambda and must be re-applied after any domain
  # replacement, or all four callers above get 403:
  #   role graphrag_workload -> crud, create_index, indices_monitor
  #                             (ingestion, vector-probe, query)
  #   role graphrag_search   -> read, search
  #                             (mcp-lambda — read-only, mirroring its narrower IAM policy)
  #
  # var.opensearch_master_user_arn points at graphrag-opensearch-master, an IAM role
  # created out-of-band (it doubles as the mapping Lambda's execution role). Import it
  # before any apply that would otherwise recreate the dependency.
  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = false

    master_user_options {
      master_user_arn = var.opensearch_master_user_arn
    }
  }

  # Cognito authentication proxy for Dashboards (ADR-0020).
  # Defined in cognito.tf; see that file's header for the trade-off and for why FGAC
  # alone does not satisfy the control. This gates the HUMAN Dashboards path only —
  # the four SigV4 workload callers above are unaffected.
  cognito_options {
    enabled          = true
    user_pool_id     = aws_cognito_user_pool.opensearch_dashboards.id
    identity_pool_id = aws_cognito_identity_pool.opensearch_dashboards.id
    role_arn         = aws_iam_role.cognito_opensearch_access.arn
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
