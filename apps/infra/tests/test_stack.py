"""T11 — IaC synth assertions for the slice-1 topology + security posture (AC8).

Synthesizes the stack in-process (no AWS account, no `cdk` CLI) and asserts the
ADR-0002 topology and the security controls. Skipped where aws-cdk-lib is absent.

# STUB: AC8
"""

from __future__ import annotations

import os
import re

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
        if any(a.startswith("neptune-db:") for a in actions) or any(
            a.startswith("s3:Get") for a in actions
        ):
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


# EC2 restricts SG group AND ingress-rule descriptions to this charset (the rule
# rejected both a non-ASCII em-dash and an ASCII '>'); `cdk synth` doesn't validate
# it, only a live deploy does — so guard the whole class here.
_EC2_DESC = re.compile(r"^[A-Za-z0-9 ._\-:/()#,@\[\]+=&;{}!$*]*$")


def test_security_group_descriptions_use_ec2_charset(template: Template) -> None:
    def _ok(desc: object, where: str) -> None:
        # Only literal strings are ours to validate; CDK auto-generates some rule
        # descriptions as Fn::Join intrinsics (dicts) that are valid at deploy.
        if isinstance(desc, str):
            assert _EC2_DESC.match(desc), f"invalid EC2 description in {where}: {desc!r}"

    for res in _resources(template).values():
        if res["Type"] == "AWS::EC2::SecurityGroup":
            _ok(res["Properties"].get("GroupDescription", ""), "SecurityGroup.GroupDescription")
            for rule in res["Properties"].get("SecurityGroupIngress", []):
                if "Description" in rule:
                    _ok(rule["Description"], "inline SecurityGroupIngress.Description")
        if res["Type"] == "AWS::EC2::SecurityGroupIngress" and "Description" in res["Properties"]:
            _ok(res["Properties"]["Description"], "SecurityGroupIngress.Description")


_GOVERNANCE_TAG_KEYS = {"Environment", "Project", "Department", "Application", "User"}


def test_governance_tags_on_taggable_resources(template: Template) -> None:
    # All five org tags must propagate to every taggable resource; check a
    # representative spread of resource types.
    for rtype in (
        "AWS::EC2::VPC",
        "AWS::S3::Bucket",
        "AWS::Neptune::DBCluster",
        "AWS::ECS::TaskDefinition",
        "AWS::ECR::Repository",
    ):
        resources = [r for r in _resources(template).values() if r["Type"] == rtype]
        assert resources, f"expected at least one {rtype}"
        for res in resources:
            tags = res["Properties"].get("Tags", [])
            keys = {t["Key"] for t in tags}
            missing = _GOVERNANCE_TAG_KEYS - keys
            assert not missing, f"{rtype} missing governance tags: {sorted(missing)}"


def test_smoke_probe_is_in_vpc_with_no_public_url(template: Template) -> None:
    # The smoke Lambda must run in-VPC (private subnets) and expose no public URL.
    fns = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Function"]
    smoke = [
        f
        for f in fns
        if "NEPTUNE_ENDPOINT" in f["Properties"].get("Environment", {}).get("Variables", {})
    ]
    assert len(smoke) == 1, "expected exactly one Neptune smoke Lambda"
    assert "VpcConfig" in smoke[0]["Properties"], "smoke Lambda must be VPC-attached"
    template.resource_count_is("AWS::Lambda::Url", 0)  # no public function URL


def test_neptune_data_access_actions_present_and_scoped(template: Template) -> None:
    # connect alone can't read/write under IAM auth — the data actions must be granted.
    data_actions = {"neptune-db:ReadDataViaQuery", "neptune-db:WriteDataViaQuery"}
    for stmt in _iam_statements(template):
        if data_actions & set(_as_list(stmt["Action"])):
            assert _as_list(stmt["Resource"]) != ["*"], "neptune data actions must be scoped"
            return
    raise AssertionError("expected Neptune data-access actions (Read/WriteDataViaQuery)")


def test_run_task_handles_are_exported_as_outputs(template: Template) -> None:
    # The smoke (live ingest + retrieve) needs these handles; export them so an
    # operator doesn't have to hunt the console.
    outputs = set(template.find_outputs("*").keys())
    assert {
        "NeptuneEndpoint",
        "CorpusBucketName",
        "EcsClusterName",
        "IngestionTaskDefArn",
        "IngestionSecurityGroupId",
        "PrivateSubnetId",
        "IngestionRepoUri",
    } <= outputs


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
