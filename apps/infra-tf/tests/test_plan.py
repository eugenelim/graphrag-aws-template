"""Terraform plan-assertion suite for the graphrag-aws-template infra-tf module.

Mirrors the CDK synth assertions in apps/infra/tests/test_stack.py.
Uses planned_values.root_module.resources (works for both fresh and applied-state plans).
The committed fixture (tests/fixtures/plan.json) is generated from applied state
so all computed attributes (Neptune ARN, S3 bucket name, role ARNs) are resolved.

CDK test → Terraform plan assertion mapping is documented in
docs/specs/infra-terraform-verification/plan.md.
"""

from __future__ import annotations

import json
import re

# ── helpers ───────────────────────────────────────────────────────────────────


def _pv_by_type(tfplan, rtype):
    """Resources of a given type from planned_values."""
    return [r for r in tfplan["planned_values"]["root_module"]["resources"] if r["type"] == rtype]


def _pv_by_address(tfplan, address):
    """Single resource by exact address; None if absent."""
    for r in tfplan["planned_values"]["root_module"]["resources"]:
        if r["address"] == address:
            return r
    return None


def _vals(resource: dict) -> dict:
    """Safe accessor for resource.values (absent when ALL attributes are computed)."""
    return resource.get("values") or {}


def _iam_inline_policies(tfplan):
    """Parsed inline IAM policy dicts from aws_iam_role_policy resources.
    Skips entries where policy is null (fresh plan with computed attributes)."""
    result = []
    for r in _pv_by_type(tfplan, "aws_iam_role_policy"):
        policy_str = _vals(r).get("policy")
        if policy_str:
            result.append(json.loads(policy_str))
    return result


def _all_iam_statements(tfplan):
    """Flat list of all statements from all inline IAM policies."""
    stmts = []
    for policy in _iam_inline_policies(tfplan):
        stmts.extend(policy.get("Statement", []))
    return stmts


def _as_list(v):
    return v if isinstance(v, list) else [v]


# ── topology tests (T2) ───────────────────────────────────────────────────────


def test_vpc_has_no_nat_gateway(tfplan):
    """CDK: test_vpc_has_no_nat_gateway"""
    assert len(_pv_by_type(tfplan, "aws_vpc")) == 1
    assert len(_pv_by_type(tfplan, "aws_nat_gateway")) == 0


def test_has_6_vpc_endpoints(tfplan):
    """CDK: test_has_the_required_vpc_endpoints"""
    assert len(_pv_by_type(tfplan, "aws_vpc_endpoint")) == 6


def test_bedrock_runtime_endpoint_present(tfplan):
    """CDK: test_bedrock_runtime_endpoint_present"""
    endpoints = _pv_by_type(tfplan, "aws_vpc_endpoint")
    bedrock = [e for e in endpoints if "bedrock-runtime" in e["values"].get("service_name", "")]
    assert len(bedrock) == 1, "expected exactly one bedrock-runtime VPC endpoint"


def test_neptune_serverless_vpc_resident(tfplan):
    """CDK: test_neptune_serverless_vpc_resident"""
    clusters = _pv_by_type(tfplan, "aws_neptune_cluster")
    assert len(clusters) == 1
    v = clusters[0]["values"]
    assert v.get("iam_database_authentication_enabled") is True
    assert v.get("storage_encrypted") is True
    scaling = v.get("serverless_v2_scaling_configuration", [{}])
    cfg = scaling[0] if scaling else {}
    assert cfg.get("min_capacity") == 1.0
    assert cfg.get("max_capacity") == 2.5
    assert v.get("engine_version") == "1.3.5.0"
    # Subnet group must reference ≥2 subnets (Neptune requires ≥2 AZs)
    groups = _pv_by_type(tfplan, "aws_neptune_subnet_group")
    assert len(groups) == 1
    # subnet_ids is null in fresh plans (computed from private subnets)
    subnet_ids = groups[0]["values"].get("subnet_ids") or []
    if subnet_ids:
        assert len(subnet_ids) >= 2


