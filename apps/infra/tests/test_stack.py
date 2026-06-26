"""T11 — IaC synth assertions for the slice-1 topology + security posture (AC8).

Synthesizes the stack in-process (no AWS account, no `cdk` CLI) and asserts the
ADR-0002 topology and the security controls. Skipped where aws-cdk-lib is absent.

# STUB: AC8
"""

from __future__ import annotations

import json
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


def test_has_the_required_vpc_endpoints(template: Template) -> None:
    # 1 gateway (S3) + 5 interface (ecr.api, ecr.dkr, logs, sts, bedrock-runtime).
    # bedrock-runtime arrived with slice 2 (Titan v2 embeddings, no NAT).
    template.resource_count_is("AWS::EC2::VPCEndpoint", 6)


def test_bedrock_runtime_endpoint_present(template: Template) -> None:
    # ServiceName is an Fn::Join intrinsic (com.amazonaws.<region>.bedrock-runtime),
    # so match against the serialized form rather than a bare string.
    endpoints = [
        r
        for r in _resources(template).values()
        if r["Type"] == "AWS::EC2::VPCEndpoint"
        and "bedrock-runtime" in json.dumps(r["Properties"].get("ServiceName"))
    ]
    assert len(endpoints) == 1, "expected the bedrock-runtime interface endpoint"


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
    # The Neptune smoke Lambda must run in-VPC (private subnets) and expose no URL of
    # its own (disambiguated by its handler — the slice-3 query Lambda also reads
    # NEPTUNE_ENDPOINT).
    fns = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Function"]
    smoke = [
        f for f in fns if f["Properties"].get("Handler") == "graphrag.smoke_lambda.lambda_handler"
    ]
    assert len(smoke) == 1, "expected exactly one Neptune smoke Lambda"
    assert "VpcConfig" in smoke[0]["Properties"], "smoke Lambda must be VPC-attached"
    # LoggingConfig => points at the stack-managed log group, not /aws/lambda/<fn>.
    assert "LoggingConfig" in smoke[0]["Properties"], "smoke Lambda needs a stack-managed log group"
    # The only Function URL in the stack is the IAM-auth query URL (slice 3); the
    # smoke probes have none.
    template.resource_count_is("AWS::Lambda::Url", 1)


def test_log_groups_are_stack_managed_and_destroyed(template: Template) -> None:
    # Both explicit log groups (ingestion + smoke probe) must be torn down with the
    # stack, so destroy leaves nothing behind.
    groups = {k: r for k, r in _resources(template).items() if r["Type"] == "AWS::Logs::LogGroup"}
    assert len(groups) >= 2, "expected stack-managed log groups for ingestion + smoke probe"
    for k, r in groups.items():
        assert r.get("DeletionPolicy") == "Delete", f"log group {k} must be deleted on destroy"


def test_neptune_data_access_actions_present_and_scoped(template: Template) -> None:
    # connect alone can't read/write under IAM auth — the data actions must be granted.
    data_actions = {"neptune-db:ReadDataViaQuery", "neptune-db:WriteDataViaQuery"}
    for stmt in _iam_statements(template):
        if data_actions & set(_as_list(stmt["Action"])):
            assert _as_list(stmt["Resource"]) != ["*"], "neptune data actions must be scoped"
            return
    raise AssertionError("expected Neptune data-access actions (Read/WriteDataViaQuery)")


def test_ingestion_task_can_write_manifest_scoped_to_manifest_key(template: Template) -> None:
    # Slice 5: the delta task records the ingest manifest to S3 and reads it back. The slice-1
    # task role was read-only, so an s3:PutObject is required — but scoped to manifest.json only,
    # never the whole bucket (least privilege). This pins the live-deploy IAM fix AC9 surfaced.
    found = False
    for stmt in _iam_statements(template):
        actions = set(_as_list(stmt.get("Action", [])))
        if "s3:PutObject" in actions:
            resources = json.dumps(stmt["Resource"])
            assert "manifest.json" in resources, "s3:PutObject must be scoped to manifest.json"
            assert resources.strip('"') != "*", "s3:PutObject must not be wildcard"
            found = True
    assert found, "expected an s3:PutObject grant for the ingest manifest"


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


# --- slice 2: OpenSearch + Bedrock + vector probe ----------------------------------


