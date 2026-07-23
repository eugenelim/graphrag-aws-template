# network.tf — VPC, private-isolated subnets, route tables, and VPC endpoints.
#
# Translated from apps/infra/stacks/graphrag_stack.py `_vpc()` (ADR-0002:
# no NAT, no internet gateway, PRIVATE_ISOLATED subnets, all egress via VPC
# endpoints). Security groups and their egress/ingress rules live in
# security_groups.tf; the interface-endpoint SGs live here, adjacent to the
# endpoints they guard.

# --- AZ selection --------------------------------------------------------------
# CDK uses max_azs=2 (a Neptune DB subnet group requires >=2 AZs — an API
# requirement, not an HA choice). Terraform must select the AZs explicitly.
data "aws_availability_zones" "available" {
  state = "available"
}

# --- VPC + private-isolated subnets --------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "graphrag-vpc" }
}

# Two /24 subnets across two AZs (CDK cidr_mask=24 allocation: 10.0.0.0/24,
# 10.0.1.0/24). map_public_ip_on_launch=false — these are private-isolated.
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  map_public_ip_on_launch = false

  tags = { Name = "graphrag-private-${count.index}" }
}

# One route table per private subnet (mirrors CDK PRIVATE_ISOLATED). Local
# routes only — no IGW/NAT route ever. The S3 gateway endpoint associates these
# so the AWS-managed S3 prefix-list route is installed (the no-NAT corpus read).
resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.main.id

  tags = { Name = "graphrag-private-rt-${count.index}" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# --- VPC endpoints -------------------------------------------------------------
# The exact endpoint set the in-VPC compute needs with no NAT (ADR-0002):
# 1 gateway (S3) + 5 interface. Keys match the CDK _INTERFACE_ENDPOINTS dict and
# the _COMPUTE_SG_EGRESS egress-target vocabulary so the mapping is legible.
locals {
  # key (CDK construct name / egress-target label) => AWS service short name
  interface_endpoints = {
    EcrApi         = "ecr.api"
    EcrDocker      = "ecr.dkr"
    CloudWatchLogs = "logs"
    Sts            = "sts"
    BedrockRuntime = "bedrock-runtime"
  }
}

# The AWS-managed S3 gateway-endpoint prefix list for the region. Resolved from
# the account at plan time (name com.amazonaws.<region>.s3), NOT supplied by an
# operator — this closes the SEC-2 footgun where a format-valid but wrong/wide
# customer-managed prefix list could widen the one egress hole closed egress
# exists to control. It is the declarative equivalent of the CDK deploy.sh
# `describe-managed-prefix-lists` resolution (egress-equivalent, strictly safer).
data "aws_ec2_managed_prefix_list" "s3" {
  name = "com.amazonaws.${var.aws_region}.s3"
}

# S3 gateway endpoint — route-table associated so the corpus read routes with no
# NAT (bedrock-runtime and the container pull ride the interface endpoints below).
resource "aws_vpc_endpoint" "s3_gateway" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = { Name = "graphrag-s3-endpoint" }
}

# One dedicated SG per interface endpoint, accepting 443 from the VPC CIDR.
# egress=[] — endpoint SGs initiate nothing (only the 4 compute SGs own egress).
# This mirrors CDK's open=True interface-endpoint default; the effective outbound
# gate is the compute SGs' closed egress (see security_groups.tf).
resource "aws_security_group" "endpoint" {
  for_each = local.interface_endpoints

  name_prefix = "graphrag-endpoint-${each.key}-"
  description = "VPC endpoint ${each.key} - accepts 443 from VPC"
  vpc_id      = aws_vpc.main.id
  egress      = []

  tags = { Name = "graphrag-endpoint-${each.key}" }
}

resource "aws_vpc_security_group_ingress_rule" "endpoint_https" {
  for_each = local.interface_endpoints

  security_group_id = aws_security_group.endpoint[each.key].id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = aws_vpc.main.cidr_block
  description       = "${each.key} endpoint accepts 443 from vpc"
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoint[each.key].id]
  private_dns_enabled = true

  tags = { Name = "graphrag-endpoint-${each.key}" }
}