def test_neptune_query_timeout_backstop_is_set(tfplan):
    """ADR-0011 SPARQL read-cost backstop: neptune_query_timeout pinned to 20s."""
    param_groups = _pv_by_type(tfplan, "aws_neptune_cluster_parameter_group")
    assert len(param_groups) == 1
    params = param_groups[0]["values"].get("parameter", [])
    timeout_params = [p for p in params if p.get("name") == "neptune_query_timeout"]
    assert timeout_params, "expected neptune_query_timeout parameter"
    assert timeout_params[0]["value"] == "20000"


def test_corpus_bucket_is_private_and_encrypted(tfplan):
    """CDK: test_corpus_bucket_is_private_and_encrypted"""
    blocks = _pv_by_type(tfplan, "aws_s3_bucket_public_access_block")
    assert len(blocks) == 1
    v = blocks[0]["values"]
    assert v.get("block_public_acls") is True
    assert v.get("block_public_policy") is True
    assert v.get("ignore_public_acls") is True
    assert v.get("restrict_public_buckets") is True

    sse = _pv_by_type(tfplan, "aws_s3_bucket_server_side_encryption_configuration")
    assert len(sse) == 1
    rules = sse[0]["values"].get("rule", [])
    algos = [
        r["apply_server_side_encryption_by_default"][0]["sse_algorithm"]
        for r in rules
        if r.get("apply_server_side_encryption_by_default")
    ]
    assert "AES256" in algos


def test_corpus_bucket_enforces_tls(tfplan):
    """CDK: test_corpus_bucket_enforces_tls"""
    bucket_policies = _pv_by_type(tfplan, "aws_s3_bucket_policy")
    assert len(bucket_policies) == 1, "expected exactly one S3 bucket policy"
    # values key absent when all attrs computed (bucket ID + ARN both unknown in fresh plan)
    policy_str = _vals(bucket_policies[0]).get("policy")
    if policy_str is None:
        # Fresh plan: bucket ARN is computed; verify resource exists
        assert bucket_policies[0]["address"] == "aws_s3_bucket_policy.corpus_tls"
        return
    policy = json.loads(policy_str)
    deny_insecure = False
    for stmt in policy.get("Statement", []):
        cond = stmt.get("Condition", {}).get("Bool", {})
        if stmt.get("Effect") == "Deny" and cond.get("aws:SecureTransport") in ("false", False):
            deny_insecure = True
    assert deny_insecure, "expected a Deny statement on aws:SecureTransport=false"


def test_fargate_task_definition_present(tfplan):
    """CDK: test_fargate_task_definition_present"""
    assert len(_pv_by_type(tfplan, "aws_ecs_task_definition")) == 1


def test_log_groups_are_stack_managed_and_destroyed(tfplan):
    """CDK: test_log_groups_are_stack_managed_and_destroyed (4 groups: ingestion + 3 Lambdas)."""
    groups = _pv_by_type(tfplan, "aws_cloudwatch_log_group")
    assert len(groups) >= 4, f"expected ≥4 stack-managed log groups, found {len(groups)}"
    for g in groups:
        assert g["values"].get("retention_in_days") == 7, (
            f"{g['address']} must have retention_in_days=7"
        )
        # skip_destroy defaults to false; stack delete removes the group + events.
        assert not g["values"].get("skip_destroy", False), (
            f"{g['address']} must not have skip_destroy=true"
        )


def test_ecr_repository_force_delete(tfplan):
    """ECR force_delete ensures destroy removes images (teardown-first, ADR-0002)."""
    repos = _pv_by_type(tfplan, "aws_ecr_repository")
    assert len(repos) == 1
    assert repos[0]["values"].get("force_delete") is True


