# cognito.tf — Cognito authentication proxy for OpenSearch Dashboards.
#
# Closes security finding Issue 39 ("AWS Elastic Search authentication for accessing
# Kibana disabled", High, scorecard-impacting) on the graphrag-vectors domain. The
# governing control is Accenture NCS 410, "Use Cognito as an authentication proxy for
# accessing Kibana", which keys specifically on the domain's CognitoOptions — fine-grained
# access control alone does not satisfy it, even though FGAC is the stronger control.
#
# WHAT THIS DOES NOT CHANGE. All four workload callers (ingestion, vector-probe, query,
# mcp-lambda) reach the domain with SigV4 IAM auth and are unaffected. Cognito gates only
# the human Dashboards path. FGAC continues to authorize every request after
# authentication — the two stack, they are not alternatives.
#
# TRADE-OFF ACCEPTED. graphrag-vectors is VPC-only with no public endpoint. A Cognito user
# pool domain is internet-facing, so this adds a public authentication surface to a domain
# that currently has none. That was weighed against filing a recurring risk exception and
# the control was chosen deliberately: an implemented control does not expire, and a human
# Dashboards path is wanted for index debugging regardless.
#
# REACHING DASHBOARDS still requires network access to the VPC (VPN or bastion) in the same
# browser session that authenticates against the public Cognito hosted UI. Cognito supplies
# identity, not connectivity.

# ── User pool ────────────────────────────────────────────────────────────────
#
# Password and MFA policy are set here rather than left at provider defaults because this
# pool fronts a cluster-admin surface. `advanced_security_mode = "ENFORCED"` turns on
# compromised-credential detection and adaptive authentication.

resource "aws_cognito_user_pool" "opensearch_dashboards" {
  name = "graphrag-opensearch-dashboards"

  # Operators are invited by an administrator; there is no self-service signup path to a
  # search cluster's admin UI.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 3
  }

  mfa_configuration = "ON"

  software_token_mfa_configuration {
    enabled = true
  }

  user_pool_add_ons {
    advanced_security_mode = "ENFORCED"
  }

  # Account recovery by email only — no SMS, which is both weaker and an extra cost and
  # deliverability dependency.
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

# The hosted-UI domain prefix is globally unique across all AWS accounts, so it carries the
# account id. This is the internet-facing surface noted in the header.
resource "aws_cognito_user_pool_domain" "opensearch_dashboards" {
  domain       = "graphrag-opensearch-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.opensearch_dashboards.id
}

# ── Identity pool ────────────────────────────────────────────────────────────
#
# OpenSearch requires BOTH a user pool (authentication) and an identity pool (credential
# vending). The identity pool exchanges a user-pool token for temporary AWS credentials
# that carry the authenticated role below.
#
# allow_unauthenticated_identities is false: an unauthenticated identity here would be
# precisely the finding this file exists to close.

resource "aws_cognito_identity_pool" "opensearch_dashboards" {
  identity_pool_name               = "graphrag_opensearch_dashboards"
  allow_unauthenticated_identities = false
  allow_classic_flow               = false
}

# ── Roles ────────────────────────────────────────────────────────────────────
#
# Two distinct roles, easily confused:
#
#   cognito_opensearch_access  — assumed by the OpenSearch SERVICE so it can configure and
#                                read the Cognito pools. Needs the AWS-managed policy.
#   cognito_authenticated      — assumed by a HUMAN after login. This is the identity FGAC
#                                maps to a backend role.

resource "aws_iam_role" "cognito_opensearch_access" {
  name = "graphrag-cognito-opensearch-access"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "es.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        # local.opensearch_domain_arn carries a trailing "/*" for data-plane paths; the
        # SourceArn the service presents is the bare domain ARN, so trim it.
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "aws:SourceArn" = trimsuffix(local.opensearch_domain_arn, "/*") }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cognito_opensearch_access" {
  role       = aws_iam_role.cognito_opensearch_access.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonOpenSearchServiceCognitoAccess"
}

# Trust is scoped to this identity pool AND to authenticated principals only. Without the
# amr condition, any identity from the pool — including an unauthenticated one, were that
# ever enabled — could assume this role.
resource "aws_iam_role" "cognito_authenticated" {
  name = "graphrag-cognito-authenticated"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "cognito-identity.amazonaws.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "cognito-identity.amazonaws.com:aud" = aws_cognito_identity_pool.opensearch_dashboards.id
        }
        "ForAnyValue:StringLike" = {
          "cognito-identity.amazonaws.com:amr" = "authenticated"
        }
      }
    }]
  })
}

# es:ESHttp* on the domain is the coarse grant that gets the user through the resource
# policy; FGAC then decides what they can actually read or write. Authorization lives in
# the OpenSearch security plugin, not here — see the role-mapping note in opensearch.tf.
resource "aws_iam_role_policy" "cognito_authenticated_dashboards" {
  name = "dashboards-access"
  role = aws_iam_role.cognito_authenticated.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "es:ESHttp*"
      Resource = local.opensearch_domain_arn # already suffixed "/*"
    }]
  })
}

resource "aws_cognito_identity_pool_roles_attachment" "opensearch_dashboards" {
  identity_pool_id = aws_cognito_identity_pool.opensearch_dashboards.id

  roles = {
    authenticated = aws_iam_role.cognito_authenticated.arn
  }
}
