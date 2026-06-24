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

Every taggable resource carries the five governance tags **Environment, Project,
Department, Application, User** (applied via `Tags.of(self)` in the stack;
asserted by `tests/test_stack.py`). Defaults are overridable; the deploy script
fills `User` from the caller identity.

## Deploy / destroy

> ⚠️ **Cost note (charter principle 4):** Neptune Serverless and (from slice 2)
> OpenSearch do **not** scale to zero — they accrue standing cost while deployed.
> `destroy` removes every billable resource. The Budgets alarm is the
> cloned-and-forgotten guardrail.

Use the scripts in [`scripts/`](scripts/) — they **cache AWS credentials once** (a
per-command credential/SSO provider gets rate-limited if every call re-resolves)
and let `cdk` block until the stack settles (**no status polling** — `cdk
deploy`/`destroy` are the signal; `status.sh` is a single ad-hoc check, never a
loop):

```bash
cd apps/infra
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
npm install -g aws-cdk                      # the cdk CLI (Node)

BUDGET_EMAIL=you@example.com \
  DEPLOY_DEPARTMENT=<dept> DEPLOY_ENV=demo \
  scripts/deploy.sh                         # bootstrap + deploy (creds cached, blocks)

scripts/status.sh                           # ONE status check (don't poll in a loop)
scripts/destroy.sh                          # removes every billable resource
```

After deploy, upload the corpus as `community/` and `enhancements/` trees **at the
bucket root** (the task defaults `CORPUS_PREFIX=""` — a non-root prefix silently
ingests nothing), then build/push the image and run the task. `NEPTUNE_ENDPOINT` +
`CORPUS_BUCKET` are baked into the task def; `AWS_REGION` is injected by the Fargate
agent (it is a reserved variable — don't set it in the task def):

```bash
aws s3 cp --recursive ./corpus s3://<corpus-bucket>/   # community/ + enhancements/ at root
docker build -f ../ingestion/Dockerfile -t <ecr-repo-uri>:latest ../.. && docker push <ecr-repo-uri>:latest
aws ecs run-task --cluster <cluster> --task-definition <task> --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<private-subnet>],securityGroups=[<ingestion-sg>]}'
```

> The VPC spans **2 AZs** because a Neptune DB subnet group requires ≥2 AZs — but
> the serverless cluster still runs a single instance, so this is an API
> requirement, not added HA cost (subnets are free). SG/rule **descriptions must use
> EC2's ASCII charset** (no em-dash, no `>`) — synth won't catch a violation, only a
> live deploy does, so `tests/test_stack.py` guards it.

## Verification

The topology + security posture is asserted in-process (no AWS account, no `cdk`
CLI) by `tests/test_stack.py` — run `pytest apps/infra/tests`.

**Live ingestion smoke check (the deferred AC9 pass condition):** after `run-task`,
confirm the ingestion CloudWatch log stream shows **non-zero** parsed/resolved
counts (an empty-corpus run exits 0 with zero counts — a silent no-op). The
`== ingest ==` report prints `parsed docs:` and `cross-source resolved nodes` — a
healthy run shows both > 0. **Live-AWS** deploy/destroy verification (that destroy
leaves nothing billable, that the alarm fires) is deferred: backlog
`graph-ingestion-resolution-live-deploy`.