def test_governance_tags_applied_to_provider(tfplan):
    """CDK: test_governance_tags_on_taggable_resources — provider default_tags propagate."""
    _REQUIRED_TAG_KEYS = {"Environment", "Project", "Department", "Application", "User"}
    # Verify VPC, S3 bucket, Neptune cluster, ECS task def, ECR repo have all 5 keys.
    check_types = [
        ("aws_vpc", 1),
        ("aws_s3_bucket", 1),
        ("aws_neptune_cluster", 1),
        ("aws_ecs_task_definition", 1),
        ("aws_ecr_repository", 1),
    ]
    for rtype, expected_count in check_types:
        resources = _pv_by_type(tfplan, rtype)
        assert len(resources) == expected_count, f"expected {expected_count} {rtype}"
        for r in resources:
            # Tags arrive via provider default_tags; they appear in planned_values.
            tags = r["values"].get("tags_all") or r["values"].get("tags") or {}
            tag_keys = set(tags.keys())
            missing = _REQUIRED_TAG_KEYS - tag_keys
            assert not missing, f"{r['address']} missing governance tags: {sorted(missing)}"


def test_smoke_probe_is_in_vpc_with_no_public_url(tfplan):
    """CDK: test_smoke_probe_is_in_vpc_with_no_public_url"""
    fns = _pv_by_type(tfplan, "aws_lambda_function")
    smoke = [f for f in fns if f["values"].get("handler") == "graphrag.smoke_lambda.lambda_handler"]
    assert len(smoke) == 1, "expected exactly one Neptune smoke Lambda"
    assert smoke[0]["values"].get("vpc_config"), "smoke Lambda must be VPC-attached"
    # Exactly one Function URL in the stack (the IAM-auth query URL; smoke has none).
    assert len(_pv_by_type(tfplan, "aws_lambda_function_url")) == 1


# ── security and IAM invariant tests (T3) ──────────────────────────────────


def test_no_security_group_allows_public_ingress(tfplan):
    """CDK: test_no_security_group_allows_public_ingress"""
    public = {"0.0.0.0/0", "::/0"}
    for r in _pv_by_type(tfplan, "aws_vpc_security_group_ingress_rule"):
        cidr = r["values"].get("cidr_ipv4") or r["values"].get("cidr_ipv6")
        assert cidr not in public, f"{r['address']} allows public ingress: {cidr}"


_EC2_DESC = re.compile(r"^[A-Za-z0-9 ._\-:/()#,@\[\]+=&;{}!$*]*$")


def test_security_group_descriptions_use_ec2_charset(tfplan):
    """CDK: test_security_group_descriptions_use_ec2_charset"""
    for r in _pv_by_type(tfplan, "aws_security_group"):
        desc = r["values"].get("description", "")
        if isinstance(desc, str):
            assert _EC2_DESC.match(desc), f"{r['address']} invalid EC2 description: {desc!r}"


# The authoritative egress specification (matches _COMPUTE_SG_EGRESS in test_stack.py).
# Keys = aws_security_group resource name (not description); values = (target, port) sets.
_TF_COMPUTE_SG_EGRESS: dict[str, set[tuple[str, int]]] = {
    "ingestion_task_sg": {
        ("neptune_sg", 8182),
        ("opensearch_sg", 443),
        ("endpoint_BedrockRuntime", 443),
        ("endpoint_EcrApi", 443),
        ("endpoint_EcrDocker", 443),
        ("endpoint_CloudWatchLogs", 443),
        ("endpoint_Sts", 443),
        ("s3_prefix_list", 443),
    },
    "smoke_probe_sg": {
        ("neptune_sg", 8182),
        ("endpoint_CloudWatchLogs", 443),
        ("endpoint_Sts", 443),
    },
    "vector_smoke_sg": {
        ("opensearch_sg", 443),
        ("endpoint_BedrockRuntime", 443),
        ("endpoint_CloudWatchLogs", 443),
        ("endpoint_Sts", 443),
    },
    "query_lambda_sg": {
        ("neptune_sg", 8182),
        ("opensearch_sg", 443),
        ("endpoint_BedrockRuntime", 443),
        ("endpoint_CloudWatchLogs", 443),
        ("endpoint_Sts", 443),
    },
}

