"""The slice-1 subset of the ephemeral, teardown-first topology (ADR-0002).

Provisions only what the graph half needs: a no-NAT VPC with the endpoints the
in-VPC ingestion uses, a Neptune Serverless cluster (VPC-resident, min capacity),
an encrypted private S3 corpus bucket, a Fargate ingestion task with a
least-privilege task role, and a Budgets alarm. OpenSearch, the ``bedrock-runtime``
endpoint, and the query Lambda arrive with slices 2–3 (ADR-0002 deferral).

Every billable resource is set to be removed on ``cdk destroy`` (teardown is a
feature — charter principle 4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import (
    CfnOutput,
    CfnParameter,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import (
    aws_budgets as budgets,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_neptune as neptune,
)
from aws_cdk import (
    aws_opensearchservice as opensearch,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

# The exact VPC-endpoint set the in-VPC ingestion + query compute needs with no NAT
# (ADR-0002). bedrock-runtime arrives with slice 2 (Titan v2 embeddings).
_INTERFACE_ENDPOINTS = {
    "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
    "EcrDocker": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
    "CloudWatchLogs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
    "Sts": ec2.InterfaceVpcEndpointAwsService.STS,
    "BedrockRuntime": ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
}

# Vector half (slice 2). The domain name is fixed so its ARN is computable without a
# self-reference in the access policy (avoids a CDK dependency cycle).
_OPENSEARCH_DOMAIN_NAME = "graphrag-vectors"
_TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"

# Slice 5: the ingest manifest object key (doc id -> content hash) at the corpus bucket root.
# Must match the entrypoint's MANIFEST_FILENAME (CORPUS_PREFIX is "" on the deployed task).
MANIFEST_KEY = "manifest.json"

# schema-guided-extraction: the per-triple trace artifact key at the corpus bucket root. Must
# match the entrypoint's SCHEMA_EXTRACTION_TRACE_FILENAME. The scoped PutObject grant below is
# widened to this one additional key (still NOT bucket-wide) — the existing grant was scoped to
# manifest.json only, so the trace write needs its own scoped grant (live AC9 finding 2026-06-27).
SCHEMA_EXTRACTION_TRACE_KEY = "schema_extraction_trace.txt"

# medallion-staging: the Silver artifact cache prefix at the corpus bucket root. Must match
# `graphrag.silver.SILVER_PREFIX`. The staged delta task writes content+config-addressed Silver
# objects (`silver/<fp>/<hash>/{chunks,candidates}.json`) here, so the PutObject grant below is a
# **prefix** wildcard (`silver/*`) — broader than a single object key, but still NOT bucket-wide
# (least privilege). Silver lives in the auto-emptied corpus bucket, so `destroy` leaves no
# residual (AC7/AC8).
SILVER_PREFIX = "silver/"

# Synthesis Claude model (slice 3). Must equal the library
# `graphrag.synthesize.DEFAULT_SYNTHESIS_MODEL_ID` — a synth test asserts the equality
# so the Bedrock IAM grant scope and the runtime default can't drift. This is a
# **cross-region inference profile**: the grant scopes BOTH the account+region-qualified
# inference-profile ARN AND each underlying regional foundation-model ARN it routes to
# (no wildcard Resource — AC8).
_SYNTHESIS_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
# The underlying foundation model the `us.` profile fronts, and the regions the US
# cross-region profile routes to (foundation-model ARNs carry no account id).
_SYNTHESIS_FOUNDATION_MODEL = "anthropic.claude-sonnet-4-6"
_SYNTHESIS_PROFILE_REGIONS = ("us-east-1", "us-east-2", "us-west-2")
# es:ESHttp* data-plane verbs, scoped to the domain (least privilege; no wildcard
# resource). The "es" prefix matches store/opensearch.py's OPENSEARCH_SERVICE so the
# SigV4 signing service and the IAM action prefix are one source.
_OPENSEARCH_DATA_ACTIONS = [
    "es:ESHttpGet",
    "es:ESHttpPut",
    "es:ESHttpPost",
    "es:ESHttpDelete",
    "es:ESHttpHead",
]

# Org governance tags applied to every taggable resource (via Tags.of(self), which
# propagates through the construct tree). Defaults are overridable per deploy with
# `cdk deploy -c <key-lowercased>=<value>`; the deploy script fills `user`.
_GOVERNANCE_TAG_DEFAULTS = {
    "Environment": "demo",
    "Project": "graphrag-aws-template",
    "Department": "unspecified",
    "Application": "graphrag",
    "User": "unspecified",
}

# The graphrag package source, zipped as-is into the smoke Lambda (pure-Python; no
# bundling/docker — boto3/botocore are in the Lambda runtime).
_GRAPHRAG_SRC = str(Path(__file__).resolve().parents[3] / "packages" / "graphrag" / "src")

# Neptune IAM-auth data access: `connect` alone is NOT enough to read/write via
# openCypher under IAM auth -- the data-plane actions are required too. Scoped to
# the specific cluster resource (no wildcard).
_NEPTUNE_DATA_ACTIONS = [
    "neptune-db:connect",
    "neptune-db:ReadDataViaQuery",
    "neptune-db:WriteDataViaQuery",
    "neptune-db:DeleteDataViaQuery",
]
# Read-only subset for the query Lambda (ADR-0004): `connect` + `ReadDataViaQuery` only — the
# load-bearing backstop for LLM-authored text2cypher queries (a write is denied by IAM before
# the engine runs it, independent of the app-layer validator's completeness).
_NEPTUNE_READ_ONLY_ACTIONS = [
    "neptune-db:connect",
    "neptune-db:ReadDataViaQuery",
]
# The Neptune engine-level read-cost backstop (ADR-0004): a per-query timeout (ms) that kills a
# runaway model-authored traversal even if the validator's unbounded-path guard is bypassed.
# Set explicitly (vs. the 120s default) so it is narratable and tunable; the parameter-group
# family must match the pinned engine version below. The version is pinned to a value the
# account/region actually offers (verify with `aws neptune describe-db-engine-versions` — the
# runtime oracle; release-notes version strings can lag/differ, e.g. 1.3.2.0 vs the real 1.3.2.1).
_NEPTUNE_QUERY_TIMEOUT_MS = "20000"
_NEPTUNE_ENGINE_VERSION = "1.3.5.0"  # latest neptune1.3.x; matches _NEPTUNE_PARAM_GROUP_FAMILY
_NEPTUNE_PARAM_GROUP_FAMILY = "neptune1.3"


class GraphragStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        budget_email = CfnParameter(
            self,
            "BudgetAlarmEmail",
            type="String",
            description="Email address that receives the AWS Budgets cost alarm.",
        )
        # The named principal allowed to invoke the IAM-auth query Function URL — the
        # demo's deploying / CLI role. IAM auth gates *that a request is signed*; this
        # scoped grant gates *who may invoke* (never Principal: * / account-root).
        invoker_role_arn = CfnParameter(
            self,
            "InvokerRoleArn",
            type="String",
            description="IAM role ARN permitted to invoke the query Function URL (SigV4).",
        )

        vpc = self._vpc()
        bucket = self._corpus_bucket()
        cluster, neptune_sg = self._neptune(vpc)

        # Roles created up front so the OpenSearch domain access policy can name them
        # without a self-referential ARN (the roles don't depend on the domain).
        task_role = iam.Role(
            self, "IngestionTaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        vector_probe_role = iam.Role(
            self,
            "VectorProbeRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                )
            ],
        )

        domain, opensearch_sg = self._opensearch(vpc, [task_role, vector_probe_role])
        # Scoped vector permissions on both compute roles (Bedrock invoke + OpenSearch
        # data plane), no wildcard resource.
        for role in (task_role, vector_probe_role):
            role.add_to_policy(self._bedrock_invoke())
            role.add_to_policy(self._opensearch_data_access())
        # The ingestion task additionally generates per-community summaries via Bedrock Converse
        # (global-community-summary slice, ADR-0005): community detection runs in this Fargate
        # task, not a standing Neptune Analytics service. Scoped to the synthesis model (the same
        # `_bedrock_synthesis_invoke` grant the query Lambda holds), no wildcard Resource.
        task_role.add_to_policy(self._bedrock_synthesis_invoke())

        self._ingestion_task(vpc, bucket, cluster, neptune_sg, task_role, domain, opensearch_sg)
        self._smoke_lambda(vpc, cluster, neptune_sg)
        self._vector_smoke_lambda(vpc, domain, opensearch_sg, vector_probe_role)
        self._query_lambda(
            vpc, cluster, neptune_sg, domain, opensearch_sg, invoker_role_arn.value_as_string
        )
        self._budget_alarm(budget_email.value_as_string)
        self._apply_governance_tags()

    # --- Scoped Neptune IAM-auth data-access statements ---------------------------
    def _neptune_cluster_arn(self, cluster: neptune.CfnDBCluster) -> str:
        return self.format_arn(
            service="neptune-db",
            resource=cluster.attr_cluster_resource_id,
            resource_name="*",
        )

    def _neptune_data_access(self, cluster: neptune.CfnDBCluster) -> iam.PolicyStatement:
        """Full read-write Neptune data access — for the roles that legitimately write
        (the ingestion Fargate task and the on-demand smoke probe)."""
        return iam.PolicyStatement(
            actions=_NEPTUNE_DATA_ACTIONS, resources=[self._neptune_cluster_arn(cluster)]
        )

    def _neptune_read_only_access(self, cluster: neptune.CfnDBCluster) -> iam.PolicyStatement:
        """Read-only Neptune data access — `connect` + `ReadDataViaQuery` only, no
        `WriteDataViaQuery`/`DeleteDataViaQuery`. This is the load-bearing backstop for the
        text2cypher path (ADR-0004): the query Lambda runs LLM-authored openCypher, so AWS IAM
        — not the app-layer validator — is what makes a write impossible. The hybrid + governed
        paths on this same role are read-only too, so the narrowing affects nothing else."""
        return iam.PolicyStatement(
            actions=_NEPTUNE_READ_ONLY_ACTIONS, resources=[self._neptune_cluster_arn(cluster)]
        )

    # --- Governance tags on every taggable resource -------------------------------
    def _apply_governance_tags(self) -> None:
        for key, default in _GOVERNANCE_TAG_DEFAULTS.items():
            value = self.node.try_get_context(key.lower()) or default
            Tags.of(self).add(key, str(value))

    # --- VPC: private, no NAT, with exactly the endpoints ingestion needs ---------
    def _vpc(self) -> ec2.Vpc:
        vpc = ec2.Vpc(
            self,
            "Vpc",
            # 2 AZs because a Neptune DB subnet group *requires* >=2 AZs — but the
            # serverless cluster still runs a single instance, so this is an API
            # requirement, not an HA choice (ADR-0002 single-instance posture holds;
            # subnets are free, only running compute costs).
            max_azs=2,
            nat_gateways=0,  # no NAT — all egress via VPC endpoints
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24
                )
            ],
        )
        vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)
        for name, service in _INTERFACE_ENDPOINTS.items():
            vpc.add_interface_endpoint(name, service=service)
        return vpc

    # --- S3 corpus snapshot bucket: private, encrypted, teardown-removable ---------
    def _corpus_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            "CorpusBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,  # so `destroy` leaves nothing billable
        )
        CfnOutput(self, "CorpusBucketName", value=bucket.bucket_name)
        return bucket

    # --- Neptune Serverless: VPC-resident (subnet group), min capacity -------------
    def _neptune(self, vpc: ec2.Vpc) -> tuple[neptune.CfnDBCluster, ec2.SecurityGroup]:
        subnet_group = neptune.CfnDBSubnetGroup(
            self,
            "NeptuneSubnets",
            db_subnet_group_description="graphrag neptune (private isolated subnets)",
            subnet_ids=vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED).subnet_ids,
        )
        sg = ec2.SecurityGroup(
            self,
            "NeptuneSg",
            vpc=vpc,
            description="Neptune - VPC-internal only",  # ASCII only (EC2 rejects non-ASCII)
            allow_all_outbound=False,
        )
        # The engine read-cost backstop (ADR-0004): a cluster parameter group pinning
        # `neptune_query_timeout` so a runaway model-authored read is killed by the engine.
        # The parameter-group family must match the engine version, so both are pinned (the
        # stack is fresh-deploy/ephemeral, so there is no upgrade/downgrade path to worry about).
        cluster_params = neptune.CfnDBClusterParameterGroup(
            self,
            "NeptuneClusterParams",
            family=_NEPTUNE_PARAM_GROUP_FAMILY,
            description="graphrag neptune - read-cost backstop (query timeout) for text2cypher",
            name=None,
            parameters={"neptune_query_timeout": _NEPTUNE_QUERY_TIMEOUT_MS},
        )
        cluster = neptune.CfnDBCluster(
            self,
            "NeptuneCluster",
            db_subnet_group_name=subnet_group.ref,
            vpc_security_group_ids=[sg.security_group_id],
            engine_version=_NEPTUNE_ENGINE_VERSION,  # pinned to match the parameter-group family
            db_cluster_parameter_group_name=cluster_params.ref,
            # Serverless at minimum capacity — scales down when idle (not to zero).
            serverless_scaling_configuration=neptune.CfnDBCluster.ServerlessScalingConfigurationProperty(
                min_capacity=1.0, max_capacity=2.5
            ),
            iam_auth_enabled=True,  # IAM-enforced access (ADR-0002)
            storage_encrypted=True,
        )
        cluster.add_dependency(subnet_group)
        cluster.add_dependency(cluster_params)
        neptune.CfnDBInstance(
            self,
            "NeptuneInstance",
            db_cluster_identifier=cluster.ref,
            db_instance_class="db.serverless",
        ).add_dependency(cluster)
        # The endpoint the in-VPC ingestion task / live round-trip test connects to.
        CfnOutput(self, "NeptuneEndpoint", value=f"https://{cluster.attr_endpoint}:8182")
        return cluster, sg

    # --- Fargate ingestion task with a least-privilege task role -------------------
    def _ingestion_task(
        self,
        vpc: ec2.Vpc,
        bucket: s3.Bucket,
        cluster: neptune.CfnDBCluster,
        neptune_sg: ec2.SecurityGroup,
        task_role: iam.Role,
        domain: opensearch.Domain,
        opensearch_sg: ec2.SecurityGroup,
    ) -> None:
        cluster_compute = ecs.Cluster(self, "EcsCluster", vpc=vpc)
        repo = ecr.Repository(
            self, "IngestionRepo", removal_policy=RemovalPolicy.DESTROY, empty_on_delete=True
        )
        log_group = logs.LogGroup(
            self,
            "IngestionLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Least privilege: read the corpus bucket; write ONLY the ingest manifest
        # (slice 5 — the delta task records doc-id->hash at the bucket root and reads it
        # back on the next --delta); Neptune data access scoped to this cluster only.
        # (Bedrock-invoke + OpenSearch-data access are granted to this same role in
        # __init__ — scoped, no wildcard.)
        bucket.grant_read(task_role)
        bucket.grant_put(task_role, MANIFEST_KEY)  # PutObject scoped to manifest.json
        # PutObject for the schema-guided extraction trace artifact (still key-scoped, not
        # bucket-wide); default-off, so written only when SCHEMA_EXTRACTION is set.
        bucket.grant_put(task_role, SCHEMA_EXTRACTION_TRACE_KEY)
        # PutObject for the medallion Silver cache (prefix-scoped to silver/*, never bucket-wide):
        # the staged delta task writes content+config-addressed chunks/candidates artifacts here.
        bucket.grant_put(task_role, SILVER_PREFIX + "*")
        task_role.add_to_policy(self._neptune_data_access(cluster))

        task_def = ecs.FargateTaskDefinition(
            self, "IngestionTask", cpu=512, memory_limit_mib=1024, task_role=task_role
        )
        task_def.add_container(
            "ingestion",
            image=ecs.ContainerImage.from_ecr_repository(repo),
            logging=ecs.LogDriver.aws_logs(stream_prefix="ingestion", log_group=log_group),
            # Self-configured: the deployed task knows its endpoints + bucket, so
            # `aws ecs run-task` needs no env overrides. AWS_REGION is NOT set here
            # — it is a reserved variable the Fargate agent injects automatically,
            # and the entrypoint/botocore read it from there. OPENSEARCH_ENDPOINT
            # turns on the single-parse dual-write (graph + vector).
            environment={
                "NEPTUNE_ENDPOINT": f"https://{cluster.attr_endpoint}:8182",
                "OPENSEARCH_ENDPOINT": f"https://{domain.domain_endpoint}",
                "CORPUS_BUCKET": bucket.bucket_name,
                # Schema-guided LLM extraction (ADR-0006) is additive + DEFAULT-OFF: the deployed
                # task is byte-identical to today unless an operator flips this to "true" (e.g. an
                # `aws ecs run-task` env override). It reuses the task role's existing
                # bedrock:Converse grant at the synthesis model — NO new grant, NO new resource.
                "SCHEMA_EXTRACTION": "false",
            },
        )

        # The task's ENI sits in the private subnets; Neptune accepts it on 8182,
        # OpenSearch on 443.
        task_sg = ec2.SecurityGroup(self, "IngestionSg", vpc=vpc, description="Fargate ingestion")
        neptune_sg.add_ingress_rule(task_sg, ec2.Port.tcp(8182), "ingestion to neptune 8182")
        opensearch_sg.add_ingress_rule(task_sg, ec2.Port.tcp(443), "ingestion to opensearch 443")

        # Handles for `aws ecs run-task` (the live ingest + retrieve smoke).
        private_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)
        CfnOutput(self, "EcsClusterName", value=cluster_compute.cluster_name)
        CfnOutput(self, "IngestionTaskDefArn", value=task_def.task_definition_arn)
        CfnOutput(self, "IngestionSecurityGroupId", value=task_sg.security_group_id)
        CfnOutput(self, "PrivateSubnetId", value=private_subnets.subnet_ids[0])
        CfnOutput(self, "IngestionRepoUri", value=repo.repository_uri)

    # --- In-VPC smoke probe: a scale-to-zero Lambda for live insert+retrieve ------
    def _smoke_lambda(
        self, vpc: ec2.Vpc, cluster: neptune.CfnDBCluster, neptune_sg: ec2.SecurityGroup
    ) -> None:
        sg = ec2.SecurityGroup(self, "SmokeSg", vpc=vpc, description="Neptune smoke probe")
        neptune_sg.add_ingress_rule(sg, ec2.Port.tcp(8182), "smoke probe to neptune 8182")

        # Stack-managed log group so `cdk destroy` removes it -- a Lambda's default
        # /aws/lambda/<fn> group is auto-created and would otherwise survive teardown.
        log_group = logs.LogGroup(
            self,
            "SmokeProbeLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        fn = lambda_.Function(
            self,
            "SmokeProbe",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="graphrag.smoke_lambda.lambda_handler",
            code=lambda_.Code.from_asset(_GRAPHRAG_SRC),  # zipped as-is, no docker
            timeout=Duration.seconds(60),  # VPC cold start + Neptune client init
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[sg],
            log_group=log_group,  # not the auto-created /aws/lambda/<fn> group
            environment={"NEPTUNE_ENDPOINT": f"https://{cluster.attr_endpoint}:8182"},
        )
        fn.add_to_role_policy(self._neptune_data_access(cluster))  # scoped; no public URL
        CfnOutput(self, "SmokeProbeName", value=fn.function_name)

    # --- Scoped vector IAM statements (shared by the task + probe roles) -----------
    def _opensearch_domain_arn(self) -> str:
        # Computed from the fixed domain name so neither the access policy nor the
        # role policies self-reference the domain (avoids a CDK dependency cycle).
        return self.format_arn(
            service="es", resource="domain", resource_name=f"{_OPENSEARCH_DOMAIN_NAME}/*"
        )

    def _opensearch_data_access(self) -> iam.PolicyStatement:
        return iam.PolicyStatement(
            actions=list(_OPENSEARCH_DATA_ACTIONS), resources=[self._opensearch_domain_arn()]
        )

    def _bedrock_invoke(self) -> iam.PolicyStatement:
        # Scoped to the one Titan embeddings model (foundation-model ARNs carry no
        # account id), never bedrock:* on "*".
        return iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                self.format_arn(
                    service="bedrock",
                    account="",
                    resource="foundation-model",
                    resource_name=_TITAN_MODEL_ID,
                )
            ],
        )

    def _bedrock_synthesis_invoke(self) -> iam.PolicyStatement:
        # Scoped to the synthesis Claude model, with bedrock:Converse (the synthesizer
        # uses the Converse API). Because the configured model is a cross-region
        # inference profile, scope BOTH the account+region-qualified inference-profile
        # ARN AND each underlying regional foundation-model ARN it routes to — never a
        # wildcard Resource (AC8). Foundation-model ARNs carry no account id.
        resources = [
            self.format_arn(
                service="bedrock",
                region=self.region,
                resource="inference-profile",
                resource_name=_SYNTHESIS_MODEL_ID,
            )
        ]
        for region in _SYNTHESIS_PROFILE_REGIONS:
            resources.append(
                self.format_arn(
                    service="bedrock",
                    region=region,
                    account="",
                    resource="foundation-model",
                    resource_name=_SYNTHESIS_FOUNDATION_MODEL,
                )
            )
        return iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:Converse"],
            resources=resources,
        )

    # --- OpenSearch: single-node, VPC-resident, encrypted, IAM-scoped access -------
    def _opensearch(
        self, vpc: ec2.Vpc, principals: list[iam.IRole]
    ) -> tuple[opensearch.Domain, ec2.SecurityGroup]:
        sg = ec2.SecurityGroup(
            self,
            "OpenSearchSg",
            vpc=vpc,
            description="OpenSearch - VPC-internal only",  # ASCII only (EC2 charset)
            allow_all_outbound=False,
        )
        # Single data node -> exactly one subnet (no zone awareness). Subnets are free;
        # this is the single-node posture (ADR-0002), not HA.
        one_subnet = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED).subnets[0]
        domain = opensearch.Domain(
            self,
            "VectorDomain",
            domain_name=_OPENSEARCH_DOMAIN_NAME,
            version=opensearch.EngineVersion.OPENSEARCH_2_11,
            capacity=opensearch.CapacityConfig(
                data_nodes=1,
                data_node_instance_type="t3.small.search",
                multi_az_with_standby_enabled=False,
            ),
            ebs=opensearch.EbsOptions(volume_size=10, volume_type=ec2.EbsDeviceVolumeType.GP3),
            zone_awareness=opensearch.ZoneAwarenessConfig(enabled=False),
            vpc=vpc,
            vpc_subnets=[ec2.SubnetSelection(subnets=[one_subnet])],
            security_groups=[sg],
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            node_to_node_encryption=True,
            enforce_https=True,
            removal_policy=RemovalPolicy.DESTROY,
            # Resource-side IAM enforcement: only the task + probe roles may call the
            # domain. Not AllPrincipals — a network path inside the VPC is not enough.
            access_policies=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ArnPrincipal(r.role_arn) for r in principals],
                    actions=["es:ESHttp*"],
                    resources=[self._opensearch_domain_arn()],
                )
            ],
        )
        CfnOutput(self, "OpenSearchEndpoint", value=f"https://{domain.domain_endpoint}")
        return domain, sg

    # --- In-VPC vector smoke probe: embed -> index -> retrieve an ingested chunk ----
    def _vector_smoke_lambda(
        self,
        vpc: ec2.Vpc,
        domain: opensearch.Domain,
        opensearch_sg: ec2.SecurityGroup,
        role: iam.Role,
    ) -> None:
        sg = ec2.SecurityGroup(
            self, "VectorSmokeSg", vpc=vpc, description="OpenSearch+Bedrock vector smoke probe"
        )
        opensearch_sg.add_ingress_rule(
            sg, ec2.Port.tcp(443), "vector smoke probe to opensearch 443"
        )
        log_group = logs.LogGroup(
            self,
            "VectorSmokeProbeLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        fn = lambda_.Function(
            self,
            "VectorSmokeProbe",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="graphrag.vector_smoke_lambda.lambda_handler",
            code=lambda_.Code.from_asset(_GRAPHRAG_SRC),  # zipped as-is, no docker
            timeout=Duration.seconds(120),  # VPC cold start + Bedrock + OpenSearch init
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[sg],
            log_group=log_group,  # not the auto-created /aws/lambda/<fn> group
            role=role,  # scoped Bedrock-invoke + OpenSearch-data access; no public URL
            environment={"OPENSEARCH_ENDPOINT": f"https://{domain.domain_endpoint}"},
        )
        CfnOutput(self, "VectorSmokeProbeName", value=fn.function_name)

    # --- In-VPC query Lambda behind an IAM-auth Function URL (slice 3) --------------
    def _query_lambda(
        self,
        vpc: ec2.Vpc,
        cluster: neptune.CfnDBCluster,
        neptune_sg: ec2.SecurityGroup,
        domain: opensearch.Domain,
        opensearch_sg: ec2.SecurityGroup,
        invoker_role_arn: str,
    ) -> None:
        # Private isolated subnets only (not public); the Function URL is the sole
        # The query Lambda is in-VPC COMPUTE that initiates outbound to Neptune (8182),
        # OpenSearch (443), and the Bedrock VPC endpoint (443) — so it allows outbound,
        # exactly like the Fargate task and the smoke probes (IngestionSg / SmokeSg /
        # VectorSmokeSg). The "no-egress-path" guarantee is the no-NAT topology, not a
        # closed SG: with no NAT, outbound can only reach VPC endpoints + in-VPC stores,
        # there is no internet path. (allow_all_outbound=False here would silently block
        # the first Bedrock call and hang the function to its timeout.)
        sg = ec2.SecurityGroup(
            self,
            "QuerySg",
            vpc=vpc,
            description="query lambda - in-VPC compute (egress to stores + VPC endpoints)",
        )
        neptune_sg.add_ingress_rule(sg, ec2.Port.tcp(8182), "query lambda to neptune 8182")
        opensearch_sg.add_ingress_rule(sg, ec2.Port.tcp(443), "query lambda to opensearch 443")

        role = iam.Role(
            self,
            "QueryRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                )
            ],
        )
        # Least privilege: Neptune data scoped to the cluster, es:ESHttp* scoped to the
        # domain, Bedrock invoke scoped to the Titan model AND the synthesis Claude
        # model (inference-profile + foundation-model ARNs) — no wildcard Resource.
        # The query Lambda's Neptune grant is READ-ONLY (connect + ReadDataViaQuery, no
        # Write/Delete): it is the only role that runs LLM-authored text2cypher openCypher,
        # so IAM — not the app-layer validator — is the load-bearing write backstop (ADR-0004).
        # The hybrid + governed paths on this role are read-only too, so nothing else regresses.
        role.add_to_policy(self._neptune_read_only_access(cluster))
        role.add_to_policy(self._opensearch_data_access())
        role.add_to_policy(self._bedrock_invoke())
        role.add_to_policy(self._bedrock_synthesis_invoke())

        log_group = logs.LogGroup(
            self,
            "QueryLambdaLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        fn = lambda_.Function(
            self,
            "QueryLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="graphrag.query_lambda.lambda_handler",
            code=lambda_.Code.from_asset(_GRAPHRAG_SRC),  # zipped as-is, no docker
            timeout=Duration.seconds(120),  # VPC cold start + Neptune/OpenSearch/Bedrock
            memory_size=512,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[sg],
            log_group=log_group,  # not the auto-created /aws/lambda/<fn> group
            role=role,
            environment={
                "NEPTUNE_ENDPOINT": f"https://{cluster.attr_endpoint}:8182",
                "OPENSEARCH_ENDPOINT": f"https://{domain.domain_endpoint}",
                "SYNTHESIS_MODEL_ID": _SYNTHESIS_MODEL_ID,
            },
        )
        # IAM-auth Function URL — the only public ingress. Invoke scoped to a named
        # principal (the deploying/CLI role), never Principal: * / account-root. The
        # permission is created explicitly (not via grant_invoke_url) because the
        # principal is a CfnParameter token, which can't be resolved into a construct
        # ID; CfnPermission takes the token as the Principal directly.
        url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)
        lambda_.CfnPermission(
            self,
            "QueryUrlInvoke",
            action="lambda:InvokeFunctionUrl",
            function_name=fn.function_arn,
            principal=invoker_role_arn,
            function_url_auth_type="AWS_IAM",
        )
        CfnOutput(self, "QueryFunctionUrl", value=url.url)
        CfnOutput(self, "QueryLambdaName", value=fn.function_name)

    # --- Budgets cost alarm: threshold + a notification subscriber -----------------
    def _budget_alarm(self, email: str) -> None:
        budgets.CfnBudget(
            self,
            "CostBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                # Re-evaluated for slice 2: standing Neptune Serverless (min NCU) +
                # single-node OpenSearch (t3.small.search) + the bedrock-runtime
                # interface endpoint. $150/mo keeps the "forgotten deploy" alarm
                # meaningful without firing on a same-day deploy/destroy.
                budget_limit=budgets.CfnBudget.SpendProperty(amount=150, unit="USD"),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=80,  # alert at 80% of the $50 idle-cost guardrail
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL", address=email
                        )
                    ],
                )
            ],
        )
