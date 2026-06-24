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

## Runbook: deploy → verify → teardown

> ⚠️ **Cost note (charter principle 4):** Neptune Serverless and (from slice 2)
> OpenSearch do **not** scale to zero — they accrue standing cost while deployed.
> Tear down promptly. `destroy.sh` removes every billable resource; the Budgets
> alarm is the cloned-and-forgotten guardrail.

The scripts in [`scripts/`](scripts/) **cache AWS credentials once** (a per-command
credential/SSO provider gets rate-limited if every call re-resolves) and let `cdk`
block until the stack settles — **no status polling**. The *why* and the verification
ladder live in
[`docs/architecture/deployment-and-verification.md`](../../docs/architecture/deployment-and-verification.md).

### Prerequisites (once)

```bash
cd apps/infra
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
npm install -g aws-cdk      # the cdk CLI (Node)
# AWS credentials reachable (env vars, SSO, or a profile). Region defaults to us-east-1.
```

### Configuration: parameters vs. logic

The scripts hold only **logic**; every tunable lives in
[`scripts/config.env`](scripts/config.env) — the single declarative source of build
parameters and resource names (stack name, region, governance-tag defaults, the SLR
service list, operational knobs). Per-deployer values that must **not** be committed (the
required `BUDGET_EMAIL`, an optional account-specific `INVOKER_ROLE_ARN`) go in a
**gitignored** `scripts/config.local.env`:

```bash
cp scripts/config.local.env.example scripts/config.local.env   # then edit BUDGET_EMAIL
```

Precedence is **explicit env var > `config.local.env` > `config.env`**, so a one-off
override still works: `BUDGET_EMAIL=you@example.com scripts/deploy.sh`. (The `config.local.env`
is *sourced* — treat it as trusted shell, like the scripts themselves.)

### 1. Deploy

```bash
# BUDGET_EMAIL from config.local.env, or inline as a one-off:
BUDGET_EMAIL=you@example.com DEPLOY_DEPARTMENT=<dept> \
  scripts/deploy.sh          # cdk bootstrap (idempotent) + deploy; blocks until done

scripts/status.sh            # optional: ONE status check (expect CREATE_COMPLETE) — never loop
```

### 2. Verify

**Offline (no account):** `pytest apps/infra/tests` — synth assertions for topology,
security posture, and tags.

**Live, in-VPC (the real graph-store round-trip):** Neptune is VPC-private, so verify
with the bundled smoke-probe Lambda — it inserts a node + edge and reads them back
through the real adapter, then cleans up. Invoke it (a control-plane call; the
function runs in-VPC):

```bash
FN=$(aws cloudformation describe-stacks --stack-name GraphragSlice1 \
  --query "Stacks[0].Outputs[?OutputKey=='SmokeProbeName'].OutputValue" --output text)
aws lambda invoke --function-name "$FN" /dev/stdout
# PASS looks like: {"ok": true, "run": "...", "retrieved_node": "person:smoke-...", "neighbors": ["sig:smoke-..."]}
```

**Optional — full corpus ingestion via Fargate** (needs docker to build the image).
Upload the corpus as `community/` and `enhancements/` trees **at the bucket root**
(the task defaults `CORPUS_PREFIX=""` — a non-root prefix silently ingests nothing).
`NEPTUNE_ENDPOINT` + `CORPUS_BUCKET` are baked into the task def; `AWS_REGION` is
injected by the Fargate agent (reserved — don't set it):

```bash
aws s3 cp --recursive ./corpus "s3://$(aws cloudformation describe-stacks --stack-name GraphragSlice1 \
  --query "Stacks[0].Outputs[?OutputKey=='CorpusBucketName'].OutputValue" --output text)/"
docker build -f ../ingestion/Dockerfile -t <ecr-repo-uri>:latest ../.. && docker push <ecr-repo-uri>:latest
aws ecs run-task --cluster <EcsClusterName> --task-definition <IngestionTaskDefArn> --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<PrivateSubnetId>],securityGroups=[<IngestionSecurityGroupId>]}'
# Confirm the ingestion log stream shows NON-ZERO parsed/resolved counts (an empty-corpus run exits 0 silently).
```

(The `<...>` handles are stack outputs: `EcsClusterName`, `IngestionTaskDefArn`,
`PrivateSubnetId`, `IngestionSecurityGroupId`, `IngestionRepoUri`.)

### 3. Teardown

```bash
scripts/destroy.sh           # removes every billable resource (incl. the probe) + sweeps Lambda log groups
scripts/status.sh            # expect DOES_NOT_EXIST
```

## Notes (live-deploy gotchas, guarded by synth tests)

- The VPC spans **2 AZs** because a Neptune DB subnet group requires ≥2 AZs — the
  cluster still runs a single instance, so it's an API requirement, not HA cost.
- SG group/rule **descriptions must use EC2's ASCII charset** (no em-dash, no `>`).
- Neptune IAM auth needs the **data-plane actions** (`Read/Write/DeleteDataViaQuery`),
  not just `neptune-db:connect`.
- `cdk synth` catches none of the above — only a live deploy does — so
  `tests/test_stack.py` guards each. Full rationale + the verified live-probe result:
  [`deployment-and-verification.md`](../../docs/architecture/deployment-and-verification.md).