# Resource name suffix → target label (matches the egress rule Terraform resource names).
_EGRESS_TARGET_FROM_SUFFIX = {
    "to_neptune": "neptune_sg",
    "to_opensearch": "opensearch_sg",
    "to_bedrock": "endpoint_BedrockRuntime",
    "to_ecr_api": "endpoint_EcrApi",
    "to_ecr_docker": "endpoint_EcrDocker",
    "to_logs": "endpoint_CloudWatchLogs",
    "to_sts": "endpoint_Sts",
    "to_s3": "s3_prefix_list",
}

# SG resource name → owning compute SG group (from egress rule name prefix).
_EGRESS_SG_FROM_PREFIX = {
    "ingestion": "ingestion_task_sg",
    "smoke": "smoke_probe_sg",
    "vector_smoke": "vector_smoke_sg",
    "query": "query_lambda_sg",
}


def _classify_egress_rule(rule_name: str):
    """Return (sg_key, target_label, port) from an egress rule resource name, or None."""
    # rule_name e.g. "ingestion_to_neptune", "vector_smoke_to_bedrock", "query_to_sts"
    for prefix, sg_key in _EGRESS_SG_FROM_PREFIX.items():
        for suffix, target in _EGRESS_TARGET_FROM_SUFFIX.items():
            if rule_name == f"{prefix}_{suffix}":
                return sg_key, target
    return None


def test_compute_sgs_egress_equals_exact_call_set(tfplan):
    """CDK: test_compute_sgs_egress_equals_exact_call_set.
    Groups egress rules by resource name (security_group_id is computed in fresh plans).
    """
    actual: dict[str, set[tuple[str, int]]] = {k: set() for k in _TF_COMPUTE_SG_EGRESS}
    for r in _pv_by_type(tfplan, "aws_vpc_security_group_egress_rule"):
        result = _classify_egress_rule(r["name"])
        if result is None:
            continue
        sg_key, target_label = result
        port = r["values"].get("from_port")
        actual[sg_key].add((target_label, port))

    for sg_key, expected in _TF_COMPUTE_SG_EGRESS.items():
        assert actual[sg_key] == expected, (
            f"{sg_key} egress mismatch:\n  actual  = {sorted(actual[sg_key])}\n"
            f"  expected = {sorted(expected)}"
        )


def test_no_iam_statement_grants_app_actions_on_wildcard_resource(tfplan):
    """CDK: test_no_iam_statement_grants_app_actions_on_wildcard_resource"""
    _WILDCARD_RESOURCE_ALLOWLIST = {"ecr:GetAuthorizationToken"}
    found_scoped = False
    for stmt in _all_iam_statements(tfplan):
        actions = _as_list(stmt.get("Action", []))
        resources = _as_list(stmt.get("Resource", []))
        if any(a.startswith("neptune-db:") for a in actions) or any(
            a.startswith("s3:Get") for a in actions
        ):
            assert resources != ["*"], f"least-privilege violated: {actions} on '*'"
            found_scoped = True
        if "*" in resources:
            assert set(actions) <= _WILDCARD_RESOURCE_ALLOWLIST, (
                f"unexpected wildcard-resource grant: {actions}"
            )
    # Only assert found_scoped when Neptune or S3 statements are actually visible.
    # On a fresh plan, Neptune and S3 policies are null (ARNs computed); Bedrock/OpenSearch
    # policies ARE readable. Guard on statement-level presence rather than any-policy existence.
    has_neptune_or_s3_stmts = any(
        any(
            a.startswith("neptune-db:") or a.startswith("s3:")
            for a in _as_list(stmt.get("Action", []))
        )
        for stmt in _all_iam_statements(tfplan)
    )
    if has_neptune_or_s3_stmts:
        assert found_scoped, "expected scoped neptune-db:connect / s3 read statements"