def test_opensearch_domain_is_single_node_encrypted_and_vpc_private(template: Template) -> None:
    template.resource_count_is("AWS::OpenSearchService::Domain", 1)
    template.has_resource_properties(
        "AWS::OpenSearchService::Domain",
        {
            "ClusterConfig": Match.object_like(
                {"InstanceCount": 1, "ZoneAwarenessEnabled": False, "DedicatedMasterEnabled": False}
            ),
            "EncryptionAtRestOptions": {"Enabled": True},
            "NodeToNodeEncryptionOptions": {"Enabled": True},
            "DomainEndpointOptions": Match.object_like({"EnforceHTTPS": True}),
            "VPCOptions": Match.any_value(),  # VPC-resident -> no public endpoint
        },
    )


def test_opensearch_access_policy_is_scoped_not_all_principals(template: Template) -> None:
    # For a VPC domain, CDK applies the access policy via an AwsCustomResource
    # (updateDomainConfig), not the inline AccessPolicies property — so assert against
    # the serialized template. The policy must name the specific role ARNs and the
    # scoped domain resource, never AllPrincipals or a wildcard resource.
    blob = json.dumps(template.to_json())
    assert "AccessPolicies" in blob
    assert "es:ESHttp*" in blob
    assert "domain/graphrag-vectors/*" in blob  # scoped to the one domain, not "*"
    assert "IngestionTaskRole" in blob and "VectorProbeRole" in blob  # named principals
    # no AllPrincipals wildcard in the access policy (either escaping form)
    assert '"Principal":"*"' not in blob
    assert '\\"Principal\\":\\"*\\"' not in blob


def test_vector_actions_are_scoped_no_wildcard_resource(template: Template) -> None:
    saw_titan = saw_opensearch = False
    for stmt in _iam_statements(template):
        actions = _as_list(stmt["Action"])
        resources = _as_list(stmt["Resource"])
        if any(a == "bedrock:InvokeModel" for a in actions):
            # Every bedrock:InvokeModel grant is scoped (slice 3 adds a second, the
            # Claude synthesis grant — also scoped, asserted in its own test).
            assert resources != ["*"], "bedrock:InvokeModel must be scoped to the model ARN"
            # At least one grant is scoped to the one Titan model specifically — so the
            # query-embedding grant can't silently widen to another model.
            if "amazon.titan-embed-text-v2:0" in json.dumps(resources):
                saw_titan = True
        if any(a.startswith("es:ESHttp") for a in actions):
            assert resources != ["*"], "es:ESHttp* must be scoped to the domain ARN"
            saw_opensearch = True
    assert saw_titan, "expected a scoped bedrock:InvokeModel grant on the Titan v2 model ARN"
    assert saw_opensearch, "expected a scoped es:ESHttp* grant"


def test_vector_smoke_probe_is_in_vpc_with_no_public_url(template: Template) -> None:
    # Disambiguated by handler — the slice-3 query Lambda also reads OPENSEARCH_ENDPOINT.
    fns = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Function"]
    vector = [
        f
        for f in fns
        if f["Properties"].get("Handler") == "graphrag.vector_smoke_lambda.lambda_handler"
    ]
    assert len(vector) == 1, "expected exactly one OpenSearch vector smoke Lambda"
    assert "VpcConfig" in vector[0]["Properties"], "vector probe must be VPC-attached"
    assert "LoggingConfig" in vector[0]["Properties"], (
        "vector probe needs a stack-managed log group"
    )
    # The only Function URL is the IAM-auth query URL (slice 3); the probe has none.
    template.resource_count_is("AWS::Lambda::Url", 1)


def test_budget_limit_re_evaluated_for_two_standing_stores(template: Template) -> None:
    # Slice 2 adds standing OpenSearch + the bedrock-runtime endpoint on top of
    # Neptune; the monthly limit was raised so the "forgotten deploy" alarm stays
    # meaningful (threshold % is still asserted by the slice-1 test).
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {"Budget": Match.object_like({"BudgetLimit": {"Amount": 150, "Unit": "USD"}})},
    )


def test_opensearch_endpoint_is_exported(template: Template) -> None:
    outputs = set(template.find_outputs("*").keys())
    assert {"OpenSearchEndpoint", "VectorSmokeProbeName"} <= outputs


# --- slice 3: query Lambda + IAM-auth Function URL + scoped Bedrock-Claude grant ----

