#!/usr/bin/env bash
# Tear down the slice-1 stack -- removes every billable resource (charter
# principle 4), INCLUDING the in-VPC smoke probe (it is a stack resource, so
# `cdk destroy` removes the Lambda + its SG + role + stack-managed log group).
#
# Belt-and-suspenders: CDK does NOT manage the auto-created /aws/lambda/<fn> log
# groups (e.g. the S3 auto-delete custom-resource provider's), so they survive a
# destroy. We sweep them after, from two sources so nothing is missed:
#   1. the Lambda physical names captured BEFORE destroy (covers any custom-named fn), and
#   2. a prefix scan of /aws/lambda/<STACK>- AFTER destroy -- the CDK custom-resource
#      PROVIDER Lambdas (the framework provider + the S3 auto-delete provider) only run
#      DURING `cdk destroy`, so their log groups can appear after the pre-destroy capture;
#      the prefix scan catches them -- so teardown genuinely leaves nothing behind.
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

# After destroy, sweep this stack's leftover Lambda log groups from both sources
# (deduped): (1) the pre-destroy FN_NAMES capture covers any custom-named function whose
# log group does NOT carry the ${STACK}- prefix; (2) the prefix scan catches the provider
# Lambdas. The /aws/lambda/<STACK>- prefix is stack-specific (the trailing hyphen keeps it
# from matching e.g. a GraphragSlice10 stack).
if ! PREFIX_GROUPS="$(aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/${STACK}-" \
  --query 'logGroups[*].logGroupName' --output text 2>/dev/null)"; then
  echo "WARNING: log-group prefix scan failed; provider log groups may remain (sweep partial)." >&2
  PREFIX_GROUPS=""
fi
{
  for fn in $FN_NAMES; do echo "/aws/lambda/${fn}"; done
  for lg in $PREFIX_GROUPS; do echo "$lg"; done
} | sort -u | while read -r lg; do
  [ -n "$lg" ] || continue
  # Distinguish "already gone" (expected: stack-managed or never created) from a real
  # failure (throttle, transient error) so a genuine leftover isn't silently reported OK.
  if err="$(aws logs delete-log-group --log-group-name "$lg" 2>&1 >/dev/null)"; then
    echo "swept leftover log group: $lg"
  else
    case "$err" in
      *ResourceNotFoundException*) : ;;  # already gone -- fine
      *) echo "WARNING: could not delete log group $lg: $err" >&2 ;;
    esac
  fi
done

echo "OK: destroyed ${STACK} (incl. smoke probe) + swept Lambda log groups."
echo "Verify with scripts/status.sh (expect DOES_NOT_EXIST)."