def test_neptune_data_access_actions_present_and_scoped(tfplan):
    """CDK: test_neptune_data_access_actions_present_and_scoped"""
    data_actions = {"neptune-db:ReadDataViaQuery", "neptune-db:WriteDataViaQuery"}
    for stmt in _all_iam_statements(tfplan):
        if data_actions & set(_as_list(stmt.get("Action", []))):
            assert _as_list(stmt.get("Resource", ["*"])) != ["*"], (
                "neptune data actions must be scoped"
            )
            return
    # Neptune policies are null when cluster_resource_id is unresolved (fresh plan).
    # Guard specifically on Neptune policy readability — Bedrock/OpenSearch may be visible.
    neptune_role_policies = [
        r for r in _pv_by_type(tfplan, "aws_iam_role_policy") if "neptune" in r["name"]
    ]
    neptune_policies_readable = any(_vals(r).get("policy") for r in neptune_role_policies)
    if not neptune_policies_readable:
        rw_policy = _pv_by_address(tfplan, "aws_iam_role_policy.ingestion_neptune_rw")
        assert rw_policy is not None, "aws_iam_role_policy.ingestion_neptune_rw must exist"
        return
    raise AssertionError("expected Neptune data-access actions (Read/WriteDataViaQuery)")


def test_query_role_neptune_grant_is_read_only(tfplan):
    """CDK: test_query_lambda_neptune_grant_is_read_only (ADR-0011 backstop)."""
    # Find all aws_iam_role_policy resources attached to query_role
    query_policies = [
        r
        for r in _pv_by_type(tfplan, "aws_iam_role_policy")
        if r["name"].startswith("query_neptune")
    ]
    assert len(query_policies) == 1, (
        f"expected exactly 1 neptune policy on query role, found {len(query_policies)}"
    )
    policy_str = query_policies[0]["values"].get("policy")
    if policy_str is None:
        # Fresh plan: neptune ARN is computed. Assert by resource name (proxy).
        assert query_policies[0]["values"].get("name") == "neptune-data-readonly", (
            "query role Neptune policy must be named 'neptune-data-readonly'"
        )
        # Confirm no Write/Delete policy exists for query role
        write_policies = [
            r
            for r in _pv_by_type(tfplan, "aws_iam_role_policy")
            if r["name"].startswith("query_") and "rw" in r["name"].lower()
        ]
        assert not write_policies, f"query role must not hold a Write policy: {write_policies}"
        return
    policy = json.loads(policy_str)
    actions = set(_as_list(policy["Statement"][0]["Action"]))
    assert "neptune-db:ReadDataViaQuery" in actions
    assert "neptune-db:connect" in actions
    assert "neptune-db:WriteDataViaQuery" not in actions
    assert "neptune-db:DeleteDataViaQuery" not in actions


def test_store_sg_ingress_rules_exact(tfplan):
    """Spec: test_store_sg_ingress_rules_exact (no CDK equivalent name).
    Neptune SG accepts port 8182 from exactly 3 sources; OpenSearch SG accepts 443 from exactly 3.
    """
    neptune_ingress = [
        r
        for r in _pv_by_type(tfplan, "aws_vpc_security_group_ingress_rule")
        if "neptune_from" in r["name"]
    ]
    opensearch_ingress = [
        r
        for r in _pv_by_type(tfplan, "aws_vpc_security_group_ingress_rule")
        if "opensearch_from" in r["name"]
    ]
    assert len(neptune_ingress) == 3, (
        f"neptune_sg must have exactly 3 ingress rules, found {len(neptune_ingress)}"
    )
    assert len(opensearch_ingress) == 3, (
        f"opensearch_sg must have exactly 3 ingress rules, found {len(opensearch_ingress)}"
    )
    for r in neptune_ingress:
        assert r["values"].get("from_port") == 8182
    for r in opensearch_ingress:
        assert r["values"].get("from_port") == 443
    # Verify expected source names (no public CIDR — all are referenced_security_group_id)
    neptune_names = {r["name"] for r in neptune_ingress}
    assert neptune_names == {"neptune_from_ingestion", "neptune_from_smoke", "neptune_from_query"}
    opensearch_names = {r["name"] for r in opensearch_ingress}
    assert opensearch_names == {
        "opensearch_from_ingestion",
        "opensearch_from_vector_smoke",
        "opensearch_from_query",
    }