# STUB: AC8


def test_query_lambda_is_vpc_resident_not_public(template: Template) -> None:
    fns = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Function"]
    query = [
        f for f in fns if f["Properties"].get("Handler") == "graphrag.query_lambda.lambda_handler"
    ]
    assert len(query) == 1, "expected exactly one query Lambda"
    props = query[0]["Properties"]
    assert "VpcConfig" in props, "query Lambda must be VPC-attached (private isolated)"
    assert "LoggingConfig" in props, "query Lambda needs a stack-managed log group"
    env = props.get("Environment", {}).get("Variables", {})
    assert "NEPTUNE_ENDPOINT" in env
    assert "OPENSEARCH_ENDPOINT" in env
    assert "SYNTHESIS_MODEL_ID" in env


def test_function_url_is_iam_auth(template: Template) -> None:
    template.has_resource_properties("AWS::Lambda::Url", {"AuthType": "AWS_IAM"})
    urls = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Url"]
    assert len(urls) == 1, "expected exactly one Function URL (IAM-auth)"
    for u in urls:
        assert u["Properties"]["AuthType"] == "AWS_IAM", "Function URL must be AWS_IAM, never NONE"


def test_function_url_invoke_permission_scoped_to_named_principal(template: Template) -> None:
    perms = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Permission"]
    url_perms = [p for p in perms if p["Properties"].get("FunctionUrlAuthType") == "AWS_IAM"]
    assert url_perms, "expected an invoke-url permission with the AWS_IAM auth-type condition"
    for p in url_perms:
        principal = p["Properties"].get("Principal")
        # never account-root / wildcard — a named principal (the InvokerRoleArn param).
        assert principal not in ("*", None), f"invoke principal must be named, got {principal}"
        assert p["Properties"].get("Action") == "lambda:InvokeFunctionUrl"


def test_query_lambda_sg_reaches_neptune_and_opensearch(template: Template) -> None:
    ports = set()
    # Match the exact query-Lambda ingress-rule descriptions (not a loose "query"
    # substring that an unrelated future rule could collide with).
    query_descs = {"query lambda to neptune 8182", "query lambda to opensearch 443"}
    for res in _resources(template).values():
        if res["Type"] == "AWS::EC2::SecurityGroupIngress":
            desc = res["Properties"].get("Description", "")
            if isinstance(desc, str) and desc.lower() in query_descs:
                ports.add(res["Properties"].get("FromPort"))
    assert 8182 in ports, "query Lambda SG must reach Neptune 8182"
    assert 443 in ports, "query Lambda SG must reach OpenSearch 443"


def test_query_lambda_sg_allows_outbound(template: Template) -> None:
    # Regression guard (live-deploy finding): the query Lambda is in-VPC COMPUTE that
    # must initiate outbound to Neptune + OpenSearch + the Bedrock VPC endpoint. A
    # closed SG (allow_all_outbound=False) silently blocks the first Bedrock call and
    # hangs the function to its 120s timeout. With no NAT, allow-all egress can only
    # reach VPC endpoints + in-VPC stores — there is no internet path.
    query_sgs = [
        r
        for r in _resources(template).values()
        if r["Type"] == "AWS::EC2::SecurityGroup"
        and "query lambda" in str(r["Properties"].get("GroupDescription", "")).lower()
    ]
    assert len(query_sgs) == 1, "expected exactly one query-Lambda SG"
    egress = query_sgs[0]["Properties"].get("SecurityGroupEgress", [])
    # allow_all_outbound=True renders as a single 0.0.0.0/0 / protocol -1 allow rule;
    # the closed shape renders as a 255.255.255.255/32 disallow sentinel.
    assert any(
        e.get("CidrIp") == "0.0.0.0/0" and str(e.get("IpProtocol")) == "-1" for e in egress
    ), f"query Lambda SG must allow outbound (egress={egress})"


