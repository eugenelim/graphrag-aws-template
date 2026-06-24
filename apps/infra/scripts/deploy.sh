#!/usr/bin/env bash
# Deploy the slice-1 stack. Credentials are cached once (see _aws-env.sh) so the
# upstream auth provider is not re-hit per call. `cdk deploy` blocks until
# CloudFormation settles -- do NOT poll describe-stacks in a loop; one blocking
# process is the whole signal. Use status.sh for a single ad-hoc status check.
#
# Required: BUDGET_EMAIL=you@example.com
# Optional: INVOKER_ROLE_ARN (the role allowed to invoke the query Function URL;
#   defaults to the deploying caller's underlying role ARN).
# Optional tag overrides: DEPLOY_ENV, DEPLOY_DEPARTMENT, DEPLOY_APPLICATION,
#   DEPLOY_USER (defaults to the caller-identity ARN's last segment).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=apps/infra/scripts/_aws-env.sh
. "$HERE/_aws-env.sh"
: "${BUDGET_EMAIL:?set BUDGET_EMAIL=you@example.com}"

# The User governance tag = the deploying identity (last ARN segment, e.g. email).
DEPLOY_USER="${DEPLOY_USER:-$(aws sts get-caller-identity --query Arn --output text | awk -F/ '{print $NF}')}"

# The IAM role permitted to invoke the IAM-auth query Function URL (slice 3). Defaults
# to the deploying caller's underlying role ARN so the same identity that deploys can
# invoke; override with INVOKER_ROLE_ARN for a separate caller. Derived from the
# assumed-role caller ARN (arn:aws:sts::<acct>:assumed-role/<role>/<session>).
if [ -z "${INVOKER_ROLE_ARN:-}" ]; then
  _acct="$(aws sts get-caller-identity --query Account --output text)"
  _role="$(aws sts get-caller-identity --query Arn --output text | awk -F/ '{print $2}')"
  INVOKER_ROLE_ARN="arn:aws:iam::${_acct}:role/${_role}"
fi

# OpenSearch needs the VPC-access service-linked role pre-created, or a VPC domain
# fails to create ("you must enable a service-linked role ... to access your VPC").
# CDK does not create it. Idempotent: ignore the "has been taken" error if it exists.
# Both names are created — the provisioned (es-engine) domain uses the legacy
# es.amazonaws.com SLR for VPC access.
for svc in opensearchservice.amazonaws.com es.amazonaws.com; do
  aws iam create-service-linked-role --aws-service-name "$svc" 2>/dev/null || true
done

cdk bootstrap "aws://${CDK_DEFAULT_ACCOUNT}/${CDK_DEFAULT_REGION}" --app "$CDK_APP"
cdk deploy "$STACK" --app "$CDK_APP" --require-approval never \
  --parameters "BudgetAlarmEmail=${BUDGET_EMAIL}" \
  --parameters "InvokerRoleArn=${INVOKER_ROLE_ARN}" \
  -c "environment=${DEPLOY_ENV:-demo}" \
  -c "department=${DEPLOY_DEPARTMENT:-unspecified}" \
  -c "application=${DEPLOY_APPLICATION:-graphrag}" \
  -c "user=${DEPLOY_USER}" \
  --outputs-file "$HERE/../cdk.out/deploy-outputs.json"

echo "OK: deployed ${STACK} (User tag=${DEPLOY_USER})."
echo "Smoke: upload corpus (community/ + enhancements/ at bucket root), run the"
echo "ingestion task, then confirm the log stream shows non-zero parsed/resolved counts."