def test_ingestion_and_smoke_roles_retain_neptune_rw(tfplan):
    """CDK: test_ingestion_and_smoke_roles_retain_read_write (ADR-0011: two roles keep full RW)."""
    ingestion_rw = _pv_by_address(tfplan, "aws_iam_role_policy.ingestion_neptune_rw")
    assert ingestion_rw is not None, "aws_iam_role_policy.ingestion_neptune_rw must exist"
    smoke_rw = _pv_by_address(tfplan, "aws_iam_role_policy.smoke_probe_neptune")
    assert smoke_rw is not None, "aws_iam_role_policy.smoke_probe_neptune must exist"

    policy_str_ingestion = ingestion_rw["values"].get("policy")
    if policy_str_ingestion:
        policy = json.loads(policy_str_ingestion)
        actions = set(_as_list(policy["Statement"][0]["Action"]))
        assert "neptune-db:WriteDataViaQuery" in actions, "ingestion must retain Neptune Write"
        assert "neptune-db:DeleteDataViaQuery" in actions, "ingestion must retain Neptune Delete"
    else:
        # Fresh plan proxy: resource name encodes RW intent
        assert ingestion_rw["values"].get("name") == "neptune-data-rw"

    policy_str_smoke = smoke_rw["values"].get("policy")
    if policy_str_smoke:
        policy = json.loads(policy_str_smoke)
        actions = set(_as_list(policy["Statement"][0]["Action"]))
        assert "neptune-db:WriteDataViaQuery" in actions, "smoke probe must retain Neptune Write"
        assert "neptune-db:DeleteDataViaQuery" in actions, "smoke probe must retain Neptune Delete"
    else:
        assert smoke_rw["values"].get("name") == "smoke-probe-neptune-full-rw"


def test_opensearch_access_policy_is_scoped_not_all_principals(tfplan):
    """CDK: test_opensearch_access_policy_is_scoped_not_all_principals.
    2 resource-policy principals (ingestion + vector-probe); query uses identity policy.
    """
    domains = _pv_by_type(tfplan, "aws_opensearch_domain")
    assert len(domains) == 1
    assert domains[0]["values"].get("domain_name") == "graphrag-vectors"
    policy_str = domains[0]["values"].get("access_policies")
    if policy_str is None:
        # Role ARNs are computed in fresh plan; verify the resource exists with correct domain.
        assert domains[0]["address"] == "aws_opensearch_domain.graphrag_vectors"
        return
    policy = json.loads(policy_str)
    blob = json.dumps(policy)
    assert '"Principal":"*"' not in blob, "OpenSearch access policy must not use AllPrincipals"
    assert "es:ESHttp*" in blob
    assert "domain/graphrag-vectors/*" in blob
    # Resource policy has exactly 2 named principals
    stmts = policy.get("Statement", [])
    assert stmts, "access policy must have at least one statement"
    principal = stmts[0].get("Principal", {})
    aws_principals = principal.get("AWS", []) if isinstance(principal, dict) else []
    assert len(aws_principals) == 2, (
        "expected 2 resource-policy principals (ingestion + vector-probe),"
        f" found {len(aws_principals)}"
    )


def test_vector_actions_are_scoped_no_wildcard_resource(tfplan):
    """CDK: test_vector_actions_are_scoped_no_wildcard_resource"""
    saw_titan = saw_opensearch = False
    for stmt in _all_iam_statements(tfplan):
        actions = _as_list(stmt.get("Action", []))
        resources = _as_list(stmt.get("Resource", []))
        if "bedrock:InvokeModel" in actions:
            assert resources != ["*"], "bedrock:InvokeModel must be scoped to the model ARN"
            if "amazon.titan-embed-text-v2:0" in json.dumps(resources):
                saw_titan = True
        if any(a.startswith("es:ESHttp") for a in actions):
            assert resources != ["*"], "es:ESHttp* must be scoped to the domain ARN"
            saw_opensearch = True
    if _iam_inline_policies(tfplan):
        assert saw_titan, "expected a scoped bedrock:InvokeModel grant on Titan v2"
        assert saw_opensearch, "expected a scoped es:ESHttp* grant"


