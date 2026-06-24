#!/usr/bin/env bash
# Tear down the slice-1 stack -- removes every billable resource (charter
# principle 4). Uses the cached creds; `cdk destroy` blocks until CloudFormation
# finishes, so no polling is needed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=apps/infra/scripts/_aws-env.sh
. "$HERE/_aws-env.sh"

cdk destroy "$STACK" --app "$CDK_APP" --force
echo "OK: destroyed ${STACK}. Verify with scripts/status.sh (expect DOES_NOT_EXIST)."
