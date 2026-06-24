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


def test_task_role_is_least_privilege_no_wildcard_resource(template: Template) -> None:
    # The app's permissions (neptune-db:connect, s3 read) must be scoped to the
    # specific cluster/bucket — never Resource "*". (The execution role's
    # ecr:GetAuthorizationToken legitimately needs "*" and is out of this check.)
    policies = [
        r["Properties"] for r in _resources(template).values() if r["Type"] == "AWS::IAM::Policy"
    ]
    scoped_actions = {"neptune-db:connect", "s3:GetObject", "s3:GetObject*", "s3:GetBucket*"}
    found_scoped = False
    for props in policies:
        for stmt in props["PolicyDocument"]["Statement"]:
            actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            if scoped_actions & set(actions):
                assert stmt["Resource"] != "*", f"least-privilege violated: {actions} on '*'"
                found_scoped = True
    assert found_scoped, "expected scoped neptune-db:connect / s3 read statements"


def _resources(template: Template) -> dict:
    return template.to_json()["Resources"]