def test_bedrock_synthesis_grant_scopes_profile_and_foundation_arns(tfplan):
    """CDK: test_bedrock_claude_grant_scopes_profile_and_foundation_no_wildcard"""
    saw_profile = saw_foundation = saw_converse = False
    for stmt in _all_iam_statements(tfplan):
        actions = _as_list(stmt.get("Action", []))
        if not any(a.startswith("bedrock:") for a in actions):
            continue
        resources_blob = json.dumps(_as_list(stmt.get("Resource", [])))
        assert "*" not in _as_list(stmt.get("Resource", [])), (
            "bedrock grant must not have wildcard resource"
        )
        if "inference-profile/us.anthropic.claude-sonnet-4-6" in resources_blob:
            saw_profile = True
        if "foundation-model/anthropic.claude-sonnet-4-6" in resources_blob:
            saw_foundation = True
        if "bedrock:Converse" in actions:
            saw_converse = True
    if _iam_inline_policies(tfplan):
        assert saw_profile, "expected the inference-profile ARN in a scoped bedrock grant"
        assert saw_foundation, "expected the foundation-model ARN in a scoped bedrock grant"
        assert saw_converse, "expected bedrock:Converse in the synthesis grant"


def test_ingestion_task_can_write_manifest_scoped_to_manifest_key(tfplan):
    """CDK: test_ingestion_task_can_write_manifest_scoped_to_manifest_key"""
    _allowed_keys = ("manifest.json", "schema_extraction_trace.txt", "silver/")
    found_manifest = False
    for stmt in _all_iam_statements(tfplan):
        actions = set(_as_list(stmt.get("Action", [])))
        if "s3:PutObject" not in actions:
            continue
        resources = json.dumps(stmt.get("Resource", ""))
        assert resources.strip('"') != "*", "s3:PutObject must not be wildcard"
        assert any(k in resources for k in _allowed_keys), (
            f"s3:PutObject must be scoped to one of {_allowed_keys}, got {resources}"
        )
        if "manifest.json" in resources:
            found_manifest = True
    # S3 policies are null when bucket ARN is unresolved (fresh plan); guard specifically.
    s3_put_policies = [
        r for r in _pv_by_type(tfplan, "aws_iam_role_policy") if "s3_put" in r["name"]
    ]
    s3_put_readable = any(_vals(r).get("policy") for r in s3_put_policies)
    if s3_put_readable:
        assert found_manifest, "expected an s3:PutObject grant for the ingest manifest"
    else:
        # Fresh plan: S3 bucket ARN is computed → policy is null. Verify resources exist.
        manifest_policy = _pv_by_address(tfplan, "aws_iam_role_policy.ingestion_s3_put_manifest")
        assert manifest_policy is not None


def test_function_url_is_iam_auth(tfplan):
    """CDK: test_function_url_is_iam_auth"""
    urls = _pv_by_type(tfplan, "aws_lambda_function_url")
    assert len(urls) == 1, "expected exactly one Function URL"
    assert urls[0]["values"].get("authorization_type") == "AWS_IAM", (
        "Function URL must be AWS_IAM, never NONE"
    )


def test_function_url_invoke_permission_scoped_to_named_principal(tfplan):
    """CDK: test_function_url_invoke_permission_scoped_to_named_principal"""
    perms = _pv_by_type(tfplan, "aws_lambda_permission")
    url_perms = [p for p in perms if p["values"].get("function_url_auth_type") == "AWS_IAM"]
    assert url_perms, "expected an aws_lambda_permission with function_url_auth_type=AWS_IAM"
    for p in url_perms:
        principal = p["values"].get("principal")
        assert principal not in ("*", None), (
            f"invoke principal must be a named role, got {principal!r}"
        )
        assert p["values"].get("action") == "lambda:InvokeFunctionUrl"


