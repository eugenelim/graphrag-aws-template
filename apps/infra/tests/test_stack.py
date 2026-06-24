"""T11 — IaC synth assertions for the slice-1 topology + security posture (AC8).

Synthesizes the stack in-process (no AWS account, no `cdk` CLI) and asserts the
ADR-0002 topology and the security controls. Skipped where aws-cdk-lib is absent.

# STUB: AC8
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")

cdk = pytest.importorskip("aws_cdk", reason="aws-cdk-lib not installed (infra extra)")
from aws_cdk.assertions import Match, Template  # noqa: E402
from stacks.graphrag_stack import GraphragStack  # noqa: E402


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = GraphragStack(app, "TestStack")
    return Template.from_stack(stack)


def test_vpc_has_no_nat_gateway(template: Template) -> None:
    template.resource_count_is("AWS::EC2::VPC", 1)
    template.resource_count_is("AWS::EC2::NatGateway", 0)  # no NAT — egress via endpoints


def test_has_the_five_required_vpc_endpoints(template: Template) -> None:
    # 1 gateway (S3) + 4 interface (ecr.api, ecr.dkr, logs, sts). bedrock-runtime
    # is deliberately NOT here (slice 2).
    template.resource_count_is("AWS::EC2::VPCEndpoint", 5)


def test_neptune_serverless_vpc_resident(template: Template) -> None:
    template.resource_count_is("AWS::Neptune::DBCluster", 1)
    template.has_resource_properties(
        "AWS::Neptune::DBCluster",
        {
            "ServerlessScalingConfiguration": {"MinCapacity": 1, "MaxCapacity": 2.5},
            "DBSubnetGroupName": Match.any_value(),  # placed in the private subnet group
            "StorageEncrypted": True,
            "IamAuthEnabled": True,
        },
    )
    template.resource_count_is("AWS::Neptune::DBSubnetGroup", 1)
    # Neptune requires a subnet group spanning >=2 AZs, or `cdk deploy` fails — a
    # deploy-time rule synth alone won't catch. The load-bearing assertion is that
    # the group references >=2 subnets; with one subnet-config per AZ that means
    # >=2 AZs. (The subnet-count line is a faithful proxy: one subnet per AZ here.)
    subnets = [r for r in _resources(template).values() if r["Type"] == "AWS::EC2::Subnet"]
    assert len(subnets) >= 2, "expected one private subnet per AZ (>=2)"
    group = next(
        r for r in _resources(template).values() if r["Type"] == "AWS::Neptune::DBSubnetGroup"
    )
    assert len(group["Properties"]["SubnetIds"]) >= 2


def test_corpus_bucket_is_private_and_encrypted(template: Template) -> None:
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": Match.any_value(),
        },
    )


def test_fargate_task_definition_present(template: Template) -> None:
    template.resource_count_is("AWS::ECS::TaskDefinition", 1)


def test_budget_alarm_has_threshold_and_subscriber(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": {"BudgetType": "COST"},
            "NotificationsWithSubscribers": Match.array_with(
                [
                    Match.object_like(
                        {
                            "Notification": {"Threshold": 80},
                            "Subscribers": Match.array_with(
                                [{"SubscriptionType": "EMAIL", "Address": Match.any_value()}]
                            ),
                        }
                    )
                ]
            ),
        },
    )


# ecr:GetAuthorizationToken is the one AWS action that legitimately requires
# Resource "*" (it grants nothing data-plane). Every other action must be scoped.
_WILDCARD_RESOURCE_ALLOWLIST = {"ecr:GetAuthorizationToken"}


def test_no_iam_statement_grants_app_actions_on_wildcard_resource(template: Template) -> None:
    # Catches a *newly added* wildcard grant, not just the known scoped ones —
    # any Resource "*" statement may only carry allowlisted actions.
    found_scoped = False
    for stmt in _iam_statements(template):
        actions = _as_list(stmt["Action"])
        resources = _as_list(stmt["Resource"])
        if "neptune-db:connect" in actions or any(a.startswith("s3:Get") for a in actions):
            assert resources != ["*"], f"least-privilege violated: {actions} on '*'"
            found_scoped = True
        if "*" in resources:
            assert set(actions) <= _WILDCARD_RESOURCE_ALLOWLIST, (
                f"unexpected wildcard-resource grant: {actions}"
            )
    assert found_scoped, "expected scoped neptune-db:connect / s3 read statements"


def test_no_security_group_allows_public_ingress(template: Template) -> None:
    public = {"0.0.0.0/0", "::/0"}

    def _not_public(value: object) -> None:
        # Intrinsic (dict/token) CIDRs are not literal public CIDRs; only flag strings.
        assert not (isinstance(value, str) and value in public), f"public ingress: {value}"

    for res in _resources(template).values():
        # Inline rules today carry intrinsic (Fn::GetAtt CidrBlock) CIDRs; this
        # branch guards against a future hardcoded literal "0.0.0.0/0" rule.
        if res["Type"] == "AWS::EC2::SecurityGroup":
            for rule in res["Properties"].get("SecurityGroupIngress", []):
                _not_public(rule.get("CidrIp"))
                _not_public(rule.get("CidrIpv6"))
        if res["Type"] == "AWS::EC2::SecurityGroupIngress":
            _not_public(res["Properties"].get("CidrIp"))
            _not_public(res["Properties"].get("CidrIpv6"))


def test_corpus_bucket_enforces_tls(template: Template) -> None:
    # enforce_ssl=True must synthesize a Deny-on-insecure-transport bucket policy.
    deny_insecure = False
    for res in _resources(template).values():
        if res["Type"] != "AWS::S3::BucketPolicy":
            continue
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            cond = stmt.get("Condition", {}).get("Bool", {})
            if stmt.get("Effect") == "Deny" and cond.get("aws:SecureTransport") in ("false", False):
                deny_insecure = True
    assert deny_insecure, "expected a Deny statement on aws:SecureTransport=false"


def _resources(template: Template) -> dict:
    return template.to_json()["Resources"]


def _iam_statements(template: Template) -> list[dict]:
    out: list[dict] = []
    for res in _resources(template).values():
        if res["Type"] == "AWS::IAM::Policy":
            out.extend(res["Properties"]["PolicyDocument"]["Statement"])
    return out


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value]
