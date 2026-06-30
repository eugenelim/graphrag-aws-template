#!/usr/bin/env bash
# Deploy the slice stack. Every tunable (stack name, region, governance-tag defaults,
# the SLR service list, the outputs path) lives in config.env; per-deploy values live
# in config.local.env (gitignored) or the environment. Credentials are cached once (see
# _aws-env.sh) so the upstream auth provider is not re-hit per call. `cdk deploy` blocks
# until CloudFormation settles -- do NOT poll describe-stacks in a loop; one blocking
# process is the whole signal. Use status.sh for a single ad-hoc status check.
#
# Required: BUDGET_EMAIL (set in config.local.env or env), e.g. you@example.com.
# Optional: INVOKER_ROLE_ARN (the role allowed to invoke the query Function URL;
#   defaults to the deploying caller's underlying role ARN).
# Optional tag overrides (config.env defaults): DEPLOY_ENV, DEPLOY_DEPARTMENT,
#   DEPLOY_APPLICATION, and DEPLOY_USER (defaults to the caller-identity ARN's last segment).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=apps/infra/scripts/_aws-env.sh
. "$HERE/_aws-env.sh"
: "${BUDGET_EMAIL:?set BUDGET_EMAIL (config.local.env or env), e.g. you@example.com}"

# The User governance tag = the deploying identity (last ARN segment, e.g. email).
DEPLOY_USER="${DEPLOY_USER:-$(aws sts get-caller-identity --query Arn --output text | awk -F/ '{print $NF}')}"

# The IAM role permitted to invoke the IAM-auth query Function URL (slice 3). Defaults
# to the deploying caller's underlying role ARN so the same identity that deploys can
# invoke; override via INVOKER_ROLE_ARN (config.local.env or env) for a separate caller.
# Derived from the assumed-role caller ARN (arn:aws:sts::<acct>:assumed-role/<role>/<session>).
if [ -z "${INVOKER_ROLE_ARN:-}" ]; then
  _acct="$(aws sts get-caller-identity --query Account --output text)"
  _role="$(aws sts get-caller-identity --query Arn --output text | awk -F/ '{print $2}')"
  INVOKER_ROLE_ARN="arn:aws:iam::${_acct}:role/${_role}"
fi

# The AWS-managed S3 prefix list id the in-VPC compute SGs allow 443 egress to (the corpus
# read rides the S3 gateway endpoint). Resolved PER-REGION from the managed prefix list name
# so the closed-egress posture is correct in any region (the CDK default is us-east-1's).
# Override with S3_PREFIX_LIST_ID (config.local.env or env) to pin it explicitly.
if [ -z "${S3_PREFIX_LIST_ID:-}" ]; then
  S3_PREFIX_LIST_ID="$(aws ec2 describe-managed-prefix-lists \
    --region "$CDK_DEFAULT_REGION" \
    --filters "Name=prefix-list-name,Values=com.amazonaws.${CDK_DEFAULT_REGION}.s3" \
    --query 'PrefixLists[0].PrefixListId' --output text)"
fi
: "${S3_PREFIX_LIST_ID:?could not resolve the S3 managed prefix list id for ${CDK_DEFAULT_REGION}}"

# OpenSearch needs the VPC-access service-linked role(s) pre-created, or a VPC domain
# fails to create ("you must enable a service-linked role ... to access your VPC"). CDK
# does not create them. Idempotent: ignore the "has been taken" error if one exists.
# The list is space-separated and iterated by an intentional word-split.
# shellcheck disable=SC2086
for svc in $OPENSEARCH_SLR_SERVICES; do
  aws iam create-service-linked-role --aws-service-name "$svc" 2>/dev/null || true
done

cdk bootstrap "aws://${CDK_DEFAULT_ACCOUNT}/${CDK_DEFAULT_REGION}" --app "$CDK_APP"
cdk deploy "$STACK" --app "$CDK_APP" --require-approval never \
  --parameters "BudgetAlarmEmail=${BUDGET_EMAIL}" \
  --parameters "InvokerRoleArn=${INVOKER_ROLE_ARN}" \
  --parameters "S3PrefixListId=${S3_PREFIX_LIST_ID}" \
  -c "environment=${DEPLOY_ENV}" \
  -c "department=${DEPLOY_DEPARTMENT}" \
  -c "application=${DEPLOY_APPLICATION}" \
  -c "user=${DEPLOY_USER}" \
  --outputs-file "$DEPLOY_OUTPUTS_FILE"

echo "OK: deployed ${STACK} (User tag=${DEPLOY_USER})."
echo "Smoke: upload corpus (community/ + enhancements/ at bucket root), run the"
echo "ingestion task, then confirm the log stream shows non-zero parsed/resolved counts."
