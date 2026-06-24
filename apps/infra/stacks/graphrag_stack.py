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

from typing import Any

from aws_cdk import (
    CfnOutput,
    CfnParameter,
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
    aws_logs as logs,
)
from aws_cdk import (
    aws_neptune as neptune,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

# The exact VPC-endpoint set the in-VPC ingestion task needs with no NAT (ADR-0002).
# bedrock-runtime is intentionally absent — embeddings arrive in slice 2.
_INTERFACE_ENDPOINTS = {
    "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
    "EcrDocker": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
    "CloudWatchLogs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
    "Sts": ec2.InterfaceVpcEndpointAwsService.STS,
}

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
        self._ingestion_task(vpc, bucket, cluster, neptune_sg)
        self._budget_alarm(budget_email.value_as_string)
        self._apply_governance_tags()

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

        task_role = iam.Role(
            self, "IngestionTaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        # Least privilege: read only the corpus bucket; connect only to this cluster.
        bucket.grant_read(task_role)
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["neptune-db:connect"],
                resources=[
                    self.format_arn(
                        service="neptune-db",
                        resource=cluster.attr_cluster_resource_id,
                        resource_name="*",
                    )
                ],
            )
        )

        task_def = ecs.FargateTaskDefinition(
            self, "IngestionTask", cpu=512, memory_limit_mib=1024, task_role=task_role
        )
        task_def.add_container(
            "ingestion",
            image=ecs.ContainerImage.from_ecr_repository(repo),
            logging=ecs.LogDriver.aws_logs(stream_prefix="ingestion", log_group=log_group),
            # Self-configured: the deployed task knows its endpoint + bucket, so
            # `aws ecs run-task` needs no env overrides. AWS_REGION is NOT set here
            # — it is a reserved variable the Fargate agent injects automatically,
            # and the entrypoint/botocore read it from there.
            environment={
                "NEPTUNE_ENDPOINT": f"https://{cluster.attr_endpoint}:8182",
                "CORPUS_BUCKET": bucket.bucket_name,
            },
        )

        # The task's ENI sits in the private subnets; Neptune accepts it on 8182.
        task_sg = ec2.SecurityGroup(self, "IngestionSg", vpc=vpc, description="Fargate ingestion")
        neptune_sg.add_ingress_rule(task_sg, ec2.Port.tcp(8182), "ingestion to neptune 8182")

        # Handles for `aws ecs run-task` (the live ingest + retrieve smoke).
        private_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)
        CfnOutput(self, "EcsClusterName", value=cluster_compute.cluster_name)
        CfnOutput(self, "IngestionTaskDefArn", value=task_def.task_definition_arn)
        CfnOutput(self, "IngestionSecurityGroupId", value=task_sg.security_group_id)
        CfnOutput(self, "PrivateSubnetId", value=private_subnets.subnet_ids[0])
        CfnOutput(self, "IngestionRepoUri", value=repo.repository_uri)

    # --- Budgets cost alarm: threshold + a notification subscriber -----------------
    def _budget_alarm(self, email: str) -> None:
        budgets.CfnBudget(
            self,
            "CostBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=50, unit="USD"),
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
