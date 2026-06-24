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


class GraphragStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        budget_email = CfnParameter(
            self,
            "BudgetAlarmEmail",
            type="String",
            description="Email address that receives the AWS Budgets cost alarm.",
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

        self._ingestion_task(vpc, bucket, cluster, neptune_sg, task_role, domain, opensearch_sg)
        self._smoke_lambda(vpc, cluster, neptune_sg)
        self._vector_smoke_lambda(vpc, domain, opensearch_sg, vector_probe_role)
        self._budget_alarm(budget_email.value_as_string)
        self._apply_governance_tags()

    # --- Scoped Neptune IAM-auth data-access statement (shared) -------------------
    def _neptune_data_access(self, cluster: neptune.CfnDBCluster) -> iam.PolicyStatement:
        return iam.PolicyStatement(
            actions=_NEPTUNE_DATA_ACTIONS,
            resources=[
                self.format_arn(
                    service="neptune-db",
                    resource=cluster.attr_cluster_resource_id,
                    resource_name="*",
                )
            ],
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
        cluster = neptune.CfnDBCluster(
            self,
            "NeptuneCluster",
            db_subnet_group_name=subnet_group.ref,
            vpc_security_group_ids=[sg.security_group_id],
            # Serverless at minimum capacity — scales down when idle (not to zero).
            serverless_scaling_configuration=neptune.CfnDBCluster.ServerlessScalingConfigurationProperty(
                min_capacity=1.0, max_capacity=2.5
            ),
            iam_auth_enabled=True,  # IAM-enforced access (ADR-0002)
            storage_encrypted=True,
        )
        cluster.add_dependency(subnet_group)
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

        # Least privilege: read only the corpus bucket; Neptune data access scoped
        # to this cluster only. (Bedrock-invoke + OpenSearch-data access are granted
        # to this same role in __init__ — scoped, no wildcard.)
        bucket.grant_read(task_role)
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
