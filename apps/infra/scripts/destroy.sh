#!/usr/bin/env bash
# Tear down the slice-1 stack -- removes every billable resource (charter
# principle 4), INCLUDING the in-VPC smoke probe (it is a stack resource, so
# `cdk destroy` removes the Lambda + its SG + role + stack-managed log group).
#
# Belt-and-suspenders: CDK does NOT manage the auto-created /aws/lambda/<fn> log
# groups (e.g. the S3 auto-delete custom-resource provider's), so they survive a
# destroy. We capture every Lambda name BEFORE destroying, then sweep those log
# groups after -- so teardown genuinely leaves nothing behind.
#
# Uses cached creds; `cdk destroy` blocks until CloudFormation finishes (no poll).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=apps/infra/scripts/_aws-env.sh
. "$HERE/_aws-env.sh"

# Capture Lambda physical names before they vanish, to sweep their log groups.
FN_NAMES="$(aws cloudformation list-stack-resources --stack-name "$STACK" \
  --query "StackResourceSummaries[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" \
  --output text 2>/dev/null || true)"

cdk destroy "$STACK" --app "$CDK_APP" --force

for fn in $FN_NAMES; do
  lg="/aws/lambda/${fn}"
  if aws logs delete-log-group --log-group-name "$lg" 2>/dev/null; then
    echo "swept leftover log group: $lg"
  fi
done

echo "OK: destroyed ${STACK} (incl. smoke probe) + swept Lambda log groups."
echo "Verify with scripts/status.sh (expect DOES_NOT_EXIST)."