def test_bedrock_claude_grant_scopes_profile_and_foundation_no_wildcard(
    template: Template,
) -> None:
    # The synthesis Claude model is a cross-region inference profile; the grant must
    # scope BOTH the inference-profile ARN AND each underlying regional foundation-model
    # ARN — never a wildcard resource, never bedrock:* on "*".
    saw_profile = saw_foundation = False
    for stmt in _iam_statements(template):
        actions = _as_list(stmt["Action"])
        if not any(a.startswith("bedrock:") for a in actions):
            continue
        resources_blob = json.dumps(_as_list(stmt["Resource"]))
        assert "*" not in _as_list(stmt["Resource"]), "bedrock grant must not be wildcard resource"
        if "inference-profile/us.anthropic.claude-sonnet-4-6" in resources_blob:
            saw_profile = True
        if "foundation-model/anthropic.claude-sonnet-4-6" in resources_blob:
            saw_foundation = True
    assert saw_profile, "expected the inference-profile ARN in a scoped bedrock grant"
    assert saw_foundation, "expected the underlying foundation-model ARN in a scoped bedrock grant"
    # bedrock:Converse must be among the granted actions (the synthesizer uses Converse).
    converse = any("bedrock:Converse" in _as_list(s["Action"]) for s in _iam_statements(template))
    assert converse, "expected bedrock:Converse in the synthesis grant"


def test_query_function_url_is_exported(template: Template) -> None:
    outputs = set(template.find_outputs("*").keys())
    assert "QueryFunctionUrl" in outputs


def test_budget_limit_unchanged_at_150(template: Template) -> None:
    # The query Lambda is scale-to-zero — no new standing cost; the limit holds at 150.
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {"Budget": Match.object_like({"BudgetLimit": {"Amount": 150, "Unit": "USD"}})},
    )


def test_cdk_synthesis_model_id_equals_library_default() -> None:
    # The CDK env default and the runtime default must not drift — the grant scope is
    # derived from the CDK constant; the runtime synthesizer defaults to the library one.
    from stacks.graphrag_stack import _SYNTHESIS_MODEL_ID

    from graphrag.synthesize import DEFAULT_SYNTHESIS_MODEL_ID

    assert _SYNTHESIS_MODEL_ID == DEFAULT_SYNTHESIS_MODEL_ID


def _resources(template: Template) -> dict:
    return template.to_json()["Resources"]


# --- text2opencypher-guarded: read-only query-Lambda Neptune grant + read-cost backstop (AC9) ---
def _neptune_actions_by_role(template: Template, role_prefix: str) -> set[str]:
    """Neptune data actions on IAM policies attached to a role whose logical id starts with
    ``role_prefix`` (CDK appends a hash, so match by prefix). Lets AC9 assert per-role grants
    rather than a cluster-wide property — two peer roles retain write by design."""
    actions: set[str] = set()
    for res in _resources(template).values():
        if res["Type"] != "AWS::IAM::Policy":
            continue
        refs = [r["Ref"] for r in res["Properties"].get("Roles", []) if isinstance(r, dict)]
        if not any(ref.startswith(role_prefix) for ref in refs):
            continue
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            actions.update(a for a in _as_list(stmt["Action"]) if a.startswith("neptune-db:"))
    return actions


def test_query_lambda_neptune_grant_is_read_only(template: Template) -> None:
    # The load-bearing ADR-0004 backstop: the query Lambda (the only role running LLM-authored
    # text2cypher openCypher) can read but physically cannot write.
    actions = _neptune_actions_by_role(template, "QueryRole")
    assert "neptune-db:ReadDataViaQuery" in actions
    assert "neptune-db:connect" in actions
    assert "neptune-db:WriteDataViaQuery" not in actions
    assert "neptune-db:DeleteDataViaQuery" not in actions


def test_ingestion_and_smoke_roles_retain_read_write(template: Template) -> None:
    # The two roles that legitimately write keep the full grant (the narrowing is query-only).
    for prefix in ("IngestionTaskRole", "SmokeProbeServiceRole"):
        actions = _neptune_actions_by_role(template, prefix)
        assert "neptune-db:WriteDataViaQuery" in actions, f"{prefix} must retain write"
        assert "neptune-db:DeleteDataViaQuery" in actions, f"{prefix} must retain delete"


def test_no_other_role_holds_neptune_write(template: Template) -> None:
    # No role beyond the two known write-holders was widened (ADR-0004 Confirmation).
    for res in _resources(template).values():
        if res["Type"] != "AWS::IAM::Policy":
            continue
        stmts = res["Properties"]["PolicyDocument"]["Statement"]
        if not any("neptune-db:WriteDataViaQuery" in _as_list(s["Action"]) for s in stmts):
            continue
        refs = [r["Ref"] for r in res["Properties"].get("Roles", []) if isinstance(r, dict)]
        assert all(
            ref.startswith("IngestionTaskRole") or ref.startswith("SmokeProbeServiceRole")
            for ref in refs
        ), f"unexpected role holds neptune write: {refs}"


