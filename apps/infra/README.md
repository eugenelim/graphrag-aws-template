# `apps/infra` — slice-1 CDK stack (deploy / destroy)

AWS CDK (Python) app for the slice-1 subset of the ephemeral, teardown-first
topology ([ADR-0002](../../docs/adr/0002-ephemeral-vpc-store-topology.md),
[ADR-0003](../../docs/adr/0003-iac-tool-aws-cdk-python.md)).

## What it provisions

- **VPC** — private isolated subnets, **no NAT gateway**.
- **VPC endpoints** — `s3` (gateway), `ecr.api`, `ecr.dkr`, `logs`, `sts`.
  (`bedrock-runtime` + OpenSearch arrive in slice 2; the query Lambda in slice 3.)
- **Neptune Serverless** — min capacity, VPC-resident (private subnet group),
  IAM-auth, storage-encrypted, **no public endpoint**.
- **S3 corpus bucket** — public access blocked, encrypted, TLS-only, auto-emptied
  on destroy.
- **Fargate ingestion task** — least-privilege task role (scoped `s3` read +
  `neptune-db:connect`; no wildcard resource), image from a created ECR repo.
- **AWS Budgets alarm** — $50/mo, alerts at 80% to an email subscriber.

## Deploy / destroy

> ⚠️ **Cost note (charter principle 4):** Neptune Serverless and (from slice 2)
> OpenSearch do **not** scale to zero — they accrue standing cost while deployed.
> `cdk destroy` removes every billable resource. The Budgets alarm is the
> cloned-and-forgotten guardrail.

```bash
cd apps/infra
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
npm install -g aws-cdk            # the cdk CLI (Node)

cdk deploy --parameters BudgetAlarmEmail=you@example.com
# then build + push the ingestion image to the created ECR repo and run the task:
#   docker build -f ../ingestion/Dockerfile -t <ecr-repo-uri>:latest ../..
#   docker push <ecr-repo-uri>:latest
#   aws ecs run-task --cluster <cluster> --task-definition <task> --launch-type FARGATE ...

cdk destroy                       # removes every billable resource
```

## Verification

The topology + security posture is asserted in-process (no AWS account, no `cdk`
CLI) by `tests/test_stack.py` — run `pytest apps/infra/tests`. **Live-AWS**
deploy/destroy verification (that destroy leaves nothing billable, that the alarm
fires) is deferred: backlog `graph-ingestion-resolution-live-deploy`.
