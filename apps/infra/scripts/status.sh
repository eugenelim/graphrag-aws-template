#!/usr/bin/env bash
# Print the stack status with a SINGLE describe-stacks call (cached creds).
#
# Deliberately one-shot: deploy.sh / destroy.sh already block until the stack
# settles, so there is nothing to poll. If you must watch a long operation, call
# this no faster than ~60s -- a tight loop is what rate-limits the auth provider.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=apps/infra/scripts/_aws-env.sh
. "$HERE/_aws-env.sh"

aws cloudformation describe-stacks --stack-name "$STACK" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST"