def test_neptune_query_timeout_backstop_is_set(template: Template) -> None:
    # The engine read-cost backstop (ADR-0004): a runaway model-authored read is killed by the
    # engine even if the validator's unbounded-path guard is bypassed.
    template.has_resource_properties(
        "AWS::Neptune::DBClusterParameterGroup",
        {"Parameters": Match.object_like({"neptune_query_timeout": Match.any_value()})},
    )


def test_text2cypher_adds_no_new_billable_resource_budget_held(template: Template) -> None:
    # The text2cypher path rides the existing query Lambda via the additive `mode` value; the
    # only infra change is the query-grant narrowing + the (free, config-only) parameter group.
    fns = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Function"]
    product = [f for f in fns if str(f["Properties"].get("Handler", "")).startswith("graphrag.")]
    assert len(product) == 3  # smoke + vector-smoke + query — no new text2cypher function
    template.resource_count_is("AWS::Lambda::Url", 1)  # still only the IAM-auth query URL
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {"Budget": Match.object_like({"BudgetLimit": {"Amount": 150, "Unit": "USD"}})},
    )


def _iam_statements(template: Template) -> list[dict]:
    out: list[dict] = []
    for res in _resources(template).values():
        if res["Type"] == "AWS::IAM::Policy":
            out.extend(res["Properties"]["PolicyDocument"]["Statement"])
    return out


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value]


# --- slice 4: permission-filtered-retrieval adds NO new infra resource ----------------
def test_slice4_permission_filter_adds_no_new_infra(template: Template) -> None:
    # The persona rides the existing query Lambda's request body and the only store change
    # is the OpenSearch index mapping (app code, applied at create_index on a fresh index).
    # So slice 4 provisions nothing new: the Lambda set, the single IAM-auth Function URL,
    # the OpenSearch domain, and the Budgets value are all unchanged from slice 3.
    fns = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Function"]
    product = [f for f in fns if str(f["Properties"].get("Handler", "")).startswith("graphrag.")]
    # smoke + vector-smoke + query Lambda — slice 4 adds no new product function.
    assert len(product) == 3
    template.resource_count_is("AWS::Lambda::Url", 1)  # still only the IAM-auth query URL
    template.resource_count_is("AWS::OpenSearchService::Domain", 1)
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {"Budget": Match.object_like({"BudgetLimit": {"Amount": 150, "Unit": "USD"}})},
    )


# --- opencypher-templates: governed Cypher-Templates path adds NO new infra (AC8) ------
def test_governed_templates_adds_no_new_infra(template: Template) -> None:
    # The governed path rides the existing query Lambda via an additive `mode` field and
    # reuses the already-granted Neptune data-access + synthesis-model Converse action
    # (selection uses the same model). So it provisions nothing new: the product Lambda set,
    # the single IAM-auth Function URL, the OpenSearch domain, and the Budgets value all
    # hold from slices 3-4.
    fns = [r for r in _resources(template).values() if r["Type"] == "AWS::Lambda::Function"]
    product = [f for f in fns if str(f["Properties"].get("Handler", "")).startswith("graphrag.")]
    assert len(product) == 3  # smoke + vector-smoke + query — no new governed function
    template.resource_count_is("AWS::Lambda::Url", 1)  # still only the IAM-auth query URL
    template.resource_count_is("AWS::OpenSearchService::Domain", 1)
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {"Budget": Match.object_like({"BudgetLimit": {"Amount": 150, "Unit": "USD"}})},
    )
    # the bedrock grant is unchanged: Converse on the synthesis model, no wildcard resource
    # (selection reuses it). Re-assert the no-wildcard invariant across every bedrock grant.
    saw_converse = False
    for stmt in _iam_statements(template):
        actions = _as_list(stmt["Action"])
        if not any(a.startswith("bedrock:") for a in actions):
            continue
        assert "*" not in _as_list(stmt["Resource"]), "bedrock grant must not be wildcard resource"
        if "bedrock:Converse" in actions:
            saw_converse = True
    assert saw_converse, "governed selection reuses the existing bedrock:Converse grant"