def test_budget_alarm_has_threshold_and_subscriber(tfplan):
    """CDK: test_budget_alarm_has_threshold_and_subscriber + test_budget_limit_unchanged_at_150"""
    budgets = _pv_by_type(tfplan, "aws_budgets_budget")
    assert len(budgets) == 1
    v = budgets[0]["values"]
    # limit_amount is "150" in fresh plans and "150.0" in applied-state plans (AWS API).
    assert float(v.get("limit_amount", 0)) == 150.0
    assert v.get("budget_type") == "COST"
    notifications = v.get("notification", [])
    assert notifications, "budget must have at least one notification"
    notif = notifications[0]
    assert notif.get("threshold") == 80
    assert notif.get("notification_type") == "ACTUAL"
    assert notif.get("subscriber_email_addresses"), "budget notification must have email subscriber"


def test_query_lambda_sg_reaches_neptune_and_opensearch(tfplan):
    """CDK: test_query_lambda_sg_reaches_neptune_and_opensearch"""
    ports = set()
    for r in _pv_by_type(tfplan, "aws_vpc_security_group_ingress_rule"):
        if "query" in r["name"]:
            ports.add(r["values"].get("from_port"))
    assert 8182 in ports, "query Lambda SG must reach Neptune 8182"
    assert 443 in ports, "query Lambda SG must reach OpenSearch 443"


def test_query_lambda_concurrency_cap(tfplan):
    """Backlog: terraform-query-lambda-concurrency-cap — blast-radius cost ceiling."""
    fns = _pv_by_type(tfplan, "aws_lambda_function")
    query = [
        f
        for f in fns
        if "query_lambda" in f["name"] or "query-lambda" in f["values"].get("function_name", "")
    ]
    assert len(query) == 1, "expected exactly one query Lambda"
    cap = query[0]["values"].get("reserved_concurrent_executions")
    assert cap is not None and cap > 0, (
        f"query_lambda must have reserved_concurrent_executions > 0, got {cap!r}"
    )
    assert cap == 10, f"expected concurrency cap of 10, got {cap}"


# ── T6: Fargate task sizing for docling (spec-ingestion-extraction-cleanse) ──


def test_ingestion_task_docling_cpu_and_memory(tfplan):
    """AC10: ECS task def must be cpu=2048, memory=8192 for docling model weights (~2.4 GB)."""
    task_defs = _pv_by_type(tfplan, "aws_ecs_task_definition")
    assert len(task_defs) == 1
    v = task_defs[0]["values"]
    assert v.get("cpu") == "2048", f"expected cpu=2048 for docling, got {v.get('cpu')!r}"
    assert v.get("memory") == "8192", f"expected memory=8192 for docling, got {v.get('memory')!r}"


def test_ingestion_task_has_offline_env_vars(tfplan):
    """AC11: ECS container env must include TRANSFORMERS_OFFLINE=1 and HF_DATASETS_OFFLINE=1."""
    task_defs = _pv_by_type(tfplan, "aws_ecs_task_definition")
    assert len(task_defs) == 1
    container_defs_raw = task_defs[0]["values"].get("container_definitions", "[]")
    container_defs = json.loads(container_defs_raw)
    assert container_defs, "expected at least one container definition"
    env_vars = {e["name"]: e["value"] for e in container_defs[0].get("environment", [])}
    assert env_vars.get("TRANSFORMERS_OFFLINE") == "1", (
        "TRANSFORMERS_OFFLINE=1 required to prevent docling runtime weight download"
    )
    assert env_vars.get("HF_DATASETS_OFFLINE") == "1", (
        "HF_DATASETS_OFFLINE=1 required to prevent docling runtime dataset download"
    )
